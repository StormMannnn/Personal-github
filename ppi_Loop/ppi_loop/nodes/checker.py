"""Independent Kimi audit layers. These nodes never create business objects."""

from __future__ import annotations

import asyncio
import copy
import re
import unicodedata
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import config
from ..prompts.contracts import (
    BENEFIT_CHECKER_PROMPT,
    COMBINATION_CHECKER_PROMPT,
    PRODUCT_COVERAGE_CHECKER_PROMPT,
    PRODUCT_SCORER_PROMPT,
)
from ..schemas import (
    CheckerAudit,
    CombinationAudit,
    ProductCoverageAudit,
    ProductScoreAudit,
)
from .common import call_json_model, is_contract_error, multimodal_messages
from .n6_price import (
    EXPLICIT_DISCOUNT_KEYWORDS,
    extract_coupon_amount,
    extract_coupon_discount,
    is_final_price,
    is_summary_coupon,
)


_CHECKER_SEMAPHORE = asyncio.Semaphore(max(config.CHECKER_CONCURRENCY, 1))

_SELF_CONTRADICTION_MARKERS = (
    "重新审计", "重新审查", "实际上已覆盖", "Qwen实际已覆盖",
    "不存在漏检", "无漏检", "但是", "然而", "虽然", "但需",
    "可能", "需确认",
)
_FIELD_SCORE_LIMITS = {
    "raw_name": ("raw_name", 20),
    "qwen_matched_name": ("matched_name", 30),
    "role": ("role", 15),
    "spec_text": ("specification", 20),
    "qty_text": ("quantity", 10),
    "combination": ("existence_and_grouping", 5),
}
_SAFE_METADATA_FIELDS = {
    "analysis", "reasoning", "notes", "note", "summary", "message",
    "explanation",
}
_PROTECTED_CHECKER_FIELDS = {
    "issues", "field_issues", "combination_checks", "coverage_checks",
    "item_evaluations", "product_reconciliation_decisions",
    "products", "prices", "coupons", "sales_combinations",
    "supplemental_products", "operations", "patch", "matched_name",
    "qwen_matched_name",
}
_SCORE_COMPONENT_FIELDS = (
    "raw_name", "matched_name", "role", "specification", "quantity",
    "existence_and_grouping",
)
_SPEC_VALUE_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(ml|毫升|g|克|kg|千克|l|升)(?![A-Za-z])",
    re.IGNORECASE,
)
_COMPACT_PUNCTUATION_RE = re.compile(r"[\s，,。；;：:、·（）()\[\]【】{}]+")
_EXPLICIT_SUMMARY_MARKERS = (
    "共", "共计", "合计", "总计", "总量", "正装量", "相当于", "到手总量",
)


def _normalize_unit(value: str) -> str:
    # 把中文单位(毫升/克/千克/升)统一成小写英文缩写(ml/g/kg/l),其他原样返回。
    # 规格比较和汇总算术都依赖这种统一量纲。
    unit = value.strip().casefold()
    return {
        "毫升": "ml", "克": "g", "千克": "kg", "升": "l",
    }.get(unit, unit)


def _parse_largest_spec(value: object) -> tuple[Decimal, str] | None:
    # 从文字里找出所有"数字+规格单位"组合,返回数字最大的那组(规格值,单位)。
    # 解析不到任何规格就返回 None,这是 summary_only 汇总算术验证的取数入口。
    matches = [
        (Decimal(number), _normalize_unit(unit))
        for number, unit in _SPEC_VALUE_RE.findall(str(value or ""))
    ]
    return max(matches, key=lambda item: item[0]) if matches else None


def _item_spec_total(item: dict[str, Any]) -> tuple[Decimal, str] | None:
    # 算单件商品的"规格×数量"总含量(如 50ml×2=100ml),供与汇总行做总量对账。
    # 规格/数量解析失败、非正或无单位时返回 None,表示该明细不可参与算术。
    parsed_spec = _parse_largest_spec(item.get("spec_text"))
    try:
        spec = Decimal(str(item.get("spec") or (parsed_spec[0] if parsed_spec else "0")))
        qty_value = item.get("qty") or item.get("qty_text") or "1"
        qty_match = re.search(r"\d+(?:\.\d+)?", str(qty_value))
        qty = Decimal(qty_match.group(0) if qty_match else "1")
    except (InvalidOperation, ValueError, TypeError):
        return None
    unit = _normalize_unit(
        str(item.get("unit") or (parsed_spec[1] if parsed_spec else ""))
    )
    if spec <= 0 or qty <= 0 or not unit:
        return None
    return spec * qty, unit


def _compact_text(value: object) -> str:
    # 把文本做 NFKC 归一化、转小写,再去掉所有空白和标点,只留字符骨架。
    # 用于证据文字与商品名称/规格的宽松子串匹配,避免标点差异导致误判。
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _COMPACT_PUNCTUATION_RE.sub("", text)


def _matches_reconciliation_summary(check: Any, state: dict) -> bool:
    # 判断这条 missing 证据是否对应 n3 已决定删除的"等总量汇总行"。
    # 用名称+规格的压缩子串去匹配 product_reconciliation_decisions 中
    # reason 为 equivalent_spec_summary 的条目,匹配上就不许再判为漏品。
    evidence = _compact_text(
        f"{check.evidence_text} {check.object_description}"
    )
    for decision in state.get("product_reconciliation_decisions") or []:
        if decision.get("reason") != "equivalent_spec_summary":
            continue
        name = _compact_text(decision.get("dropped_raw_name"))
        spec = _compact_text(
            f"{decision.get('dropped_spec')}{decision.get('dropped_unit')}"
        )
        if spec and spec in evidence and (not name or name in evidence):
            return True
    return False


def _is_safe_metadata_value(value: Any) -> bool:
    # 判断元数据字段值是否安全:None、字符串、空列表或空字典才算安全,可放心删除。
    # 其余非空结构化内容属于越权信息,由上层按 CHECKER_UNSAFE_METADATA 拒绝。
    return value is None or isinstance(value, str) or value == [] or value == {}


def _decimal_number(value: Any, path: str) -> Decimal:
    # 把任意值安全转成 Decimal:布尔值、非法数字、非有限数一律抛 CHECKER_INVALID_TOTAL。
    # 是评分分项合计校验的取数入口,报错时带上 JSON 路径方便定位问题字段。
    if isinstance(value, bool):
        raise ValueError(f"CHECKER_INVALID_TOTAL: {path}必须是数字")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"CHECKER_INVALID_TOTAL: {path}必须是数字") from exc
    if not number.is_finite():
        raise ValueError(f"CHECKER_INVALID_TOTAL: {path}必须是有限数字")
    return number


