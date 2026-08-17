"""Bounded dual-loop LangGraph with strict maker/checker separation."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .state import ImageState
from .nodes import checker, gates, maker, n1_prepare, n6_price, product_processing


def initialize_loop(state: dict) -> dict:
    # 初始化一整套循环控制字段:迭代计数、上限、检查器重试、终态与产物容器。
    # 返回的增量字典会被 LangGraph 合并进共享 state,是每个 run 的起点。
    from . import config

    return {
        "loop_version": config.LOOP_VERSION,
        "product_iteration": 0,
        "benefit_iteration": 0,
        "max_product_iterations": config.MAX_PRODUCT_ITERATIONS,
        "max_benefit_iterations": config.MAX_BENEFIT_ITERATIONS,
        "current_issues": [],
        "checker_issues": [],
        "checker_complete": False,
        "checker_retry_count": 0,
        "checker_semantic_errors": [],
        "combination_checker_issues": [],
        "combination_checker_complete": False,
        "combination_checks": [],
        "coverage_checks": [],
        "coverage_validation_details": [],
        "product_reconciliation_decisions": [],
        "coverage_checker_issues": [],
        "coverage_checker_complete": False,
        "scorer_issues": [],
        "scorer_complete": False,
        "checker_retry_counts": {},
        "checker_semantic_errors_by_layer": {},
        "checker_sanitized_fields_by_layer": {},
        "checker_rejected_audits_by_layer": {},
        "model_contract_retry_counts": {},
        "model_contract_errors_by_stage": {},
        "latest_item_evaluations": [],
        "last_issue_signature": None,
        "last_issue_meanings": [],
        "no_progress_count": 0,
        "no_progress_comparison": {},
        "no_progress_limit": config.NO_PROGRESS_LIMIT,
        "terminal_status": "",
        "next_action": "prepare",
        "result": [],
        "verified_result": [],
        "candidate_result": [],
        "proof_bundle": {},
        "candidate_history": [],
    }


def route_after_prepare(state: dict) -> str:
    # 准备阶段后的路由:本地图片路径缺失就收尾(stop),否则进入商品抽取(products)。
    return "stop" if not state.get("local_image_path") else "products"


def route_after_model(state: dict, success: str) -> str:
    # 各模型节点跑完后的公共出口:只要终态非空就立刻 stop,防止空结果继续流转。
    # 否则按调用方传入的 success 目标路由(如 process)。
    return "stop" if state.get("terminal_status") else success


def route_action(state: dict) -> str:
    # 通用路由:直接读 state 的 next_action 决定下一跳,没有值就默认 stop。
    # gate 校验失败时会改写 next_action(如 repair_product),由此把图导向修复或收尾。
    return str(state.get("next_action") or "stop")


def route_after_scores(state: dict) -> str:
    # 打分落库后的路由:权益还没抽取就去 benefits 循环,已抽取就直接 assemble 组装。
    return "assemble" if state.get("benefits_extracted") else "benefits"


def finalize_unverified(state: dict) -> dict:
    # 未通过验证时的统一收尾:把终态、未解决问题和候选快照打包进 proof_bundle 存证。
    # result/verified_result 置空,保证只有 VERIFIED 才产出业务行。
    status = state.get("terminal_status") or "NEEDS_HUMAN"
    return {
        "terminal_status": status,
        "result": [],
        "verified_result": [],
        "proof_bundle": {
            "schema_valid": False,
            "terminal_status": status,
            "unresolved_issues": state.get("current_issues", []),
            "candidate_snapshot": state.get("candidate_result", []),
            "raw_candidate_snapshot": {
                "products": state.get("raw_items", []),
                "prices": state.get("raw_prices", []),
                "coupons": state.get("raw_coupons", []),
                "sales_combinations": state.get("sales_combinations", []),
            },
            "product_iterations": state.get("product_iteration", 0),
            "benefit_iterations": state.get("benefit_iteration", 0),
        },
    }


def build_graph(checkpointer=None):
    # 组装约 20 节点的双循环图:商品循环(抽取→处理→组合审计→gate→覆盖审计→gate→
    # 评分→gate→打分落库,失败走 repair_product 回 process);权益循环(抽取→处理→审计→
    # gate,失败 repair_benefit);最后 assemble→verify_final→VERIFIED 或修复或转人工收尾。
    # checkpointer 传 SQLite 后支持断点续跑与结果复用。
    graph = StateGraph(ImageState)
    graph.add_node("initialize", initialize_loop)
    graph.add_node("prepare", n1_prepare.prepare_node)
    graph.add_node("extract_products", maker.extract_products)
    graph.add_node("process_products", product_processing.process_product_candidates)
    graph.add_node(
        "audit_product_combinations", checker.audit_product_combinations
    )
    graph.add_node(
        "verify_product_combinations", gates.verify_product_combinations
    )
    graph.add_node("audit_product_coverage", checker.audit_product_coverage)
    graph.add_node("verify_product_coverage", gates.verify_product_coverage)
    graph.add_node("score_products", checker.score_products)
    graph.add_node("verify_product_scores", gates.verify_product_scores)
    graph.add_node("apply_product_scores", product_processing.apply_product_scores)
    graph.add_node("repair_product", maker.repair_products)
    graph.add_node("extract_benefits", maker.extract_benefits)
    graph.add_node("process_benefits", n6_price.process_prices)
    graph.add_node("audit_benefits", checker.audit_benefits)
    graph.add_node("verify_benefits", gates.verify_benefits)
    graph.add_node("repair_benefit", maker.repair_benefits)
    graph.add_node("assemble", gates.assemble_candidate)
    graph.add_node("verify_final", gates.verify_final)
    graph.add_node("finalize_unverified", finalize_unverified)

    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "prepare")
    graph.add_conditional_edges(
        "prepare", route_after_prepare,
        {"products": "extract_products", "stop": "finalize_unverified"},
    )
    graph.add_conditional_edges(
        "extract_products", lambda state: route_after_model(state, "process"),
        {"process": "process_products", "stop": "finalize_unverified"},
    )
    graph.add_edge("process_products", "audit_product_combinations")
    graph.add_edge("audit_product_combinations", "verify_product_combinations")
    graph.add_conditional_edges(
        "verify_product_combinations", route_action,
        {
            "coverage": "audit_product_coverage",
            "repair_product": "repair_product",
            "stop": "finalize_unverified",
        },
    )
    graph.add_edge("audit_product_coverage", "verify_product_coverage")
    graph.add_conditional_edges(
        "verify_product_coverage", route_action,
        {
            "score": "score_products",
            "repair_product": "repair_product",
            "stop": "finalize_unverified",
        },
    )
    graph.add_edge("score_products", "verify_product_scores")
    graph.add_conditional_edges(
        "verify_product_scores", route_action,
        {
            "apply_scores": "apply_product_scores",
            "stop": "finalize_unverified",
        },
    )
    graph.add_conditional_edges(
        "apply_product_scores", route_after_scores,
        {"benefits": "extract_benefits", "assemble": "assemble"},
    )
    graph.add_conditional_edges(
        "repair_product", lambda state: route_after_model(state, "process"),
        {"process": "process_products", "stop": "finalize_unverified"},
    )
    graph.add_conditional_edges(
        "extract_benefits", lambda state: route_after_model(state, "process"),
        {"process": "process_benefits", "stop": "finalize_unverified"},
    )
    graph.add_edge("process_benefits", "audit_benefits")
    graph.add_edge("audit_benefits", "verify_benefits")
    graph.add_conditional_edges(
        "verify_benefits", route_action,
        {
            "repair_benefit": "repair_benefit",
            "assemble": "assemble",
            "stop": "finalize_unverified",
        },
    )
    graph.add_conditional_edges(
        "repair_benefit", lambda state: route_after_model(state, "process"),
        {"process": "process_benefits", "stop": "finalize_unverified"},
    )
    graph.add_edge("assemble", "verify_final")
    graph.add_conditional_edges(
        "verify_final", route_action,
        {
            "verified": END,
            "repair_product": "repair_product",
            "repair_benefit": "repair_benefit",
            "stop": "finalize_unverified",
        },
    )
    graph.add_edge("finalize_unverified", END)
    return graph.compile(checkpointer=checkpointer)
