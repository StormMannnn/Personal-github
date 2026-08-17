"""Deterministic processing of Qwen product candidates."""

from __future__ import annotations

from rapidfuzz import fuzz, process

from .. import config
from .n3_normalize import normalize_item, remove_equivalent_gift_summaries


def _filter_and_unpack(items: list[dict]) -> list[dict]:
    # 只过滤:丢弃非美妆(is_cosmetic 非 True)或购物金(is_shopping_credit)对象;
    # Python 绝不制造商品,缺的盒内容由 Kimi 报告、Qwen Patch 补。
    """Filter only; never manufacture child product records from box text.

    Qwen is the sole maker. Missing box contents must be reported by Kimi and
    added by a later Qwen patch, rather than being emitted by Python.
    """
    filtered: list[dict] = []
    for source in items:
        if source.get("is_cosmetic") is not True or source.get("is_shopping_credit") is True:
            continue
        filtered.append(dict(source))
    return filtered


def _fuzzy_evidence(item: dict, word_package: list[str]) -> tuple[str | None, float]:
    # 用 rapidfuzz WRatio 对候选名做词袋模糊匹配:精确命中返回 100 分,
    # 否则返回最佳候选名与相似度;无输入或空词袋返回 (None, 0.0)。
    source = str(item.get("qwen_matched_name") or item.get("normalized_name") or item.get("raw_name") or "")
    if not source or not word_package:
        return None, 0.0
    if source in word_package:
        return source, 100.0
    result = process.extractOne(source, word_package, scorer=fuzz.WRatio)
    if not result:
        return None, 0.0
    name, score, _ = result
    return str(name), float(score)


def process_product_candidates(state: dict) -> dict:
    # 确定性商品处理主流程:过滤→n3 归一化→去等总量汇总行(记录 product_reconciliation_decisions)
    # →词袋匹配:不在词袋且规格/单位齐全标"无法匹配",否则标"无法识别",并附模糊证据后组装分类结果。
    word_package = state.get("word_package") or []
    filtered = _filter_and_unpack(state.get("raw_items") or [])
    normalized = [normalize_item(item) for item in filtered]
    normalized, reconciliation_decisions = remove_equivalent_gift_summaries(
        normalized, return_decisions=True
    )
    matched = []
    for item in normalized:
        result = dict(item)
        qwen_name = result.get("qwen_matched_name")
        if qwen_name in word_package:
            matched_name = qwen_name
        else:
            matched_name = "无法匹配" if result.get("spec") != "0" and result.get("unit") else "无法识别"
        suggestion, fuzzy_score = _fuzzy_evidence(result, word_package)
        result.update({
            "matched_name": matched_name,
            "match_source": "qwen_maker",
            "match_score": 100.0 if qwen_name in word_package else 0.0,
            "fuzzy_suggested_name": suggestion,
            "fuzzy_match_score": fuzzy_score,
            "fuzzy_match_method": "exact" if fuzzy_score == 100 else "WRatio",
        })
        matched.append(result)
    classified = []
    for source in matched:
        item = dict(source)
        item["类型"] = "主品" if item.get("role") == "主品" else "赠品"
        item["名称"] = item.get("matched_name") or item.get("raw_name")
        item.pop("使用条件", None)
        classified.append(item)
    return {
        "filtered_items": filtered,
        "normalized_items": normalized,
        "classified_items": classified,
        "product_reconciliation_decisions": reconciliation_decisions,
    }


def apply_product_scores(state: dict) -> dict:
    # 把最终评分写回当前商品行:仅 latest_item_evaluations 中存在的 item_id 才写入,
    # 否则移除旧分,保证商品行只保留最终评分。
    """Attach only the final, validated scorer output to current product rows."""
    evaluations = {
        item.get("target_item_id"): item.get("vlm_evaluation_score")
        for item in state.get("latest_item_evaluations") or []
    }

    def attach(rows: list[dict]) -> list[dict]:
        # 逐行把 item_id 对应的 vlm_evaluation_score 写回;无评分则清掉该字段,避免残留旧分。
        result = []
        for source in rows:
            item = dict(source)
            item_id = item.get("item_id")
            if item_id in evaluations:
                item["vlm_evaluation_score"] = evaluations[item_id]
            else:
                item.pop("vlm_evaluation_score", None)
            result.append(item)
        return result

    return {
        "normalized_items": attach(state.get("normalized_items") or []),
        "classified_items": attach(state.get("classified_items") or []),
    }


node = process_product_candidates