def _sanitize_score_totals(payload: dict, dropped_paths: list[str]) -> None:
    # 清洗 item_evaluations 里每个 score_breakdown 的 total/total_score 冗余字段。
    # 规则:别名必须严格等于六个分项之和,一致才删除并把路径记入 dropped_paths 备查。
    evaluations = payload.get("item_evaluations")
    if not isinstance(evaluations, list):
        return
    for index, evaluation in enumerate(evaluations):
        if not isinstance(evaluation, dict):
            continue
        breakdown = evaluation.get("score_breakdown")
        if not isinstance(breakdown, dict):
            continue
        aliases = [key for key in ("total", "total_score") if key in breakdown]
        if not aliases:
            continue
        base_path = f"$.item_evaluations[{index}].score_breakdown"
        try:
            expected = sum(
                (_decimal_number(breakdown[key], f"{base_path}.{key}")
                 for key in _SCORE_COMPONENT_FIELDS),
                Decimal(0),
            )
        except KeyError as exc:
            raise ValueError(
                f"CHECKER_INVALID_TOTAL: {base_path}缺少评分分项{exc.args[0]}"
            ) from exc
        for alias in aliases:
            path = f"{base_path}.{alias}"
            actual = _decimal_number(breakdown[alias], path)
            if actual != expected:
                raise ValueError(
                    f"CHECKER_TOTAL_MISMATCH: {path}={actual}，分项合计={expected}"
                )
            breakdown.pop(alias)
            dropped_paths.append(path)


def sanitize_checker_payload(payload: Any, response_schema: type) -> tuple[Any, list[str]]:
    # 校验前安全清洗(深拷贝后操作):只删顶层安全元数据(notes/summary/analysis 等,
    # 值必须为 str/None/空容器)和 score_breakdown 里与分项合计一致的 total/total_score。
    # 其余跨层业务字段只要在本层 schema 之外且命中保护清单,就按 CHECKER_FORBIDDEN_EXTRA 拒绝。
    """Drop only explicitly safe Checker metadata before strict validation."""
    if not isinstance(payload, dict):
        return payload, []
    cleaned = copy.deepcopy(payload)
    dropped_paths: list[str] = []
    allowed_fields = set(response_schema.model_fields)
    for key in cleaned:
        if key in _PROTECTED_CHECKER_FIELDS and key not in allowed_fields:
            raise ValueError(
                f"CHECKER_FORBIDDEN_EXTRA: $.{key}超出当前Checker层职责"
            )
    for key in _SAFE_METADATA_FIELDS:
        if key not in cleaned:
            continue
        if not _is_safe_metadata_value(cleaned[key]):
            raise ValueError(
                f"CHECKER_UNSAFE_METADATA: $.{key}包含非空结构化内容"
            )
        cleaned.pop(key)
        dropped_paths.append(f"$.{key}")
    _sanitize_score_totals(cleaned, dropped_paths)
    return cleaned, dropped_paths


def _merge_stats(total: dict[str, int], current: dict[str, int]) -> None:
    # 把单次调用的 token 统计(prompt/completion/reasoning)累加进累计字典 total。
    # 多次重试的用量都汇入 total,最后统一记账,不覆盖历史。
    for key in ("prompt", "completion", "reasoning"):
        total[key] = total.get(key, 0) + int(current.get(key, 0) or 0)


def _issue_text_errors(issue: Any) -> list[str]:
    # 检查 issue 文本的硬性规范:observed_fact 超 240 字,或包含自我推翻词表
    # (_SELF_CONTRADICTION_MARKERS,如"重新审计""但是""可能")都要报错,防 Kimi 叙事自相矛盾。
    errors: list[str] = []
    fact = issue.observed_fact.strip()
    if len(fact) > 240:
        errors.append(f"{issue.code}.observed_fact过长，必须只保留最终事实")
    markers = [marker for marker in _SELF_CONTRADICTION_MARKERS if marker in fact]
    if markers:
        errors.append(f"{issue.code}.observed_fact包含自我推翻表达: {markers}")
    return errors


def _combination_audit_semantic_errors(
    parsed: CombinationAudit, state: dict
) -> list[str]:
    # 组合层语义校验:逐条检查 evidence_kind/status 状态机与组合ID引用是否合法、证据ID是否重复,
    # 并核对每个 Maker 销售组合都至少有一条证据覆盖(否则报"无对应证据检查")。
    # 对 issues 再做文本与作用域检查,强制业务 code 只能由 combination_checks 确定性生成。
    """Layer 1 must classify each combination-forming/non-forming evidence."""
    errors: list[str] = []
    combinations = {
        str(item.get("combination_id")): item
        for item in state.get("sales_combinations") or []
        if item.get("combination_id")
    }
    evidence_ids: set[str] = set()
    audited_combination_ids: set[str] = set()
    for check in parsed.combination_checks:
        if not check.evidence_id.strip():
            errors.append("combination_checks.evidence_id不得为空")
        elif check.evidence_id in evidence_ids:
            errors.append(f"重复combination evidence_id: {check.evidence_id}")
        evidence_ids.add(check.evidence_id)

        matched = check.matched_combination_ids
        if len(matched) != len(set(matched)):
            errors.append(f"{check.evidence_id}.matched_combination_ids不得重复")
        unknown = sorted(set(matched) - set(combinations))
        if unknown:
            errors.append(f"{check.evidence_id}引用不存在的销售组合ID: {unknown}")

        if check.evidence_kind == "purchase_option":
            if check.status == "covered" and len(matched) != 1:
                errors.append(
                    f"{check.evidence_id}是covered购买方案，必须且只能关联一个组合ID"
                )
            elif check.status == "missing" and matched:
                errors.append(
                    f"{check.evidence_id}是missing购买方案，不得关联现有组合ID"
                )
            elif check.status == "ignored":
                errors.append(
                    f"{check.evidence_id}是购买方案，不得标记ignored"
                )
            elif check.status == "invalid_assignment" and not matched:
                errors.append(
                    f"{check.evidence_id}标记invalid_assignment但未指出错误组合ID"
                )
        else:
            if check.status not in {"ignored", "invalid_assignment"}:
                errors.append(
                    f"{check.evidence_id}不是购买方案，只能标记ignored或invalid_assignment"
                )
            if check.status == "ignored" and matched:
                errors.append(
                    f"{check.evidence_id}标记ignored但仍关联了组合ID"
                )
            if check.status == "invalid_assignment" and not matched:
                errors.append(
                    f"{check.evidence_id}标记invalid_assignment但未指出错误组合ID"
                )

        if check.expected_main_spec_text and check.evidence_kind != "purchase_option":
            errors.append(
                f"{check.evidence_id}不是购买方案，不得填写expected_main_spec_text"
            )
        if check.status in {"covered", "invalid_assignment"}:
            audited_combination_ids.update(
                combo_id for combo_id in matched if combo_id in combinations
            )

    unaudited = sorted(set(combinations) - audited_combination_ids)
    if unaudited:
        errors.append(f"以下Maker销售组合没有对应证据检查: {unaudited}")

    for issue in parsed.issues:
        errors.extend(_issue_text_errors(issue))
        if issue.scope not in {"combination", "global"}:
            errors.append(f"{issue.code}.scope必须是combination或global")
        if issue.target_item_id is not None:
            errors.append(f"{issue.code}不得引用商品target_item_id")
        if any(field != "combination" for field in issue.affected_fields):
            errors.append(f"{issue.code}.affected_fields包含非组合字段")
        if issue.code in {
            "MISSING_SALES_COMBINATION", "INVALID_SALES_COMBINATION",
            "MISSING_COMBINATION_MAIN", "WRONG_COMBINATION_MAIN_SPEC",
        }:
            errors.append(f"{issue.code}必须由combination_checks确定性生成")
    return errors


