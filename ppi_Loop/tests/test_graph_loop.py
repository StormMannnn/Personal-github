import asyncio

from ppi_loop import graph as graph_module


COMBOS = [{
    "combination_id": "S1", "purchase_condition_text": "50ml",
    "section_hint": "左侧", "evidence_text": "50ml礼赠",
}, {
    "combination_id": "S2", "purchase_condition_text": "100ml",
    "section_hint": "右侧", "evidence_text": "100ml礼赠",
}]


def product(item_id, combo, spec):
    return {
        "item_id": item_id, "raw_name": "玉龙茶香香水",
        "qwen_matched_name": "玉龙茶香香水", "combination_ids": [combo],
        "spec_text": spec, "qty_text": "1", "is_cosmetic": True,
        "is_shopping_credit": False, "role": "主品", "gift_label": None,
        "is_gift_box": False, "box_contents": None, "is_sample": False,
        "section_hint": combo,
    }


def evaluation(item_id):
    return {
        "target_item_id": item_id,
        "score_breakdown": {"raw_name": 20, "matched_name": 30, "role": 15,
                            "specification": 20, "quantity": 10,
                            "existence_and_grouping": 5},
        "vlm_evaluation_score": 100, "reason": "全部正确",
    }


def test_full_loop_requires_qwen_repair_then_second_kimi_audit(monkeypatch):
    calls = {
        "combination_audits": 0,
        "coverage_audits": 0,
        "score_calls": 0,
        "repairs": 0,
    }

    async def prepare(state):
        return {"local_image_path": "/tmp/fake.jpg", "ocr_merged_text": "100ml礼赠"}

    async def extract_products(state):
        return {"raw_items": [product("prd_001", "S1", "50ml")],
                "sales_combinations": COMBOS}

    async def audit_product_combinations(state):
        calls["combination_audits"] += 1
        if calls["combination_audits"] == 1:
            return {
                "combination_checker_complete": True,
                "combination_checker_issues": [{
                    "code": "MISSING_COMBINATION_MAIN", "scope": "combination",
                    "severity": "fatal", "combination_id": "S2", "target_item_id": None,
                    "evidence_text": "100ml礼赠", "evidence_region": "右侧",
                    "observed_fact": "S2缺主品", "repair_instruction": "Qwen补齐",
                    "confidence": 1.0, "repairable": True,
                }],
            }
        return {
            "combination_checker_complete": True,
            "combination_checker_issues": [],
        }

    async def audit_product_coverage(state):
        calls["coverage_audits"] += 1
        return {
            "coverage_checker_complete": True,
            "coverage_checker_issues": [],
            "coverage_checks": [],
        }

    async def score_products(state):
        calls["score_calls"] += 1
        scores = [evaluation("prd_001"), evaluation("prd_002")]
        return {
            "scorer_complete": True,
            "scorer_issues": [],
            "latest_item_evaluations": scores,
            "item_evaluation_history": scores,
        }

    async def repair_products(state):
        calls["repairs"] += 1
        return {
            "raw_items": state["raw_items"] + [product("prd_002", "S2", "100ml")],
            "product_iteration": state.get("product_iteration", 0) + 1,
            "repair_history": [{"scope": "product", "iteration": 1, "accepted": True,
                                "operations": [], "reason": "Qwen补齐S2"}],
        }

    async def extract_benefits(state):
        return {"raw_prices": [], "raw_coupons": [], "benefits_extracted": True}

    async def audit_benefits(state):
        return {"checker_complete": True, "checker_issues": []}

    monkeypatch.setattr(graph_module.n1_prepare, "prepare_node", prepare)
    monkeypatch.setattr(graph_module.maker, "extract_products", extract_products)
    monkeypatch.setattr(
        graph_module.checker,
        "audit_product_combinations",
        audit_product_combinations,
    )
    monkeypatch.setattr(
        graph_module.checker, "audit_product_coverage", audit_product_coverage
    )
    monkeypatch.setattr(graph_module.checker, "score_products", score_products)
    monkeypatch.setattr(graph_module.maker, "repair_products", repair_products)
    monkeypatch.setattr(graph_module.maker, "extract_benefits", extract_benefits)
    monkeypatch.setattr(graph_module.checker, "audit_benefits", audit_benefits)

    graph = graph_module.build_graph()
    result = asyncio.run(graph.ainvoke({
        "image_url": "image", "product_title": "", "word_package": ["玉龙茶香香水"],
        "label_mapping": {"玉龙茶香香水": "玉龙茶香香水"},
    }, config={"recursion_limit": 80}))
    assert result["terminal_status"] == "VERIFIED"
    assert calls == {
        "combination_audits": 2,
        "coverage_audits": 1,
        "score_calls": 1,
        "repairs": 1,
    }
    assert [(row["组合ID"], row["规格"], row["VLM测评分数"])
            for row in result["verified_result"]] == [("S1", "50", 100), ("S2", "100", 100)]
