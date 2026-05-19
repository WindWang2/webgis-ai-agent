"""OpenAI-compatible Chat Completions HTTP 客户端（M1：从 chat_engine.py 抽离）。

把 `_call_llm` / `_call_llm_stream` 提为模块级自由函数 — 接收显式 config dict，
不依赖 ChatEngine 实例。便于：
- 在 subagent / 测试中以"无侧效"方式独立调起 LLM
- 后续接入更细粒度的重试 / 限流（统一抓 client 入口）
- 把推理流细节（reasoning 兼容、<think> 标签、tool_call delta）局部封装

`LLMConfig` 是一个轻量 dataclass，由 ChatEngine 每次调用前组装一次。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    use_prompt_caching: bool = False
    max_tokens: int = 16384


def _build_headers(cfg: LLMConfig) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
    }
    if cfg.use_prompt_caching:
        headers["X-Prompt-Cache"] = "1"
        if "deepseek" in cfg.base_url.lower():
            headers["deepseek-caching"] = "true"
    return headers


def _build_payload(cfg: LLMConfig, messages: list[dict], tools: Optional[list], stream: bool) -> dict:
    payload: dict = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": cfg.max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if stream:
        payload["stream"] = True
    return payload


async def call_llm(
    cfg: LLMConfig,
    messages: list[dict],
    tools: Optional[list] = None,
) -> dict:
    """同步（非流式）调用 LLM API；返回完整响应 JSON。"""
    headers = _build_headers(cfg)
    payload = _build_payload(cfg, messages, tools, stream=False)
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{cfg.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def call_llm_stream(
    cfg: LLMConfig,
    messages: list[dict],
    tools: Optional[list] = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """流式调用 LLM。Yields (event_type, data)：
    - ('token', {'content': str, 'is_reasoning': bool}) — 增量 token
    - ('done', {'message': dict, 'finish_reason': str|None}) — 流结束、整条 assistant 消息

    兼容 DeepSeek-R1 / MiniMax-M2.7 风格的 reasoning_content / <think> 标签：
    - 显式 reasoning_content delta 单独走 is_reasoning=True 通道
    - content 里 <think>...</think> 块自动剥到 reasoning，避免污染历史正文
    """
    headers = _build_headers(cfg)
    payload = _build_payload(cfg, messages, tools, stream=True)

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_accum: dict[int, dict] = {}
    finish_reason: Optional[str] = None
    in_think_block = False

    timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{cfg.base_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                logger.error(f"LLM Stream Error {response.status_code}: {error_text.decode()}")
            response.raise_for_status()

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse SSE chunk: {data_str[:200]}")
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                finish_reason = choices[0].get("finish_reason") or finish_reason

                # reasoning delta（显式字段）
                delta_reasoning = (
                    delta.get("reasoning")
                    or delta.get("reasoning_content")
                    or delta.get("thinking_content")
                    or delta.get("thinking")
                )
                if delta_reasoning:
                    reasoning_parts.append(delta_reasoning)
                    yield ("token", {"content": delta_reasoning, "is_reasoning": True})

                # content delta；可能含 <think> 内联标签
                delta_content = delta.get("content")
                if delta_content:
                    remaining = delta_content
                    while remaining:
                        if not in_think_block:
                            idx = remaining.find("<think>")
                            if idx == -1:
                                content_parts.append(remaining)
                                yield ("token", {"content": remaining})
                                remaining = ""
                            else:
                                pre = remaining[:idx]
                                if pre:
                                    content_parts.append(pre)
                                    yield ("token", {"content": pre})
                                in_think_block = True
                                remaining = remaining[idx + 7:]
                        else:
                            idx = remaining.find("</think>")
                            if idx == -1:
                                reasoning_parts.append(remaining)
                                yield ("token", {"content": remaining, "is_reasoning": True})
                                remaining = ""
                            else:
                                think_chunk = remaining[:idx]
                                if think_chunk:
                                    reasoning_parts.append(think_chunk)
                                    yield ("token", {"content": think_chunk, "is_reasoning": True})
                                in_think_block = False
                                remaining = remaining[idx + 8:].lstrip()

                # tool_call delta
                delta_tool_calls = delta.get("tool_calls")
                if delta_tool_calls:
                    for tc_delta in delta_tool_calls:
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        tc_entry = tool_calls_accum[idx]
                        if tc_delta.get("id"):
                            tc_entry["id"] = tc_delta["id"]
                        if tc_delta.get("type"):
                            tc_entry["type"] = tc_delta["type"]
                        fn_delta = tc_delta.get("function", {})
                        if fn_delta.get("name"):
                            tc_entry["function"]["name"] += fn_delta["name"]
                        if fn_delta.get("arguments"):
                            tc_entry["function"]["arguments"] += fn_delta["arguments"]

    # Assemble final message
    assembled_content = "".join(content_parts)
    assembled_reasoning = "".join(reasoning_parts)
    assembled_message: dict = {"role": "assistant", "content": assembled_content}
    if assembled_reasoning:
        assembled_message["reasoning_content"] = assembled_reasoning

    if tool_calls_accum:
        assembled_tool_calls = []
        for idx in sorted(tool_calls_accum.keys()):
            tc = tool_calls_accum[idx]
            assembled_tool_calls.append({
                "id": tc["id"],
                "type": tc.get("type", "function"),
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            })
        assembled_message["tool_calls"] = assembled_tool_calls

    yield ("done", {"message": assembled_message, "finish_reason": finish_reason})
