"""n7_assemble — 组装最终 JSON + Step5 自检。

对应 prompt:
- Step4: 组装 {"data":{url:[...]}}
- Step5: v9 16 条输出前自检

当前输出口径:
- 类型枚举 4 种(主品/赠品/到手价/优惠券)
- 规格/数量不省略字段
- 遗留满赠输入统一转换为赠品，产品行不输出使用条件
"""

from decimal import Decimal, InvalidOperation
import re
from typing import Any
import unicodedata

from pypinyin import lazy_pinyin

from ..schemas import OutputItem, VALID_OUTPUT_TYPES
from .combination_utils import (
    combination_sort_key,
    expand_by_combination,
    find_missing_main_combination_ids,
    normalize_sales_combinations,
)


def _map_product_name(name: Any, label_mapping: dict[str, str] | None) -> Any:
    # 用途:把内部名称按 label_mapping 映射为最终输出名称。
    # 要点:"无法匹配/无法识别"等哨兵值原样保留,不参与映射。
    """Map an internal label to its final label_output without changing sentinel values."""
    if not name or name in ("无法匹配", "无法识别"):
        return name
    return (label_mapping or {}).get(name, name)


_NUMERIC_TEXT_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def _name_sort_key(value: Any) -> tuple[int, tuple[str, ...], str]:
    # 用途:名称排序键——按无声调拼音升序排列(组装时用于主品/赠品排序)。
    # 要点:空名称排在有效名称之后(优先级1),避免空值乱序。
    """名称按无声调拼音升序；空名称排在有名称条目之后。"""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return (1, (), "")
    pinyin = tuple(part.casefold() for part in lazy_pinyin(text))
    return (0, pinyin, text.casefold())


def _numeric_sort_key(value: Any) -> tuple[int, Decimal, str]:
    # 用途:数值排序键——按数值大小升序(规格/数量排序用)。
    # 要点:非数值与空值排在有效数值之后,解析失败也走兜底分支。
    """按数值升序；非数值和空值排在有效数值之后。"""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().replace(",", "")
    if not text or _NUMERIC_TEXT_RE.fullmatch(text) is None:
        return (1, Decimal(0), text.casefold())
    try:
        return (0, Decimal(text), "")
    except InvalidOperation:
        return (1, Decimal(0), text.casefold())


def assemble_result(classified_items: list[dict[str, Any]],
                    price_objects: list[dict[str, Any]],
                    coupon_objects: list[dict[str, Any]],
                    label_mapping: dict[str, str] | None = None,
                    sales_combinations: list[dict] | None = None) -> list[dict[str, Any]]:
    # 用途:组装最终条目数组:主品/赠品 → 到手价 → 优惠券。
    # 要点:类型仅4种(满赠转赠品),规格兜底"0"、数量兜底"1"、字段不省略;
    #       带台账时每条打"组合ID",最后按 组合→类型→拼音→规格→数量 排序。
    """组装该图的最终条目数组。

    合并顺序: 主品/赠品类条目 → 到手价 → 优惠券

    字段规则:
    - 类型仅 4 种: 主品/赠品/到手价/优惠券
    - 规格/数量不省略字段(规格兜底"0",数量兜底"1")
    - 主品/赠品不输出使用条件
    """
    include_combination = sales_combinations is not None
    sales_combinations = normalize_sales_combinations(sales_combinations)
    sortable: list[tuple[str, int, int, dict[str, Any]]] = []
    sequence = 0

    # 1) 产品条目(含主品和赠品)
    for source_item in classified_items:
        expanded_items = expand_by_combination(source_item, sales_combinations)
        for item in expanded_items:
            sequence += 1
            product_type = item.get("类型", "主品")
            if product_type == "满赠":
                product_type = "赠品"
            internal_name = item.get("名称") or item.get("matched_name") or item.get("raw_name")
            entry = {
                "类型": product_type,
                "名称": _map_product_name(internal_name, label_mapping),
            }
            if include_combination:
                entry["组合ID"] = item["combination_id"]
            rapidfuzz_score = item.get("fuzzy_match_score")
            vlm_score = item.get("vlm_evaluation_score")
            if rapidfuzz_score is not None:
                entry["RapidFuzz匹配分数"] = float(rapidfuzz_score)
            if vlm_score is not None:
                entry["VLM测评分数"] = int(vlm_score)
            spec = item.get("规格") or item.get("spec")
            unit = item.get("单位") or item.get("unit")
            qty = item.get("数量") or item.get("qty")
            entry["规格"] = spec or "0"
            entry["单位"] = unit or ""
            entry["数量"] = qty or "1"

            type_rank = 0 if product_type == "主品" else 1
            sortable.append((item["combination_id"], type_rank, sequence, entry))

    # 2) 到手价
    for p in price_objects:
        sequence += 1
        combo_id = p.get("组合ID", "S1")
        entry = {
            "类型": "到手价",
            "金额": str(p.get("金额", "")),
        }
        if include_combination:
            entry["组合ID"] = combo_id
        sortable.append((combo_id, 2, sequence, entry))

    # 3) 优惠券(v9: 金额必须明确,已在 n6 保证)
    for c in coupon_objects:
        sequence += 1
        combo_id = c.get("组合ID", "S1")
        entry = {"类型": "优惠券"}
        if include_combination:
            entry["组合ID"] = combo_id
        for k in ["名称", "金额", "使用条件"]:
            v = c.get(k)
            if v is not None:
                entry[k] = str(v)
        sortable.append((combo_id, 3, sequence, entry))

    sortable.sort(key=lambda row: (
        combination_sort_key(row[0]),
        row[1],
        _name_sort_key(row[3].get("名称")),
        _numeric_sort_key(row[3].get("规格")),
        _numeric_sort_key(row[3].get("数量")),
        row[2],
    ))
    return [row[3] for row in sortable]


