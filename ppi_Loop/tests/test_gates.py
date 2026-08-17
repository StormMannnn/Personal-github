from ppi_loop.diagnostics import diagnostic_record
from ppi_loop.nodes import gates, product_processing


COMBOS = [{
    "combination_id": "S1", "purchase_condition_text": "50ml",
    "section_hint": "左侧", "evidence_text": "50ml礼赠",
}, {
    "combination_id": "S2", "purchase_condition_text": "100ml",
    "section_hint": "右侧", "evidence_text": "100ml礼赠",
}]


def _raw(item_id, combo, spec):
    return {
        "item_id": item_id, "raw_name": "玉龙茶香香水",
        "qwen_matched_name": "玉龙茶香香水", "combination_ids": [combo],
        "spec_text": spec, "qty_text": "1", "is_cosmetic": True,
        "is_shopping_credit": False, "role": "主品", "gift_label": None,
        "is_gift_box": False, "box_contents": None, "is_sample": False,
        "section_hint": combo,
    }


def _evaluation(item_id):
    return {
        "target_item_id": item_id,
        "score_breakdown": {
            "raw_name": 20, "matched_name": 30, "role": 15,
            "specification": 20, "quantity": 10, "existence_and_grouping": 5,
        },
        "vlm_evaluation_score": 100, "reason": "正确",
    }


def test_missing_combo_main_routes_to_qwen_repair_not_kimi_generation():
    state = {
        "raw_items": [_raw("prd_001", "S1", "50ml")],
        "word_package": ["玉龙茶香香水"], "sales_combinations": COMBOS,
        "latest_item_evaluations": [_evaluation("prd_001")],
        "combination_checker_issues": [{
            "code": "MISSING_COMBINATION_MAIN", "scope": "product", "severity": "fatal",
            "combination_id": "S2", "target_item_id": None, "evidence_text": "100ml礼赠",
            "evidence_region": "右侧", "observed_fact": "S2缺主品",
            "repair_instruction": "Qwen补齐S2", "confidence": 0.99, "repairable": True,
        }],
        "combination_checker_complete": True, "product_iteration": 0,
        "max_product_iterations": 2, "last_issue_signature": None,
        "no_progress_count": 0, "benefits_extracted": False,
    }
    state.update(product_processing.process_product_candidates(state))
    result = gates.verify_product_combinations(state)
    assert result["next_action"] == "repair_product"
    assert result["terminal_status"] == ""


def test_same_issue_twice_stops_for_human():
    issues = [{
        "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
        "observed_fact": "漏品", "repair_instruction": "重新提取", "confidence": 1,
        "repairable": True,
    }]
    first = gates._decide({
        "product_iteration": 0, "max_product_iterations": 2,
        "last_issue_signature": None, "no_progress_count": 0,
    }, issues, "product", "benefits")
    second = gates._decide({
        "product_iteration": 1, "max_product_iterations": 2,
        "last_issue_signature": first["last_issue_signature"],
        "no_progress_count": first["no_progress_count"],
    }, issues, "product", "benefits")
    assert second["terminal_status"] == "NEEDS_HUMAN"
    assert second["next_action"] == "stop"


def test_intermediate_layer_pass_does_not_erase_no_progress_signature():
    issue = {
        "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
        "observed_fact": "右侧漏品", "repair_instruction": "重新提取",
        "confidence": 1, "repairable": True,
    }
    base = {
        "product_iteration": 0,
        "max_product_iterations": 2,
        "last_issue_signature": None,
        "no_progress_count": 0,
        "no_progress_limit": 2,
    }
    first = gates._decide(base, [issue], "product", "score")
    after_repair = {
        **base,
        **first,
        "product_iteration": 1,
        "combination_checker_complete": True,
        "combination_checker_issues": [],
        "classified_items": [{
            "item_id": "prd_001", "类型": "主品", "combination_ids": ["S1"],
        }],
        "normalized_items": [{
            "item_id": "prd_001", "combination_ids": ["S1"],
        }],
        "sales_combinations": [{
            "combination_id": "S1", "purchase_condition_text": "购买主品",
            "section_hint": "中央", "evidence_text": "购买主品",
        }],
    }
    combination_pass = gates.verify_product_combinations(after_repair)
    assert "last_issue_signature" not in combination_pass
    second = gates._decide(
        {**after_repair, **combination_pass},
        [issue],
        "product",
        "score",
    )
    assert second["terminal_status"] == "NEEDS_HUMAN"
    assert second["next_action"] == "stop"


def test_issue_signature_ignores_checker_wording_drift():
    first = [{
        "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
        "combination_id": "S1", "target_item_id": None,
        "evidence_text": "红管唇釉小样", "evidence_region": "右侧赠品区",
        "observed_fact": "右侧漏一个赠品",
    }]
    second = [{**first[0], "observed_fact": "赠品区仍有一个商品未覆盖"}]
    assert gates._signature(first) == gates._signature(second)


def test_same_issue_code_with_different_evidence_has_different_meaning():
    first = [{
        "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
        "combination_id": "S1", "target_item_id": None,
        "evidence_text": "红色盖子唇釉小样", "evidence_region": "右侧",
        "observed_fact": "漏了唇釉小样",
    }]
    second = [{
        **first[0],
        "evidence_text": "银色盖子粉底液小样",
        "observed_fact": "漏了粉底液小样",
    }]
    assert gates._signature(first) != gates._signature(second)