def _coverage_audit_semantic_errors(
    parsed: ProductCoverageAudit, state: dict
) -> list[str]:
    # 覆盖层语义校验:约束六种 coverage status 的引用与算术(如同一证据不得既 covered 又 missing,
    # 引用的商品ID必须存在);summary_only 必须能解析汇总规格,且引用明细的"规格×数量"
    # 合计严格等于证据总量;missing 不得命中已删除的等总量汇总行。
    """Layer 2 validates visual coverage and product-field findings only."""
    errors: list[str] = []
    known_ids = {
        str(item.get("item_id")) for item in state.get("normalized_items") or []
        if item.get("item_id")
    }
    evidence_ids: set[str] = set()
    evidence_statuses: dict[str, str] = {}
    for check in parsed.coverage_checks:
        if not check.evidence_id.strip():
            errors.append("coverage_checks.evidence_id不得为空")
        elif check.evidence_id in evidence_ids:
            errors.append(f"重复evidence_id: {check.evidence_id}")
        evidence_ids.add(check.evidence_id)

        unknown = sorted(set(check.matched_item_ids) - known_ids)
        if unknown:
            errors.append(f"{check.evidence_id}引用不存在的Qwen商品ID: {unknown}")
        if len(check.matched_item_ids) != len(set(check.matched_item_ids)):
            errors.append(f"{check.evidence_id}.matched_item_ids不得重复")
        if check.status in {"covered", "summary_only", "duplicate_display"} \
                and not check.matched_item_ids:
            errors.append(
                f"{check.evidence_id}标记{check.status}但没有matched_item_ids"
            )
        if check.status in {"missing", "non_cosmetic"} and check.matched_item_ids:
            errors.append(
                f"{check.evidence_id}标记{check.status}但不应关联Qwen商品"
            )
        if check.status == "missing" and not (
            check.evidence_text.strip() or check.object_description.strip()
        ):
            errors.append(f"{check.evidence_id}标记missing但缺少文字或视觉证据")
        if check.status == "missing" and _matches_reconciliation_summary(check, state):
            errors.append(
                f"{check.evidence_id}对应已确定删除的等总量汇总行，不得标记missing"
            )

        evidence_key = _compact_text(
            check.evidence_text or check.object_description
        )
        previous_status = evidence_statuses.get(evidence_key) if evidence_key else None
        if previous_status and previous_status != check.status:
            errors.append(
                f"同一证据被同时标记为{previous_status}和{check.status}: "
                f"{check.evidence_id}"
            )
        elif evidence_key:
            evidence_statuses[evidence_key] = check.status

        if check.status == "summary_only":
            summary_text = f"{check.evidence_text} {check.object_description}"
            expected = _parse_largest_spec(
                summary_text
            )
            referenced = [
                _item_spec_total(item)
                for item in state.get("normalized_items") or []
                if str(item.get("item_id")) in set(check.matched_item_ids)
            ]
            if expected is None:
                errors.append(
                    f"{check.evidence_id}标记summary_only但无法解析汇总规格"
                )
            elif (
                not any(marker in summary_text for marker in _EXPLICIT_SUMMARY_MARKERS)
                and len(check.matched_item_ids) < 2
            ):
                errors.append(
                    f"{check.evidence_id}没有明确汇总语义，且未引用两条以上明细，"
                    "不得标记summary_only"
                )
            elif any(value is None for value in referenced):
                errors.append(
                    f"{check.evidence_id}引用的明细存在不可计算规格或数量"
                )
            else:
                totals = [value for value in referenced if value is not None]
                units = {unit for _, unit in totals}
                actual = sum((value for value, _ in totals), Decimal(0))
                if units != {expected[1]} or actual != expected[0]:
                    errors.append(
                        f"{check.evidence_id}汇总算术不成立: "
                        f"证据={expected[0]}{expected[1]}，"
                        f"引用明细={actual}{'/'.join(sorted(units)) or '无单位'}"
                    )

    for issue in parsed.field_issues:
        errors.extend(_issue_text_errors(issue))
        if issue.code == "MISSING_PRODUCT":
            errors.append("MISSING_PRODUCT必须由coverage_checks的missing记录生成")
        if issue.scope not in {"product", "global"}:
            errors.append(f"{issue.code}.scope必须是product或global")
        if issue.target_item_id and issue.target_item_id not in known_ids:
            errors.append(f"{issue.code}引用不存在的Qwen商品ID")
        if (
            issue.scope == "product" and issue.target_item_id
            and issue.code.startswith("WRONG_") and not issue.affected_fields
        ):
            errors.append(f"{issue.code}必须填写affected_fields")
    return errors


