"""测试 n3_normalize 模块: 归一化/规格分离/拆条/数量兜底。"""

import sys
sys.path.insert(0, "/Users/stormman/Documents/coding/PPI项目")

from ppi_loop.nodes import n3_normalize, product_processing
from ppi_loop.prompts.contracts import (
    BENEFIT_CHECKER_PROMPT,
    BENEFIT_MAKER_PROMPT,
    PRODUCT_MAKER_PROMPT,
)


def _product(
    spec_text,
    qty_text,
    *,
    raw_name="权力PRO粉底液",
    role="赠品",
    gift_label="赠",
):
    return {
        "raw_name": raw_name,
        "spec_text": spec_text,
        "qty_text": qty_text,
        "is_cosmetic": True,
        "role": role,
        "gift_label": gift_label,
    }


def _normalize_products(items):
    return n3_normalize.get_node()({"raw_items": items})["normalized_items"]


def test_normalize_name():
    assert "黑绷带面霜" in n3_normalize.normalize_name(
        "赫莲娜HR新黑绷带面霜50ml抗皱紧致保湿护肤品化妆品礼盒"
    )


def test_split_spec_unit():
    assert n3_normalize.split_spec_unit("50ml") == ("50", "ml")
    assert n3_normalize.split_spec_unit("125") == (None, None)
    assert n3_normalize.split_spec_unit("30mL") == ("30", "ml")


def test_parse_qty_default():
    assert n3_normalize.parse_qty_from_text("50ml") == "1"


def test_parse_qty_multiply():
    assert n3_normalize.parse_qty_from_text("5ml×7") == "7"
    assert n3_normalize.parse_qty_from_text("1片×7") == "7"


def test_split_gift_detail_simple():
    items = n3_normalize.split_gift_detail("黑绷带面霜5ml×5")
    assert len(items) == 1
    assert items[0]["raw_name"] == "黑绷带面霜"
    assert items[0]["spec"] == "5"
    assert items[0]["unit"] == "ml"
    assert items[0]["qty"] == "5"
    assert items[0]["gift_label"] == "赠品"


def test_split_gift_detail_with_label():
    items = n3_normalize.split_gift_detail(
        "黑绷带面霜5ml×1(会员直播加赠)"
    )
    assert len(items) == 1
    assert items[0]["gift_label"] == "会员直播加赠"
    assert items[0]["qty"] == "1"


def test_split_gift_detail_multiple():
    items = n3_normalize.split_gift_detail(
        "黑绷带面霜5ml×5 + 白绷带面霜5ml×1(会员直播加赠) + 小露珠精华水30ml×1"
    )
    assert len(items) == 3
    assert items[0]["raw_name"] == "黑绷带面霜"
    assert items[1]["raw_name"] == "白绷带面霜"
    assert items[1]["gift_label"] == "会员直播加赠"
    assert items[2]["raw_name"] == "小露珠精华水"


def test_normalize_item():
    item = {
        "raw_name": "赫莲娜HR新黑绷带面霜50ml抗皱紧致保湿护肤品化妆品礼盒",
        "spec_text": "50ml",
        "qty_text": None,
        "is_cosmetic": True,
        "role": "主品",
    }
    result = n3_normalize.normalize_item(item)
    assert "黑绷带面霜" in result["normalized_name"]
    assert result["spec"] == "50"
    assert result["unit"] == "ml"
    assert result["qty"] == "1"


def test_parse_qty_pair():
    """'6对' → qty='6', unit从qty_text回填为'对'"""
    assert n3_normalize.parse_qty_from_text("6对") == "6"
    assert n3_normalize.parse_qty_from_text("6 对") == "6"


def test_eye_mask_unit_backfill():
    """眼膜 qty_text='6对' + spec_text='' → spec='0' unit='对' qty='6'"""
    item = {
        "raw_name": "「小熨斗」眼膜",
        "spec_text": "",
        "qty_text": "6对",
        "is_cosmetic": True,
        "role": "赠品",
    }
    result = n3_normalize.normalize_item(item)
    assert result["spec"] == "0", f"spec 应为'0',实际 {result['spec']!r}"
    assert result["unit"] == "对", f"unit 应为'对',实际 {result['unit']!r}"
    assert result["qty"] == "6", f"qty 应为'6',实际 {result['qty']!r}"


