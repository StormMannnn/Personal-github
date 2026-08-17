import asyncio

from ppi_loop.nodes import checker, gates
from ppi_loop.schemas import (
    CheckerAudit,
    CombinationAudit,
    ProductCoverageAudit,
    ProductScoreAudit,
)


def _product(
    item_id: str, *, qwen_name: str | None = "权力PRO粉底液",
    spec_text: str = "5ml", qty_text: str = "1", role: str = "赠品",
    combination_ids: list[str] | None = None,
) -> dict:
    return {
        "item_id": item_id,
        "raw_name": "权力PRO粉底液",
        "qwen_matched_name": qwen_name,
        "combination_ids": combination_ids or ["S1"],
        "spec_text": spec_text,
        "qty_text": qty_text,
        "is_cosmetic": True,
        "is_shopping_credit": False,
        "role": role,
        "gift_label": "礼赠明细",
        "is_gift_box": False,
        "box_contents": None,
        "is_sample": True,
        "section_hint": "右侧",
    }


def _evaluation(
    item_id: str, *, matched_name: int = 30, role: int = 15
) -> dict:
    total = 20 + matched_name + role + 20 + 10 + 5
    return {
        "target_item_id": item_id,
        "score_breakdown": {
            "raw_name": 20,
            "matched_name": matched_name,
            "role": role,
            "specification": 20,
            "quantity": 10,
            "existence_and_grouping": 5,
        },
        "vlm_evaluation_score": total,
        "reason": "分项评分",
    }


def _state() -> dict:
    return {
        "normalized_items": [_product("prd_002"), _product("prd_003")],
        "word_package": ["权力PRO粉底液"],
        "sales_combinations": [{
            "combination_id": "S1",
            "purchase_condition_text": "购买主品",
            "section_hint": "全图",
            "evidence_text": "购买主品",
        }],
        "product_iteration": 1,
    }


def _audit(*, status: str, matched_ids: list[str]) -> ProductCoverageAudit:
    return ProductCoverageAudit.model_validate({
        "coverage_checks": [{
            "evidence_id": "ev_001",
            "evidence_text": "5ml*6+5ml",
            "evidence_region": "右侧小样区",
            "object_description": "7支5ml小样",
            "status": status,
            "matched_item_ids": matched_ids,
            "confidence": 0.98,
        }],
        "field_issues": [],
        "audit_complete": True,
    })


def test_six_plus_one_is_covered_without_missing_issue():
    parsed = _audit(status="covered", matched_ids=["prd_002", "prd_003"])
    assert checker._coverage_audit_semantic_errors(parsed, _state()) == []
    assert checker._coverage_issues_from_audit(parsed, _state()) == []


def test_missing_coverage_is_the_only_source_of_missing_product():
    parsed = _audit(status="missing", matched_ids=[])
    assert checker._coverage_audit_semantic_errors(parsed, _state()) == []
    issues = checker._coverage_issues_from_audit(parsed, _state())
    assert len(issues) == 1
    assert issues[0]["code"] == "MISSING_PRODUCT"
    assert issues[0]["combination_id"] == "S1"


def test_summary_only_never_creates_product_issue():
    state = _state()
    state["normalized_items"] = [
        _product("prd_101", spec_text="7.5ml"),
        _product("prd_102", spec_text="7.5ml"),
        _product("prd_103", spec_text="2ml"),
        _product("prd_104", spec_text="2ml"),
    ]
    parsed = ProductCoverageAudit.model_validate({
        "coverage_checks": [{
            "evidence_id": "ev_019",
            "evidence_text": "会员享高定香氛19ml",
            "evidence_region": "礼赠区",
            "object_description": "7.5+7.5+2+2ml礼赠总量",
            "status": "summary_only",
            "matched_item_ids": ["prd_101", "prd_102", "prd_103", "prd_104"],
            "confidence": 0.99,
        }],
        "field_issues": [],
        "audit_complete": True,
    })
    assert checker._coverage_audit_semantic_errors(parsed, state) == []
    assert checker._coverage_issues_from_audit(parsed, state) == []
    detail = checker._coverage_validation_details(parsed, state)[0]
    assert detail["valid"] is True
    assert detail["referenced_total"] == "19.0"


