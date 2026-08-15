"""OpenAI-compatible Chat Completions HTTP 客户端（M1：从 chat_engine.py 抽离）。

把 `_call_llm` / `_call_llm_stream` 提为模块级自由函数 — 接收显式 config dict，
不依赖 ChatEngine 实例。便于：
- 在 subagent / 测试中以"无侧效"方式独立调起 LLM
- 后续接入更细粒度的重试 / 限流（统一抓 client 入口）
- 把推理流细节（reasoning 兼容、<think> 标签、tool_call delta）局部封装

`LLMConfig` 是一个轻量 dataclass，由 ChatEngine 每次调用前组装一次。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

import httpx

logger = logging.getLogger(__name__)


# ─── LRU Cache ──────────────────────────────────────────────


class LRUCache(OrderedDict):
    """Simple LRU Cache to bound memory usage.

    Thread-safe: protects internal ``OrderedDict`` mutations
    (``move_to_end``, ``__delitem__``) with a reentrant lock so
    concurrent async reads cannot corrupt the data structure.
    """

    def __init__(self, capacity: int = 100):
        super().__init__()
        self.capacity = capacity
        import threading
        self._lock = threading.RLock()

    def __getitem__(self, key):
        with self._lock:
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)
            if len(self) > self.capacity:
                oldest = next(iter(self))
                del self[oldest]

    def __contains__(self, key):
        with self._lock:
            return super().__contains__(key)

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)

    def pop(self, key, *args):
        with self._lock:
            return super().pop(key, *args)


# ─── XML tool-call 解析（MiniMax 风格） ─────────────────────────


_INVOKE_PAT = re.compile(
    r'minimax:tool_call\s+<invoke\s+name="([^"]+)">(.*?)(?:</invoke>|$)',
    re.DOTALL,
)
_PARAM_PAT = re.compile(r'<parameter\s+name="([^"]+)">(.*?)</parameter>', re.DOTALL)


def parse_minimax_xml_tool_calls(content: str) -> list[dict]:
    """Parse MiniMax XML-format tool calls from content field.

    Handles: minimax:tool_call <invoke name="tool"> <parameter name="p">v</parameter> </invoke>
    """
    tool_calls: list[dict] = []
    for tool_name, body in _INVOKE_PAT.findall(content):
        params: dict = {}
        for p_name, p_value in _PARAM_PAT.findall(body):
            v = p_value.strip()
            try:
                params[p_name] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                params[p_name] = v
        if tool_name.strip():
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "function": {"name": tool_name.strip(), "arguments": params},
            })
    return tool_calls



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


# ─── Provider HTTP client lifecycle ──────────────────────────────────────────
#
# Before this block, ``call_llm`` / ``call_llm_stream`` opened a *fresh*
# ``httpx.AsyncClient`` on every call (a new TCP/TLS handshake + connection pool
# each time → zero keep-alive reuse across calls to the same provider).
# ``LLMHttpClientRegistry`` replaces that with one pooled client per provider
# base_url per running event loop, so repeated calls to the same provider reuse
# the keep-alive connection pool instead of re-handshaking every turn.
#
# Design contract (see PR description):
#   * Bounded — one client per distinct base_url; ``httpx.Limits`` bounds the pool.
#   * Loop-aware — a client is bound to the event loop that created it; reusing it
#     on a different loop raises "Future attached to a different loop". We key by
#     (base_url, running loop); on loop change the stale entry is discarded. A
#     client bound to an already-closed loop cannot be ``await``-closed (it raises
#     ``RuntimeError``), so it is dropped for GC instead. This keeps the registry
#     correct under pytest's per-function loop scope and on app restart.
#   * Credentials-safe — NO Authorization or auth header is ever set on the pooled
#     client. Every call passes ``Authorization`` per-request, so two configs that
#     share a base_url but differ in api_key / prompt-caching share one client with
#     zero header bleed (httpcore pools by origin scheme+host+port, never by
#     Authorization). ASSUMPTION: OpenAI-compatible providers do not set cookies;
#     if one ever did, keying would need to include that (see PR "deferred items").
#   * Concurrency-safe — one registry-level ``asyncio.Lock`` (created lazily on
#     first use, inside a running loop) guards the check-then-create path with a
#     double check, so two concurrent first-of-provider calls don't make two
#     clients. (Under single-threaded asyncio the critical section has no await,
#     so the lock is belt-and-suspenders against a future await being added.)
#   * Shutdown-safe — ``aclose_all`` is idempotent and closes every client on the
#     live loop (a real socket close), dropping only foreign/closed-loop entries.
#     Wired into the FastAPI lifespan shutdown alongside the existing aiohttp
#     ``close_shared_client()``.
#
# No retry is performed at this layer. A mid-stream retry would duplicate tokens
# and a post-send retry could double-execute tool calls; the only safe retry
# boundary is connect/pool-phase only (ConnectError / ConnectTimeout /
# PoolTimeout) and real resilience belongs at the orchestration layer after a
# clean LLM error. That seam is intentionally left unimplemented here.

# Bounded pool defaults, exposed for tests/ops. Pool sizing must be >= peak
# concurrent in-flight LLM calls + headroom (streams dominate); the per-request
# pool timeout stays short so self-inflicted exhaustion fails fast instead of
# being mislabelled as upstream latency. keepalive_expiry is set above httpx's 5s
# default so the pooled connection survives a short human pause between turns
# (the headline reuse benefit) while still bounded — agentic multi-round bursts
# (sub-second apart) reuse the pool regardless.
_DEFAULT_MAX_CONNECTIONS = 100
_DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
_DEFAULT_KEEPALIVE_EXPIRY = 30.0


def _normalize_base_url(base_url: str) -> tuple[str, str]:
    """Return ``(registry_key, url_prefix)`` for a provider base_url.

    The key collapses trailing-slash / host-case variants so the same provider
    reuses one pooled client. ``url_prefix`` is the canonical form (no trailing
    slash) used to build request URLs, fixing a latent double slash when base_url
    ended in ``/`` under the old ``f"{cfg.base_url}/chat/completions"`` build.
    """
    url = httpx.URL(base_url).copy_with(query=None, fragment=None)
    canonical = str(url).rstrip("/")
    return canonical.lower(), canonical


class _PooledEntry:
    """A pooled client bound to the event loop that created it."""

    __slots__ = ("client", "loop")

    def __init__(self, client: httpx.AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
        self.client = client
        self.loop = loop


async def _safe_aclose(client: httpx.AsyncClient) -> None:
    """Close a pooled client, swallowing double-close / loop-gone failures.

    On the live loop this is a real socket close. It is defensive by design: a
    shutdown triggered twice, or a client already closed, must never raise.
    """
    try:
        await client.aclose()
    except Exception as e:  # noqa: BLE001 — best-effort teardown, never fatal
        logger.debug("LLM HTTP client aclose failed (ignored): %s", e)


class LLMHttpClientRegistry:
    """Bounded, loop-aware, concurrency-safe pool of ``httpx.AsyncClient`` per base_url."""

    def __init__(
        self,
        max_connections: int = _DEFAULT_MAX_CONNECTIONS,
        max_keepalive_connections: int = _DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry: float = _DEFAULT_KEEPALIVE_EXPIRY,
    ) -> None:
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=keepalive_expiry,
        )
        self._entries: dict[str, _PooledEntry] = {}
        self._lock: asyncio.Lock | None = None
        # Number of clients ever created — a deterministic "work-count" signal for
        # the perf regression tests (before the fix this grew linearly with calls).
        self.created_count = 0

    def _ensure_lock(self) -> asyncio.Lock:
        # Created lazily inside a running loop (``acquire`` is always awaited). On
        # 3.10+ ``asyncio.Lock`` does not bind to a loop at construction, and any
        # contended waiter is resolved/cancelled before its loop closes (asyncio is
        # single-threaded per loop), so a single registry lock is safe across the
        # per-test loop changes that ``function`` loop scope produces.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _is_live(entry: _PooledEntry, loop: asyncio.AbstractEventLoop) -> bool:
        return entry.loop is loop and not entry.loop.is_closed()

    async def acquire(self, base_url: str) -> httpx.AsyncClient:
        """Return a pooled client for ``base_url``, bound to the running loop."""
        key, _prefix = _normalize_base_url(base_url)
        loop = asyncio.get_running_loop()

        entry = self._entries.get(key)
        if entry is not None and self._is_live(entry, loop):
            return entry.client

        stale: _PooledEntry | None = None
        async with self._ensure_lock():
            # Re-check inside the lock: another coroutine may have created it.
            entry = self._entries.get(key)
            if entry is not None and self._is_live(entry, loop):
                return entry.client
            stale = self._entries.pop(key, None)
            client = httpx.AsyncClient(
                limits=self._limits,
                # Safety net only: both call sites pass an explicit per-request
                # timeout (which overrides this), but a future caller of
                # ``get_llm_http_client`` that forgets one still gets a sane bound
                # instead of httpx's 5s default.
                timeout=120.0,
            )
            self._entries[key] = _PooledEntry(client, loop)
            self.created_count += 1

        # Close the replaced entry *outside* the lock so creation isn't serialized.
        # Only a same-live-loop entry can be closed; a foreign/closed-loop client
        # cannot be await-closed (RuntimeError) and is dropped for GC.
        if stale is not None:
            if self._is_live(stale, loop):
                await _safe_aclose(stale.client)
            else:
                logger.debug(
                    "LLM HTTP client for %s was bound to a finished loop; dropped for GC",
                    key,
                )
        # Return the local, NOT ``self._entries[key]``: a concurrent ``aclose_all``
        # can clear ``_entries`` during the ``await _safe_aclose(stale)`` above,
        # which would make a re-read raise ``KeyError``.
        return client

    def active_client_count(self) -> int:
        """Pooled clients currently held (for tests/observability)."""
        return len(self._entries)

    async def aclose_all(self) -> None:
        """Close every pooled client on the live loop. Idempotent; resets the registry."""
        async with self._ensure_lock():
            snapshot = list(self._entries.items())
            self._entries.clear()
        loop = asyncio.get_running_loop()
        for _key, entry in snapshot:
            if self._is_live(entry, loop):
                await _safe_aclose(entry.client)
            else:
                logger.debug(
                    "LLM HTTP client for %s was bound to a finished loop; dropped for GC",
                    _key,
                )

    def _reset_for_tests(self) -> None:
        """Synchronously clear pooled clients WITHOUT closing (tests own their loops).

        Used by the lifecycle test autouse fixture so each test starts hermetic.
        Safe because tests use ``httpx.MockTransport`` (no real sockets → no leak).
        """
        self._entries.clear()
        self._lock = None
        self.created_count = 0


# Process-wide registry. Lazy: clients are created on first ``acquire`` inside a
# running loop, so importing this module never opens connections.
_registry = LLMHttpClientRegistry()


async def get_llm_http_client(base_url: str) -> httpx.AsyncClient:
    """Return the pooled HTTP client for a provider ``base_url`` (loop-bound)."""
    return await _registry.acquire(base_url)


async def close_llm_http_clients() -> None:
    """Close every pooled LLM HTTP client. Called from the FastAPI lifespan shutdown."""
    await _registry.aclose_all()


async def call_llm(
    cfg: LLMConfig,
    messages: list[dict],
    tools: Optional[list] = None,
) -> dict:
    """同步（非流式）调用 LLM API；返回完整响应 JSON。"""
    headers = _build_headers(cfg)
    payload = _build_payload(cfg, messages, tools, stream=False)
    _key, prefix = _normalize_base_url(cfg.base_url)
    # Pooled client: the connection/keep-alive pool is reused across calls to the
    # same provider. Timeout is passed per-request to preserve the wire contract
    # (flat 120s) exactly, independent of the shared client's own defaults.
    client = await get_llm_http_client(cfg.base_url)
    response = await client.post(
        f"{prefix}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()


async def test_llm_connection(
    cfg: LLMConfig,
    timeout: httpx.Timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=5.0),
) -> dict:
    """连通性探针：向 provider 发一次最小补全请求并校验 HTTP 状态。

    供 ``POST /api/v1/config/llm/test`` 使用（#390）：设置面板的
    "Connectivity Test" 之前是前端 setTimeout 假成功，从不触达后端。
    这里复用与生产调用相同的 payload/header 构造与连接池，但用短超时
    让失败快速返回，而不是等生产路径的 120s。

    失败（HTTP 非 2xx / 传输层错误）向上抛异常，由调用方转成带错误
    详情的响应，绝不返回"假成功"。
    """
    headers = _build_headers(cfg)
    payload = _build_payload(
        cfg,
        [{"role": "user", "content": "ping"}],
        tools=None,
        stream=False,
    )
    _key, prefix = _normalize_base_url(cfg.base_url)
    client = await get_llm_http_client(cfg.base_url)
    response = await client.post(
        f"{prefix}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
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

    _key, prefix = _normalize_base_url(cfg.base_url)
    # Pooled client reused across calls to the same provider. ``async with
    # client.stream(...)`` releases the connection back to the pool on normal exit,
    # exception, CancelledError and GeneratorExit, so a cancelled stream forfeits at
    # most one connection and never poisons the pooled client. Pool timeout is
    # shortened to 5s so self-inflicted pool exhaustion fails fast instead of being
    # mislabelled as upstream latency; connect/read/write are unchanged.
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=5.0)
    client = await get_llm_http_client(cfg.base_url)
    async with client.stream(
        "POST",
        f"{prefix}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
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
