#!/usr/bin/env python3
"""Minimal OpenAI-compatible DeepSpeed inference baseline."""

from __future__ import annotations

import argparse
import os

import deepspeed
import torch
from transformers_openai_server import build_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")
    parser.add_argument("--served-name", default="deepseek-r1-distill-qwen-14b")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8110)
    parser.add_argument("--tp-size", type=int, default=2)
    parser.add_argument("--local_rank", "--local-rank", type=int, default=int(os.getenv("LOCAL_RANK", "0")))
    args = parser.parse_args()

    def init_deepspeed(model):
        return deepspeed.init_inference(
            model,
            mp_size=args.tp_size,
            dtype=torch.bfloat16,
            replace_method="auto",
            replace_with_kernel_inject=False,
        )

    app = build_app(args.model, args.served_name, quantization=None, post_init=init_deepspeed)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
