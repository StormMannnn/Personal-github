"""Qwen-only business-object generation and repair nodes."""

from __future__ import annotations

from typing import Any

from .. import config
from ..prompts.contracts import BENEFIT_MAKER_PROMPT, PATCH_MAKER_PROMPT, PRODUCT_MAKER_PROMPT
from ..schemas import BenefitExtraction, MakerPatch, ProductExtraction
from .combination_utils import normalize_combination_ids, normalize_sales_combinations
from .common import (
    assign_ids,
    call_json_model_with_contract_retry,
    multimodal_messages,
)
from .patching import apply_maker_patch


def _contract_retry_count(errors: list[str]) -> int:
    # 结构契约重试次数 = 契约错误条数,封顶到配置的 MODEL_CONTRACT_MAX_RETRY。
    return min(len(errors), max(config.MODEL_CONTRACT_MAX_RETRY, 0))


def _contract_diagnostics(stage: str, errors: list[str]) -> dict:
    # 汇总某阶段的重试次数与契约错误明细,写入诊断字段供后续审计。
    return {
        "model_contract_retry_counts": {
            stage: _contract_retry_count(errors)
        },
        "model_contract_errors_by_stage": {stage: errors},
    }


def _model_failure_update(
    *,
    stage: str,
    label: str,
    stats: dict[str, int],
    contract_errors: list[str],
    error: str,
) -> dict:
    # 模型失败归类:有契约错误→NEEDS_HUMAN(结构连续不符),纯调用失败→INFRA_ERROR;
    # 返回停走 update,并附带 token 用量与契约诊断。
    contract_failed = bool(contract_errors)
    category = "连续输出不符合结构契约" if contract_failed else "调用失败"
    update = {
        "terminal_status": "NEEDS_HUMAN" if contract_failed else "INFRA_ERROR",
        "next_action": "stop",
        "errors": [f"{label}{category}: {error}"],
        "token_usage": {config.QWEN_MODEL: stats},
    }
    update.update(_contract_diagnostics(stage, contract_errors))
    return update


async def extract_products(state: dict) -> dict:
    # 商品 Maker(唯一):让 Qwen 按契约生成商品与销售组合对象,失败走失败归类;
    # 成功后归一化组合ID、给商品分配 prd 前缀ID,并重置组合/覆盖/评分三层 Checker 状态。
    messages = multimodal_messages(state, PRODUCT_MAKER_PROMPT)
    stage = "product_maker"
    parsed, stats, contract_errors, error = await call_json_model_with_contract_retry(
        config.QWEN_MODEL, messages, ProductExtraction, "[Maker·商品]"
    )
    if parsed is None:
        update = _model_failure_update(
            stage=stage, label="Qwen商品生成", stats=stats,
            contract_errors=contract_errors, error=error,
        )
        update["current_issues"] = []
        return update
    combinations = normalize_sales_combinations(
        [item.model_dump() for item in parsed.sales_combinations]
    )
    products = []
    for model in parsed.products:
        item = model.model_dump()
        item["combination_ids"] = normalize_combination_ids(
            item.get("combination_ids"), combinations
        )
        products.append(item)
    update = {
        "raw_items": assign_ids(products, "prd"),
        "sales_combinations": combinations,
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
        "latest_item_evaluations": [],
        "checker_retry_counts": {
            "combination": 0, "coverage": 0, "score": 0,
        },
        "checker_semantic_errors_by_layer": {
            "combination": [], "coverage": [], "score": [],
        },
        "checker_sanitized_fields_by_layer": {
            "combination": [], "coverage": [], "score": [],
        },
        "checker_rejected_audits_by_layer": {
            "combination": [], "coverage": [], "score": [],
        },
        "candidate_history": [{
            "stage": "product_initial", "iteration": 0,
            "products": assign_ids(products, "prd"),
            "sales_combinations": combinations,
        }],
        "token_usage": {config.QWEN_MODEL: stats},
    }
    update.update(_contract_diagnostics(stage, contract_errors))
    return update


