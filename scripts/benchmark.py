#!/usr/bin/env python3
# 逐行注释版：下面每一行有效代码都配有中文说明，便于理解测试流程。
# 说明：模块或函数说明文字，概括这段代码的用途。
"""Unified LLM serving benchmark for OpenAI-compatible and Ollama endpoints."""

# 说明：启用新版类型注解行为，让类型标注在运行时更轻量。
from __future__ import annotations

# 说明：导入后续代码要用的标准库或第三方库。
import argparse
# 说明：导入后续代码要用的标准库或第三方库。
import asyncio
# 说明：导入后续代码要用的标准库或第三方库。
import csv
# 说明：导入后续代码要用的标准库或第三方库。
import json
# 说明：导入后续代码要用的标准库或第三方库。
import math
# 说明：导入后续代码要用的标准库或第三方库。
import statistics
# 说明：导入后续代码要用的标准库或第三方库。
import time
# 说明：从指定模块导入后续代码要用的对象。
from dataclasses import asdict, dataclass
# 说明：从指定模块导入后续代码要用的对象。
from datetime import datetime, timezone
# 说明：从指定模块导入后续代码要用的对象。
from pathlib import Path
# 说明：从指定模块导入后续代码要用的对象。
from typing import Any

# 说明：导入后续代码要用的标准库或第三方库。
import httpx
# 说明：导入后续代码要用的标准库或第三方库。
import yaml


# 说明：为下面的类或函数应用装饰器，改变或增强其行为。
@dataclass
# 说明：定义一个类，用来组织相关的数据和行为。
class RequestResult:
    # 说明：读取或传递当前框架、场景相关配置。
    framework: str
    # 说明：读取或传递当前框架、场景相关配置。
    scenario: str
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    concurrency: int
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    repetition: int
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    request_id: int
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    ok: bool
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    status_code: int | None
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    error: str | None
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    prompt_tokens: int
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    output_tokens: int
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    total_tokens: int
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    json_valid: bool | None
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    ttft_ms: float | None
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    latency_ms: float
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    tokens_per_second: float
    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
    started_at: str
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    batch_elapsed_s: float | None = None


# 说明：定义一个函数，把可复用逻辑封装起来。
def load_yaml(path: Path) -> dict[str, Any]:
    # 说明：进入上下文管理器，确保文件或资源使用后自动关闭。
    with path.open("r", encoding="utf-8") as f:
        # 说明：返回计算结果，交给调用方继续使用。
        return yaml.safe_load(f)


# 说明：定义一个函数，把可复用逻辑封装起来。
def percentile(values: list[float], p: float) -> float | None:
    # 说明：根据条件决定是否执行下面的代码块。
    if not values:
        # 说明：返回计算结果，交给调用方继续使用。
        return None
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    values = sorted(values)
    # 说明：根据条件决定是否执行下面的代码块。
    if len(values) == 1:
        # 说明：返回计算结果，交给调用方继续使用。
        return values[0]
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    rank = (len(values) - 1) * p
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    low = math.floor(rank)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    high = math.ceil(rank)
    # 说明：根据条件决定是否执行下面的代码块。
    if low == high:
        # 说明：返回计算结果，交给调用方继续使用。
        return values[low]
    # 说明：返回计算结果，交给调用方继续使用。
    return values[low] + (values[high] - values[low]) * (rank - low)


async def check_endpoint(framework_cfg: dict[str, Any], timeout_seconds: float = 10.0) -> tuple[bool, str]:
    base_url = framework_cfg["base_url"].rstrip("/")
    if framework_cfg["kind"] == "ollama":
        url = f"{base_url}/api/tags"
    else:
        url = f"{base_url}/models"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(url)
        if resp.status_code < 500:
            return True, f"{url} -> HTTP {resp.status_code}"
        return False, f"{url} -> HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{url} -> {exc}"


