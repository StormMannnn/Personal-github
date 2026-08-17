import asyncio
from types import SimpleNamespace

from ppi_loop import graph
from ppi_loop.diagnostics import diagnostic_record
from ppi_loop.nodes import common, maker
from ppi_loop.schemas import BenefitExtraction, MakerPatch, ProductExtraction


def _stats(prompt: int = 1, completion: int = 1) -> dict[str, int]:
    return {"prompt": prompt, "completion": completion, "reasoning": 0}


def _s1() -> list[dict]:
    return [{
        "combination_id": "S1",
        "purchase_condition_text": "单一购买方案",
        "section_hint": "全图",
        "evidence_text": "单一购买方案",
    }]


def test_raw_model_call_classifies_malformed_json_as_contract_error(monkeypatch):
    calls = 0

    class Completions:
        async def create(self, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"products": }')
                )],
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    monkeypatch.setattr(common, "get_async_client", lambda model_name: client)
    monkeypatch.setattr(common, "get_extra_body", lambda *args, **kwargs: {})
    parsed, _, error = asyncio.run(common.call_json_model(
        "qwen", [], ProductExtraction, "[Maker·test]"
    ))

    assert parsed is None and calls == 1
    assert common.is_contract_error(error)
    assert "MODEL_CONTRACT_ERROR" in error


def test_common_contract_retry_recovers_and_sums_token_usage(monkeypatch):
    valid = ProductExtraction.model_validate({
        "sales_combinations": _s1(), "products": [],
    })
    responses = [
        (None, _stats(10, 3), "1 validation error: Field required"),
        (valid, _stats(12, 4), ""),
    ]
    calls: list[list[dict]] = []

    async def fake_call(model_name, messages, response_schema, tag, **kwargs):
        calls.append(messages)
        return responses.pop(0)

    monkeypatch.setattr(common, "call_json_model", fake_call)
    parsed, stats, errors, error = asyncio.run(
        common.call_json_model_with_contract_retry(
            "qwen", [{"role": "system", "content": "contract"}],
            ProductExtraction, "[Maker·test]",
        )
    )

    assert parsed == valid and error == ""
    assert len(calls) == 2
    assert calls[1][-1]["role"] == "user"
    assert "ProductExtraction" in calls[1][-1]["content"]
    assert stats == {"prompt": 22, "completion": 7, "reasoning": 0}
    assert len(errors) == 1


def test_common_contract_retry_stops_immediately_for_provider_error(monkeypatch):
    calls = 0

    async def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None, _stats(), "Error code: 402 - account balance is insufficient"

    monkeypatch.setattr(common, "call_json_model", fake_call)
    parsed, _, errors, error = asyncio.run(
        common.call_json_model_with_contract_retry(
            "qwen", [], ProductExtraction, "[Maker·test]"
        )
    )

    assert parsed is None and calls == 1
    assert errors == []
    assert "402" in error


def test_product_maker_contract_exhaustion_needs_human(monkeypatch):
    errors = ["first", "second", "third"]

    async def fake_structured_call(*args, **kwargs):
        return None, _stats(30, 9), errors, "final schema error"

    monkeypatch.setattr(
        maker, "call_json_model_with_contract_retry", fake_structured_call
    )
    monkeypatch.setattr(maker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(maker.extract_products({}))

    assert update["terminal_status"] == "NEEDS_HUMAN"
    assert update["next_action"] == "stop"
    assert update["model_contract_retry_counts"]["product_maker"] == 2
    assert update["model_contract_errors_by_stage"]["product_maker"] == errors
    assert graph.route_after_model(update, "process") == "stop"


def test_benefit_maker_provider_failure_remains_infra_error(monkeypatch):
    async def fake_structured_call(*args, **kwargs):
        return None, _stats(), [], "Error code: 401 - unauthorized"

    monkeypatch.setattr(
        maker, "call_json_model_with_contract_retry", fake_structured_call
    )
    monkeypatch.setattr(maker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(maker.extract_benefits({"sales_combinations": []}))

    assert update["terminal_status"] == "INFRA_ERROR"
    assert update["model_contract_retry_counts"]["benefit_maker"] == 0
    assert graph.route_after_model(update, "process") == "stop"


def test_benefit_maker_records_recovered_contract_retry(monkeypatch):
    parsed = BenefitExtraction.model_validate({"prices": [], "coupons": []})

    async def fake_structured_call(*args, **kwargs):
        return parsed, _stats(), ["first invalid output"], ""

    monkeypatch.setattr(
        maker, "call_json_model_with_contract_retry", fake_structured_call
    )
    monkeypatch.setattr(maker, "multimodal_messages", lambda *args, **kwargs: [])
    update = asyncio.run(maker.extract_benefits({"sales_combinations": []}))

    assert "terminal_status" not in update
    assert update["model_contract_retry_counts"]["benefit_maker"] == 1
    assert update["model_contract_errors_by_stage"]["benefit_maker"] == [
        "first invalid output"
    ]


def test_patch_contract_failure_does_not_consume_business_iteration(monkeypatch):
    async def fake_structured_call(*args, **kwargs):
        return None, _stats(), ["one", "two", "three"], "invalid patch"

    monkeypatch.setattr(
        maker, "call_json_model_with_contract_retry", fake_structured_call
    )
    monkeypatch.setattr(maker, "multimodal_messages", lambda *args, **kwargs: [])
    state = {"product_iteration": 1, "current_issues": []}
    update = asyncio.run(maker.repair_products(state))

    assert update["terminal_status"] == "NEEDS_HUMAN"
    assert "product_iteration" not in update
    assert state["product_iteration"] == 1
    assert update["model_contract_retry_counts"]["product_patch_maker"] == 2


def test_recovered_patch_increments_business_iteration_once(monkeypatch):
    parsed = MakerPatch.model_validate({"operations": []})

    async def fake_structured_call(*args, **kwargs):
        return parsed, _stats(), ["first invalid patch"], ""

    monkeypatch.setattr(
        maker, "call_json_model_with_contract_retry", fake_structured_call
    )
    monkeypatch.setattr(maker, "multimodal_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        maker, "apply_maker_patch",
        lambda state, patch, scope: ({}, True, "accepted"),
    )
    update = asyncio.run(maker.repair_benefits({
        "benefit_iteration": 3,
        "current_issues": [],
        "raw_items": [], "raw_prices": [], "raw_coupons": [],
        "sales_combinations": [], "repair_history": [],
    }))

    assert update["benefit_iteration"] == 4
    assert update["model_contract_retry_counts"]["benefit_patch_maker"] == 1
    assert len(update["repair_history"]) == 1


def test_contract_retry_diagnostics_are_exported():
    record = diagnostic_record({
        "model_contract_retry_counts": {"product_maker": 1},
        "model_contract_errors_by_stage": {
            "product_maker": ["ProductExtraction: missing products"]
        },
    }, "thread-1")
    assert record["model_contract_retry_counts"] == {"product_maker": 1}
    assert record["model_contract_errors_by_stage"]["product_maker"] == [
        "ProductExtraction: missing products"
    ]