def test_summary_only_requires_references_and_exact_same_unit_arithmetic():
    state = _state()
    no_refs = ProductCoverageAudit.model_validate({
        "coverage_checks": [{
            "evidence_id": "ev_sum", "evidence_text": "共10ml",
            "evidence_region": "礼赠区", "object_description": "赠品总量10ml",
            "status": "summary_only", "matched_item_ids": [], "confidence": 1.0,
        }],
        "field_issues": [], "audit_complete": True,
    })
    assert any(
        "没有matched_item_ids" in error
        for error in checker._coverage_audit_semantic_errors(no_refs, state)
    )

    wrong_total = no_refs.model_copy(deep=True)
    wrong_total.coverage_checks[0].matched_item_ids = ["prd_002"]
    errors = checker._coverage_audit_semantic_errors(wrong_total, state)
    assert any("汇总算术不成立" in error for error in errors)


def test_independent_15ml_and_1_2ml_products_are_not_a_summary():
    state = _state()
    state["normalized_items"] = [
        _product("prd_015", spec_text="15ml"),
        _product("prd_012", spec_text="1.2ml"),
    ]
    parsed = ProductCoverageAudit.model_validate({
        "coverage_checks": [{
            "evidence_id": "ev_015",
            "evidence_text": "购即赠迷情挚爱香水15ml",
            "evidence_region": "右侧赠品区",
            "object_description": "迷情挚爱香水15ml赠品",
            "status": "summary_only",
            "matched_item_ids": ["prd_015"],
            "confidence": 0.98,
        }],
        "field_issues": [],
        "audit_complete": True,
    })
    errors = checker._coverage_audit_semantic_errors(parsed, state)
    assert any("没有明确汇总语义" in error for error in errors)

    parsed.coverage_checks[0].matched_item_ids = ["prd_015", "prd_012"]
    errors = checker._coverage_audit_semantic_errors(parsed, state)
    assert any("汇总算术不成立" in error for error in errors)


def test_summary_without_a_numeric_total_is_rejected():
    parsed = ProductCoverageAudit.model_validate({
        "coverage_checks": [{
            "evidence_id": "ev_no_total",
            "evidence_text": "明星香氛礼",
            "evidence_region": "右侧赠品区",
            "object_description": "香氛赠品组",
            "status": "summary_only",
            "matched_item_ids": ["prd_002", "prd_003"],
            "confidence": 0.8,
        }],
        "field_issues": [],
        "audit_complete": True,
    })
    errors = checker._coverage_audit_semantic_errors(parsed, _state())
    assert any("无法解析汇总规格" in error for error in errors)


def test_duplicate_display_must_reference_existing_product_and_never_patches():
    valid = _audit(status="duplicate_display", matched_ids=["prd_002"])
    assert checker._coverage_audit_semantic_errors(valid, _state()) == []
    assert checker._coverage_issues_from_audit(valid, _state()) == []
    assert checker._coverage_validation_details(valid, _state())[0]["valid"] is True

    invalid = _audit(status="duplicate_display", matched_ids=[])
    assert any(
        "没有matched_item_ids" in error
        for error in checker._coverage_audit_semantic_errors(invalid, _state())
    )


def test_removed_equivalent_summary_cannot_be_reclassified_as_missing():
    state = _state()
    state["product_reconciliation_decisions"] = [{
        "reason": "equivalent_spec_summary",
        "dropped_raw_name": "权力PRO粉底液",
        "dropped_spec": "30",
        "dropped_unit": "ml",
    }]
    parsed = ProductCoverageAudit.model_validate({
        "coverage_checks": [{
            "evidence_id": "ev_removed", "evidence_text": "权力PRO粉底液30ml",
            "evidence_region": "右侧", "object_description": "赠品汇总30ml",
            "status": "missing", "matched_item_ids": [], "confidence": 0.99,
        }],
        "field_issues": [], "audit_complete": True,
    })
    errors = checker._coverage_audit_semantic_errors(parsed, state)
    assert any("已确定删除的等总量汇总行" in error for error in errors)