def test_observed_fact_defines_meaning_when_direct_evidence_is_empty():
    first = [{
        "code": "FINAL_VALIDATION_FAILED", "scope": "product",
        "severity": "fatal", "evidence_text": "", "observed_fact": "S1缺少主品",
    }]
    second = [{**first[0], "observed_fact": "S2缺少主品"}]
    assert gates._signature(first) != gates._signature(second)


def test_issue_meaning_normalizes_surface_punctuation_and_whitespace():
    first = [{
        "code": "WRONG_FIELD_VALUE", "scope": "product", "severity": "fatal",
        "target_item_id": "prd_001", "affected_fields": ["qty_text"],
        "evidence_text": "粉底液5ml，数量  6",
    }]
    second = [{
        **first[0], "evidence_text": "粉底液 5ml, 数量 6!",
    }]
    assert gates._signature(first) == gates._signature(second)


def test_affected_fields_are_part_of_issue_meaning():
    first = [{
        "code": "WRONG_FIELD_VALUE", "scope": "product", "severity": "fatal",
        "target_item_id": "prd_001", "affected_fields": ["qty_text"],
        "evidence_text": "右侧粉底液小样",
    }]
    second = [{**first[0], "affected_fields": ["role"]}]
    assert gates._signature(first) != gates._signature(second)


def test_issue_order_does_not_change_semantic_signature():
    first = {
        "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
        "combination_id": "S1", "evidence_text": "唇釉小样",
    }
    second = {
        "code": "WRONG_ROLE", "scope": "product", "severity": "fatal",
        "combination_id": "S1", "target_item_id": "prd_002",
        "affected_fields": ["role"], "evidence_text": "粉底液小样",
    }
    assert gates._signature([first, second]) == gates._signature([second, first])


def test_partial_issue_resolution_counts_as_progress():
    issue_a = {
        "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
        "combination_id": "S1", "evidence_text": "唇釉小样",
        "repairable": True,
    }
    issue_b = {
        "code": "WRONG_ROLE", "scope": "product", "severity": "fatal",
        "target_item_id": "prd_002", "affected_fields": ["role"],
        "evidence_text": "粉底液小样", "repairable": True,
    }
    base = {
        "product_iteration": 0, "max_product_iterations": 4,
        "last_issue_signature": None, "no_progress_count": 0,
        "no_progress_limit": 2,
    }
    first = gates._decide(base, [issue_a, issue_b], "product", "score")
    second = gates._decide({
        **base, **first, "product_iteration": 1,
    }, [issue_b], "product", "score")

    assert second["no_progress_count"] == 1
    assert second["no_progress_comparison"]["same_meaning"] is False
    assert second["terminal_status"] == ""


def test_a_b_a_issue_change_is_not_false_consecutive_no_progress():
    def issue(evidence):
        return {
            "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
            "combination_id": "S1", "evidence_text": evidence,
            "repairable": True,
        }

    base = {
        "product_iteration": 0, "max_product_iterations": 3,
        "last_issue_signature": None, "no_progress_count": 0,
        "no_progress_limit": 2,
    }
    first = gates._decide(base, [issue("A赠品")], "product", "score")
    second = gates._decide(
        {**base, **first, "product_iteration": 1},
        [issue("B赠品")], "product", "score",
    )
    third = gates._decide(
        {**base, **second, "product_iteration": 2},
        [issue("A赠品")], "product", "score",
    )

    assert second["no_progress_count"] == 1
    assert third["no_progress_count"] == 1
    assert third["terminal_status"] == ""
    assert third["next_action"] == "repair_product"


def test_success_resets_semantic_no_progress_state():
    update = gates._decide({
        "last_issue_signature": "old",
        "last_issue_meanings": [{"code": "MISSING_PRODUCT"}],
        "no_progress_count": 1,
        "no_progress_comparison": {"same_meaning": True},
    }, [], "product", "score")

    assert update["last_issue_signature"] is None
    assert update["last_issue_meanings"] == []
    assert update["no_progress_count"] == 0
    assert update["no_progress_comparison"] == {}


def test_semantic_no_progress_details_are_written_to_diagnostics():
    issue = {
        "code": "MISSING_PRODUCT", "scope": "product", "severity": "fatal",
        "combination_id": "S1", "evidence_text": "右侧唇釉小样",
        "evidence_region": "右侧赠品区", "repairable": True,
    }
    decision = gates._decide({
        "product_iteration": 0, "max_product_iterations": 2,
        "last_issue_signature": None, "no_progress_count": 0,
        "no_progress_limit": 2,
    }, [issue], "product", "score")
    record = diagnostic_record(decision, "thread-1")

    assert record["last_issue_signature"] == decision["last_issue_signature"]
    assert record["last_issue_meanings"][0]["evidence_anchor"] == "右侧唇釉小样"
    assert record["no_progress_comparison"]["same_meaning"] is False


def test_python_gate_rejects_qwen_name_outside_word_package():
    state = {
        "raw_items": [_raw("prd_001", "S1", "50ml"), _raw("prd_002", "S2", "100ml")],
        "word_package": ["其他名称"], "sales_combinations": COMBOS,
        "latest_item_evaluations": [_evaluation("prd_001"), _evaluation("prd_002")],
        "coverage_checker_issues": [], "coverage_checker_complete": True,
        "product_iteration": 0,
        "max_product_iterations": 2, "last_issue_signature": None,
        "no_progress_count": 0, "no_progress_limit": 2, "benefits_extracted": False,
    }
    state.update(product_processing.process_product_candidates(state))
    result = gates.verify_product_coverage(state)
    assert result["next_action"] == "repair_product"
    assert any(issue["code"] == "QWEN_NAME_OUTSIDE_WORD_PACKAGE"
               for issue in result["current_issues"])
