"""测试 n5_classify 模块: 产品仅区分主品/赠品 + 礼盒拆解。"""

import sys
import pytest
sys.path.insert(0, "/Users/stormman/Documents/coding/PPI项目")

from ppi_loop.nodes import n5_classify
from ppi_loop.schemas import OutputItem


# ===== 原满赠标签统一归赠品 =====

def test_man_gift_with_number():
    """'满1000赠' → 赠品，不保留门槛。"""
    t, thr = n5_classify.classify_gift_type("满1000赠")
    assert t == "赠品"
    assert thr is None


def test_every_man_gift():
    """'每满1000赠一次' → 赠品，不保留门槛。"""
    t, thr = n5_classify.classify_gift_type("每满1000赠一次")
    assert t == "赠品"
    assert thr is None


def test_member_man_gift():
    """'会员满1000赠' → 赠品。"""
    t, thr = n5_classify.classify_gift_type("会员满1000赠")
    assert t == "赠品"
    assert thr is None


def test_man_no_number():
    """'满额赠'(无数字) → 赠品。"""
    t, thr = n5_classify.classify_gift_type("满额赠")
    assert t == "赠品"
    assert thr is None


def test_member_man_no_number():
    """'会员满额赠'(无数字) → 赠品。"""
    t, thr = n5_classify.classify_gift_type("会员满额赠")
    assert t == "赠品"
    assert thr is None


# ===== v9 赠品类(无"满"字,会员/直播间/新人/加赠/买赠/模糊→全部赠品) =====

def test_member_gift_no_man():
    """'会员赠送'(无满字) → 赠品"""
    t, thr = n5_classify.classify_gift_type("会员赠送")
    assert t == "赠品", f"无满字→赠品,实际 {t}"
    assert thr is None


def test_live_gift():
    """'直播间下单赠' → 赠品(v9 取消直播间增等复合类型)"""
    t, thr = n5_classify.classify_gift_type("直播间下单赠")
    assert t == "赠品"
    assert thr is None


def test_newcomer_gift():
    """'新人赠' → 赠品"""
    t, thr = n5_classify.classify_gift_type("新人赠")
    assert t == "赠品"
    assert thr is None


def test_extra_gift():
    """'加赠' → 赠品"""
    t, thr = n5_classify.classify_gift_type("加赠")
    assert t == "赠品"
    assert thr is None


def test_simple_gift():
    """'赠' → 赠品"""
    t, thr = n5_classify.classify_gift_type("赠")
    assert t == "赠品"
    assert thr is None


def test_none_label():
    """None → 赠品"""
    t, thr = n5_classify.classify_gift_type(None)
    assert t == "赠品"
    assert thr is None


# ===== 主品/赠品归类 =====

def test_classify_main_product():
    item = {"raw_name": "黑绷带面霜", "role": "主品", "matched_name": "黑绷带面霜"}
    results = n5_classify.classify_items([item])
    assert results[0]["类型"] == "主品"


def test_classify_gift_drops_legacy_condition():
    item = {
        "raw_name": "赠品面霜", "role": "赠品", "matched_name": "赠品面霜",
        "gift_label": "满1000赠", "使用条件": "满1000",
    }
    result = n5_classify.classify_items([item])[0]
    assert result["类型"] == "赠品"
    assert "使用条件" not in result


def test_non_main_role_defaults_to_gift():
    item = {"raw_name": "赠品面霜", "role": "未知", "matched_name": "赠品面霜"}
    assert n5_classify.classify_items([item])[0]["类型"] == "赠品"


# ===== 礼盒拆解 =====

def test_unpack_gift_box():
    item = {
        "raw_name": "赫莲娜礼盒", "role": "主品", "is_gift_box": True,
        "box_contents": "黑绷带面霜50ml + 白绷带面霜50ml",
    }
    results = n5_classify.classify_items([item])
    assert len(results) == 2  # 拆成两个单品


# ===== v9 产物中不出现任何会员复合类型 =====

def test_all_gift_labels_are_gifts():
    """所有赠品标签只能归为赠品。"""
    labels = [
        "会员满1000赠", "每满1000赠一次", "满额赠",
        "会员赠送", "会员满额赠", "直播间下单赠", "新人赠",
        "加赠", "买赠", "赠", None,
    ]
    for lb in labels:
        assert n5_classify.classify_gift_type(lb) == ("赠品", None)


def test_output_schema_rejects_full_gift_type():
    with pytest.raises(ValueError):
        OutputItem.model_validate({"类型": "满赠", "名称": "旧满赠"})


if __name__ == "__main__":
    test_man_gift_with_number()
    test_every_man_gift()
    test_member_man_gift()
    test_man_no_number()
    test_member_man_no_number()
    test_member_gift_no_man()
    test_live_gift()
    test_newcomer_gift()
    test_extra_gift()
    test_simple_gift()
    test_none_label()
    test_classify_main_product()
    test_classify_gift_drops_legacy_condition()
    test_non_main_role_defaults_to_gift()
    test_unpack_gift_box()
    test_all_gift_labels_are_gifts()
    test_output_schema_rejects_full_gift_type()
    print("n5_classify: 全部测试通过 ✓")