def test_combination_checks_drive_missing_main_and_invalid_group_issues():
    state = _state()
    state["sales_combinations"] = [{
        "combination_id": "S1", "purchase_condition_text": "50ml",
        "section_hint": "左侧", "evidence_text": "50ml礼赠",
    }, {
        "combination_id": "S2", "purchase_condition_text": "100ml",
        "section_hint": "右侧", "evidence_text": "100ml礼赠",
    }]
    state["normalized_items"] = [
        _product(
            "prd_001", spec_text="50ml", role="主品",
            combination_ids=["S1"],
        )
    ]
    parsed = CombinationAudit.model_validate({
        "combination_checks": [{
            "evidence_id": "cmb_1", "evidence_text": "50ml礼赠",
            "evidence_region": "左侧", "object_description": "50ml购买方案",
            "evidence_kind": "purchase_option", "status": "covered",
            "matched_combination_ids": ["S1"],
            "expected_main_spec_text": "50ml", "confidence": 0.99,
        }, {
            "evidence_id": "cmb_2", "evidence_text": "100ml礼赠",
            "evidence_region": "右侧", "object_description": "100ml购买方案",
            "evidence_kind": "purchase_option", "status": "covered",
            "matched_combination_ids": ["S2"],
            "expected_main_spec_text": "100ml", "confidence": 0.99,
        }],
        "issues": [], "audit_complete": True,
    })
    assert checker._combination_audit_semantic_errors(parsed, state) == []
    issues = checker._combination_issues_from_audit(parsed, state)
    assert [issue["code"] for issue in issues] == ["MISSING_COMBINATION_MAIN"]
    assert issues[0]["combination_id"] == "S2"


def test_non_purchase_evidence_cannot_legitimize_sales_combination():
    state = _state()
    parsed = CombinationAudit.model_validate({
        "combination_checks": [{
            "evidence_id": "cmb_bad", "evidence_text": "累计优惠170元",
            "evidence_region": "底部", "object_description": "优惠汇总",
            "evidence_kind": "discount_summary", "status": "invalid_assignment",
            "matched_combination_ids": ["S1"],
            "expected_main_spec_text": None, "confidence": 0.99,
        }],
        "issues": [], "audit_complete": True,
    })
    assert checker._combination_audit_semantic_errors(parsed, state) == []
    issues = checker._combination_issues_from_audit(parsed, state)
    assert issues[0]["code"] == "INVALID_SALES_COMBINATION"


def test_every_maker_combination_requires_an_evidence_check():
    state = _state()
    parsed = CombinationAudit.model_validate({
        "combination_checks": [], "issues": [], "audit_complete": True,
    })
    errors = checker._combination_audit_semantic_errors(parsed, state)
    assert any("没有对应证据检查" in error for error in errors)


def test_direct_missing_issue_is_rejected_by_coverage_layer():
    payload = _audit(
        status="covered", matched_ids=["prd_002", "prd_003"]
    ).model_dump()
    payload["field_issues"] = [{
        "code": "MISSING_PRODUCT",
        "scope": "product",
        "severity": "fatal",
        "combination_id": "S1",
        "target_item_id": None,
        "evidence_text": "5ml*6+5ml",
        "evidence_region": "右侧",
        "observed_fact": "Qwen漏掉一支小样",
        "repair_instruction": "Qwen新增商品",
        "confidence": 0.9,
        "repairable": True,
        "affected_fields": [],
    }]
    parsed = ProductCoverageAudit.model_validate(payload)
    errors = checker._coverage_audit_semantic_errors(parsed, _state())
    assert any("MISSING_PRODUCT必须由coverage_checks" in error for error in errors)


