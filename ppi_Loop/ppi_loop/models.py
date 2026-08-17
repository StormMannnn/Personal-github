"""阿里云百炼 AsyncOpenAI 客户端工厂。

按模型名路由到对应 base_url / api_key / extra_body。
不使用 LangChain ChatOpenAI — VLM 调用走现有 openai 包的 AsyncOpenAI 手调。
"""

import httpx
from openai import AsyncOpenAI

from . import config

_clients: dict[str, AsyncOpenAI] = {}


def get_async_client(model_name: str) -> AsyncOpenAI:
    # 按模型名取端点配置,以 base_url 为 key 做单例缓存(同端点复用同一客户端);
    # 客户端关闭 SSL 校验并 trust_env=False,避免本机代理/证书影响调用。
    """按模型名获取对应端点的 AsyncOpenAI 客户端（单例缓存）。

    - 'kimi-k2.6' → 阿里云百炼 DashScope
    - 'qwen3.7-plus' → 阿里云百炼 DashScope
    """
    ep = config.endpoint_for(model_name)

    base_url = ep["base_url"]
    if base_url not in _clients:
        _clients[base_url] = AsyncOpenAI(
            api_key=ep["api_key"],
            base_url=base_url,
            http_client=httpx.AsyncClient(verify=False, trust_env=False),
        )
    return _clients[base_url]


def get_extra_body(model_name: str, *, enable_thinking: bool | None = None) -> dict:
    # 取端点默认 extra_body 并做防御性拷贝,避免污染配置;
    # 显式传入 enable_thinking 时覆盖之:Checker 关思考,Maker 保留默认。
    """获取模型专属的 extra_body 参数。

    Checker 使用 JSON 结构化输出时会显式关闭思考模式；Maker 仍沿用
    模型默认配置。
    """
    ep = config.endpoint_for(model_name)
    extra_body = dict(ep["extra_body"])  # 防御性拷贝
    if enable_thinking is not None:
        extra_body["enable_thinking"] = enable_thinking
    return extra_body
