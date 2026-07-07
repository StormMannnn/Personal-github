#!/usr/bin/env python3
# 逐行注释版：下面每一行有效代码都配有中文说明，便于理解测试流程。
# 说明：模块或函数说明文字，概括这段代码的用途。
"""Run 10 short Chinese smoke-test prompts against a configured endpoint."""

# 说明：启用新版类型注解行为，让类型标注在运行时更轻量。
from __future__ import annotations

# 说明：导入后续代码要用的标准库或第三方库。
import argparse
# 说明：导入后续代码要用的标准库或第三方库。
import asyncio
# 说明：导入后续代码要用的标准库或第三方库。
import json
# 说明：从指定模块导入后续代码要用的对象。
from pathlib import Path

# 说明：导入后续代码要用的标准库或第三方库。
import httpx
# 说明：导入后续代码要用的标准库或第三方库。
import yaml


# 说明：定义一个函数，把可复用逻辑封装起来。
def load_yaml(path: Path) -> dict:
    # 说明：进入上下文管理器，确保文件或资源使用后自动关闭。
    with path.open("r", encoding="utf-8") as f:
        # 说明：返回计算结果，交给调用方继续使用。
        return yaml.safe_load(f)


# 说明：定义一个异步函数，用于并发执行网络请求或主流程。
async def main() -> None:
    # 说明：创建命令行参数解析器，用来读取用户传入的参数。
    parser = argparse.ArgumentParser()
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--framework", required=True)
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--frameworks-config", type=Path, default=Path("configs/frameworks.yaml"))
    # 说明：注册一个命令行参数，运行脚本时可以通过它调整行为。
    parser.add_argument("--prompts-config", type=Path, default=Path("configs/prompts_zh.yaml"))
    # 说明：解析命令行参数，得到后续流程使用的配置值。
    args = parser.parse_args()
    # 说明：读取或传递当前框架、场景相关配置。
    frameworks = load_yaml(args.frameworks_config)["frameworks"]
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    prompts = load_yaml(args.prompts_config)
    # 说明：给变量赋值或设置字段，供后续测试流程使用。
    cfg = frameworks[args.framework]

    # 说明：进入异步上下文，自动管理网络客户端或流式连接的生命周期。
    async with httpx.AsyncClient(timeout=120) as client:
        # 说明：开始循环，逐个处理集合中的元素。
        for idx, prompt in enumerate(prompts["base_questions"], start=1):
            # 说明：根据条件决定是否执行下面的代码块。
            if cfg["kind"] == "ollama":
                # 说明：拼接目标接口地址，后续请求会发到这里。
                url = f"{cfg['base_url'].rstrip('/')}/api/generate"
                # 说明：构造请求体，准备发给模型推理服务。
                body = {"model": cfg["model"], "prompt": prompt, "stream": False, "options": {"num_predict": 128}}
                # 说明：发送非流式 HTTP POST 请求，等待完整响应返回。
                resp = await client.post(url, json=body)
                # 说明：如果 HTTP 状态码表示失败，就抛出异常进入错误处理。
                resp.raise_for_status()
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                text = resp.json().get("response", "")
            # 说明：当前面的条件都不满足时，执行这个兜底分支。
            else:
                # 说明：拼接目标接口地址，后续请求会发到这里。
                url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
                # 说明：构造请求体，准备发给模型推理服务。
                body = {
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    "model": cfg["model"],
                    # 说明：执行这一行逻辑，推进冒烟测试或压力测试流程。
                    "messages": [
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        {"role": "system", "content": prompts["system_prompt"]},
                        # 说明：提供多行参数、列表项或字典项的一部分。
                        {"role": "user", "content": prompt},
                    # 说明：结束上一段多行结构。
                    ],
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    "max_tokens": 128,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    "temperature": 0.0,
                    # 说明：提供多行参数、列表项或字典项的一部分。
                    "stream": False,
                # 说明：结束上一段多行结构。
                }
                # 说明：发送非流式 HTTP POST 请求，等待完整响应返回。
                resp = await client.post(url, json=body)
                # 说明：如果 HTTP 状态码表示失败，就抛出异常进入错误处理。
                resp.raise_for_status()
                # 说明：解析响应或中间数据，转换成 Python 对象。
                payload = resp.json()
                # 说明：给变量赋值或设置字段，供后续测试流程使用。
                text = payload["choices"][0]["message"]["content"]
            # 说明：把当前状态或结果打印到终端，方便观察测试进度。
            print(json.dumps({"idx": idx, "prompt": prompt, "answer_preview": text[:120]}, ensure_ascii=False))


# 说明：判断脚本是否被直接运行，是的话执行主入口。
if __name__ == "__main__":
    # 说明：启动异步事件循环，运行主流程。
    asyncio.run(main())
