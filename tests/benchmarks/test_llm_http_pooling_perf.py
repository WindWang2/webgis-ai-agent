"""Deterministic LLM HTTP client pooling performance evidence (no LLM, no mock).

Two structural, fully deterministic proofs of the LLM Provider HTTP Runtime
pooling goal — work-count metrics, not fragile wall-clock:

  1. client_creation_count_constant_in_n
     The cost this goal eliminates. Before the fix every LLM call constructed a
     fresh ``httpx.AsyncClient`` (O(N) clients for N calls). After: O(1) — one
     pooled client per provider regardless of N. Asserted by the registry's
     ``created_count`` counter.

  2. pooled_client_reuses_one_tcp_connection
     ``httpx.MockTransport`` bypasses httpcore's connection pool, so it CANNOT
     prove real keep-alive reuse — only "1 client". This workload stands up a
     real localhost HTTP/1.1 keep-alive server and counts TCP ``accept()``s:
     N pooled requests must reuse ONE socket (the provider no longer pays a
     TCP/TLS handshake per turn). This is the only honest proof of reuse.

Both are hard-assert structural gates (no baseline file needed) and run fast.
Marked ``perf`` to group with the transport/compute perf harnesses.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.chat.llm_client import (
    _registry,
    close_llm_http_clients,
    get_llm_http_client,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Hermetic reset for the module singleton under per-function loop scope."""
    _registry._reset_for_tests()
    yield
    _registry._reset_for_tests()


# ─── 1. client creation count is O(1), not O(N) ──────────────────────────────


@pytest.mark.perf
@pytest.mark.asyncio
async def test_client_creation_count_constant_in_n(monkeypatch):
    """N calls to the same provider must create exactly ONE pooled client.

    This is the deterministic work-count metric for the eliminated cost: before
    the fix this counter grew linearly (one ``httpx.AsyncClient`` per call).
    Uses ``httpx.MockTransport`` so no socket is touched.
    """
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(_handler)

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    base = "http://provider.example/v1"
    n = 50
    for i in range(n):
        client = await get_llm_http_client(base)
        # every call returns the SAME client instance
        assert client is await get_llm_http_client(base)
        resp = await client.post(
            f"{base}/chat/completions",
            json={"i": i},
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
        assert resp.status_code == 200

    assert _registry.created_count == 1, (
        f"{n} calls must create exactly 1 pooled client, got {_registry.created_count}"
    )
    assert _registry.active_client_count() == 1
    await close_llm_http_clients()


# ─── 2. real-socket keep-alive: N requests reuse ONE TCP connection ──────────


async def _start_keepalive_server(
    accept_counter: list[int],
) -> asyncio.base_events.Server:
    """Minimal HTTP/1.1 keep-alive server that counts NEW TCP connections.

    Speaks just enough HTTP/1.1: read request line + headers, drain the body by
    Content-Length, reply with ``Connection: keep-alive`` so the client reuses
    the socket. ``accept_counter`` counts new TCP accepts — the reuse metric.
    """

    async def _handle_one_conn(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            while True:
                request_line = await reader.readline()
                if not request_line:
                    break  # client closed the keep-alive socket
                content_length = 0
                while True:
                    header = await reader.readline()
                    if header in (b"\r\n", b"\n", b""):
                        break
                    if header.lower().startswith(b"content-length:"):
                        content_length = int(header.split(b":", 1)[1].strip())
                if content_length:
                    await reader.readexactly(content_length)
                body = (
                    b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'
                )
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"Connection: keep-alive\r\n"
                    b"\r\n" + body
                )
                writer.write(response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass

    async def _counted(reader, writer):
        accept_counter[0] += 1
        await _handle_one_conn(reader, writer)

    return await asyncio.start_server(_counted, "127.0.0.1", 0)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_pooled_client_reuses_one_tcp_connection():
    """The smoking gun: N pooled requests to a real keep-alive server reuse ONE
    TCP connection (one ``accept``), proving the per-turn TCP/TLS handshake cost
    is eliminated.

    ``httpx.MockTransport`` cannot prove this — it replaces the transport and
    bypasses httpcore's connection pool entirely, so only a real socket does.
    """
    accept_counter = [0]
    server = await _start_keepalive_server(accept_counter)
    port = server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        # The pooled client the registry hands out — the same instance every call.
        client = await get_llm_http_client(base)
        same_identity = 0
        n = 20
        for _ in range(n):
            assert (await get_llm_http_client(base)) is client
            same_identity += 1
            resp = await client.post(
                f"{base}/chat/completions",
                json={"x": 1},
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            assert resp.status_code == 200
            assert resp.json()["choices"][0]["message"]["content"] == "ok"

        assert same_identity == n
        assert _registry.created_count == 1, _registry.created_count
        assert accept_counter[0] == 1, (
            f"{n} pooled requests must reuse ONE TCP connection (got "
            f"{accept_counter[0]} accepts); the per-turn handshake was not eliminated"
        )
        await close_llm_http_clients()
        assert _registry.active_client_count() == 0
    finally:
        server.close()
        await server.wait_closed()