def _benefit_state() -> dict:
    return {
        "sales_combinations": [{
            "combination_id": "S1",
            "purchase_condition_text": "购买30ml主品",
            "section_hint": "全图",
            "evidence_text": "直播间下单购30ml",
        }],
        "raw_prices": [{
            "amount": "99",
            "context_text": "直播间限时闪降 ¥99 以实际下单为准",
            "combination_ids": ["S1"],
            "item_id": "prc_001",
        }, {
            "amount": "60",
            "context_text": "下拉美妆惊喜券 ¥60 满600可用",
            "combination_ids": ["S1"],
            "item_id": "prc_002",
        }, {
            "amount": "25",
            "context_text": "下拉领88VIP消费券 ¥25 满200可用",
            "combination_ids": ["S1"],
            "item_id": "prc_003",
        }, {
            "amount": "184",
            "context_text": "进直播间下单 领券至高叠减¥184",
            "combination_ids": ["S1"],
            "item_id": "prc_004",
        }],
        "raw_coupons": [{
            "text": "直播间限时闪降 ¥99 以实际下单为准",
            "combination_ids": ["S1"],
            "item_id": "cpn_001",
        }, {
            "text": "下拉美妆惊喜券 ¥60 满600可用",
            "combination_ids": ["S1"],
            "item_id": "cpn_002",
        }, {
            "text": "下拉领88VIP消费券 ¥25 满200可用",
            "combination_ids": ["S1"],
            "item_id": "cpn_003",
        }],
        "price_objects": [],
        "coupon_objects": [{
            "组合ID": "S1", "类型": "优惠券", "名称": "闪降",
            "金额": "99", "使用条件": "无",
        }, {
            "组合ID": "S1", "类型": "优惠券", "名称": "美妆惊喜券",
            "金额": "60", "使用条件": "满600",
        }, {
            "组合ID": "S1", "类型": "优惠券", "名称": "88VIP消费券",
            "金额": "25", "使用条件": "满200",
        }],
    }


def _benefit_issue(
    code: str, evidence: str, *, scope: str = "coupon",
    target_item_id: str | None = None, repairable: bool = True,
) -> dict:
    return {
        "code": code,
        "scope": scope,
        "severity": "fatal",
        "combination_id": "S1",
        "target_item_id": target_item_id,
        "evidence_text": evidence,
        "evidence_region": "底部",
        "observed_fact": f"{code}最终结论",
        "repair_instruction": "按证据处理",
        "confidence": 0.9,
        "repairable": repairable,
        "affected_fields": [],
    }


def test_latest_false_benefit_findings_are_rejected_by_program_facts():
    summary = "进直播间下单 领券至高叠减¥184"
    flash = "直播间限时闪降 ¥99 以实际下单为准"
    parsed = CheckerAudit.model_validate({
        "issues": [
            _benefit_issue("MISSING_COUPON", summary),
            _benefit_issue("NORMALIZATION_LOSS", summary, repairable=False),
            _benefit_issue(
                "PRICE_COUPON_ROLE_CONFLICT", flash, scope="price",
                target_item_id="prc_001", repairable=False,
            ),
            _benefit_issue(
                "MISSING_NORMALIZED_PRICE", flash, scope="price",
                repairable=False,
            ),
        ],
        "coverage_checks": [], "item_evaluations": [], "audit_complete": True,
    })
    errors = checker._benefit_audit_semantic_errors(parsed, _benefit_state())
    assert any("优惠汇总" in error for error in errors)
    assert any("raw层重叠不算冲突" in error for error in errors)
    assert any("不具备实际支付价语义" in error for error in errors)


def test_true_missing_coupon_remains_a_repairable_checker_issue():
    evidence = "PLUS券 ¥150 满1500可用"
    parsed = CheckerAudit.model_validate({
        "issues": [_benefit_issue("MISSING_COUPON", evidence)],
        "coverage_checks": [], "item_evaluations": [], "audit_complete": True,
    })
    assert checker._benefit_audit_semantic_errors(
        parsed, _benefit_state()
    ) == []


