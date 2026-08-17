import pytest
from pydantic import ValidationError

from ppi_loop.nodes.patching import apply_maker_patch
from ppi_loop.schemas import (
    CombinationAudit,
    ItemEvaluation,
    MakerPatch,
    ProductCoverageAudit,
    ProductScoreAudit,
)


def _product(name="红腰子精华", combo="S1"):
    return {
        "item_id": "prd_001",
        "raw_name": name,
        "qwen_matched_name": name,
        "combination_ids": [combo],
        "spec_text": "50ml",
        "qty_text": "1",
        "is_cosmetic": True,
        "is_shopping_credit": False,
        "role": "主品",
        "gift_label": None,
        "is_gift_box": False,
        "box_contents": None,
        "is_sample": False,
        "section_hint": "中央",
    }


def test_checker_cannot_return_supplemental_products_or_matched_name():
    with pytest.raises(ValidationError):
        ProductCoverageAudit.model_validate({
            "coverage_checks": [],
            "field_issues": [],
            "audit_complete": True,
            "supplemental_products": [_product()],
        })
    with pytest.raises(ValidationError):
        ProductScoreAudit.model_validate({
            "item_evaluations": [],
            "audit_complete": True,
            "matched_name": "红腰子精华",
        })


def test_three_product_checker_schemas_reject_cross_layer_fields():
    with pytest.raises(ValidationError):
        CombinationAudit.model_validate({
            "combination_checks": [],
            "issues": [],
            "audit_complete": True,
            "coverage_checks": [],
        })
    with pytest.raises(ValidationError):
        ProductCoverageAudit.model_validate({
            "coverage_checks": [],
            "field_issues": [],
            "audit_complete": True,
            "item_evaluations": [],
        })
    with pytest.raises(ValidationError):
        ProductScoreAudit.model_validate({
            "item_evaluations": [],
            "audit_complete": True,
            "issues": [],
        })


def test_checker_score_requires_exact_arithmetic_sum():
    valid = {
        "target_item_id": "prd_001",
        "score_breakdown": {
            "raw_name": 20, "matched_name": 30, "role": 15,
            "specification": 20, "quantity": 10, "existence_and_grouping": 5,
        },
        "vlm_evaluation_score": 100,
        "reason": "分项合计",
    }
    assert ItemEvaluation.model_validate(valid).vlm_evaluation_score == 100
    with pytest.raises(ValidationError, match="INVALID_CHECKER_SCORE"):
        ItemEvaluation.model_validate({**valid, "vlm_evaluation_score": 99})


def test_product_patch_is_transactional_and_qwen_is_only_writer():
    state = {
        "raw_items": [_product()],
        "raw_prices": [],
        "raw_coupons": [],
        "sales_combinations": [{
            "combination_id": "S1", "purchase_condition_text": "50ml",
            "section_hint": "左侧", "evidence_text": "50ml礼赠",
        }, {
            "combination_id": "S2", "purchase_condition_text": "100ml",
            "section_hint": "右侧", "evidence_text": "100ml礼赠",
        }],
        "current_issues": [{"code": "MISSING_COMBINATION_MAIN", "target_item_id": None}],
    }
    new_product = _product("红腰子精华", "S2")
    new_product.pop("item_id")
    new_product["spec_text"] = "100ml"
    patch = MakerPatch.model_validate({"operations": [{
        "action": "add", "scope": "product", "target_item_id": None,
        "changes": new_product, "evidence": ["100ml礼赠"],
        "addresses_issue_codes": ["MISSING_COMBINATION_MAIN"],
    }]})
    update, accepted, _ = apply_maker_patch(state, patch, "product")
    assert accepted is True
    assert [item["item_id"] for item in update["raw_items"]] == ["prd_001", "prd_002"]
    assert update["raw_items"][1]["spec_text"] == "100ml"
    assert len(state["raw_items"]) == 1

    invalid = MakerPatch.model_validate({"operations": [{
        "action": "add", "scope": "coupon", "target_item_id": None,
        "changes": {"text": "美妆券减100", "combination_ids": ["S1"]},
        "evidence": [], "addresses_issue_codes": ["MISSING_COMBINATION_MAIN"],
    }]})
    update, accepted, _ = apply_maker_patch(state, invalid, "product")
    assert accepted is False and update == {}