def test_missing_spec_and_qty_use_fixed_fallbacks():
    item = {
        "raw_name": "红腰子精华",
        "spec_text": None,
        "qty_text": None,
        "is_cosmetic": True,
        "role": "主品",
    }
    result = n3_normalize.normalize_item(item)
    assert result["spec"] == "0"
    assert result["unit"] == ""
    assert result["qty"] == "1"


def test_ml_unit_not_overwritten():
    """spec_text 有 ml 时,unit 沿用 spec,不被 qty_text 的计数单位覆盖"""
    item = {
        "raw_name": "红腰子精华",
        "spec_text": "50ml",
        "qty_text": "2瓶",
        "is_cosmetic": True,
        "role": "主品",
    }
    result = n3_normalize.normalize_item(item)
    assert result["unit"] == "ml", f"spec 已给 ml,不应被'瓶'覆盖"


# ===== strip_matching_trailing_spec =====

def test_strip_trailing_spec_basic():
    """'「蓝胖子」防晒50ml' + '50ml' → '「蓝胖子」防晒'"""
    result = n3_normalize.strip_matching_trailing_spec("「蓝胖子」防晒50ml", "50ml")
    assert result == "「蓝胖子」防晒", f"期望'「蓝胖子」防晒',实际 {result!r}"


def test_strip_trailing_spec_case_insensitive():
    """'防晒 50ML' + '50ml' → '防晒'"""
    result = n3_normalize.strip_matching_trailing_spec("防晒 50ML", "50ml")
    assert result == "防晒", f"期望'防晒',实际 {result!r}"


def test_strip_trailing_spec_digit_boundary():
    """'防晒150ml' + spec_text='50ml' → 不误删"""
    result = n3_normalize.strip_matching_trailing_spec("防晒150ml", "50ml")
    assert result == "防晒150ml", f"期望'防晒150ml',实际 {result!r}"


def test_strip_trailing_spec_not_at_end():
    """'50ml防晒' → 规格不在末尾,不删"""
    result = n3_normalize.strip_matching_trailing_spec("50ml防晒", "50ml")
    assert result == "50ml防晒"


def test_strip_trailing_spec_none():
    """spec_text=None → 名称不变"""
    result = n3_normalize.strip_matching_trailing_spec("防晒", None)
    assert result == "防晒"


def test_normalize_item_strips_trailing_spec():
    """normalize_item 集成: raw_name='「蓝胖子」防晒50ml' + spec_text='50ml'
    → normalized_name 不含 '50ml', raw_name 仍保留原文"""
    item = {
        "raw_name": "「蓝胖子」防晒50ml",
        "spec_text": "50ml",
        "qty_text": None,
        "is_cosmetic": True,
        "role": "主品",
    }
    result = n3_normalize.normalize_item(item)
    # raw_name 保留原文
    assert result["raw_name"] == "「蓝胖子」防晒50ml"
    # normalized_name 已剥离尾部规格
    assert "50ml" not in result["normalized_name"], \
        f"normalized_name 不应含50ml: {result['normalized_name']!r}"
    # 规格解析不受影响
    assert result["spec"] == "50"
    assert result["unit"] == "ml"


def test_equal_total_gift_summary_removed():
    results = _normalize_products([
        _product("25ml", None, gift_label="享"),
        _product("5ml", "*5", gift_label="赠"),
    ])
    assert [(item["spec"], item["qty"]) for item in results] == [("5", "5")]


def test_product_processing_records_removed_summary_reconciliation():
    summary = _product("25ml", None, gift_label="享")
    summary["item_id"] = "prd_001"
    detail = _product("5ml", "*5", gift_label="赠")
    detail["item_id"] = "prd_002"
    update = product_processing.process_product_candidates({
        "raw_items": [summary, detail],
        "word_package": ["权力PRO粉底液"],
    })

    assert [item["item_id"] for item in update["normalized_items"]] == ["prd_002"]
    decision = update["product_reconciliation_decisions"][0]
    assert decision["reason"] == "equivalent_spec_summary"
    assert decision["dropped_item_id"] == "prd_001"
    assert decision["retained_item_ids"] == ["prd_002"]
    assert decision["calculated_total"] == "25"


