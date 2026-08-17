"""Evidence gates and bounded-loop routing decisions."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .combination_utils import (
    find_missing_main_combination_ids,
    normalize_sales_combinations,
    sales_combination_contract_errors,
)
from .n7_assemble import _run_checks, assemble_result, validate_result


def _issue(
    code: str,
    scope: str,
    fact: str,
    instruction: str,
    *,
    combination_id: str | None = None,
    target_item_id: str | None = None,
    repairable: bool = True,
) -> dict:
    # 构造统一的致命 issue 对象:code/scope/severity 固定 fatal,事实写进 observed_fact,
    # evidence_text 留空(归一化时优先取它),repairable 决定能否走修复路径。
    return {
        "code": code,
        "scope": scope,
        "severity": "fatal",
        "combination_id": combination_id,
        "target_item_id": target_item_id,
        "evidence_text": "",
        "evidence_region": None,
        "observed_fact": fact,
        "repair_instruction": instruction,
        "confidence": 1.0,
        "repairable": repairable,
    }


_SURFACE_PUNCTUATION = re.compile(
    r"[\s，,。；;：:！!？?、·“”‘’\"'（）()\[\]【】{}]+"
)


def _normalize_issue_text(value: object) -> str:
    # "无进展"签名的文本归一化:NFKC+casefold+去空白与标点,只抹平展现差异、绝不删字母数字。
    """Normalize presentation differences without deleting letters or numbers."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _SURFACE_PUNCTUATION.sub("", text).strip()


def _issue_meaning(issue: dict) -> dict:
    # 把单个致命 issue 折叠成确定性身份字典:affected_fields 排序去重,
    # 证据锚点优先用 evidence_text、没有则用 observed_fact,供签名比较使用。
    """Build a deterministic, evidence-first identity for one fatal issue."""
    evidence_text = _normalize_issue_text(issue.get("evidence_text"))
    observed_fact = _normalize_issue_text(issue.get("observed_fact"))
    return {
        "code": str(issue.get("code") or ""),
        "scope": str(issue.get("scope") or ""),
        "combination_id": str(issue.get("combination_id") or ""),
        "target_item_id": str(issue.get("target_item_id") or ""),
        "affected_fields": sorted({
            str(field) for field in issue.get("affected_fields") or []
            if field is not None
        }),
        "evidence_region": _normalize_issue_text(issue.get("evidence_region")),
        "evidence_anchor": evidence_text or observed_fact,
    }


def _signature_payload(issues: list[dict]) -> list[dict]:
    # 只取 fatal 级别 issue 的语义身份,按 JSON 序列化结果排序,保证集合顺序与签名无关。
    meanings = [
        _issue_meaning(issue)
        for issue in issues if issue.get("severity") == "fatal"
    ]
    return sorted(
        meanings,
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )


def _signature(issues: list[dict]) -> str:
    # 对排序后的 issue 身份列表做紧凑 JSON + SHA-256,得到"无进展"比较签名。
    payload = _signature_payload(issues)
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _decide(
    state: dict,
    issues: list[dict],
    scope: str,
    success_action: str,
    *,
    reset_progress: bool = True,
) -> dict:
    # 核心决策节点:无 fatal 直接返回成功动作并(可选)清零无进展计数;
    # 有 fatal 则比签名:相同则 no_progress+1,达上限或不可修复转 NEEDS_HUMAN,
    # 迭代耗尽转 BUDGET_EXHAUSTED,否则路由到 repair_product/repair_benefit。
    fatal = [issue for issue in issues if issue.get("severity") == "fatal"]
    if not fatal:
        update = {
            "current_issues": [],
            "checker_issues": [],
            "next_action": success_action,
            "terminal_status": "",
        }
        if reset_progress:
            update.update({
                "last_issue_signature": None,
                "last_issue_meanings": [],
                "no_progress_count": 0,
                "no_progress_comparison": {},
            })
        return update

    previous_signature = state.get("last_issue_signature")
    signature = _signature(fatal)
    meanings = _signature_payload(fatal)
    same = signature == previous_signature
    no_progress = int(state.get("no_progress_count", 0)) + 1 if same else 1
    max_key = "max_product_iterations" if scope == "product" else "max_benefit_iterations"
    iteration_key = "product_iteration" if scope == "product" else "benefit_iteration"
    exhausted = int(state.get(iteration_key, 0)) >= int(state.get(max_key, 0))
    unrepairable = any(issue.get("repairable") is False for issue in fatal)

    status = ""
    action = f"repair_{scope}"
    if unrepairable or no_progress >= int(state.get("no_progress_limit", 2)):
        status, action = "NEEDS_HUMAN", "stop"
    elif exhausted:
        status, action = "BUDGET_EXHAUSTED", "stop"
    return {
        "current_issues": fatal,
        "issue_history": fatal,
        "next_action": action,
        "terminal_status": status,
        "last_issue_signature": signature,
        "last_issue_meanings": meanings,
        "no_progress_count": no_progress,
        "no_progress_comparison": {
            "previous_signature": previous_signature,
            "current_signature": signature,
            "same_meaning": same,
            "no_progress_count": no_progress,
        },
    }


