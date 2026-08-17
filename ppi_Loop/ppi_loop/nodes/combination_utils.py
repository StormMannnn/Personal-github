"""销售组合ID的确定性校验、主品补齐、归一化与展开。"""

import re
from typing import Any


_COMBINATION_SPEC_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(ml|毫升|g|克)(?![A-Za-z])",
    re.IGNORECASE,
)
_THRESHOLD_HINT_RE = re.compile(r"每?满|满额")


def default_sales_combinations() -> list[dict[str, Any]]:
    # 用途:返回兜底单组合台账(S1,单一销售组合)。
    # 要点:证据文本说明"未识别到多组合,按单组合兼容处理",供审计可追溯。
    return [{
        "combination_id": "S1",
        "purchase_condition_text": "单一销售组合",
        "section_hint": None,
        "evidence_text": "未识别到多组合，按单组合兼容处理",
    }]


def sales_combination_contract_errors(
    candidates: list[dict] | None,
) -> list[str]:
    # 用途:返回台账契约错误列表:空台账、字段缺失、S 编号不连续。
    # 要点:只做校验不做兼容回退,错误列表为空表示台账合法可用。
    """Return deterministic ledger errors without applying compatibility fallback."""
    if not candidates:
        return ["销售组合台账为空"]
    ids: list[str] = []
    errors: list[str] = []
    for index, raw in enumerate(candidates):
        combo_id = str(raw.get("combination_id") or "").strip().upper()
        ids.append(combo_id)
        for field in ("purchase_condition_text", "evidence_text"):
            if not str(raw.get(field) or "").strip():
                errors.append(f"第{index + 1}个组合缺少{field}")
    expected = [f"S{index}" for index in range(1, len(ids) + 1)]
    if ids != expected:
        errors.append(f"组合ID不连续: expected={expected}, actual={ids}")
    return errors


def normalize_sales_combinations(
    candidates: list[dict] | None,
    fallback: list[dict] | None = None,
) -> list[dict[str, Any]]:
    # 用途:归一化台账——只接受按顺序连续编号的 S1..Sn。
    # 要点:任一字段缺失或编号不连续即整体作废,回退旧台账(fallback),再不行用默认 S1。
    """只接受按顺序连续编号的S1..Sn；非法候选回退旧台账或S1。"""
    cleaned: list[dict[str, Any]] = []
    for raw in candidates or []:
        combo_id = str(raw.get("combination_id") or "").strip().upper()
        condition = str(raw.get("purchase_condition_text") or "").strip()
        evidence = str(raw.get("evidence_text") or "").strip()
        if not combo_id or not condition or not evidence:
            cleaned = []
            break
        cleaned.append({
            "combination_id": combo_id,
            "purchase_condition_text": condition,
            "section_hint": raw.get("section_hint"),
            "evidence_text": evidence,
        })

    if cleaned and not sales_combination_contract_errors(cleaned):
        return cleaned
    if fallback:
        return normalize_sales_combinations(fallback, None)
    return default_sales_combinations()


def normalize_combination_ids(
    values: list[str] | None,
    sales_combinations: list[dict] | None,
) -> list[str]:
    # 用途:归一化条目所属组合ID列表。
    # 要点:单组合强制 ["S1"];多组合只保留台账内的有效 S 组(去重);
    #       无任何有效归属时返回 ["UNK"]。
    """单组合强制S1；多组合保留有效S组，无有效归属时仅返回UNK。"""
    combos = normalize_sales_combinations(sales_combinations)
    if len(combos) == 1:
        return ["S1"]

    allowed = {item["combination_id"] for item in combos}
    valid: list[str] = []
    for value in values or []:
        combo_id = str(value or "").strip().upper()
        if combo_id in allowed and combo_id not in valid:
            valid.append(combo_id)
    return valid or ["UNK"]


