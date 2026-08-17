"""销售组合台账、归属展开和最终区块顺序回归测试。"""

import pytest
from pydantic import ValidationError

from ppi_loop.nodes.combination_utils import (
    backfill_spec_gift_combination_mains,
    find_missing_main_combination_ids,
    normalize_combination_ids,
    normalize_sales_combinations,
)
from ppi_loop.nodes import gates, n7_assemble
from ppi_loop.schemas import ProductExtraction
from ppi_loop.nodes.n6_price import (
    extract_coupons,
    extract_final_prices,
    validate_exclusivity,
)
from ppi_loop.nodes.n7_assemble import assemble_result


COMBOS = [
    {
        "combination_id": "S1",
        "purchase_condition_text": "50ml",
        "section_hint": "左侧",
        "evidence_text": "50ml礼赠明细",
    },
    {
        "combination_id": "S2",
        "purchase_condition_text": "100ml",
        "section_hint": "右侧",
        "evidence_text": "100ml礼赠明细",
    },
]


def test_combination_ledger_requires_contiguous_ids_and_safe_fallback():
    invalid = [
        {**COMBOS[0]},
        {**COMBOS[1], "combination_id": "S3"},
    ]
    assert normalize_sales_combinations(invalid, COMBOS) == COMBOS
    assert normalize_combination_ids([], COMBOS) == ["UNK"]
    assert normalize_combination_ids(["S2", "S1", "S2"], COMBOS) == ["S2", "S1"]


def test_qwen_product_contract_rejects_empty_or_noncontiguous_combination_ledger():
    with pytest.raises(ValidationError, match="至少输出S1"):
        ProductExtraction.model_validate({
            "sales_combinations": [], "products": [],
        })
    with pytest.raises(ValidationError, match="组合ID必须连续"):
        ProductExtraction.model_validate({
            "sales_combinations": [COMBOS[0], {**COMBOS[1], "combination_id": "S3"}],
            "products": [],
        })


def test_price_and_coupon_processing_is_isolated_by_combination():
    prices = extract_final_prices([
        {"amount": "699", "context_text": "50ml到手价699", "combination_ids": ["S1"]},
        {"amount": "599", "context_text": "50ml到手价599", "combination_ids": ["S1"]},
        {"amount": "899", "context_text": "100ml到手价899", "combination_ids": ["S2"]},
    ], COMBOS)
    assert prices == [
        {"类型": "到手价", "组合ID": "S1", "金额": "599"},
        {"类型": "到手价", "组合ID": "S2", "金额": "899"},
    ]

    coupons = extract_coupons([{
        "text": "美妆券 ¥100 满1000可用",
        "combination_ids": ["S1", "S2"],
    }], COMBOS)
    assert [item["组合ID"] for item in coupons] == ["S1", "S2"]

    # 同额券只能与同一组合的到手价互斥。
    conflict_prices = [
        {"类型": "到手价", "组合ID": "S1", "金额": "100"},
        {"类型": "到手价", "组合ID": "S2", "金额": "100"},
    ]
    validate_exclusivity(conflict_prices, [coupons[0]])
    assert conflict_prices == [
        {"类型": "到手价", "组合ID": "S2", "金额": "100"},
    ]


def test_final_output_expands_public_items_and_orders_combination_blocks():
    products = [
        {"类型": "主品", "名称": "A50", "spec": "50", "unit": "ml", "qty": "1", "combination_ids": ["S1"]},
        {"类型": "主品", "名称": "A100", "spec": "100", "unit": "ml", "qty": "1", "combination_ids": ["S2"]},
        {"类型": "赠品", "名称": "公共赠品", "spec": "5", "unit": "ml", "qty": "1", "combination_ids": ["S1", "S2"]},
        {"类型": "赠品", "名称": "归属不明", "spec": "2", "unit": "ml", "qty": "1", "combination_ids": ["UNK"]},
    ]
    result = assemble_result(
        products,
        [{"类型": "到手价", "组合ID": "S2", "金额": "899"}],
        [{"类型": "优惠券", "组合ID": "S1", "名称": "美妆券", "金额": "100", "使用条件": "满1000"}],
        None,
        COMBOS,
    )
    assert [(item["组合ID"], item["类型"]) for item in result] == [
        ("S1", "主品"), ("S1", "赠品"), ("S1", "优惠券"),
        ("S2", "主品"), ("S2", "赠品"), ("S2", "到手价"),
        ("UNK", "赠品"),
    ]
    assert sum(item.get("名称") == "公共赠品" for item in result) == 2