def _score_audit_semantic_errors(
    parsed: ProductScoreAudit, state: dict
) -> list[str]:
    # 评分层语义校验:每个待评分商品必须恰好评一次(查重复、查未知ID、查遗漏);
    # 商品名不在词包内时 matched_name 必须为 0 分;并与覆盖层声明的字段错误联动,
    # 声明有错的字段(如 raw_name/role/specification)分数不允许拿到满分。
    """Layer 3 must score each immutable Qwen item exactly once."""
    errors: list[str] = []
    products = {
        str(item.get("item_id")): item for item in state.get("normalized_items") or []
        if item.get("item_id") and item.get("is_cosmetic") is True
        and not item.get("is_shopping_credit")
    }
    targets = [item.target_item_id for item in parsed.item_evaluations]
    duplicates = sorted({target for target in targets if targets.count(target) > 1})
    unknown = sorted(set(targets) - set(products))
    missing = sorted(set(products) - set(targets))
    if duplicates:
        errors.append(f"评分对象ID重复: {duplicates}")
    if unknown:
        errors.append(f"评分引用不存在的Qwen商品ID: {unknown}")
    if missing:
        errors.append(f"缺少Qwen商品评分: {missing}")

    evaluations = {
        evaluation.target_item_id: evaluation
        for evaluation in parsed.item_evaluations
    }
    word_package = set(state.get("word_package") or [])
    for item_id, product in products.items():
        evaluation = evaluations.get(item_id)
        if not evaluation:
            continue
        qwen_name = product.get("qwen_matched_name")
        if (
            (qwen_name is None or qwen_name not in word_package)
            and evaluation.score_breakdown.matched_name != 0
        ):
            errors.append(
                f"{item_id}.qwen_matched_name为空或词包外，但matched_name评分不是0/30"
            )

    for issue in state.get("coverage_checker_issues") or []:
        target = issue.get("target_item_id")
        evaluation = evaluations.get(str(target or ""))
        if not evaluation:
            continue
        for field_name in issue.get("affected_fields") or []:
            score_rule = _FIELD_SCORE_LIMITS.get(field_name)
            if not score_rule:
                continue
            score_name, full_score = score_rule
            actual_score = getattr(evaluation.score_breakdown, score_name)
            if actual_score >= full_score:
                errors.append(
                    f"{issue.get('code')}声明{field_name}错误，"
                    f"但{target}评分仍为{actual_score}/{full_score}"
                )
    return errors


def _benefit_audit_semantic_errors(
    parsed: CheckerAudit, state: dict
) -> list[str]:
    # 权益层语义校验:先禁止越权(不得返回商品评分/覆盖检查),再逐条核对 issue。
    # 每条 issue 都要能对照 raw/normalized 价格与券做确定性复现——MISSING_COUPON 须有
    # 非汇总的明确减免证据且确未入账,冲突类 issue 只能由 normalized 层真实冲突触发。
    errors: list[str] = []
    if parsed.item_evaluations:
        errors.append("权益Checker不得返回商品评分")
    if parsed.coverage_checks:
        errors.append("权益Checker不得返回商品覆盖检查")

    raw_prices = state.get("raw_prices") or []
    raw_coupons = state.get("raw_coupons") or []
    normalized_prices = state.get("price_objects") or []
    normalized_coupons = state.get("coupon_objects") or []

    def combo_ids(record: dict[str, Any], *, normalized: bool = False) -> set[str]:
        # 取一条价格/券记录涉及的组合ID集合:normalized 对象固定读"组合ID"字段(缺省 S1),
        # raw 对象读 combination_ids;只有一个销售组合时兜底用那个组合。
        if normalized:
            return {str(record.get("组合ID") or "S1")}
        values = [str(value) for value in record.get("combination_ids") or []]
        if values:
            return set(values)
        combinations = state.get("sales_combinations") or []
        if len(combinations) == 1:
            return {str(combinations[0].get("combination_id") or "S1")}
        return {"UNK"}

    def matches_evidence(record: dict[str, Any], field: str, issue: Any) -> bool:
        # 判断一条 raw 记录是否对应本 issue:item_id 直接命中,或用压缩文本做证据与
        # 记录字段的双向包含匹配(互相包含即算对上),用于把 issue 定位到具体证据行。
        if issue.target_item_id and str(record.get("item_id")) == issue.target_item_id:
            return True
        evidence = _compact_text(issue.evidence_text)
        candidate = _compact_text(record.get(field))
        return bool(
            evidence and candidate
            and (evidence == candidate or evidence in candidate or candidate in evidence)
        )

    def relevant_combo(issue: Any, record_combos: set[str]) -> bool:
        # issue 未限定组合,或限定的组合在记录的组合集合里,才算这条记录与本 issue 相关。
        return not issue.combination_id or issue.combination_id in record_combos

    normalized_price_pairs = {
        (str(item.get("组合ID") or "S1"), str(item.get("金额") or ""))
        for item in normalized_prices if item.get("金额") not in {None, ""}
    }
    normalized_coupon_pairs = {
        (str(item.get("组合ID") or "S1"), str(item.get("金额") or ""))
        for item in normalized_coupons
        if item.get("金额") not in {None, "", "无"}
        and not str(item.get("使用条件") or "").endswith("折")
    }
    normalized_overlap = normalized_price_pairs & normalized_coupon_pairs

    for issue in parsed.issues:
        errors.extend(_issue_text_errors(issue))
        if issue.scope not in {"price", "coupon", "combination", "global"}:
            errors.append(f"{issue.code}.scope不属于权益Checker范围")

        evidence = issue.evidence_text.strip()
        price_records = [
            item for item in raw_prices
            if matches_evidence(item, "context_text", issue)
            and relevant_combo(issue, combo_ids(item))
        ]
        coupon_records = [
            item for item in raw_coupons
            if matches_evidence(item, "text", issue)
            and relevant_combo(issue, combo_ids(item))
        ]
        evidence_is_summary = bool(evidence and is_summary_coupon(evidence))
        amount = extract_coupon_amount(evidence) if evidence else None
        discount = extract_coupon_discount(evidence) if evidence else None
        deterministic_program_issue = False

        if evidence_is_summary and issue.code in {
            "MISSING_COUPON", "NORMALIZATION_LOSS", "MISSING_NORMALIZED_PRICE",
        }:
            errors.append(
                f"{issue.code}引用的是优惠汇总，不是独立优惠券或到手价"
            )

        if issue.code == "MISSING_COUPON":
            if not evidence or (not amount and not discount) or evidence_is_summary:
                errors.append(
                    "MISSING_COUPON必须有非汇总的明确减免金额或X折证据"
                )
            if coupon_records:
                errors.append(
                    "MISSING_COUPON证据已存在于qwen_raw_coupons，不得再判为Qwen漏券"
                )
            expected_value = discount or amount
            if expected_value and any(
                str(item.get("金额")) == expected_value
                and relevant_combo(issue, combo_ids(item, normalized=True))
                for item in normalized_coupons
            ):
                errors.append(
                    "MISSING_COUPON对应金额或折扣已进入normalized_coupons"
                )

        elif issue.code == "MISSING_NORMALIZED_PRICE":
            source_texts = [str(item.get("context_text") or "") for item in price_records]
            if not source_texts and evidence:
                source_texts = [evidence]
            final_sources = [text for text in source_texts if is_final_price(text)]
            if not final_sources:
                errors.append(
                    "MISSING_NORMALIZED_PRICE的证据不具备实际支付价语义"
                )
            else:
                expected_amounts = {
                    str(item.get("amount")) for item in price_records
                    if is_final_price(str(item.get("context_text") or ""))
                }
                if not expected_amounts and amount:
                    expected_amounts.add(amount)
                already_normalized = any(
                    str(item.get("金额")) in expected_amounts
                    and relevant_combo(issue, combo_ids(item, normalized=True))
                    for item in normalized_prices
                )
                if already_normalized:
                    errors.append(
                        "MISSING_NORMALIZED_PRICE所指到手价已进入normalized_prices"
                    )
                elif expected_amounts:
                    deterministic_program_issue = True

        elif issue.code in {"PRICE_COUPON_ROLE_CONFLICT", "PRICE_COUPON_CONFLICT"}:
            relevant_overlap = {
                pair for pair in normalized_overlap
                if not issue.combination_id or pair[0] == issue.combination_id
            }
            if not relevant_overlap:
                errors.append(
                    f"{issue.code}只能由normalized_prices与normalized_coupons"
                    "中同组合同金额的实际冲突触发，raw层重叠不算冲突"
                )
            else:
                deterministic_program_issue = True

        elif issue.code == "NORMALIZATION_LOSS":
            price_sources = [
                item for item in price_records
                if is_final_price(str(item.get("context_text") or ""))
            ]
            coupon_sources = [
                item for item in coupon_records
                if not is_summary_coupon(str(item.get("text") or ""))
                and (
                    extract_coupon_amount(str(item.get("text") or ""))
                    or extract_coupon_discount(str(item.get("text") or ""))
                )
            ]
            coupon_sources.extend(
                item for item in price_records
                if not is_summary_coupon(str(item.get("context_text") or ""))
                and not is_final_price(str(item.get("context_text") or ""))
                and any(
                    keyword in str(item.get("context_text") or "")
                    for keyword in EXPLICIT_DISCOUNT_KEYWORDS
                )
                and (
                    extract_coupon_amount(str(item.get("context_text") or ""))
                    or extract_coupon_discount(str(item.get("context_text") or ""))
                )
            )

            missing_price = any(
                not any(
                    str(normalized.get("金额")) == str(source.get("amount"))
                    and bool(
                        combo_ids(source)
                        & combo_ids(normalized, normalized=True)
                    )
                    for normalized in normalized_prices
                )
                for source in price_sources
            )
            missing_coupon = False
            for source in coupon_sources:
                text = str(source.get("text") or source.get("context_text") or "")
                expected_value = extract_coupon_discount(text) or extract_coupon_amount(text)
                if expected_value and not any(
                    str(normalized.get("金额")) == expected_value
                    and bool(
                        combo_ids(source)
                        & combo_ids(normalized, normalized=True)
                    )
                    for normalized in normalized_coupons
                ):
                    missing_coupon = True
                    break
            if evidence_is_summary or not (missing_price or missing_coupon):
                errors.append(
                    "NORMALIZATION_LOSS必须有raw正确证据且对应normalized对象确实缺失"
                )
            else:
                deterministic_program_issue = True

        if issue.repairable is False and not deterministic_program_issue:
            errors.append(
                f"{issue.code}不是程序可确定复现的归一化问题，"
                "repairable不得为false"
            )
    return errors