def find_missing_main_combination_ids(
    products: list[dict[str, Any]],
    sales_combinations: list[dict] | None,
) -> list[str]:
    # 用途:找出台账中缺少主品条目的组合ID。
    # 要点:兼容内部字段(role/combination_ids)与最终中文字段(类型/组合ID)两种写法。
    """返回没有主品条目的S组合ID，兼容内部字段和最终中文字段。"""
    combos = normalize_sales_combinations(sales_combinations)
    present: set[str] = set()
    for item in products:
        if (item.get("role") or item.get("类型")) != "主品":
            continue
        values = item.get("combination_ids")
        if values is None:
            values = [item.get("combination_id") or item.get("组合ID")]
        for value in values or []:
            combo_id = str(value or "").strip().upper()
            if combo_id:
                present.add(combo_id)
    return [
        combo["combination_id"]
        for combo in combos
        if combo["combination_id"] not in present
    ]


def backfill_spec_gift_combination_mains(
    products: list[dict[str, Any]],
    sales_combinations: list[dict] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    # 用途:按"规格+礼赠"证据,为缺主品的组合补齐逻辑主品(复制锚点主品条目)。
    # 要点:仅当全图主品锚点唯一、证据含"礼赠"且无满额门槛、并匹配规格时才补;
    #       其余情况原样返回,交给 Kimi 或最终强校验处理。
    """按明确的“规格+礼赠”证据，为缺主品组合安全补齐唯一主品锚点。

    仅当全图已有主品指向同一个商品名称，且组合证据明确给出规格并且
    不含满额门槛语义时补齐；其余情况保持原样，交给Kimi或最终强校验。
    """
    combos = normalize_sales_combinations(sales_combinations)
    result = [dict(item) for item in products]
    missing_ids = set(find_missing_main_combination_ids(result, combos))
    if not missing_ids:
        return result, []

    anchors = [item for item in result if item.get("role") == "主品"]
    identities = {
        str(item.get("qwen_matched_name") or item.get("raw_name") or "").strip()
        for item in anchors
    }
    identities.discard("")
    if len(identities) != 1:
        return result, []

    anchor = next(
        item for item in anchors
        if str(item.get("qwen_matched_name") or item.get("raw_name") or "").strip()
        in identities
    )
    added_ids: list[str] = []
    for combo in combos:
        combo_id = combo["combination_id"]
        if combo_id not in missing_ids:
            continue
        evidence = " ".join(
            str(combo.get(key) or "")
            for key in ("purchase_condition_text", "evidence_text")
        )
        if "礼赠" not in evidence or _THRESHOLD_HINT_RE.search(evidence):
            continue
        match = _COMBINATION_SPEC_RE.search(evidence)
        if not match:
            continue
        spec, unit = match.groups()
        normalized_unit = "ml" if unit.lower() == "ml" or unit == "毫升" else "g"

        backfill = dict(anchor)
        backfill.update({
            "combination_ids": [combo_id],
            "spec_text": f"{spec}{normalized_unit}",
            "qty_text": "1",
            "role": "主品",
            "gift_label": None,
            "section_hint": combo.get("section_hint"),
            "combination_main_backfill": True,
        })
        result.append(backfill)
        added_ids.append(combo_id)

    return result, added_ids


def expand_by_combination(
    item: dict[str, Any],
    sales_combinations: list[dict] | None,
) -> list[dict[str, Any]]:
    # 用途:把条目的多组合IDs展开成多条,每条只带一个 combination_id。
    # 要点:供 n6/n7 按组合独立取价与组装,展开结果直接替换原条目字段。
    """将内部combination_ids展开为每条仅一个combination_id。"""
    expanded = []
    for combo_id in normalize_combination_ids(item.get("combination_ids"), sales_combinations):
        row = dict(item)
        row["combination_ids"] = [combo_id]
        row["combination_id"] = combo_id
        expanded.append(row)
    return expanded


def combination_sort_key(combo_id: str) -> tuple[int, int]:
    # 用途:S 编号排序键——数字小的 S 在前,UNK 与非法编号排最后。
    # 要点:返回值 (优先级, 序号) 直接用于组装阶段的 tuple 排序。
    if combo_id == "UNK":
        return (1, 10**9)
    if combo_id.startswith("S") and combo_id[1:].isdigit():
        return (0, int(combo_id[1:]))
    return (1, 10**9)
