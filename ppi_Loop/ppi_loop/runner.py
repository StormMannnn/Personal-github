"""Concurrent image runner with SQLite checkpoint recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from . import config
from .diagnostics import diagnostic_record, write_diagnostics
from .graph import build_graph
from .image_utils import cleanup_old_cache


def stable_thread_id(
    image_url: str,
    brand: str,
    product_title: str,
    word_package: list[str],
    label_mapping: dict[str, str] | None = None,
) -> str:
    # 用 循环版本+图片+品牌+词袋+标签映射 求 SHA-256,产出稳定 thread id:
    # 相同输入必然得到相同 id,从而复用 SQLite checkpoint,不重复烧 token。
    payload = {
        "loop_version": config.LOOP_VERSION,
        "image_url": image_url,
        "brand": brand,
        "product_title": product_title,
        "word_package": word_package,
        "label_mapping": label_mapping or {},
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"ppi:{digest}"


async def process_one_image(
    image_url: str,
    product_title: str,
    word_package: list[str],
    label_mapping: dict[str, str],
    brand: str,
    semaphore: asyncio.Semaphore,
    compiled_graph,
) -> tuple[list[dict], dict]:
    # 单图执行入口:先按 thread_id 查 checkpoint——终态在就直接复用结果,
    # 有中断点就从断点续跑,否则全新开跑;只有 VERIFIED 的 verified_result 才进业务输出。
    # 返回 (输出行列表, 该图一条诊断记录)。
    thread_id = stable_thread_id(
        image_url, brand, product_title, word_package, label_mapping
    )
    graph_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 80,
    }
    async with semaphore:
        started = time.time()
        snapshot = await compiled_graph.aget_state(graph_config)
        if snapshot.values and snapshot.values.get("terminal_status") in {
            "VERIFIED", "NEEDS_HUMAN", "BUDGET_EXHAUSTED", "INVALID_INPUT", "INFRA_ERROR",
        }:
            result = dict(snapshot.values)
            print(f"[{brand}] 复用checkpoint: {result.get('terminal_status')} {image_url}")
        elif snapshot.values and snapshot.next:
            result = await compiled_graph.ainvoke(None, config=graph_config)
            print(f"[{brand}] 从checkpoint继续: {image_url}")
        else:
            result = await compiled_graph.ainvoke(
                {
                    "image_url": image_url,
                    "product_title": product_title,
                    "word_package": word_package,
                    "label_mapping": label_mapping,
                },
                config=graph_config,
            )
        print(
            f"[{brand}] {result.get('terminal_status')} "
            f"耗时 {time.time() - started:.1f}s {image_url}"
        )

    rows = []
    if result.get("terminal_status") == "VERIFIED":
        for item in result.get("verified_result") or result.get("result") or []:
            row = {"主图URL": image_url}
            row.update(item)
            rows.append(row)
    return rows, diagnostic_record(result, thread_id)


def _brand_word_package(word_package: pd.DataFrame, brand: str) -> tuple[list[str], dict[str, str]]:
    # 按品牌筛词袋:同一 label 必须映射到唯一 label_output,有冲突直接抛错防错配。
    # 返回 (词袋标签列表, label→label_output 映射)。
    rows = word_package[word_package["brand_name_product"] == brand].copy()
    rows = rows[rows["label"].notna()].copy()
    rows["label"] = rows["label"].astype(str).str.strip()
    if "label_output" not in rows.columns:
        rows["label_output"] = rows["label"]
    else:
        rows["label_output"] = rows["label_output"].fillna("").astype(str).str.strip()
        rows.loc[rows["label_output"] == "", "label_output"] = rows["label"]
    conflicts = rows.groupby("label")["label_output"].nunique()
    if (conflicts > 1).any():
        raise ValueError(f"[{brand}] label_output映射冲突: {list(conflicts[conflicts > 1].index)}")
    rows = rows.drop_duplicates("label", keep="first")
    labels = list(rows["label"])
    return labels, dict(zip(rows["label"], rows["label_output"]))


async def run_all(raw_data: pd.DataFrame, word_package: pd.DataFrame) -> tuple[pd.DataFrame, dict, list[dict]]:
    # 整批入口:Semaphore 限流 + SQLite checkpoint 图,按品牌分组并发扇出每张图,
    # 汇总输出行与 token 用量,并把诊断记录写成 JSONL + 汇总 JSON。
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_cache(config.IMG_CACHE_DIR, config.CACHE_MAX_DAYS)
    rows: list[dict] = []
    diagnostics: list[dict] = []
    semaphore = asyncio.Semaphore(max(config.CONCURRENCY, 1))

    async with AsyncSqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as saver:
        compiled_graph = build_graph(checkpointer=saver)
        for brand_value in raw_data["brand_name_product"].drop_duplicates():
            if pd.isna(brand_value):
                continue
            brand = str(brand_value)
            target = raw_data[
                (raw_data["brand_name_product"] == brand_value) & raw_data["image_url"].notna()
            ]
            labels, mapping = _brand_word_package(word_package, brand)
            tasks = [
                process_one_image(
                    str(row.image_url), "" if pd.isna(row.product_title) else str(row.product_title), labels,
                    mapping, brand, semaphore, compiled_graph,
                )
                for row in target[["image_url", "product_title"]].itertuples(index=False)
            ]
            for future in asyncio.as_completed(tasks):
                image_rows, record = await future
                rows.extend(image_rows)
                diagnostics.append(record)

    output = pd.DataFrame(rows)
    columns = [
        "主图URL", "组合ID", "类型", "名称", "规格", "单位", "数量", "金额", "使用条件",
        "RapidFuzz匹配分数", "VLM测评分数",
    ]
    if not output.empty:
        for column in columns:
            if column not in output.columns:
                output[column] = np.nan
        output = output[columns]

    token_stats: dict[str, dict[str, int]] = {}
    for record in diagnostics:
        for model, stats in record.get("token_usage", {}).items():
            target = token_stats.setdefault(model, {"prompt": 0, "completion": 0, "reasoning": 0})
            for key in target:
                target[key] += int(stats.get(key, 0) or 0)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path, summary_path = write_diagnostics(diagnostics, config.DIAGNOSTICS_DIR, run_id)
    print(f"循环诊断: {jsonl_path}\n诊断汇总: {summary_path}")
    return output, token_stats, diagnostics


def print_token_summary(stats: dict) -> None:
    # 打印各模型 token 汇总(输入/输出/思考),方便一眼看清消耗。
    print("\n========== Token用量汇总 ==========")
    for model, values in stats.items():
        print(
            f"{model}: 输入={values.get('prompt', 0):,} "
            f"输出={values.get('completion', 0):,} 思考={values.get('reasoning', 0):,}"
        )