# 说明：定义一个类，用来组织相关的数据和行为。
class TokenCounter:
    # 说明：定义一个函数，把可复用逻辑封装起来。
    def __init__(self, model_id: str):
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        self.model_id = model_id
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        self.tokenizer = None
        # 说明：开始异常捕获块，把可能失败的操作包起来。
        try:
            # 说明：从指定模块导入后续代码要用的对象。
            from transformers import AutoTokenizer

            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            self.tokenizer = AutoTokenizer.from_pretrained(
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                model_id, trust_remote_code=True, use_fast=True
            # 说明：结束上一段多行结构。
            )
        # 说明：捕获异常，把失败请求记录为错误结果而不是让脚本崩溃。
        except Exception as exc:  # noqa: BLE001
            # 说明：把当前状态或结果打印到终端，方便观察测试进度。
            print(f"[warn] tokenizer load failed, using rough char estimate: {exc}")

    # 说明：定义一个函数，把可复用逻辑封装起来。
    def encode_len(self, text: str) -> int:
        # 说明：根据条件决定是否执行下面的代码块。
        if self.tokenizer is not None:
            # 说明：返回计算结果，交给调用方继续使用。
            return len(self.tokenizer.encode(text, add_special_tokens=False))
        # 说明：返回计算结果，交给调用方继续使用。
        return max(1, len(text) // 2)

    # 说明：定义一个函数，把可复用逻辑封装起来。
    def make_text(self, target_tokens: int, seed: str) -> str:
        # 说明：根据条件决定是否执行下面的代码块。
        if target_tokens <= 0:
            # 说明：返回计算结果，交给调用方继续使用。
            return seed
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        text = seed
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        filler = (
            # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
            "\n请基于企业级大模型推理服务的部署、监控、容量规划、故障处理、"
            # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
            "吞吐优化、首 token 延迟、KV Cache、批处理和结构化输出进行分析。"
        # 说明：结束上一段多行结构。
        )
        # 说明：开始循环，直到条件不再满足才停止。
        while self.encode_len(text) < target_tokens:
            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            text += filler
        # 说明：根据条件决定是否执行下面的代码块。
        if self.tokenizer is None:
            # 说明：返回计算结果，交给调用方继续使用。
            return text
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        token_ids = self.tokenizer.encode(text, add_special_tokens=False)[:target_tokens]
        # 说明：返回计算结果，交给调用方继续使用。
        return self.tokenizer.decode(token_ids)


# 说明：定义一个函数，把可复用逻辑封装起来。
def build_user_prompt(
    # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
    token_counter: TokenCounter,
    # 说明：提供多行参数、列表项或字典项的一部分。
    prompts_cfg: dict[str, Any],
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_cfg: dict[str, Any],
    # 说明：提供多行参数、列表项或字典项的一部分。
    request_id: int,
# 说明：结束函数签名或多行调用参数列表。
) -> str:
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    input_tokens = int(scenario_cfg["input_tokens"])
    # 说明：根据条件决定是否执行下面的代码块。
    if scenario_cfg.get("json_mode"):
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        seed = prompts_cfg["json_schema_prompt"]
        # 说明：返回计算结果，交给调用方继续使用。
        return token_counter.make_text(input_tokens, seed)

    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    questions = prompts_cfg["base_questions"]
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    question = questions[request_id % len(questions)]
    # 说明：根据条件决定是否执行下面的代码块。
    if scenario_cfg.get("shared_prefix_ratio"):
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        shared_ratio = float(scenario_cfg["shared_prefix_ratio"])
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        prefix_tokens = max(1, int(input_tokens * shared_ratio))
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        suffix_tokens = max(1, input_tokens - prefix_tokens)
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        shared = token_counter.make_text(
            # 说明：提供多行参数、列表项或字典项的一部分。
            prefix_tokens,
            # 说明：提供多行参数、列表项或字典项的一部分。
            "以下是企业知识库固定上下文，用于测试 prefix cache：",
        # 说明：结束上一段多行结构。
        )
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        suffix = token_counter.make_text(suffix_tokens, f"用户问题：{question}")
        # 说明：返回计算结果，交给调用方继续使用。
        return f"{shared}\n\n{suffix}"

    # 说明：返回计算结果，交给调用方继续使用。
    return token_counter.make_text(input_tokens, f"用户问题：{question}")


# 说明：定义一个函数，把可复用逻辑封装起来。
def extract_sse_content(line: str) -> str:
    # 说明：根据条件决定是否执行下面的代码块。
    if not line.startswith("data:"):
        # 说明：返回计算结果，交给调用方继续使用。
        return ""
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    data = line[5:].strip()
    # 说明：根据条件决定是否执行下面的代码块。
    if not data or data == "[DONE]":
        # 说明：返回计算结果，交给调用方继续使用。
        return ""
    # 说明：开始异常捕获块，把可能失败的操作包起来。
    try:
        # 说明：解析响应或中间数据，转换成 Python 对象。
        payload = json.loads(data)
    # 说明：捕获异常，把失败请求记录为错误结果而不是让脚本崩溃。
    except json.JSONDecodeError:
        # 说明：返回计算结果，交给调用方继续使用。
        return ""
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    choices = payload.get("choices") or []
    # 说明：根据条件决定是否执行下面的代码块。
    if not choices:
        # 说明：返回计算结果，交给调用方继续使用。
        return ""
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    delta = choices[0].get("delta") or {}
    # 说明：返回计算结果，交给调用方继续使用。
    return delta.get("content") or choices[0].get("text") or ""


# 说明：定义一个函数，把可复用逻辑封装起来。
def is_valid_json_output(text: str) -> bool:
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    text = text.strip()
    # 说明：根据条件决定是否执行下面的代码块。
    if text.startswith("```"):
        # 说明：返回计算结果，交给调用方继续使用。
        return False
    # 说明：开始异常捕获块，把可能失败的操作包起来。
    try:
        # 说明：处理 JSON 数据，用于请求、响应或结果落盘。
        json.loads(text)
    # 说明：捕获异常，把失败请求记录为错误结果而不是让脚本崩溃。
    except json.JSONDecodeError:
        # 说明：返回计算结果，交给调用方继续使用。
        return False
    # 说明：返回计算结果，交给调用方继续使用。
    return True


# 说明：定义一个异步函数，用于并发执行网络请求或主流程。
async def call_openai(
    # 说明：提供多行参数、列表项或字典项的一部分。
    client: httpx.AsyncClient,
    # 说明：读取或传递当前框架、场景相关配置。
    framework: str,
    # 说明：读取或传递当前框架、场景相关配置。
    framework_cfg: dict[str, Any],
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_name: str,
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_cfg: dict[str, Any],
    # 说明：提供多行参数、列表项或字典项的一部分。
    prompt: str,
    # 说明：提供多行参数、列表项或字典项的一部分。
    prompt_tokens: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    concurrency: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    repetition: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    request_id: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    timeout_seconds: float,
    # 说明：提供多行参数、列表项或字典项的一部分。
    stream: bool,
    # 说明：提供多行参数、列表项或字典项的一部分。
    temperature: float,
    # 说明：提供多行参数、列表项或字典项的一部分。
    top_p: float,
    # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
    token_counter: TokenCounter,
# 说明：结束函数签名或多行调用参数列表。
) -> RequestResult:
    # 说明：拼接目标接口地址，后续请求会发到这里。
    url = f"{framework_cfg['base_url'].rstrip('/')}/chat/completions"
    # 说明：构造 OpenAI Chat 格式的消息列表。
    messages = [
        # 说明：提供多行参数、列表项或字典项的一部分。
        {"role": "system", "content": "你是企业内部技术助手，用简洁、准确的中文回答问题。"},
        # 说明：提供多行参数、列表项或字典项的一部分。
        {"role": "user", "content": prompt},
    # 说明：结束上一段多行结构。
    ]
    # 说明：构造请求体，准备发给模型推理服务。
    body = {
        # 说明：提供多行参数、列表项或字典项的一部分。
        "model": framework_cfg["model"],
        # 说明：提供多行参数、列表项或字典项的一部分。
        "messages": messages,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "max_tokens": int(scenario_cfg["max_tokens"]),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "temperature": temperature,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "top_p": top_p,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "stream": stream,
    # 说明：结束上一段多行结构。
    }
    # 说明：根据条件决定是否执行下面的代码块。
    if scenario_cfg.get("json_mode") and scenario_cfg.get("use_response_format"):
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        body["response_format"] = {"type": "json_object"}

    # 说明：记录请求开始时间，用来计算延迟。
    started = time.perf_counter()
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    started_iso = datetime.now(timezone.utc).isoformat()
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    ttft_ms: float | None = None
    # 说明：处理模型输出文本，用于统计 token 数或校验结果。
    output_text_parts: list[str] = []
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    status_code: int | None = None

    # 说明：开始异常捕获块，把可能失败的操作包起来。
    try:
        # 说明：根据条件决定是否执行下面的代码块。
        if stream:
            # 说明：进入异步上下文，自动管理网络客户端或流式连接的生命周期。
            async with client.stream("POST", url, json=body, timeout=timeout_seconds) as resp:
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                status_code = resp.status_code
                # 说明：如果 HTTP 状态码表示失败，就抛出异常进入错误处理。
                resp.raise_for_status()
                # 说明：异步遍历流式响应中的每一行数据。
                async for line in resp.aiter_lines():
                    # 说明：给变量赋值或设置字段，供后续测试流程使用。
                    content = extract_sse_content(line)
                    # 说明：根据条件决定是否执行下面的代码块。
                    if content:
                        # 说明：根据条件决定是否执行下面的代码块。
                        if ttft_ms is None:
                            # 说明：初始化首 token 延迟字段，等待收到第一段输出后填充。
                            ttft_ms = (time.perf_counter() - started) * 1000
                        # 说明：处理模型输出文本，用于统计 token 数或校验结果。
                        output_text_parts.append(content)
        # 说明：当前面的条件都不满足时，执行这个兜底分支。
        else:
            # 说明：发送非流式 HTTP POST 请求，等待完整响应返回。
            resp = await client.post(url, json=body, timeout=timeout_seconds)
            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            status_code = resp.status_code
            # 说明：如果 HTTP 状态码表示失败，就抛出异常进入错误处理。
            resp.raise_for_status()
            # 说明：解析响应或中间数据，转换成 Python 对象。
            payload = resp.json()
            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            choices = payload.get("choices") or []
            # 说明：根据条件决定是否执行下面的代码块。
            if choices:
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                message = choices[0].get("message") or {}
                # 说明：处理模型输出文本，用于统计 token 数或校验结果。
                output_text_parts.append(message.get("content") or choices[0].get("text") or "")
    # 说明：捕获异常，把失败请求记录为错误结果而不是让脚本崩溃。
    except Exception as exc:  # noqa: BLE001
        # 说明：计算端到端耗时，单位是毫秒。
        latency_ms = (time.perf_counter() - started) * 1000
        # 说明：返回计算结果，交给调用方继续使用。
        return RequestResult(
            # 说明：读取或传递当前框架、场景相关配置。
            framework,
            # 说明：读取或传递当前框架、场景相关配置。
            scenario_name,
            # 说明：提供多行参数、列表项或字典项的一部分。
            concurrency,
            # 说明：提供多行参数、列表项或字典项的一部分。
            repetition,
            # 说明：提供多行参数、列表项或字典项的一部分。
            request_id,
            # 说明：提供多行参数、列表项或字典项的一部分。
            False,
            # 说明：提供多行参数、列表项或字典项的一部分。
            status_code,
            # 说明：提供多行参数、列表项或字典项的一部分。
            str(exc),
            # 说明：提供多行参数、列表项或字典项的一部分。
            prompt_tokens,
            # 说明：提供多行参数、列表项或字典项的一部分。
            0,
            # 说明：提供多行参数、列表项或字典项的一部分。
            prompt_tokens,
            # 说明：提供多行参数、列表项或字典项的一部分。
            False if scenario_cfg.get("json_mode") else None,
            # 说明：提供多行参数、列表项或字典项的一部分。
            ttft_ms,
            # 说明：提供多行参数、列表项或字典项的一部分。
            latency_ms,
            # 说明：提供多行参数、列表项或字典项的一部分。
            0.0,
            # 说明：提供多行参数、列表项或字典项的一部分。
            started_iso,
        # 说明：结束上一段多行结构。
        )

    # 说明：计算端到端耗时，单位是毫秒。
    latency_ms = (time.perf_counter() - started) * 1000
    # 说明：处理模型输出文本，用于统计 token 数或校验结果。
    output_text = "".join(output_text_parts)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    output_tokens = token_counter.encode_len(output_text)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    json_valid = is_valid_json_output(output_text) if scenario_cfg.get("json_mode") else None
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    tps = output_tokens / (latency_ms / 1000) if latency_ms > 0 else 0.0
    # 说明：返回计算结果，交给调用方继续使用。
    return RequestResult(
        # 说明：读取或传递当前框架、场景相关配置。
        framework,
        # 说明：读取或传递当前框架、场景相关配置。
        scenario_name,
        # 说明：提供多行参数、列表项或字典项的一部分。
        concurrency,
        # 说明：提供多行参数、列表项或字典项的一部分。
        repetition,
        # 说明：提供多行参数、列表项或字典项的一部分。
        request_id,
        # 说明：提供多行参数、列表项或字典项的一部分。
        True,
        # 说明：提供多行参数、列表项或字典项的一部分。
        status_code,
        # 说明：提供多行参数、列表项或字典项的一部分。
        None,
        # 说明：提供多行参数、列表项或字典项的一部分。
        prompt_tokens,
        # 说明：提供多行参数、列表项或字典项的一部分。
        output_tokens,
        # 说明：提供多行参数、列表项或字典项的一部分。
        prompt_tokens + output_tokens,
        # 说明：提供多行参数、列表项或字典项的一部分。
        json_valid,
        # 说明：提供多行参数、列表项或字典项的一部分。
        ttft_ms,
        # 说明：提供多行参数、列表项或字典项的一部分。
        latency_ms,
        # 说明：提供多行参数、列表项或字典项的一部分。
        tps,
        # 说明：提供多行参数、列表项或字典项的一部分。
        started_iso,
    # 说明：结束上一段多行结构。
    )


# 说明：定义一个异步函数，用于并发执行网络请求或主流程。
async def call_ollama(
    # 说明：提供多行参数、列表项或字典项的一部分。
    client: httpx.AsyncClient,
    # 说明：读取或传递当前框架、场景相关配置。
    framework: str,
    # 说明：读取或传递当前框架、场景相关配置。
    framework_cfg: dict[str, Any],
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_name: str,
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_cfg: dict[str, Any],
    # 说明：提供多行参数、列表项或字典项的一部分。
    prompt: str,
    # 说明：提供多行参数、列表项或字典项的一部分。
    prompt_tokens: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    concurrency: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    repetition: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    request_id: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    timeout_seconds: float,
    # 说明：提供多行参数、列表项或字典项的一部分。
    stream: bool,
    # 说明：提供多行参数、列表项或字典项的一部分。
    temperature: float,
    # 说明：提供多行参数、列表项或字典项的一部分。
    top_p: float,
    # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
    token_counter: TokenCounter,
# 说明：结束函数签名或多行调用参数列表。
) -> RequestResult:
    # 说明：拼接目标接口地址，后续请求会发到这里。
    url = f"{framework_cfg['base_url'].rstrip('/')}/api/generate"
    # 说明：构造请求体，准备发给模型推理服务。
    body = {
        # 说明：提供多行参数、列表项或字典项的一部分。
        "model": framework_cfg["model"],
        # 说明：提供多行参数、列表项或字典项的一部分。
        "prompt": prompt,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "stream": stream,
        # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
        "options": {
            # 说明：提供多行参数、列表项或字典项的一部分。
            "temperature": temperature,
            # 说明：提供多行参数、列表项或字典项的一部分。
            "top_p": top_p,
            # 说明：提供多行参数、列表项或字典项的一部分。
            "num_predict": int(scenario_cfg["max_tokens"]),
            # 说明：提供多行参数、列表项或字典项的一部分。
            "num_ctx": max(8192, int(scenario_cfg["input_tokens"]) + int(scenario_cfg["max_tokens"])),
        # 说明：结束上一段多行结构。
        },
    # 说明：结束上一段多行结构。
    }
    # 说明：记录请求开始时间，用来计算延迟。
    started = time.perf_counter()
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    started_iso = datetime.now(timezone.utc).isoformat()
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    ttft_ms: float | None = None
    # 说明：处理模型输出文本，用于统计 token 数或校验结果。
    output_text_parts: list[str] = []
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    status_code: int | None = None

    # 说明：开始异常捕获块，把可能失败的操作包起来。
    try:
        # 说明：根据条件决定是否执行下面的代码块。
        if stream:
            # 说明：进入异步上下文，自动管理网络客户端或流式连接的生命周期。
            async with client.stream("POST", url, json=body, timeout=timeout_seconds) as resp:
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                status_code = resp.status_code
                # 说明：如果 HTTP 状态码表示失败，就抛出异常进入错误处理。
                resp.raise_for_status()
                # 说明：异步遍历流式响应中的每一行数据。
                async for line in resp.aiter_lines():
                    # 说明：根据条件决定是否执行下面的代码块。
                    if not line:
                        # 说明：跳过当前循环的剩余部分，处理下一个元素。
                        continue
                    # 说明：解析响应或中间数据，转换成 Python 对象。
                    payload = json.loads(line)
                    # 说明：给变量赋值或设置字段，供后续测试流程使用。
                    content = payload.get("response") or ""
                    # 说明：根据条件决定是否执行下面的代码块。
                    if content:
                        # 说明：根据条件决定是否执行下面的代码块。
                        if ttft_ms is None:
                            # 说明：初始化首 token 延迟字段，等待收到第一段输出后填充。
                            ttft_ms = (time.perf_counter() - started) * 1000
                        # 说明：处理模型输出文本，用于统计 token 数或校验结果。
                        output_text_parts.append(content)
        # 说明：当前面的条件都不满足时，执行这个兜底分支。
        else:
            # 说明：发送非流式 HTTP POST 请求，等待完整响应返回。
            resp = await client.post(url, json=body, timeout=timeout_seconds)
            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            status_code = resp.status_code
            # 说明：如果 HTTP 状态码表示失败，就抛出异常进入错误处理。
            resp.raise_for_status()
            # 说明：解析响应或中间数据，转换成 Python 对象。
            payload = resp.json()
            # 说明：处理模型输出文本，用于统计 token 数或校验结果。
            output_text_parts.append(payload.get("response") or "")
    # 说明：捕获异常，把失败请求记录为错误结果而不是让脚本崩溃。
    except Exception as exc:  # noqa: BLE001
        # 说明：计算端到端耗时，单位是毫秒。
        latency_ms = (time.perf_counter() - started) * 1000
        # 说明：返回计算结果，交给调用方继续使用。
        return RequestResult(
            # 说明：读取或传递当前框架、场景相关配置。
            framework,
            # 说明：读取或传递当前框架、场景相关配置。
            scenario_name,
            # 说明：提供多行参数、列表项或字典项的一部分。
            concurrency,
            # 说明：提供多行参数、列表项或字典项的一部分。
            repetition,
            # 说明：提供多行参数、列表项或字典项的一部分。
            request_id,
            # 说明：提供多行参数、列表项或字典项的一部分。
            False,
            # 说明：提供多行参数、列表项或字典项的一部分。
            status_code,
            # 说明：提供多行参数、列表项或字典项的一部分。
            str(exc),
            # 说明：提供多行参数、列表项或字典项的一部分。
            prompt_tokens,
            # 说明：提供多行参数、列表项或字典项的一部分。
            0,
            # 说明：提供多行参数、列表项或字典项的一部分。
            prompt_tokens,
            # 说明：提供多行参数、列表项或字典项的一部分。
            False if scenario_cfg.get("json_mode") else None,
            # 说明：提供多行参数、列表项或字典项的一部分。
            ttft_ms,
            # 说明：提供多行参数、列表项或字典项的一部分。
            latency_ms,
            # 说明：提供多行参数、列表项或字典项的一部分。
            0.0,
            # 说明：提供多行参数、列表项或字典项的一部分。
            started_iso,
        # 说明：结束上一段多行结构。
        )

    # 说明：计算端到端耗时，单位是毫秒。
    latency_ms = (time.perf_counter() - started) * 1000
    # 说明：处理模型输出文本，用于统计 token 数或校验结果。
    output_text = "".join(output_text_parts)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    output_tokens = token_counter.encode_len(output_text)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    json_valid = is_valid_json_output(output_text) if scenario_cfg.get("json_mode") else None
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    tps = output_tokens / (latency_ms / 1000) if latency_ms > 0 else 0.0
    # 说明：返回计算结果，交给调用方继续使用。
    return RequestResult(
        # 说明：读取或传递当前框架、场景相关配置。
        framework,
        # 说明：读取或传递当前框架、场景相关配置。
        scenario_name,
        # 说明：提供多行参数、列表项或字典项的一部分。
        concurrency,
        # 说明：提供多行参数、列表项或字典项的一部分。
        repetition,
        # 说明：提供多行参数、列表项或字典项的一部分。
        request_id,
        # 说明：提供多行参数、列表项或字典项的一部分。
        True,
        # 说明：提供多行参数、列表项或字典项的一部分。
        status_code,
        # 说明：提供多行参数、列表项或字典项的一部分。
        None,
        # 说明：提供多行参数、列表项或字典项的一部分。
        prompt_tokens,
        # 说明：提供多行参数、列表项或字典项的一部分。
        output_tokens,
        # 说明：提供多行参数、列表项或字典项的一部分。
        prompt_tokens + output_tokens,
        # 说明：提供多行参数、列表项或字典项的一部分。
        json_valid,
        # 说明：提供多行参数、列表项或字典项的一部分。
        ttft_ms,
        # 说明：提供多行参数、列表项或字典项的一部分。
        latency_ms,
        # 说明：提供多行参数、列表项或字典项的一部分。
        tps,
        # 说明：提供多行参数、列表项或字典项的一部分。
        started_iso,
    # 说明：结束上一段多行结构。
    )


# 说明：定义一个异步函数，用于并发执行网络请求或主流程。
async def run_case(
    # 说明：读取或传递当前框架、场景相关配置。
    framework: str,
    # 说明：读取或传递当前框架、场景相关配置。
    framework_cfg: dict[str, Any],
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_name: str,
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_cfg: dict[str, Any],
    # 说明：提供多行参数、列表项或字典项的一部分。
    defaults: dict[str, Any],
    # 说明：提供多行参数、列表项或字典项的一部分。
    prompts_cfg: dict[str, Any],
    # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
    token_counter: TokenCounter,
    # 说明：提供多行参数、列表项或字典项的一部分。
    concurrency: int,
    # 说明：提供多行参数、列表项或字典项的一部分。
    repetition: int,
# 说明：结束函数签名或多行调用参数列表。
) -> list[RequestResult]:
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    timeout_seconds = float(defaults.get("timeout_seconds", 180))
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    stream = bool(defaults.get("stream", True))
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    temperature = float(scenario_cfg.get("temperature", defaults.get("temperature", 0.0)))
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    top_p = float(scenario_cfg.get("top_p", defaults.get("top_p", 1.0)))
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    num_requests = int(scenario_cfg["num_requests"])
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    semaphore = asyncio.Semaphore(concurrency)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    limits = httpx.Limits(max_connections=max(16, concurrency * 2), max_keepalive_connections=max(8, concurrency))

    # 说明：进入异步上下文，自动管理网络客户端或流式连接的生命周期。
    async with httpx.AsyncClient(limits=limits) as client:
        # 说明：定义一个异步函数，用于并发执行网络请求或主流程。
        async def one(request_id: int) -> RequestResult:
            # 说明：进入异步上下文，自动管理网络客户端或流式连接的生命周期。
            async with semaphore:
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                prompt = build_user_prompt(token_counter, prompts_cfg, scenario_cfg, request_id)
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                prompt_tokens = token_counter.encode_len(prompt)
                # 说明：根据条件决定是否执行下面的代码块。
                if framework_cfg["kind"] == "ollama":
                    # 说明：返回计算结果，交给调用方继续使用。
                    return await call_ollama(
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        client,
                        # 说明：读取或传递当前框架、场景相关配置。
                        framework,
                        # 说明：读取或传递当前框架、场景相关配置。
                        framework_cfg,
                        # 说明：读取或传递当前框架、场景相关配置。
                        scenario_name,
                        # 说明：读取或传递当前框架、场景相关配置。
                        scenario_cfg,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        prompt,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        prompt_tokens,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        concurrency,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        repetition,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        request_id,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        timeout_seconds,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        stream,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        temperature,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        top_p,
                        # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
                        token_counter,
                    # 说明：结束上一段多行结构。
                    )
                # 说明：返回计算结果，交给调用方继续使用。
                return await call_openai(
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    client,
                    # 说明：读取或传递当前框架、场景相关配置。
                    framework,
                    # 说明：读取或传递当前框架、场景相关配置。
                    framework_cfg,
                    # 说明：读取或传递当前框架、场景相关配置。
                    scenario_name,
                    # 说明：读取或传递当前框架、场景相关配置。
                    scenario_cfg,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    prompt,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    prompt_tokens,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    concurrency,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    repetition,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    request_id,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    timeout_seconds,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    stream,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    temperature,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    top_p,
                    # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
                    token_counter,
                # 说明：结束上一段多行结构。
                )

        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        batch_started = time.perf_counter()
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        results = await asyncio.gather(*(one(i) for i in range(num_requests)))
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        batch_elapsed_s = time.perf_counter() - batch_started
        # 说明：开始循环，逐个处理集合中的元素。
        for result in results:
            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            result.batch_elapsed_s = batch_elapsed_s
        # 说明：返回计算结果，交给调用方继续使用。
        return results


# 说明：定义一个函数，把可复用逻辑封装起来。
def summarize_case(results: list[RequestResult]) -> dict[str, Any]:
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    ok = [r for r in results if r.ok]
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    latencies = [r.latency_ms for r in ok]
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    ttfts = [r.ttft_ms for r in ok if r.ttft_ms is not None]
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    output_tokens = sum(r.output_tokens for r in ok)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    total_tokens = sum(r.total_tokens for r in ok)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    json_results = [r.json_valid for r in ok if r.json_valid is not None]
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    request_count = len(results)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    ok_count = len(ok)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    elapsed_s = results[0].batch_elapsed_s if results and results[0].batch_elapsed_s else 0.0
    # 说明：返回计算结果，交给调用方继续使用。
    return {
        # 说明：提供多行参数、列表项或字典项的一部分。
        "framework": results[0].framework if results else "",
        # 说明：提供多行参数、列表项或字典项的一部分。
        "scenario": results[0].scenario if results else "",
        # 说明：提供多行参数、列表项或字典项的一部分。
        "concurrency": results[0].concurrency if results else 0,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "repetition": results[0].repetition if results else 0,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "requests": request_count,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "success": ok_count,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "error_rate": 1 - (ok_count / request_count if request_count else 0),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "elapsed_s_est": elapsed_s,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "requests_per_s": ok_count / elapsed_s if elapsed_s > 0 else 0,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "output_tokens_per_s": output_tokens / elapsed_s if elapsed_s > 0 else 0,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "total_tokens_per_s": total_tokens / elapsed_s if elapsed_s > 0 else 0,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "latency_p50_ms": percentile(latencies, 0.50),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "latency_p95_ms": percentile(latencies, 0.95),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "latency_p99_ms": percentile(latencies, 0.99),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "ttft_p50_ms": percentile(ttfts, 0.50),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "ttft_p95_ms": percentile(ttfts, 0.95),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "ttft_p99_ms": percentile(ttfts, 0.99),
        # 说明：提供多行参数、列表项或字典项的一部分。
        "output_tokens_mean": statistics.mean([r.output_tokens for r in ok]) if ok else 0,
        # 说明：提供多行参数、列表项或字典项的一部分。
        "json_valid_rate": (sum(1 for value in json_results if value) / len(json_results)) if json_results else None,
    # 说明：结束上一段多行结构。
    }


# 说明：定义一个函数，把可复用逻辑封装起来。
def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    # 说明：进入上下文管理器，确保文件或资源使用后自动关闭。
    with path.open("a", encoding="utf-8") as f:
        # 说明：开始循环，逐个处理集合中的元素。
        for row in rows:
            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# 说明：定义一个函数，把可复用逻辑封装起来。
def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    # 说明：根据条件决定是否执行下面的代码块。
    if not rows:
        # 说明：直接返回，不携带额外结果。
        return
    # 说明：进入上下文管理器，确保文件或资源使用后自动关闭。
    with path.open("w", encoding="utf-8", newline="") as f:
        # 说明：给变量赋值或设置字段，供后续测试流程使用。
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
        writer.writeheader()
        # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
        writer.writerows(rows)


# 说明：定义一个异步函数，用于并发执行网络请求或主流程。
async def main() -> None:
    # 说明：创建命令行参数解析器，用来读取用户传入的参数。
    parser = argparse.ArgumentParser()
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--frameworks-config", type=Path, default=Path("configs/frameworks.yaml"))
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--scenarios-config", type=Path, default=Path("configs/scenarios.yaml"))
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--prompts-config", type=Path, default=Path("configs/prompts_zh.yaml"))
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--framework", action="append", help="Framework key to run. Repeatable.")
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--scenario", action="append", help="Scenario key to run. Repeatable.")
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--tokenizer-model", default=None)
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--skip-warmup", action="store_true")
    parser.add_argument("--require-health-check", action="store_true")
    parser.add_argument("--health-check-timeout", type=float, default=10.0)
    parser.add_argument("--abort-on-case-error-rate", type=float, default=None)
    # 说明：解析命令行参数，得到后续流程使用的配置值。
    args = parser.parse_args()

    # 说明：读取或传递当前框架、场景相关配置。
    frameworks_cfg = load_yaml(args.frameworks_config)
    # 说明：读取或传递当前框架、场景相关配置。
    scenarios_cfg = load_yaml(args.scenarios_config)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    prompts_cfg = load_yaml(args.prompts_config)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # 说明：读取或传递当前框架、场景相关配置。
    framework_keys = args.framework or list(frameworks_cfg["frameworks"].keys())
    # 说明：读取或传递当前框架、场景相关配置。
    scenario_keys = args.scenario or list(scenarios_cfg["scenarios"].keys())
    # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
    token_counter = TokenCounter(args.tokenizer_model or frameworks_cfg["model"]["hf_id"])

    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    details_path = args.output_dir / "request_details.jsonl"
    summary_jsonl_path = args.output_dir / "summary.jsonl"
    # 说明：保存所有测试批次的汇总结果。
    summaries: list[dict[str, Any]] = []

    # 说明：开始循环，逐个处理集合中的元素。
    for framework in framework_keys:
        # 说明：读取或传递当前框架、场景相关配置。
        framework_cfg = frameworks_cfg["frameworks"][framework]
        if args.require_health_check:
            ok, message = await check_endpoint(framework_cfg, args.health_check_timeout)
            print(f"[health] {framework}: {message}")
            if not ok:
                raise SystemExit(f"[abort] health check failed before benchmark: {message}")
        # 说明：开始循环，逐个处理集合中的元素。
        for scenario_name in scenario_keys:
            # 说明：读取或传递当前框架、场景相关配置。
            scenario_cfg = scenarios_cfg["scenarios"][scenario_name]
            # 说明：根据条件决定是否执行下面的代码块。
            if not args.skip_warmup:
                if args.require_health_check:
                    ok, message = await check_endpoint(framework_cfg, args.health_check_timeout)
                    print(f"[health] {framework} before warmup {scenario_name}: {message}")
                    if not ok:
                        write_csv(args.output_dir / "summary.csv", summaries)
                        raise SystemExit(f"[abort] health check failed before warmup: {message}")
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                warmup_cfg = dict(scenario_cfg)
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                warmup_cfg["num_requests"] = int(scenarios_cfg["defaults"].get("warmup_requests", 8))
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                warmup_cfg["max_tokens"] = min(int(warmup_cfg["max_tokens"]), 64)
                # 说明：把当前状态或结果打印到终端，方便观察测试进度。
                print(f"[warmup] {framework} {scenario_name}")
                # 说明：等待异步任务完成，并取得它的执行结果。
                await run_case(
                    # 说明：读取或传递当前框架、场景相关配置。
                    framework,
                    # 说明：读取或传递当前框架、场景相关配置。
                    framework_cfg,
                    # 说明：读取或传递当前框架、场景相关配置。
                    scenario_name,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    warmup_cfg,
                    # 说明：读取或传递当前框架、场景相关配置。
                    scenarios_cfg["defaults"],
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    prompts_cfg,
                    # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
                    token_counter,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    min(2, int(scenario_cfg["concurrency"][0])),
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    0,
                # 说明：结束上一段多行结构。
                )

            # 说明：给变量赋值或设置字段，供后续测试流程使用。
            stop_case = False
            # 说明：开始循环，逐个处理集合中的元素。
            for concurrency in scenario_cfg["concurrency"]:
                # 说明：根据条件决定是否执行下面的代码块。
                if stop_case:
                    # 说明：结束当前循环，停止继续遍历。
                    break
                # 说明：开始循环，逐个处理集合中的元素。
                for repetition in range(1, int(scenarios_cfg["defaults"].get("repetitions", 3)) + 1):
                    if args.require_health_check:
                        ok, message = await check_endpoint(framework_cfg, args.health_check_timeout)
                        print(f"[health] {framework} before {scenario_name} c={concurrency} rep={repetition}: {message}")
                        if not ok:
                            write_csv(args.output_dir / "summary.csv", summaries)
                            raise SystemExit(f"[abort] health check failed before run: {message}")
                    # 说明：把当前状态或结果打印到终端，方便观察测试进度。
                    print(f"[run] {framework} {scenario_name} c={concurrency} rep={repetition}")
                    # 说明：给变量赋值或设置字段，供后续测试流程使用。
                    results = await run_case(
                        # 说明：读取或传递当前框架、场景相关配置。
                        framework,
                        # 说明：读取或传递当前框架、场景相关配置。
                        framework_cfg,
                        # 说明：读取或传递当前框架、场景相关配置。
                        scenario_name,
                        # 说明：读取或传递当前框架、场景相关配置。
                        scenario_cfg,
                        # 说明：读取或传递当前框架、场景相关配置。
                        scenarios_cfg["defaults"],
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        prompts_cfg,
                        # 说明：创建或使用 token 计数器，让测试按 token 规模构造输入。
                        token_counter,
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        int(concurrency),
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        repetition,
                    # 说明：结束上一段多行结构。
                    )
                    # 说明：把测试结果写入文件，便于后续分析和画图。
                    append_jsonl(details_path, [asdict(r) for r in results])
                    # 说明：汇总当前测试批次的吞吐、延迟和错误率指标。
                    summary = summarize_case(results)
                    # 说明：保存所有测试批次的汇总结果。
                    summaries.append(summary)
                    write_csv(args.output_dir / "summary.csv", summaries)
                    append_jsonl(summary_jsonl_path, [summary])
                    # 说明：把当前状态或结果打印到终端，方便观察测试进度。
                    print(json.dumps(summary, ensure_ascii=False, indent=2))
                    if (
                        args.abort_on_case_error_rate is not None
                        and summary["error_rate"] >= args.abort_on_case_error_rate
                    ):
                        write_csv(args.output_dir / "summary.csv", summaries)
                        raise SystemExit(
                            f"[abort] {framework} {scenario_name} c={concurrency} rep={repetition} "
                            f"error_rate={summary['error_rate']:.4f} >= {args.abort_on_case_error_rate:.4f}"
                        )
                    # 说明：给变量赋值或设置字段，供后续测试流程使用。
                    error_limit = scenario_cfg.get("stop_on_error_rate_gt")
                    # 说明：给变量赋值或设置字段，供后续测试流程使用。
                    ttft_limit = scenario_cfg.get("stop_on_ttft_p95_ms_gt")
                    # 说明：根据条件决定是否执行下面的代码块。
                    if error_limit is not None and summary["error_rate"] > float(error_limit):
                        # 说明：给变量赋值或设置字段，供后续测试流程使用。
                        stop_case = True
                    # 说明：根据条件决定是否执行下面的代码块。
                    if (
                        # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
                        ttft_limit is not None
                        # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
                        and summary["ttft_p95_ms"] is not None
                        # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
                        and summary["ttft_p95_ms"] > float(ttft_limit)
                    # 说明：结束函数签名或多行调用参数列表。
                    ):
                        # 说明：给变量赋值或设置字段，供后续测试流程使用。
                        stop_case = True
                    # 说明：根据条件决定是否执行下面的代码块。
                    if stop_case:
                        # 说明：把当前状态或结果打印到终端，方便观察测试进度。
                        print(f"[stop] {framework} {scenario_name} reached stop condition")
                        # 说明：结束当前循环，停止继续遍历。
                        break

    # 说明：把测试结果写入文件，便于后续分析和画图。
    write_csv(args.output_dir / "summary.csv", summaries)
    # 说明：把当前状态或结果打印到终端，方便观察测试进度。
    print(f"[done] wrote {args.output_dir / 'summary.csv'}")


# 说明：判断脚本是否被直接运行，是的话执行主入口。
if __name__ == "__main__":
    # 说明：启动异步事件循环，运行主流程。
    asyncio.run(main())