def test_benefit_semantic_retry_stays_in_checker_layer(monkeypatch):
    calls = 0
    bad = CheckerAudit.model_validate({
        "issues": [_benefit_issue(
            "MISSING_NORMALIZED_PRICE",
            "直播间限时闪降 ¥99 以实际下单为准",
            scope="price", repairable=False,
        )],
        "coverage_checks": [], "item_evaluations": [], "audit_complete": True,
    })
    corrected = CheckerAudit.model_validate({
        "issues": [], "coverage_checks": [], "item_evaluations": [],
        "audit_complete": True,
    })

    async def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = bad if calls == 1 else corrected
        return result, {"prompt": 1, "completion": 1, "reasoning": 0}, ""

    monkeypatch.setattr(checker, "call_json_model", fake_call)
    monkeypatch.setattr(checker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(checker.audit_benefits(_benefit_state()))

    assert calls == 2
    assert update["checker_issues"] == []
    assert update["checker_retry_count"] == 1
    assert "benefit_iteration" not in update
    rejected = update["checker_rejected_audits_by_layer"]["benefit"]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["issues"][0]["code"] == "MISSING_NORMALIZED_PRICE"


def test_section_hint_only_issue_is_downgraded_to_warning():
    payload = _audit(
        status="covered", matched_ids=["prd_002", "prd_003"]
    ).model_dump()
    payload["field_issues"] = [{
        "code": "WRONG_FIELD_VALUE",
        "scope": "product",
        "severity": "fatal",
        "combination_id": "S1",
        "target_item_id": "prd_002",
        "evidence_text": "",
        "evidence_region": "右侧",
        "observed_fact": "prd_002区域描述不精确",
        "repair_instruction": "Qwen修正定位描述",
        "confidence": 0.7,
        "repairable": True,
        "affected_fields": ["section_hint"],
    }]
    parsed = ProductCoverageAudit.model_validate(payload)
    assert checker._coverage_audit_semantic_errors(parsed, _state()) == []
    issue = checker._coverage_issues_from_audit(parsed, _state())[0]
    assert issue["severity"] == "warning"
    assert issue["repairable"] is False


def test_coverage_semantic_retry_does_not_consume_maker_iteration(monkeypatch):
    calls: list[bool | None] = []
    invalid_payload = _audit(
        status="covered", matched_ids=["prd_002", "prd_003"]
    ).model_dump()
    invalid_payload["field_issues"] = [{
        "code": "MISSING_PRODUCT",
        "scope": "product",
        "severity": "fatal",
        "combination_id": "S1",
        "target_item_id": None,
        "evidence_text": "5ml*6+5ml",
        "evidence_region": "右侧",
        "observed_fact": "Qwen漏掉一支小样",
        "repair_instruction": "Qwen新增商品",
        "confidence": 0.9,
        "repairable": True,
        "affected_fields": [],
    }]
    responses = [
        ProductCoverageAudit.model_validate(invalid_payload),
        _audit(status="covered", matched_ids=["prd_002", "prd_003"]),
    ]

    async def fake_call(*args, enable_thinking=None, **kwargs):
        calls.append(enable_thinking)
        return responses.pop(0), {
            "prompt": 10, "completion": 5, "reasoning": 0
        }, ""

    monkeypatch.setattr(checker, "call_json_model", fake_call)
    monkeypatch.setattr(checker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(checker.audit_product_coverage(_state()))

    assert calls == [False, False]
    assert update["checker_retry_counts"]["coverage"] == 1
    assert update["coverage_checker_issues"] == []
    assert "product_iteration" not in update
    assert update["token_usage"][checker.config.KIMI_MODEL]["prompt"] == 20


def test_repeated_coverage_failure_stops_before_qwen_patch(monkeypatch):
    invalid_payload = _audit(
        status="covered", matched_ids=["prd_002", "prd_003"]
    ).model_dump()
    invalid_payload["field_issues"] = [{
        "code": "MISSING_PRODUCT",
        "scope": "product",
        "severity": "fatal",
        "combination_id": "S1",
        "target_item_id": None,
        "evidence_text": "5ml*6+5ml",
        "evidence_region": "右侧",
        "observed_fact": "Qwen漏掉一支小样",
        "repair_instruction": "Qwen新增商品",
        "confidence": 0.9,
        "repairable": True,
        "affected_fields": [],
    }]
    invalid = ProductCoverageAudit.model_validate(invalid_payload)
    calls = 0

    async def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return invalid, {"prompt": 1, "completion": 1, "reasoning": 0}, ""

    monkeypatch.setattr(checker, "call_json_model", fake_call)
    monkeypatch.setattr(checker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(checker.audit_product_coverage(_state()))

    assert calls == checker.config.CHECKER_SEMANTIC_MAX_RETRY + 1
    assert update["coverage_checker_complete"] is False
    assert update["checker_retry_counts"]["coverage"] == (
        checker.config.CHECKER_SEMANTIC_MAX_RETRY
    )
    assert update["coverage_checker_issues"][0]["code"] == "CHECKER_UNSTABLE"
    assert "product_iteration" not in update

    decision = gates.verify_product_coverage({
        **_state(),
        **update,
        "max_product_iterations": 2,
        "last_issue_signature": None,
        "no_progress_count": 0,
        "no_progress_limit": 2,
        "benefits_extracted": False,
    })
    assert decision["terminal_status"] == "NEEDS_HUMAN"
    assert decision["next_action"] == "stop"


def test_scorer_checks_null_name_and_coverage_issue_score_consistency():
    state = _state()
    state["normalized_items"][0]["qwen_matched_name"] = None
    state["coverage_checker_issues"] = [{
        "code": "WRONG_ROLE",
        "target_item_id": "prd_003",
        "affected_fields": ["role"],
    }]
    parsed = ProductScoreAudit.model_validate({
        "item_evaluations": [
            _evaluation("prd_002", matched_name=30),
            _evaluation("prd_003", role=15),
        ],
        "audit_complete": True,
    })
    errors = checker._score_audit_semantic_errors(parsed, state)
    assert any("matched_name评分不是0/30" in error for error in errors)
    assert any("声明role错误" in error for error in errors)


def test_scorer_semantic_retry_never_consumes_product_iteration(monkeypatch):
    state = _state()
    state["normalized_items"][0]["qwen_matched_name"] = None
    invalid = ProductScoreAudit.model_validate({
        "item_evaluations": [
            _evaluation("prd_002", matched_name=30),
            _evaluation("prd_003"),
        ],
        "audit_complete": True,
    })
    valid = ProductScoreAudit.model_validate({
        "item_evaluations": [
            _evaluation("prd_002", matched_name=0),
            _evaluation("prd_003"),
        ],
        "audit_complete": True,
    })
    responses = [invalid, valid]
    calls = 0

    async def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return responses.pop(0), {
            "prompt": 2, "completion": 1, "reasoning": 0
        }, ""

    monkeypatch.setattr(checker, "call_json_model", fake_call)
    monkeypatch.setattr(checker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(checker.score_products(state))

    assert calls == 2
    assert update["checker_retry_counts"]["score"] == 1
    assert update["scorer_complete"] is True
    assert "product_iteration" not in update


def test_cross_layer_schema_failure_retries_only_current_layer(monkeypatch):
    responses = [
        (None, "1 validation error for ProductScoreAudit extra_forbidden"),
        (
            ProductScoreAudit.model_validate({
                "item_evaluations": [
                    _evaluation("prd_002"), _evaluation("prd_003")
                ],
                "audit_complete": True,
            }),
            "",
        ),
    ]
    calls = 0

    async def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        parsed, error = responses.pop(0)
        return parsed, {"prompt": 1, "completion": 1, "reasoning": 0}, error

    monkeypatch.setattr(checker, "call_json_model", fake_call)
    monkeypatch.setattr(checker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(checker.score_products(_state()))

    assert calls == 2
    assert update["scorer_complete"] is True
    assert update["checker_retry_counts"]["score"] == 1
    assert "terminal_status" not in update