def _combination_issues_from_audit(
    parsed: CombinationAudit, state: dict
) -> list[dict[str, Any]]:
    # 把校验过的组合证据确定性翻译成业务 issue:missing 购买方案→MISSING_SALES_COMBINATION,
    # invalid_assignment→INVALID_SALES_COMBINATION,covered 但缺主品→MISSING_COMBINATION_MAIN,
    # 主品规格不符→WRONG_COMBINATION_MAIN_SPEC;最后按 code+组合+证据文本去重返回。
    """Convert validated evidence classifications into stable repair issues."""
    issues = [issue.model_dump() for issue in parsed.issues]
    products = state.get("normalized_items") or []
    for check in parsed.combination_checks:
        description = check.object_description.strip() or check.evidence_text.strip()
        if check.evidence_kind == "purchase_option" and check.status == "missing":
            issues.append({
                "code": "MISSING_SALES_COMBINATION",
                "scope": "combination",
                "severity": "fatal",
                "combination_id": None,
                "target_item_id": None,
                "evidence_text": check.evidence_text,
                "evidence_region": check.evidence_region,
                "observed_fact": f"购买方案{description}未进入Qwen销售组合台账",
                "repair_instruction": "Qwen根据该购买方案新增连续组合ID及对应主品",
                "confidence": check.confidence,
                "repairable": True,
                "affected_fields": ["combination"],
            })
            continue

        if check.status == "invalid_assignment":
            for combo_id in check.matched_combination_ids:
                issues.append({
                    "code": "INVALID_SALES_COMBINATION",
                    "scope": "combination",
                    "severity": "fatal",
                    "combination_id": combo_id,
                    "target_item_id": None,
                    "evidence_text": check.evidence_text,
                    "evidence_region": check.evidence_region,
                    "observed_fact": f"{description}不是独立购买方案，却被分配给{combo_id}",
                    "repair_instruction": "Qwen删除该错误组合或改用真实购买方案证据",
                    "confidence": check.confidence,
                    "repairable": True,
                    "affected_fields": ["combination"],
                })
            continue

        if check.evidence_kind != "purchase_option" or check.status != "covered":
            continue
        combo_id = check.matched_combination_ids[0]
        mains = [
            item for item in products
            if item.get("role") == "主品"
            and combo_id in (item.get("combination_ids") or [])
        ]
        if not mains:
            issues.append({
                "code": "MISSING_COMBINATION_MAIN",
                "scope": "combination",
                "severity": "fatal",
                "combination_id": combo_id,
                "target_item_id": None,
                "evidence_text": check.evidence_text,
                "evidence_region": check.evidence_region,
                "observed_fact": f"{combo_id}由购买方案{description}确认，但没有主品",
                "repair_instruction": "Qwen根据该购买方案补齐对应主品",
                "confidence": check.confidence,
                "repairable": True,
                "affected_fields": ["combination"],
            })
            continue

        expected = _parse_largest_spec(check.expected_main_spec_text)
        if expected and not any(
            _parse_largest_spec(item.get("spec_text")) == expected for item in mains
        ):
            issues.append({
                "code": "WRONG_COMBINATION_MAIN_SPEC",
                "scope": "combination",
                "severity": "fatal",
                "combination_id": combo_id,
                "target_item_id": None,
                "evidence_text": check.evidence_text,
                "evidence_region": check.evidence_region,
                "observed_fact": (
                    f"{combo_id}购买方案要求主品规格"
                    f"{check.expected_main_spec_text}，Qwen主品规格未对应"
                ),
                "repair_instruction": "Qwen修正该组合主品规格或组合归属",
                "confidence": check.confidence,
                "repairable": True,
                "affected_fields": ["combination", "spec_text"],
            })

    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for issue in issues:
        key = (
            issue.get("code"), issue.get("combination_id"),
            issue.get("target_item_id"), issue.get("evidence_text"),
        )
        if key not in seen:
            unique.append(issue)
            seen.add(key)
    return unique