def test_equal_total_gift_summary_removed_in_reverse_order():
    results = _normalize_products([
        _product("5ml×5", None),
        _product("25ml", None, gift_label="赠品"),
    ])
    assert [(item["spec"], item["qty"]) for item in results] == [("5", "5")]


def test_unequal_total_gifts_are_kept():
    results = _normalize_products([_product("30ml", None), _product("5ml", "*5")])
    assert len(results) == 2


def test_same_total_main_and_gift_are_kept():
    results = _normalize_products([
        _product("25ml", None, role="主品", gift_label=None),
        _product("5ml", "*5"),
    ])
    assert len(results) == 2


def test_same_total_different_gift_conditions_are_kept():
    results = _normalize_products([
        _product("25ml", None, gift_label="满600赠"),
        _product("5ml", "*5", gift_label="会员加赠"),
    ])
    assert len(results) == 2


def test_same_total_different_units_or_missing_spec_are_kept():
    different_units = _normalize_products([_product("25g", None), _product("5ml", "*5")])
    missing_spec = _normalize_products([_product(None, None), _product("5ml", "*5")])
    assert len(different_units) == 2
    assert len(missing_spec) == 2


def test_two_explicit_multiply_gifts_are_kept():
    results = _normalize_products([_product("25ml×1", None), _product("5ml×5", None)])
    assert len(results) == 2


def test_single_product_is_unchanged():
    product = _product("5ml", "*5")
    product["qwen_matched_name"] = "权力PRO粉底液"
    results = _normalize_products([product])
    assert len(results) == 1
    assert results[0]["raw_name"] == "权力PRO粉底液"
    assert results[0]["qwen_matched_name"] == "权力PRO粉底液"


def test_gift_summary_removed_after_detail_split():
    results = _normalize_products([
        _product("25ml", None, gift_label="享"),
        _product(
            "权力PRO粉底液5ml×5 + 红管唇釉3ml×1",
            None,
            raw_name="赠品组合",
            gift_label="赠",
        ),
    ])
    assert [(item["raw_name"], item["spec"], item["qty"]) for item in results] == [
        ("权力PRO粉底液", "5", "5"),
        ("红管唇釉", "3", "1"),
    ]
    assert all(item["qwen_matched_name"] is None for item in results)


def test_products_prompt_uses_required_structure_and_summary_example():
    prompt = PRODUCT_MAKER_PROMPT
    assert "sole Maker" in prompt
    assert "sales_combinations" in prompt
    assert "qwen_matched_name" in prompt
    assert "every S-combination has at least one main" in prompt


def test_price_coupons_prompt_preserves_structure_and_new_rules():
    prompt = BENEFIT_MAKER_PROMPT
    assert "sole Maker" in prompt
    assert "prices and coupons" in prompt
    assert "different names remain separate" in prompt
    assert "X折" in prompt
    assert "shopping credit" in prompt


def test_benefit_checker_distinguishes_raw_and_normalized_layers():
    prompt = BENEFIT_CHECKER_PROMPT
    assert "raw层是原始证据候选池" in prompt
    assert "不得因raw重叠输出PRICE_COUPON_CONFLICT" in prompt
    assert "优惠券/8.5/8.5折" in prompt
    assert 'code="NORMALIZATION_LOSS"' in prompt
    assert "repairable=false" in prompt


if __name__ == "__main__":
    test_normalize_name()
    test_split_spec_unit()
    test_parse_qty_default()
    test_parse_qty_multiply()
    test_split_gift_detail_simple()
    test_split_gift_detail_with_label()
    test_split_gift_detail_multiple()
    test_normalize_item()
    test_parse_qty_pair()
    test_eye_mask_unit_backfill()
    test_ml_unit_not_overwritten()
    test_strip_trailing_spec_basic()
    test_strip_trailing_spec_case_insensitive()
    test_strip_trailing_spec_digit_boundary()
    test_strip_trailing_spec_not_at_end()
    test_strip_trailing_spec_none()
    test_normalize_item_strips_trailing_spec()
    test_products_prompt_uses_required_structure_and_summary_example()
    test_price_coupons_prompt_preserves_structure_and_new_rules()
    print("n3_normalize: 全部测试通过 ✓")