def verify_product_combinations(state: dict) -> dict:
    # 组合层 gate:审计未完成、组合台账契约错误、缺主品组合、非法组合ID 都会产 issue;
    # reset_progress=False 让中间层通过时不清零无进展计数,保证同一问题在后层再现仍算连续。
    if state.get("terminal_status") == "INFRA_ERROR":
        return {"next_action": "stop"}
    issues = list(state.get("combination_checker_issues") or [])
    if not state.get("combination_checker_complete"):
        issues.append(_issue(
            "CHECKER_INCOMPLETE", "global", "Checker未完成销售组合审计",
            "重新进行独立销售组合审计", repairable=False,
        ))
    for error in sales_combination_contract_errors(state.get("sales_combinations")):
        issues.append(_issue(
            "INVALID_SALES_COMBINATION_LEDGER", "combination", error,
            "Qwen重建从S1开始连续编号且证据完整的销售组合台账",
        ))
    combos = normalize_sales_combinations(state.get("sales_combinations"))
    existing_missing_mains = {
        issue.get("combination_id") for issue in issues
        if issue.get("code") == "MISSING_COMBINATION_MAIN"
    }
    for combo_id in find_missing_main_combination_ids(state.get("classified_items") or [], combos):
        if combo_id in existing_missing_mains:
            continue
        issues.append(_issue(
            "MISSING_COMBINATION_MAIN", "product",
            f"组合{combo_id}缺少主品", f"补齐{combo_id}的主品及规格",
            combination_id=combo_id,
        ))
    allowed = {combo["combination_id"] for combo in combos}
    for item in state.get("normalized_items") or []:
        for combo_id in item.get("combination_ids") or []:
            if combo_id != "UNK" and combo_id not in allowed:
                issues.append(_issue(
                    "INVALID_COMBINATION_ID", "product", f"非法组合ID {combo_id}",
                    "修正商品组合归属", target_item_id=item.get("item_id"),
                ))
    return _decide(
        state, issues, "product", "coverage", reset_progress=False
    )


def verify_product_coverage(state: dict) -> dict:
    # 覆盖层 gate:词袋外名称(QWEN_NAME_OUTSIDE_WORD_PACKAGE)与完全重复商品都产 issue,
    # 同样 reset_progress=False,让无进展计数在多层间保持连续。
    if state.get("terminal_status") == "INFRA_ERROR":
        return {"next_action": "stop"}
    issues = list(state.get("coverage_checker_issues") or [])
    if not state.get("coverage_checker_complete"):
        issues.append(_issue(
            "CHECKER_INCOMPLETE", "global", "Checker未完成全图商品覆盖与字段审计",
            "重新进行独立商品覆盖审计", repairable=False,
        ))
    word_package = set(state.get("word_package") or [])
    for item in state.get("normalized_items") or []:
        qwen_name = item.get("qwen_matched_name")
        if qwen_name is not None and qwen_name not in word_package:
            issues.append(_issue(
                "QWEN_NAME_OUTSIDE_WORD_PACKAGE", "product",
                f"Qwen初步真名{qwen_name!r}不在当前品牌词袋",
                "将qwen_matched_name修正为词袋原文或null",
                target_item_id=item.get("item_id"),
            ))
    seen: dict[tuple, str] = {}
    for item in state.get("normalized_items") or []:
        key = (
            item.get("raw_name"), item.get("qwen_matched_name"),
            tuple(item.get("combination_ids") or []), item.get("spec"), item.get("unit"),
            item.get("qty"), item.get("role"), item.get("gift_label"),
        )
        if key in seen:
            issues.append(_issue(
                "DUPLICATE_PRODUCT", "product",
                f"Qwen对象{item.get('item_id')}与{seen[key]}完全重复",
                "删除重复对象或用qty_text表示同款多件",
                target_item_id=item.get("item_id"),
            ))
        else:
            seen[key] = item.get("item_id")
    return _decide(state, issues, "product", "score", reset_progress=False)