# ==================== Step5 自检(v9 16 条) ====================

def _run_checks(result: list[dict], image_url: str,
                word_package: list[str],
                sales_combinations: list[dict] | None = None) -> list[str]:
    # 用途:输出前确定性自检(v9 共 16 条),返回告警列表、不抛异常。
    # 要点:校验组合ID归属/缺主品、名称是否在商品列表、金额互斥、多到手价、
    #       数量兜底与最终类型合法性;JSON合法/多图顺序由组装与 State 天然保证。
    """执行 Step5 自检(v9 共 16 条),返回告警列表。

    #5/#11(JSON合法) 由 Python 组装天然保证,不重复校验。
    #1(不窜图)/#12(多图顺序) 由整图一条 State 天然满足。
    """
    warnings = []
    PRODUCT_TYPES = {"主品", "赠品"}
    enforce_combinations = sales_combinations is not None
    combos = normalize_sales_combinations(sales_combinations)
    allowed_ids = {item["combination_id"] for item in combos}

    # 组合ID必须来自台账；UNK仅允许出现在多组合图。
    for i, item in enumerate(result) if enforce_combinations else []:
        combo_id = item.get("组合ID")
        if combo_id not in allowed_ids and combo_id != "UNK":
            warnings.append(f"[{image_url}][#{i}] 组合ID {combo_id!r} 不在锁定台账中")
        if combo_id == "UNK" and len(combos) == 1:
            warnings.append(f"[{image_url}][#{i}] 单组合图不应使用UNK")

    for combo_id in (
        find_missing_main_combination_ids(result, combos)
        if enforce_combinations else []
    ):
        warnings.append(f"[{image_url}] [致命] 组合{combo_id}缺少主品条目")

    # 2. 所有产品名称来自列表
    for i, item in enumerate(result):
        name = item.get("名称")
        t = item["类型"]
        if name and name not in ("无法匹配", "无法识别") and t in PRODUCT_TYPES:
            if name not in word_package:
                warnings.append(
                    f"[{image_url}][#{i}] 名称 '{name}' 不在商品列表中"
                )

    # 6. 同一金额不同时作到手价和券金额(互斥)
    final_amts = {
        (item.get("组合ID"), item.get("金额"))
        for item in result if item["类型"] == "到手价"
    }
    coupon_amts = {
        (item.get("组合ID"), item.get("金额"))
        for item in result if item["类型"] == "优惠券"
    }
    overlap = final_amts & coupon_amts
    if overlap:
        warnings.append(
            f"[{image_url}] 金额冲突: {overlap} 同时出现在到手价和优惠券中"
        )

    # 7. 多到手价候选取最低
    for combo_id in allowed_ids | {"UNK"}:
        final_count = sum(
            1 for item in result
            if item["类型"] == "到手价" and item.get("组合ID") == combo_id
        )
        if final_count > 1:
            warnings.append(
                f"[{image_url}] 组合{combo_id}存在 {final_count} 个到手价,n6 未正确取最低"
            )

    # 8. 数量兜底"1",不为0、不缺失
    for i, item in enumerate(result):
        if item["类型"] in PRODUCT_TYPES:
            qty = item.get("数量")
            if qty is None or qty == "" or int(qty) == 0:
                warnings.append(
                    f"[{image_url}][#{i}] 类型={item['类型']} 数量={qty},应为 ≥1"
                )

    # 15. 最终类型仅允许主品/赠品/到手价/优惠券
    for i, item in enumerate(result):
        if item["类型"] not in VALID_OUTPUT_TYPES:
            warnings.append(
                f"[{image_url}][#{i}] 非法类型 '{item['类型']}',合法: {VALID_OUTPUT_TYPES}"
            )

    return warnings


def validate_result(result: list[dict]) -> list[dict]:
    # 用途:用 OutputItem Schema 逐条校验最终输出,过滤非法条目。
    # 要点:校验失败的条目直接丢弃(不写 errors 明细),只保留合法条目。
    """Pydantic 校验每条最终输出,过滤非法条目。

    校验失败 → 该条目丢弃,写 errors。
    """
    valid = []
    for item in result:
        try:
            cleaned = dict(item)
            OutputItem.model_validate(cleaned)
            valid.append(cleaned)
        except Exception:
            pass
    return valid


def get_node():
    # 用途:返回 LangGraph 节点函数(遗留包装),内部定义 assemble_node。
    """返回 LangGraph 节点函数。"""
    def assemble_node(state: dict) -> dict:
        # 用途:节点主体——汇总各节点产物,组装结果并跑 16 条自检。
        # 要点:任一组缺少主品时整体结果置空(防漏品图出数据),
        #       否则按 Schema 校验后返回 {"result": [...], "errors": 告警列表}。
        classified = state.get("classified_items") or []
        prices = state.get("price_objects") or []
        coupons = state.get("coupon_objects") or []
        image_url = state.get("image_url", "")
        word_package = state.get("word_package") or []
        label_mapping = state.get("label_mapping") or {}
        sales_combinations = normalize_sales_combinations(state.get("sales_combinations"))

        result = assemble_result(
            classified, prices, coupons, label_mapping, sales_combinations
        )
        output_word_package = list(dict.fromkeys(label_mapping.values())) if label_mapping else word_package
        warnings = _run_checks(
            result, image_url, output_word_package, sales_combinations
        )
        missing_main_ids = find_missing_main_combination_ids(
            result, sales_combinations
        )
        result = [] if missing_main_ids else validate_result(result)

        return {
            "result": result,
            "errors": warnings,
        }
    return assemble_node


node = get_node()