def test_final_output_orders_name_by_pinyin_then_spec_and_qty_numerically():
    products = [
        {"类型": "赠品", "名称": "红腰子精华", "spec": "10", "qty": "1", "combination_ids": ["S1"]},
        {"类型": "赠品", "名称": "白檀香水", "spec": "100", "qty": "1", "combination_ids": ["S1"]},
        {"类型": "主品", "名称": "玉龙茶香香水", "spec": "100", "qty": "1", "combination_ids": ["S2"]},
        {"类型": "赠品", "名称": "白檀香水", "spec": "2", "qty": "10", "combination_ids": ["S1"]},
        {"类型": "主品", "名称": "玉龙茶香香水", "spec": "50", "qty": "1", "combination_ids": ["S1"]},
        {"类型": "赠品", "名称": "悦薇水", "spec": "0", "qty": "1", "combination_ids": ["S1"]},
        {"类型": "赠品", "名称": "白檀香水", "spec": "2", "qty": "2", "combination_ids": ["S1"]},
        {"类型": "赠品", "名称": "归属不明", "spec": "5", "qty": "1", "combination_ids": ["UNK"]},
    ]
    result = assemble_result(
        products,
        [{"类型": "到手价", "组合ID": "S1", "金额": "699"}],
        [{"类型": "优惠券", "组合ID": "S1", "名称": "美妆券", "金额": "50", "使用条件": "满500"}],
        None,
        COMBOS,
    )

    assert [
        (item["组合ID"], item["类型"], item.get("名称"), item.get("规格"), item.get("数量"))
        for item in result
    ] == [
        ("S1", "主品", "玉龙茶香香水", "50", "1"),
        ("S1", "赠品", "白檀香水", "2", "2"),
        ("S1", "赠品", "白檀香水", "2", "10"),
        ("S1", "赠品", "白檀香水", "100", "1"),
        ("S1", "赠品", "红腰子精华", "10", "1"),
        ("S1", "赠品", "悦薇水", "0", "1"),
        ("S1", "到手价", None, None, None),
        ("S1", "优惠券", "美妆券", None, None),
        ("S2", "主品", "玉龙茶香香水", "100", "1"),
        ("UNK", "赠品", "归属不明", "5", "1"),
    ]


def test_spec_gift_combo_safely_backfills_missing_main():
    products = [{
        "raw_name": "玉龙茶香香水",
        "qwen_matched_name": "玉龙茶香香水",
        "combination_ids": ["S1"],
        "spec_text": "50ml",
        "qty_text": "1",
        "is_cosmetic": True,
        "is_shopping_credit": False,
        "role": "主品",
        "gift_label": None,
    }]
    repaired, added = backfill_spec_gift_combination_mains(products, COMBOS)
    assert added == ["S2"]
    assert find_missing_main_combination_ids(repaired, COMBOS) == []
    assert repaired[-1]["raw_name"] == "玉龙茶香香水"
    assert repaired[-1]["combination_ids"] == ["S2"]
    assert repaired[-1]["spec_text"] == "100ml"
    assert repaired[-1]["qty_text"] == "1"
    assert repaired[-1]["combination_main_backfill"] is True


def test_spec_gift_combo_backfill_is_conservative():
    base = [{
        "raw_name": "玉龙茶香香水", "qwen_matched_name": "玉龙茶香香水",
        "combination_ids": ["S1"], "role": "主品", "spec_text": "50ml",
    }]
    already_complete = base + [{**base[0], "combination_ids": ["S2"], "spec_text": "100ml"}]
    repaired, added = backfill_spec_gift_combination_mains(already_complete, COMBOS)
    assert added == [] and len(repaired) == 2

    threshold_combos = [COMBOS[0], {**COMBOS[1], "evidence_text": "满100ml礼赠"}]
    repaired, added = backfill_spec_gift_combination_mains(base, threshold_combos)
    assert added == [] and find_missing_main_combination_ids(repaired, threshold_combos) == ["S2"]

    multiple_mains = base + [{
        "raw_name": "另一主品", "qwen_matched_name": "另一主品",
        "combination_ids": ["S1"], "role": "主品", "spec_text": "50ml",
    }]
    repaired, added = backfill_spec_gift_combination_mains(multiple_mains, COMBOS)
    assert added == [] and find_missing_main_combination_ids(repaired, COMBOS) == ["S2"]


def test_gate_routes_missing_main_to_product_repair():
    state = {
        "classified_items": [{
            "类型": "主品", "名称": "玉龙茶香香水", "spec": "50", "unit": "ml",
            "qty": "1", "combination_ids": ["S1"],
        }, {
            "类型": "赠品", "名称": "玉龙茶香身体乳", "spec": "50", "unit": "ml",
            "qty": "1", "combination_ids": ["S2"],
        }],
        "sales_combinations": COMBOS,
        "word_package": ["玉龙茶香香水", "玉龙茶香身体乳"],
        "image_url": "image",
        "combination_checker_complete": True,
        "combination_checker_issues": [],
        "latest_item_evaluations": [],
        "normalized_items": [],
        "product_iteration": 0,
        "max_product_iterations": 2,
        "last_issue_signature": None,
        "no_progress_count": 0,
        "benefits_extracted": False,
    }
    result = gates.verify_product_combinations(state)
    assert result["next_action"] == "repair_product"
    assert any(issue["code"] == "MISSING_COMBINATION_MAIN" for issue in result["current_issues"])