def _coverage_validation_details(
    parsed: ProductCoverageAudit, state: dict
) -> list[dict[str, Any]]:
    # 为每条覆盖检查生成可审计明细:summary_only 附期望总量、引用明细合计与是否相等,
    # duplicate_display 只校验是否引用了已有商品,missing 附是否命中已删汇总行的守卫结论。
    products = {
        str(item.get("item_id")): item
        for item in state.get("normalized_items") or [] if item.get("item_id")
    }
    details: list[dict[str, Any]] = []
    for check in parsed.coverage_checks:
        detail: dict[str, Any] = {
            "evidence_id": check.evidence_id,
            "status": check.status,
            "matched_item_ids": list(check.matched_item_ids),
        }
        if check.status == "summary_only":
            expected = _parse_largest_spec(
                f"{check.evidence_text} {check.object_description}"
            )
            totals = [
                _item_spec_total(products[item_id])
                for item_id in check.matched_item_ids if item_id in products
            ]
            valid_totals = [value for value in totals if value is not None]
            units = sorted({unit for _, unit in valid_totals})
            actual = sum(
                (value for value, _ in valid_totals), Decimal(0)
            )
            detail.update({
                "validation": "summary_arithmetic",
                "expected_total": str(expected[0]) if expected else None,
                "expected_unit": expected[1] if expected else None,
                "referenced_total": str(actual),
                "referenced_units": units,
                "valid": bool(
                    expected and len(valid_totals) == len(totals)
                    and units == [expected[1]] and actual == expected[0]
                ),
            })
        elif check.status == "duplicate_display":
            detail.update({
                "validation": "existing_product_reference",
                "valid": bool(check.matched_item_ids),
            })
        elif check.status == "missing":
            detail.update({
                "validation": "reconciliation_guard",
                "matches_removed_summary": _matches_reconciliation_summary(
                    check, state
                ),
            })
        details.append(detail)
    return details


def _coverage_issues_from_audit(
    parsed: ProductCoverageAudit, state: dict
) -> list[dict[str, Any]]:
    # 只有 coverage_checks 里 status==missing 的结构化记录才允许生成 MISSING_PRODUCT issue;
    # 其余 field_issues 原样透传,仅把只含 section_hint 的降级为 warning 且不可修复。
    """Only structured missing coverage may create MISSING_PRODUCT."""
    issues: list[dict[str, Any]] = []
    for issue in parsed.field_issues:
        dumped = issue.model_dump()
        if set(issue.affected_fields) == {"section_hint"}:
            dumped["severity"] = "warning"
            dumped["repairable"] = False
        issues.append(dumped)

    combinations = state.get("sales_combinations") or []
    only_combination = (
        combinations[0].get("combination_id") if len(combinations) == 1 else None
    )
    for check in parsed.coverage_checks:
        if check.status != "missing":
            continue
        description = check.object_description.strip() or check.evidence_text.strip()
        region = check.evidence_region or "未定位区域"
        issues.append({
            "code": "MISSING_PRODUCT",
            "scope": "product",
            "severity": "fatal",
            "combination_id": only_combination,
            "target_item_id": None,
            "evidence_text": check.evidence_text,
            "evidence_region": check.evidence_region,
            "observed_fact": f"原图{region}的{description}未被任何Qwen商品覆盖",
            "repair_instruction": "Qwen重新读取该证据区域并补齐商品对象",
            "confidence": check.confidence,
            "repairable": True,
            "affected_fields": [],
        })
    return issues


def _semantic_retry_messages(
    base_messages: list[dict], layer: str, errors: list[str]
) -> list[dict]:
    # 构造语义重试提示:在原始消息后追加一条 user 消息,列出本轮契约错误(最多12条),
    # 并按层附上纠偏指引(coverage 的 summary_only 判定、benefit 的冲突判定),要求 Kimi 重审。
    error_text = "\n".join(f"- {error}" for error in errors[:12])
    layer_guidance = {
        "coverage": (
            "summary_only只用于明确汇总语义，或两条以上明细严格等于"
            "证据总量的情况。‘享’和‘至高’本身不是汇总证据；"
            "无法确认时改为covered或ambiguous，不得重复无效汇总结论。"
        ),
        "benefit": (
            "优惠汇总不是独立优惠券或到手价；raw_prices与raw_coupons"
            "重叠不是角色冲突；冲突只比较normalized对象。"
            "删除被程序事实否定的issue，observed_fact只保留最终事实。"
        ),
    }.get(layer, "")
    return [
        *base_messages,
        {
            "role": "user",
            "content": (
                f"上一份{layer}层输出未通过语义一致性校验。请仅基于本层职责、"
                "原图、OCR和Qwen台账重新生成，不得沿用上一份结论。\n"
                f"需修正的契约问题：\n{error_text}\n{layer_guidance}"
            ),
        },
    ]