async def extract_benefits(state: dict) -> dict:
    # 权益 Maker(唯一):让 Qwen 生成价格与优惠券对象(锁定当前销售组合),
    # 成功后分配 prc/cpn 前缀ID、记录收益已提取,并重置 benefit 层审计状态。
    messages = multimodal_messages(
        state,
        BENEFIT_MAKER_PROMPT,
        {"locked_sales_combinations": state.get("sales_combinations", [])},
    )
    stage = "benefit_maker"
    parsed, stats, contract_errors, error = await call_json_model_with_contract_retry(
        config.QWEN_MODEL, messages, BenefitExtraction, "[Maker·权益]"
    )
    if parsed is None:
        return _model_failure_update(
            stage=stage, label="Qwen权益生成", stats=stats,
            contract_errors=contract_errors, error=error,
        )
    combinations = normalize_sales_combinations(state.get("sales_combinations"))
    prices = []
    for model in parsed.prices:
        item = model.model_dump()
        item["combination_ids"] = normalize_combination_ids(
            item.get("combination_ids"), combinations
        )
        prices.append(item)
    coupons = []
    for model in parsed.coupons:
        item = model.model_dump()
        item["combination_ids"] = normalize_combination_ids(
            item.get("combination_ids"), combinations
        )
        coupons.append(item)
    identified_prices = assign_ids(prices, "prc")
    identified_coupons = assign_ids(coupons, "cpn")
    update = {
        "raw_prices": identified_prices,
        "raw_coupons": identified_coupons,
        "benefits_extracted": True,
        "checker_sanitized_fields_by_layer": {"benefit": []},
        "checker_rejected_audits_by_layer": {"benefit": []},
        "candidate_history": [{
            "stage": "benefit_initial", "iteration": 0,
            "prices": identified_prices, "coupons": identified_coupons,
        }],
        "token_usage": {config.QWEN_MODEL: stats},
    }
    update.update(_contract_diagnostics(stage, contract_errors))
    return update


async def _repair(state: dict, repair_scope: str) -> dict:
    # 统一修复节点:把 current_issues 与当前各集合交给 Qwen 生成 MakerPatch,
    # 由 apply_maker_patch 事务式应用,仅被接受才推进业务迭代(结构重试不消耗迭代),
    # 并重置对应 scope 的 Checker 状态以待重新审计。
    payload: dict[str, Any] = {
        "repair_scope": repair_scope,
        "issues": state.get("current_issues", []),
        "sales_combinations": state.get("sales_combinations", []),
        "current_products": state.get("raw_items", []),
        "current_prices": state.get("raw_prices", []),
        "current_coupons": state.get("raw_coupons", []),
        "repair_history": state.get("repair_history", []),
    }
    messages = multimodal_messages(state, PATCH_MAKER_PROMPT, payload)
    stage = f"{repair_scope}_patch_maker"
    parsed, stats, contract_errors, error = await call_json_model_with_contract_retry(
        config.QWEN_MODEL, messages, MakerPatch, f"[Maker·{repair_scope}Patch]"
    )
    iteration_key = "product_iteration" if repair_scope == "product" else "benefit_iteration"
    if parsed is None:
        return _model_failure_update(
            stage=stage, label=f"Qwen {repair_scope} Patch生成", stats=stats,
            contract_errors=contract_errors, error=error,
        )
    iteration = int(state.get(iteration_key, 0)) + 1
    update, accepted, reason = apply_maker_patch(state, parsed, repair_scope)
    update[iteration_key] = iteration
    update["repair_history"] = [{
        "scope": repair_scope,
        "iteration": iteration,
        "accepted": accepted,
        "operations": [operation.model_dump() for operation in parsed.operations],
        "reason": reason,
    }]
    if not accepted:
        update.setdefault("errors", []).append(f"Patch被拒绝: {reason}")
    update["candidate_history"] = [{
        "stage": f"{repair_scope}_repair",
        "iteration": iteration,
        "accepted": accepted,
        "products": update.get("raw_items", state.get("raw_items", [])),
        "prices": update.get("raw_prices", state.get("raw_prices", [])),
        "coupons": update.get("raw_coupons", state.get("raw_coupons", [])),
        "sales_combinations": update.get(
            "sales_combinations", state.get("sales_combinations", [])
        ),
    }]
    update["token_usage"] = {config.QWEN_MODEL: stats}
    update.update(_contract_diagnostics(stage, contract_errors))
    if repair_scope == "product":
        update.update({
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
            "latest_item_evaluations": [],
            "checker_retry_counts": {
                "combination": 0, "coverage": 0, "score": 0,
            },
            "checker_semantic_errors_by_layer": {
                "combination": [], "coverage": [], "score": [],
            },
            "checker_sanitized_fields_by_layer": {
                "combination": [], "coverage": [], "score": [],
            },
            "checker_rejected_audits_by_layer": {
                "combination": [], "coverage": [], "score": [],
            },
        })
    else:
        update["checker_sanitized_fields_by_layer"] = {"benefit": []}
        update["checker_rejected_audits_by_layer"] = {"benefit": []}
    return update


async def repair_products(state: dict) -> dict:
    # graph 注册的商品修复节点:代理到 _repair(state, "product")。
    return await _repair(state, "product")


async def repair_benefits(state: dict) -> dict:
    # graph 注册的权益修复节点:代理到 _repair(state, "benefit")。
    return await _repair(state, "benefit")
