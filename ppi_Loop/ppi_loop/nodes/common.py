"""Shared model-call and multimodal-message utilities."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .. import config
from ..image_utils import local_to_data_url
from ..models import get_async_client, get_extra_body


class ModelContractError(ValueError):
    """The provider responded, but the body violated the requested contract."""


def is_contract_error(error: str) -> bool:
    # 用 marker 表判断错误是否属于"本地契约失败"(JSON 解析/Pydantic 校验/空正文等),
    # 区别于传输或供应商错误(429/503/超时等);只有契约失败才值得带反馈重试。
    """Separate local JSON/schema failures from transport and provider errors."""
    lowered = (error or "").lower()
    markers = (
        "model_contract_error",
        "validation error",
        "errors.pydantic.dev",
        "extra_forbidden",
        "field required",
        "input should be",
        "expecting value",
        "expecting property name",
        "extra data",
        "unterminated string",
        "invalid \\escape",
        "模型返回空正文",
        "checker_forbidden_extra",
        "checker_unsafe_metadata",
        "checker_invalid_total",
        "checker_total_mismatch",
    )
    return any(marker in lowered for marker in markers)


def _merge_stats(total: dict[str, int], current: dict[str, int]) -> None:
    # 把单次调用的 token 统计(prompt/completion/reasoning)累加进总表,
    # 供多次重试时汇总整体消耗。
    for key in ("prompt", "completion", "reasoning"):
        total[key] = total.get(key, 0) + int(current.get(key, 0) or 0)


def _compact_contract_error(error: str, response_schema: type) -> str:
    # 压缩错误文本:去多余空白、超 2000 字符截断,并冠以 Schema 类名,
    # 方便把具体结构错误塞进重试提示消息。
    compact = " ".join((error or "").split())
    if len(compact) > 2000:
        compact = compact[:2000] + "..."
    return f"{response_schema.__name__}: {compact}"


def _contract_retry_messages(
    base_messages: list[dict], response_schema: type, error: str
) -> list[dict]:
    # 在原始消息后追加一条 user 反馈,把具体结构错误告诉模型,
    # 要求它只输出完整合法 JSON,不夹带解释或 Schema 外字段。
    return [
        *base_messages,
        {
            "role": "user",
            "content": (
                f"上一份输出未通过{response_schema.__name__}结构校验。"
                "请重新阅读原始任务和输出契约，只输出完整、合法的JSON，"
                "不得输出Markdown、解释或Schema外字段。\n"
                f"结构错误：{_compact_contract_error(error, response_schema)}"
            ),
        },
    ]


def reasoning_tokens(usage: Any) -> int:
    # 从 usage 提取思考 token 数:兼容 completion_tokens_details.reasoning_tokens
    # 与 usage.reasoning_tokens 两种字段位置,拿不到就返回 0。
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        value = getattr(details, "reasoning_tokens", None)
        if value is None and isinstance(details, dict):
            value = details.get("reasoning_tokens")
        if value:
            return int(value)
    return int(getattr(usage, "reasoning_tokens", 0) or 0)


async def call_json_model(
    model_name: str,
    messages: list[dict],
    response_schema: type,
    tag: str,
    *,
    enable_thinking: bool | None = None,
    response_adapter: Callable[[Any], tuple[Any, list[str]]] | None = None,
    sanitized_fields: list[str] | None = None,
) -> tuple[Any | None, dict[str, int], str]:
    # 单次模型调用:强制 json_object 输出 → 解析 JSON → adapter 清洗 → Pydantic 校验;
    # 传输/供应商错误按退避重试,契约错误(JSON/校验失败)立即返回不再重试。
    stats = {"prompt": 0, "completion": 0, "reasoning": 0}
    for attempt in range(1, max(config.MAX_RETRY, 1) + 1):
        try:
            response = await get_async_client(model_name).chat.completions.create(
                model=model_name,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=config.TEMPERATURE,
                top_p=config.TOP_P,
                max_tokens=config.MAX_TOKENS,
                extra_body=get_extra_body(
                    model_name, enable_thinking=enable_thinking
                ),
            )
            usage = getattr(response, "usage", None)
            if usage:
                stats = {
                    "prompt": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion": int(getattr(usage, "completion_tokens", 0) or 0),
                    "reasoning": reasoning_tokens(usage),
                }
            content = response.choices[0].message.content or ""
            if not content.strip():
                raise ModelContractError("MODEL_CONTRACT_ERROR: 模型返回空正文")
            try:
                payload = json.loads(content)
                if response_adapter is not None:
                    payload, dropped_paths = response_adapter(payload)
                    if sanitized_fields is not None:
                        sanitized_fields.extend(dropped_paths)
                parsed = response_schema.model_validate(payload)
            except ModelContractError:
                raise
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                raise ModelContractError(
                    f"MODEL_CONTRACT_ERROR: {exc}"
                ) from exc
            print(
                f"{tag} token: 输入={stats['prompt']} 输出={stats['completion']} "
                f"思考={stats['reasoning']}"
            )
            return parsed, stats, ""
        except Exception as exc:
            message = str(exc)
            retryable = any(hint in message.lower() for hint in config.RETRYABLE_HINTS)
            if attempt >= config.MAX_RETRY or not retryable:
                return None, stats, message
            delay = config.BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 1)
            print(f"{tag} 第{attempt}次调用失败，{delay:.1f}s后重试: {message}")
            await asyncio.sleep(delay)
    return None, stats, "多次重试失败"


async def call_json_model_with_contract_retry(
    model_name: str,
    messages: list[dict],
    response_schema: type,
    tag: str,
    *,
    enable_thinking: bool | None = None,
) -> tuple[Any | None, dict[str, int], list[str], str]:
    # Maker 专用包装:契约错误时最多再额外重试 MODEL_CONTRACT_MAX_RETRY 次,
    # 每次把具体结构错误回喂给模型,且不消耗图的迭代次数。
    """Retry local JSON/schema failures without consuming a graph iteration."""
    base_messages = messages
    current_messages = base_messages
    total_stats = {"prompt": 0, "completion": 0, "reasoning": 0}
    contract_errors: list[str] = []
    attempts = max(config.MODEL_CONTRACT_MAX_RETRY, 0) + 1
    final_error = ""
    for attempt in range(1, attempts + 1):
        parsed, stats, final_error = await call_json_model(
            model_name,
            current_messages,
            response_schema,
            tag,
            enable_thinking=enable_thinking,
        )
        _merge_stats(total_stats, stats)
        if parsed is not None:
            return parsed, total_stats, contract_errors, ""
        if not is_contract_error(final_error):
            return None, total_stats, contract_errors, final_error
        compact = _compact_contract_error(final_error, response_schema)
        contract_errors.append(f"第{attempt}次: {compact}")
        if attempt < attempts:
            print(
                f"{tag} 第{attempt}次结构校验失败，仅重试当前Maker阶段: "
                f"{compact}"
            )
            current_messages = _contract_retry_messages(
                base_messages, response_schema, final_error
            )
    return None, total_stats, contract_errors, final_error


def multimodal_messages(
    state: dict,
    system_prompt: str,
    payload: dict | None = None,
) -> list[dict]:
    # 用 state 拼多模态消息:每次调用现转 base64(data_url 不入 checkpoint),
    # context 带原图URL/标题/词袋/OCR文本,可选 payload 追加业务字段。
    local_path = state.get("local_image_path")
    if not local_path:
        raise ValueError("缺少本地原图路径")
    data_url = local_to_data_url(str(local_path))
    context = {
        "image_url": state.get("image_url", ""),
        "product_title": state.get("product_title", ""),
        "word_package": state.get("word_package", []),
        "ocr_text": state.get("ocr_merged_text", ""),
    }
    if payload:
        context.update(payload)
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": json.dumps(context, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


def assign_ids(items: list[dict], prefix: str, existing: set[str] | None = None) -> list[dict]:
    # 稳定分配 item_id:已有且不重复的 ID 原样保留,空位按 prefix_001 顺序补号,
    # 补号时跳过已用编号,保证同一张图多次运行时结果一致。
    reserved = set(existing or ())
    seen: set[str] = set()
    next_number = 1
    result = []
    for source in items:
        item = dict(source)
        current = str(item.get("item_id") or "")
        if current and current not in seen:
            seen.add(current)
        else:
            while f"{prefix}_{next_number:03d}" in reserved | seen:
                next_number += 1
            current = f"{prefix}_{next_number:03d}"
            next_number += 1
            seen.add(current)
        item["item_id"] = current
        result.append(item)
    return result