async def _run_checker_layer(
    state: dict,
    *,
    layer: str,
    prompt: str,
    schema: type,
    validator: Callable[[Any, dict], list[str]],
    payload: dict,
) -> tuple[
    Any | None, dict[str, int], list[str], list[str], list[dict[str, Any]], str
]:
    # 统一层内重试循环:全局信号量限流 Kimi 并发,最多尝试 CHECKER_SEMANTIC_MAX_RETRY+1 次;
    # 每次先 sanitize 再校验,契约错误(JSON/Schema)与语义错误都只重试本层,连续失败把
    # 被拒审计快照收进 rejected_audits;最终返回解析结果、token 用量与各错误清单。
    base_messages = multimodal_messages(state, prompt, payload)
    messages = base_messages
    total_stats = {"prompt": 0, "completion": 0, "reasoning": 0}
    semantic_errors: list[str] = []
    sanitized_fields: list[str] = []
    rejected_audits: list[dict[str, Any]] = []
    attempts = max(config.CHECKER_SEMANTIC_MAX_RETRY, 0) + 1
    parsed = None
    error = ""
    async with _CHECKER_SEMAPHORE:
        for attempt in range(1, attempts + 1):
            current_sanitized_fields: list[str] = []
            parsed, stats, error = await call_json_model(
                config.KIMI_MODEL,
                messages,
                schema,
                f"[Checker·{layer}]",
                enable_thinking=False,
                response_adapter=lambda value: sanitize_checker_payload(
                    value, schema
                ),
                sanitized_fields=current_sanitized_fields,
            )
            sanitized_fields.extend(current_sanitized_fields)
            _merge_stats(total_stats, stats)
            if parsed is None:
                if is_contract_error(error):
                    current_errors = [f"JSON或Schema不符合{layer}层契约: {error}"]
                    semantic_errors.extend(
                        f"第{attempt}次: {item}" for item in current_errors
                    )
                    if attempt < attempts:
                        print(
                            f"[Checker·{layer}] 第{attempt}次Schema校验失败，"
                            f"仅重试本层: {error}"
                        )
                        messages = _semantic_retry_messages(
                            base_messages, layer, current_errors
                        )
                        continue
                break
            current_errors = validator(parsed, state)
            if not current_errors:
                break
            rejected_audits.append({
                "attempt": attempt,
                "payload": parsed.model_dump(),
                "semantic_errors": list(current_errors),
            })
            semantic_errors.extend(
                f"第{attempt}次: {item}" for item in current_errors
            )
            parsed = None
            error = "; ".join(current_errors)
            if attempt < attempts:
                print(
                    f"[Checker·{layer}] 第{attempt}次语义校验失败，重新审计: "
                    f"{error}"
                )
                messages = _semantic_retry_messages(
                    base_messages, layer, current_errors
                )
    sanitized_fields = list(dict.fromkeys(sanitized_fields))
    return (
        parsed, total_stats, semantic_errors, sanitized_fields,
        rejected_audits, error,
    )


def _unstable_issue(layer: str) -> dict[str, Any]:
    # 生成 CHECKER_UNSTABLE 兜底 issue:层内语义重试全失败时把该层标为不稳定并转人工。
    # observed_fact 按层给出中文标签(销售组合/商品覆盖与字段/商品评分/权益)。
    labels = {
        "combination": "销售组合",
        "coverage": "商品覆盖与字段",
        "score": "商品评分",
        "benefit": "权益",
    }
    label = labels[layer]
    return {
        "code": "CHECKER_UNSTABLE",
        "scope": "global",
        "severity": "fatal",
        "combination_id": None,
        "target_item_id": None,
        "evidence_text": "",
        "evidence_region": None,
        "observed_fact": f"Kimi{label}Checker连续输出不符合本层契约的结果",
        "repair_instruction": f"需要人工检查{label}Checker的证据与结论",
        "confidence": 1.0,
        "repairable": False,
        "affected_fields": [],
    }


def _layer_failure_update(
    layer: str,
    stats: dict[str, int],
    semantic_errors: list[str],
    sanitized_fields: list[str],
    rejected_audits: list[dict[str, Any]],
    error: str,
) -> dict:
    # 语义不稳定的失败路径更新:按层写入重试次数、语义错误、被拒快照与 token 用量,
    # 并塞入一条 CHECKER_UNSTABLE issue、把该层 complete 置 False;score 层额外清空评分。
    retries = max(len({item.split(":", 1)[0] for item in semantic_errors}) - 1, 0)
    update = {
        "checker_retry_counts": {layer: retries},
        "checker_semantic_errors_by_layer": {layer: semantic_errors},
        "checker_sanitized_fields_by_layer": {layer: sanitized_fields},
        "checker_rejected_audits_by_layer": {layer: rejected_audits},
        "token_usage": {config.KIMI_MODEL: stats},
    }
    if layer == "benefit":
        update.update({
            "checker_issues": [_unstable_issue(layer)],
            "checker_complete": False,
            "checker_retry_count": retries,
            "checker_semantic_errors": semantic_errors,
            "errors": [f"Kimi {layer}层语义不稳定: {error}"],
        })
        return update
    issue_key = {
        "combination": "combination_checker_issues",
        "coverage": "coverage_checker_issues",
        "score": "scorer_issues",
    }[layer]
    complete_key = {
        "combination": "combination_checker_complete",
        "coverage": "coverage_checker_complete",
        "score": "scorer_complete",
    }[layer]
    update[issue_key] = [_unstable_issue(layer)]
    update[complete_key] = False
    if layer == "score":
        update["latest_item_evaluations"] = []
    update["errors"] = [f"Kimi {layer}层语义不稳定: {error}"]
    return update


def _infra_failure_update(
    layer: str, stats: dict[str, int], sanitized_fields: list[str], error: str
) -> dict:
    # 基础设施失败路径更新(Kimi 调用本身报错):终态置 INFRA_ERROR、next_action=stop,
    # 重试次数记 0,错误归因到调用失败而非语义问题,便于上层直接终止流程。
    update = {
        "terminal_status": "INFRA_ERROR",
        "next_action": "stop",
        "checker_retry_counts": {layer: 0},
        "checker_semantic_errors_by_layer": {layer: []},
        "checker_sanitized_fields_by_layer": {layer: sanitized_fields},
        "checker_rejected_audits_by_layer": {layer: []},
        "errors": [f"Kimi {layer}层调用失败: {error}"],
        "token_usage": {config.KIMI_MODEL: stats},
    }
    if layer == "benefit":
        update.update({
            "checker_issues": [],
            "checker_complete": False,
            "checker_retry_count": 0,
            "checker_semantic_errors": [],
        })
    return update


