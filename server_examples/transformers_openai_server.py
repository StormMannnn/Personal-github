#!/usr/bin/env python3
"""Minimal OpenAI-compatible Transformers serving baseline."""

from __future__ import annotations

import argparse
import time
import uuid
from typing import Any, Callable

import torch
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TextIteratorStreamer
from threading import Thread


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    max_tokens: int = 256
    temperature: float = 0.0
    top_p: float = 1.0
    stream: bool = False


def render_prompt(tokenizer: Any, messages: list[Message]) -> str:
    payload = [{"role": item.role, "content": item.content} for item in messages]
    try:
        return tokenizer.apply_chat_template(payload, tokenize=False, add_generation_prompt=True)
    except Exception:  # noqa: BLE001
        return "\n".join(f"{item.role}: {item.content}" for item in messages) + "\nassistant:"


def build_app(
    model_id: str,
    served_name: str,
    quantization: str | None,
    post_init: Callable[[Any], Any] | None = None,
) -> FastAPI:
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    quant_config = None
    dtype = torch.bfloat16
    if quantization == "bnb4":
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
        )
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        dtype=dtype,
        device_map="auto",
        quantization_config=quant_config,
        trust_remote_code=True,
    )
    if post_init is not None:
        model = post_init(model)
    model.eval()

    app = FastAPI()

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": served_name, "object": "model"}]}

    @app.post("/v1/chat/completions")
    def chat(req: ChatRequest) -> Any:
        prompt = render_prompt(tokenizer, req.messages)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        generation_kwargs = {
            **inputs,
            "max_new_tokens": req.max_tokens,
            "do_sample": req.temperature > 0,
            "temperature": req.temperature if req.temperature > 0 else None,
            "top_p": req.top_p,
            "pad_token_id": tokenizer.eos_token_id,
        }
        generation_kwargs = {k: v for k, v in generation_kwargs.items() if v is not None}

        if req.stream:
            streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
            thread = Thread(target=model.generate, kwargs={**generation_kwargs, "streamer": streamer})
            thread.start()

            def events():
                completion_id = f"chatcmpl-{uuid.uuid4().hex}"
                for text in streamer:
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": served_name,
                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                    }
                    yield f"data: {chunk_to_json(chunk)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")

        with torch.inference_mode():
            output_ids = model.generate(**generation_kwargs)
        new_ids = output_ids[0][inputs["input_ids"].shape[1] :]
        text = tokenizer.decode(new_ids, skip_special_tokens=True)
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": served_name,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            }
        )

    return app


def chunk_to_json(chunk: dict[str, Any]) -> str:
    import json

    return json.dumps(chunk, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")
    parser.add_argument("--served-name", default="deepseek-r1-distill-qwen-14b")
    parser.add_argument("--quantization", choices=["bnb4"], default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()

    import uvicorn

    app = build_app(args.model, args.served_name, args.quantization)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
