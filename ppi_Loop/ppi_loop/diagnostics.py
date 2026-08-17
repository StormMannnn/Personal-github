"""JSONL audit artifacts for loop runs."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def diagnostic_record(state: dict, thread_id: str) -> dict:
    # 把 state 里的审计/重试/对账字段摘成一条扁平 JSONL 记录,缺字段时用空值兜底。
    return {
        "thread_id": thread_id,
        "image_url": state.get("image_url"),
        "terminal_status": state.get("terminal_status"),
        "product_iteration": state.get("product_iteration", 0),
        "benefit_iteration": state.get("benefit_iteration", 0),
        "last_issue_signature": state.get("last_issue_signature"),
        "last_issue_meanings": state.get("last_issue_meanings", []),
        "no_progress_count": state.get("no_progress_count", 0),
        "no_progress_comparison": state.get("no_progress_comparison", {}),
        "checker_retry_count": state.get("checker_retry_count", 0),
        "checker_semantic_errors": state.get("checker_semantic_errors", []),
        "checker_retry_counts": state.get("checker_retry_counts", {}),
        "checker_semantic_errors_by_layer": state.get(
            "checker_semantic_errors_by_layer", {}
        ),
        "checker_sanitized_fields_by_layer": state.get(
            "checker_sanitized_fields_by_layer", {}
        ),
        "checker_rejected_audits_by_layer": state.get(
            "checker_rejected_audits_by_layer", {}
        ),
        "model_contract_retry_counts": state.get(
            "model_contract_retry_counts", {}
        ),
        "model_contract_errors_by_stage": state.get(
            "model_contract_errors_by_stage", {}
        ),
        "combination_checker_issues": state.get(
            "combination_checker_issues", []
        ),
        "combination_checks": state.get("combination_checks", []),
        "product_reconciliation_decisions": state.get(
            "product_reconciliation_decisions", []
        ),
        "coverage_checks": state.get("coverage_checks", []),
        "coverage_validation_details": state.get(
            "coverage_validation_details", []
        ),
        "coverage_checker_issues": state.get("coverage_checker_issues", []),
        "scorer_issues": state.get("scorer_issues", []),
        "current_issues": state.get("current_issues", []),
        "issue_history": state.get("issue_history", []),
        "repair_history": state.get("repair_history", []),
        "item_evaluation_history": state.get("item_evaluation_history", []),
        "candidate_history": state.get("candidate_history", []),
        "candidate_result": state.get("candidate_result", []),
        "verified_result": state.get("verified_result", []),
        "proof_bundle": state.get("proof_bundle", {}),
        "errors": state.get("errors", []),
        "token_usage": state.get("token_usage", {}),
    }


def write_diagnostics(records: list[dict], output_dir: Path, run_id: str) -> tuple[Path, Path]:
    # 写 jsonl(每图一条)并统计各终态出现次数,再写一份 _summary.json 汇总。
    # 返回 (jsonl 路径, summary 路径)。
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{run_id}.jsonl"
    summary_path = output_dir / f"{run_id}_summary.json"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    statuses = Counter(record.get("terminal_status") or "UNKNOWN" for record in records)
    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_images": len(records),
        "terminal_status_counts": dict(statuses),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonl_path, summary_path