async def audit_product_combinations(state: dict) -> dict:
    # 组合层入口节点:调 _run_checker_layer 跑 Kimi 组合审计,成功把结构化 evidence 存进
    # combination_checks 并确定性生成业务 issue;失败按语义不稳定/基础设施错误两条路径分流。
    parsed, stats, semantic_errors, sanitized_fields, rejected_audits, error = await _run_checker_layer(
        state,
        layer="combination",
        prompt=COMBINATION_CHECKER_PROMPT,
        schema=CombinationAudit,
        validator=_combination_audit_semantic_errors,
        payload={
            "sales_combinations": state.get("sales_combinations", []),
            "qwen_products": state.get("normalized_items", []),
        },
    )
    if parsed is None:
        if semantic_errors:
            return _layer_failure_update(
                "combination", stats, semantic_errors, sanitized_fields,
                rejected_audits, error
            )
        return _infra_failure_update("combination", stats, sanitized_fields, error)
    return {
        "combination_checks": [
            check.model_dump() for check in parsed.combination_checks
        ],
        "combination_checker_issues": _combination_issues_from_audit(
            parsed, state
        ),
        "combination_checker_complete": parsed.audit_complete,
        "checker_retry_counts": {
            "combination": len(
                {item.split(":", 1)[0] for item in semantic_errors}
            )
        },
        "checker_semantic_errors_by_layer": {
            "combination": semantic_errors
        },
        "checker_sanitized_fields_by_layer": {
            "combination": sanitized_fields
        },
        "checker_rejected_audits_by_layer": {
            "combination": rejected_audits
        },
        "token_usage": {config.KIMI_MODEL: stats},
    }


async def audit_product_coverage(state: dict) -> dict:
    # 覆盖层入口节点:跑 Kimi 商品覆盖审计,保存 coverage_checks 与逐条验证明细,
    # 再经 _coverage_issues_from_audit 产出 MISSING_PRODUCT 等 issue;失败同样分流处理。
    parsed, stats, semantic_errors, sanitized_fields, rejected_audits, error = await _run_checker_layer(
        state,
        layer="coverage",
        prompt=PRODUCT_COVERAGE_CHECKER_PROMPT,
        schema=ProductCoverageAudit,
        validator=_coverage_audit_semantic_errors,
        payload={
            "sales_combinations": state.get("sales_combinations", []),
            "qwen_raw_products": state.get("raw_items", []),
            "qwen_products": state.get("normalized_items", []),
            "product_reconciliation_decisions": state.get(
                "product_reconciliation_decisions", []
            ),
        },
    )
    if parsed is None:
        if semantic_errors:
            return _layer_failure_update(
                "coverage", stats, semantic_errors, sanitized_fields,
                rejected_audits, error
            )
        return _infra_failure_update("coverage", stats, sanitized_fields, error)
    return {
        "coverage_checks": [check.model_dump() for check in parsed.coverage_checks],
        "coverage_validation_details": _coverage_validation_details(
            parsed, state
        ),
        "coverage_checker_issues": _coverage_issues_from_audit(parsed, state),
        "coverage_checker_complete": parsed.audit_complete,
        "checker_retry_counts": {
            "coverage": len(
                {item.split(":", 1)[0] for item in semantic_errors}
            )
        },
        "checker_semantic_errors_by_layer": {"coverage": semantic_errors},
        "checker_sanitized_fields_by_layer": {"coverage": sanitized_fields},
        "checker_rejected_audits_by_layer": {"coverage": rejected_audits},
        "token_usage": {config.KIMI_MODEL: stats},
    }


async def score_products(state: dict) -> dict:
    # 评分层入口节点:跑 Kimi 逐商品评分,成功写入 latest_item_evaluations 并追加历史;
    # scorer_issues 恒为空(评分层不产 issue),失败按语义不稳定/基础设施错误分流。
    parsed, stats, semantic_errors, sanitized_fields, rejected_audits, error = await _run_checker_layer(
        state,
        layer="score",
        prompt=PRODUCT_SCORER_PROMPT,
        schema=ProductScoreAudit,
        validator=_score_audit_semantic_errors,
        payload={
            "sales_combinations": state.get("sales_combinations", []),
            "qwen_products": state.get("normalized_items", []),
        },
    )
    if parsed is None:
        if semantic_errors:
            return _layer_failure_update(
                "score", stats, semantic_errors, sanitized_fields,
                rejected_audits, error
            )
        return _infra_failure_update("score", stats, sanitized_fields, error)
    evaluations = [
        evaluation.model_dump() for evaluation in parsed.item_evaluations
    ]
    return {
        "latest_item_evaluations": evaluations,
        "item_evaluation_history": evaluations,
        "scorer_issues": [],
        "scorer_complete": parsed.audit_complete,
        "checker_retry_counts": {
            "score": len(
                {item.split(":", 1)[0] for item in semantic_errors}
            )
        },
        "checker_semantic_errors_by_layer": {"score": semantic_errors},
        "checker_sanitized_fields_by_layer": {"score": sanitized_fields},
        "checker_rejected_audits_by_layer": {"score": rejected_audits},
        "token_usage": {config.KIMI_MODEL: stats},
    }


async def audit_benefits(state: dict) -> dict:
    # 权益层入口节点:跑 Kimi 权益审计,成功后 checker_issues 即为最终 issue 清单;
    # 失败时 _layer_failure_update 写 CHECKER_UNSTABLE 转人工,基础设施错误置 INFRA_ERROR。
    payload = {
        "sales_combinations": state.get("sales_combinations", []),
        "qwen_raw_prices": state.get("raw_prices", []),
        "qwen_raw_coupons": state.get("raw_coupons", []),
        "normalized_prices": state.get("price_objects", []),
        "normalized_coupons": state.get("coupon_objects", []),
    }
    parsed, stats, semantic_errors, sanitized_fields, rejected_audits, error = await _run_checker_layer(
        state,
        layer="benefit",
        prompt=BENEFIT_CHECKER_PROMPT,
        schema=CheckerAudit,
        validator=_benefit_audit_semantic_errors,
        payload=payload,
    )
    if parsed is None:
        if semantic_errors:
            return _layer_failure_update(
                "benefit", stats, semantic_errors, sanitized_fields,
                rejected_audits, error
            )
        return _infra_failure_update("benefit", stats, sanitized_fields, error)
    return {
        "checker_issues": [issue.model_dump() for issue in parsed.issues],
        "checker_complete": parsed.audit_complete,
        "checker_retry_count": len(
            {item.split(":", 1)[0] for item in semantic_errors}
        ),
        "checker_semantic_errors": semantic_errors,
        "checker_retry_counts": {"benefit": len(
            {item.split(":", 1)[0] for item in semantic_errors}
        )},
        "checker_semantic_errors_by_layer": {"benefit": semantic_errors},
        "checker_sanitized_fields_by_layer": {"benefit": sanitized_fields},
        "checker_rejected_audits_by_layer": {"benefit": rejected_audits},
        "token_usage": {config.KIMI_MODEL: stats},
    }
