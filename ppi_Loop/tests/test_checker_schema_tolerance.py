import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ppi_loop.diagnostics import diagnostic_record
from ppi_loop.nodes import checker, common
from ppi_loop.schemas import (
    CheckerAudit,
    CombinationAudit,
    ProductCoverageAudit,
    ProductExtraction,
    ProductScoreAudit,
)


def _evaluation(*, total_key: str | None = None, total: int = 100) -> dict:
    breakdown = {
        "raw_name": 20,
        "matched_name": 30,
        "role": 15,
        "specification": 20,
        "quantity": 10,
        "existence_and_grouping": 5,
    }
    if total_key:
        breakdown[total_key] = total
    return {
        "target_item_id": "prd_001",
        "score_breakdown": breakdown,
        "vlm_evaluation_score": 100,
        "reason": "分项合计",
    }


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            CombinationAudit,
            {"combination_checks": [], "issues": [], "audit_complete": True},
        ),
        (
            ProductCoverageAudit,
            {"coverage_checks": [], "field_issues": [], "audit_complete": True},
        ),
        (
            ProductScoreAudit,
            {"item_evaluations": [_evaluation()], "audit_complete": True},
        ),
        (
            CheckerAudit,
            {
                "issues": [], "coverage_checks": [], "item_evaluations": [],
                "audit_complete": True,
            },
        ),
    ],
)
def test_safe_top_level_metadata_is_removed_for_every_checker_schema(
    schema, payload
):
    original = {**payload, "notes": "仅为说明", "summary": {}}
    cleaned, paths = checker.sanitize_checker_payload(original, schema)

    assert schema.model_validate(cleaned)
    assert set(paths) == {"$.notes", "$.summary"}
    assert "notes" in original and "summary" in original


def test_nonempty_structured_metadata_is_not_silently_discarded():
    with pytest.raises(ValueError, match="CHECKER_UNSAFE_METADATA"):
        checker.sanitize_checker_payload(
            {
                "issues": [],
                "audit_complete": True,
                "notes": {"products": [{"raw_name": "越权对象"}]},
            },
            CombinationAudit,
        )


def test_verified_redundant_score_total_is_removed_but_mismatch_is_rejected():
    payload = {
        "item_evaluations": [_evaluation(total_key="total_score")],
        "audit_complete": True,
    }
    cleaned, paths = checker.sanitize_checker_payload(payload, ProductScoreAudit)
    assert ProductScoreAudit.model_validate(cleaned)
    assert paths == [
        "$.item_evaluations[0].score_breakdown.total_score"
    ]

    payload["item_evaluations"][0]["score_breakdown"]["total_score"] = 99
    with pytest.raises(ValueError, match="CHECKER_TOTAL_MISMATCH"):
        checker.sanitize_checker_payload(payload, ProductScoreAudit)


@pytest.mark.parametrize(
    "field",
    [
        "coverage_checks", "item_evaluations", "supplemental_products",
        "products", "operations", "matched_name",
    ],
)
def test_protected_cross_layer_and_business_fields_remain_forbidden(field):
    payload = {"issues": [], "audit_complete": True, field: []}
    with pytest.raises(ValueError, match="CHECKER_FORBIDDEN_EXTRA"):
        checker.sanitize_checker_payload(payload, CombinationAudit)


def test_unknown_extra_is_still_rejected_by_strict_schema():
    cleaned, paths = checker.sanitize_checker_payload(
        {"issues": [], "audit_complete": True, "arbitrary": "value"},
        CombinationAudit,
    )
    assert paths == []
    with pytest.raises(ValidationError):
        CombinationAudit.model_validate(cleaned)


def test_qwen_maker_schema_does_not_use_checker_tolerance():
    with pytest.raises(ValidationError):
        ProductExtraction.model_validate({
            "sales_combinations": [],
            "products": [],
            "notes": "Maker不得多返回字段",
        })


def test_call_json_model_accepts_safe_metadata_without_api_retry(monkeypatch):
    calls = 0
    payload = {
        "combination_checks": [], "issues": [],
        "audit_complete": True, "notes": "说明",
    }

    class Completions:
        async def create(self, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload))
                )],
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    monkeypatch.setattr(common, "get_async_client", lambda model_name: client)
    monkeypatch.setattr(common, "get_extra_body", lambda *args, **kwargs: {})
    dropped: list[str] = []

    parsed, _, error = asyncio.run(common.call_json_model(
        "checker-model",
        [],
        CombinationAudit,
        "[test]",
        response_adapter=lambda value: checker.sanitize_checker_payload(
            value, CombinationAudit
        ),
        sanitized_fields=dropped,
    ))

    assert parsed is not None and error == ""
    assert calls == 1
    assert dropped == ["$.notes"]


def test_product_checker_reports_sanitized_paths_without_retry(monkeypatch):
    calls = 0

    async def fake_call(*args, response_adapter=None, sanitized_fields=None, **kwargs):
        nonlocal calls
        calls += 1
        payload = {
            "combination_checks": [], "issues": [],
            "audit_complete": True, "summary": "完成",
        }
        cleaned, paths = response_adapter(payload)
        sanitized_fields.extend(paths)
        return CombinationAudit.model_validate(cleaned), {
            "prompt": 1, "completion": 1, "reasoning": 0,
        }, ""

    monkeypatch.setattr(checker, "call_json_model", fake_call)
    monkeypatch.setattr(checker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(checker.audit_product_combinations({
        "sales_combinations": [], "normalized_items": [],
    }))

    assert calls == 1
    assert update["checker_retry_counts"]["combination"] == 0
    assert update["checker_sanitized_fields_by_layer"]["combination"] == [
        "$.summary"
    ]


def test_benefit_contract_violation_retries_then_routes_to_human(monkeypatch):
    calls = 0
    invalid = CheckerAudit.model_validate({
        "issues": [],
        "coverage_checks": [{
            "evidence_id": "ev_001",
            "evidence_text": "商品",
            "evidence_region": "中央",
            "object_description": "越权商品检查",
            "status": "covered",
            "matched_item_ids": ["prd_001"],
            "confidence": 1.0,
        }],
        "item_evaluations": [],
        "audit_complete": True,
    })

    async def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return invalid, {"prompt": 1, "completion": 1, "reasoning": 0}, ""

    monkeypatch.setattr(checker, "call_json_model", fake_call)
    monkeypatch.setattr(checker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(checker.audit_benefits({}))

    assert calls == checker.config.CHECKER_SEMANTIC_MAX_RETRY + 1
    assert update["checker_issues"][0]["code"] == "CHECKER_UNSTABLE"
    assert update["checker_issues"][0]["repairable"] is False
    assert update["checker_complete"] is False
    assert update.get("terminal_status") != "INFRA_ERROR"


def test_diagnostics_include_sanitized_checker_paths():
    record = diagnostic_record({
        "checker_sanitized_fields_by_layer": {
            "score": ["$.item_evaluations[0].score_breakdown.total"]
        },
        "checker_rejected_audits_by_layer": {
            "benefit": [{
                "attempt": 1,
                "payload": {"issues": [{"code": "MISSING_COUPON"}]},
                "semantic_errors": ["汇总不是优惠券"],
            }]
        },
    }, "thread-1")
    assert record["checker_sanitized_fields_by_layer"]["score"] == [
        "$.item_evaluations[0].score_breakdown.total"
    ]
    rejected = record["checker_rejected_audits_by_layer"]["benefit"]
    assert rejected[0]["attempt"] == 1
    assert rejected[0]["payload"]["issues"][0]["code"] == "MISSING_COUPON"