def verify_product_scores(state: dict) -> dict:
    # 评分层 gate:按应评商品集(化妆品且非购物金)核对 Scorer 输出,
    # 发现重复/非法/遗漏评分目标即产 issue,通过则进入 apply_scores 写回评分。
    if state.get("terminal_status") == "INFRA_ERROR":
        return {"next_action": "stop"}
    issues = list(state.get("scorer_issues") or [])
    if not state.get("scorer_complete"):
        issues.append(_issue(
            "CHECKER_INCOMPLETE", "global", "Checker未完成逐商品VLM评分",
            "重新进行独立商品评分", repairable=False,
        ))
    expected_ids = {
        item.get("item_id") for item in state.get("normalized_items") or []
        if item.get("is_cosmetic") is True and not item.get("is_shopping_credit")
    }
    evaluation_targets = [
        item.get("target_item_id") for item in state.get("latest_item_evaluations") or []
    ]
    evaluated_ids = set(evaluation_targets)
    duplicate_targets = {
        item_id for item_id in evaluation_targets
        if item_id and evaluation_targets.count(item_id) > 1
    }
    for item_id in sorted(duplicate_targets):
        issues.append(_issue(
            "DUPLICATE_CHECKER_EVALUATION", "product",
            f"Scorer对Qwen对象{item_id}重复评分", "重新输出每个ID唯一的评分",
            target_item_id=item_id, repairable=False,
        ))
    for item_id in sorted(evaluated_ids - expected_ids):
        issues.append(_issue(
            "INVALID_CHECKER_TARGET", "product",
            f"Scorer评价了不存在的Qwen对象{item_id}",
            "拒绝该评分并重新评价现有对象",
            target_item_id=item_id, repairable=False,
        ))
    for item_id in sorted(expected_ids - evaluated_ids):
        issues.append(_issue(
            "MISSING_CHECKER_EVALUATION", "product",
            f"Scorer未评价Qwen对象{item_id}", "重新评价该Qwen对象",
            target_item_id=item_id, repairable=False,
        ))
    return _decide(state, issues, "product", "apply_scores")


def verify_benefits(state: dict) -> dict:
    # 权益层 gate:审计未完成则补 CHECKER_INCOMPLETE,其余问题直接来自 Checker 审计结果,
    # 全部通过则路由到 assemble 组装最终候选。
    if state.get("terminal_status") == "INFRA_ERROR":
        return {"next_action": "stop"}
    issues = list(state.get("checker_issues") or [])
    if not state.get("checker_complete"):
        issues.append(_issue(
            "CHECKER_INCOMPLETE", "global", "Checker未完成全图权益审计",
            "重新进行完整权益审计", repairable=False,
        ))
    return _decide(state, issues, "benefit", "assemble")


def assemble_candidate(state: dict) -> dict:
    # 把最新一轮 Scorer 评分按 target_item_id 写回 classified_items,
    # 再与价格/优惠券/组合等对象组装成候选结果,交给 verify_final 校验。
    evaluations = {
        item.get("target_item_id"): item.get("vlm_evaluation_score")
        for item in state.get("latest_item_evaluations") or []
    }
    classified = []
    for source in state.get("classified_items") or []:
        item = dict(source)
        if item.get("item_id") in evaluations:
            item["vlm_evaluation_score"] = evaluations[item["item_id"]]
        classified.append(item)
    result = assemble_result(
        classified,
        state.get("price_objects") or [],
        state.get("coupon_objects") or [],
        state.get("label_mapping") or {},
        state.get("sales_combinations") or [],
    )
    return {"candidate_result": result}


def verify_final(state: dict) -> dict:
    # 最终确定性 gate:跑全部确定性检查并用 OutputItem Schema 校验候选结果,
    # 全通过写 VERIFIED;失败则按 warning 关键词归到 product/coupon/global scope,
    # global 或 Schema 失败直接转 NEEDS_HUMAN,其余走对应修复。
    result = state.get("candidate_result") or []
    mapped_word_package = (
        list(dict.fromkeys((state.get("label_mapping") or {}).values()))
        or state.get("word_package") or []
    )
    warnings = _run_checks(
        result, state.get("image_url", ""), mapped_word_package,
        state.get("sales_combinations") or [],
    )
    validated = validate_result(result)
    if not warnings and len(validated) == len(result):
        return {
            "verified_result": validated,
            "result": validated,
            "terminal_status": "VERIFIED",
            "next_action": "verified",
            "proof_bundle": {
                "schema_valid": True,
                "deterministic_warnings": [],
                "product_iterations": state.get("product_iteration", 0),
                "benefit_iterations": state.get("benefit_iteration", 0),
            },
        }
    issues = []
    for warning in warnings:
        if any(keyword in warning for keyword in ("主品", "名称", "数量")):
            scope = "product"
        elif any(keyword in warning for keyword in ("金额冲突", "到手价", "优惠券")):
            scope = "coupon"
        else:
            scope = "global"
        issues.append(_issue("FINAL_VALIDATION_FAILED", scope, warning, "定向修复最终校验问题"))
    if len(validated) != len(result):
        issues.append(_issue(
            "SCHEMA_INVALID", "global", "最终对象Schema校验失败",
            "修复非法字段", repairable=False,
        ))
    scopes = {issue["scope"] for issue in issues}
    repair_scope = "product" if "product" in scopes else "benefit"
    decision = _decide(state, issues, repair_scope, "verified")
    if any(issue["scope"] == "global" for issue in issues):
        decision.update({"terminal_status": "NEEDS_HUMAN", "next_action": "stop"})
    return decision
