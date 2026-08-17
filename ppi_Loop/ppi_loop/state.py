"""Checkpoint-safe state for one image loop."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_token_usage(left: dict, right: dict) -> dict:
    # LangGraph reducer:按模型名累加 token 用量(prompt/completion/reasoning 逐项相加)。
    # 用于 Annotated[dict, merge_token_usage] 字段,多次节点返回自动合并而不是互相覆盖。
    merged = {key: dict(value) for key, value in (left or {}).items()}
    for model, stats in (right or {}).items():
        current = merged.setdefault(model, {"prompt": 0, "completion": 0, "reasoning": 0})
        for key in ("prompt", "completion", "reasoning"):
            current[key] = current.get(key, 0) + int(stats.get(key, 0) or 0)
    return merged


def merge_mapping(left: dict, right: dict) -> dict:
    # LangGraph reducer:字典按 key 覆盖式合并,后写(right)优先。
    # 用于各类计数/错误分层字典字段,实现后写覆盖先写。
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class ImageState(TypedDict, total=False):
    # 共享状态契约:一张图跑完整条循环的所有字段都在这里,全部可选(total=False)。
    # 带 Annotated 的字段有特殊合并语义:operator.add 的列表追加合并(issue_history/
    # repair_history/candidate_history/errors 等),merge_mapping 的字典按 key 覆盖。
    image_url: str
    product_title: str
    word_package: list[str]
    label_mapping: dict[str, str]
    loop_version: str

    # n1 persists paths/text only. data_url is cleared before each checkpoint boundary.
    local_image_path: str
    data_url: str
    ocr_blocks: list[dict]
    ocr_markdown: str
    ocr_merged_text: str
    ocr_tile_results: list[dict]
    visual_regions: list[dict]
    vision_diagnostics: dict

    sales_combinations: list[dict]
    raw_items: list[dict]
    filtered_items: list[dict]
    normalized_items: list[dict]
    classified_items: list[dict]
    raw_prices: list[dict]
    raw_coupons: list[dict]
    price_objects: list[dict]
    coupon_objects: list[dict]
    product_reconciliation_decisions: list[dict]

    current_issues: list[dict]
    issue_history: Annotated[list[dict], operator.add]
    repair_history: Annotated[list[dict], operator.add]
    item_evaluation_history: Annotated[list[dict], operator.add]
    candidate_history: Annotated[list[dict], operator.add]
    latest_item_evaluations: list[dict]
    combination_checker_issues: list[dict]
    combination_checker_complete: bool
    combination_checks: list[dict]
    coverage_checks: list[dict]
    coverage_validation_details: list[dict]
    coverage_checker_issues: list[dict]
    coverage_checker_complete: bool
    scorer_issues: list[dict]
    scorer_complete: bool
    checker_retry_counts: Annotated[dict[str, int], merge_mapping]
    checker_semantic_errors_by_layer: Annotated[dict[str, list[str]], merge_mapping]
    checker_sanitized_fields_by_layer: Annotated[dict[str, list[str]], merge_mapping]
    checker_rejected_audits_by_layer: Annotated[
        dict[str, list[dict[str, Any]]], merge_mapping
    ]
    model_contract_retry_counts: Annotated[dict[str, int], merge_mapping]
    model_contract_errors_by_stage: Annotated[dict[str, list[str]], merge_mapping]

    # Benefit Checker keeps its existing independent state.
    checker_issues: list[dict]
    checker_complete: bool
    checker_retry_count: int
    checker_semantic_errors: list[str]
    benefits_extracted: bool
    product_iteration: int
    benefit_iteration: int
    max_product_iterations: int
    max_benefit_iterations: int
    last_issue_signature: str | None
    last_issue_meanings: list[dict[str, Any]]
    no_progress_count: int
    no_progress_comparison: dict[str, Any]
    no_progress_limit: int
    next_action: str
    terminal_status: str

    candidate_result: list[dict]
    verified_result: list[dict]
    result: list[dict]
    proof_bundle: dict[str, Any]
    errors: Annotated[list[str], operator.add]
    token_usage: Annotated[dict, merge_token_usage]
