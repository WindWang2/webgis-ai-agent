"""LLM HTTP client lifecycle regression tests.

Locks in the provider HTTP client pooling guarantees introduced for the
LLM Provider HTTP Runtime reliability goal:

  - N calls to the same provider reuse ONE pooled ``httpx.AsyncClient`` (no
    per-call client/connection-pool recreation, so the keep-alive pool is
    reused instead of re-handshaking every turn).
  - different providers get separate clients; base_url variants collapse.
  - ``Authorization`` is per-request → no header bleed across api_keys that
    share a pooled client.
  - concurrent first-of-provider calls share one client (the creation lock).
  - ``aclose_all`` is idempotent, leaves no open clients, and the registry is
    reusable afterwards.
  - a cancelled stream forfeits at most one connection and never poisons the
    pooled client (the next call succeeds on the same client).
  - clients never cross event loops: under pytest's per-function loop scope a
    new loop gets a new client and the stale one is discarded.

All deterministic: ``httpx.MockTransport`` replaces the network — no real LLM,
no subprocess, no sockets.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.services.chat import llm_client
from app.services.chat.llm_client import (
    LLMConfig,
    _normalize_base_url,
    _registry,
    call_llm,
    call_llm_stream,
    close_llm_http_clients,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Hermetic reset for the registry's own tests.

    The registry is a module singleton, but ``asyncio_default_fixture_loop_scope
    = function`` gives every test a fresh event loop. Without this reset, a
    client created on a previous test's (now closed) loop lingers in the
    registry until the next ``acquire`` detects the loop change — which makes
    count assertions order-dependent and flaky. The reset is local to this
    module: the rest of the suite mocks at the engine/planner boundary (above
    the client) and never touches the registry.
    """
    _registry._reset_for_tests()
    yield
    _registry._reset_for_tests()


def _install_mock_transport(monkeypatch, handler):
    """Make registry-created clients use a MockTransport (no real network).

    Patches the ``httpx.AsyncClient`` symbol the registry calls inside
    ``acquire`` so the real creation path runs (``created_count`` increments,
    entries are stored) but every client speaks to the mock transport.
    """
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.setdefault("transport", transport)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", _factory)
    return transport


def _ok_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("stream"):
        sse = (
            "data: "
            + json.dumps(
                {"choices": [{"delta": {"content": "hi "}, "finish_reason": None}]}
            )
            + "\n\n"
            + "data: "
            + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            + "\n\n"
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    )


def _cfg(
    base_url: str = "http://provider.example/v1", api_key: str = "k1"
) -> LLMConfig:
    return LLMConfig(base_url=base_url, model="m", api_key=api_key)


# ─── client reuse ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_n_calls_same_provider_create_one_client(monkeypatch):
    _install_mock_transport(monkeypatch, _ok_response)
    cfg = _cfg()
    for _ in range(25):
        resp = await call_llm(cfg, [{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "ok"
    assert _registry.created_count == 1, "25 calls must reuse one pooled client"
    assert _registry.active_client_count() == 1


@pytest.mark.asyncio
async def test_stream_and_nonstream_share_one_client(monkeypatch):
    _install_mock_transport(monkeypatch, _ok_response)
    cfg = _cfg()
    await call_llm(cfg, [{"role": "user", "content": "hi"}])
    events = [
        e async for e in call_llm_stream(cfg, [{"role": "user", "content": "hi"}])
    ]
    assert _registry.created_count == 1
    assert any(t == "done" for t, _ in events)


@pytest.mark.asyncio
async def test_different_providers_get_separate_clients(monkeypatch):
    _install_mock_transport(monkeypatch, _ok_response)
    await call_llm(_cfg("http://a.example/v1"), [{"role": "user", "content": "x"}])
    await call_llm(_cfg("http://b.example/v1"), [{"role": "user", "content": "x"}])
    assert _registry.created_count == 2
    assert _registry.active_client_count() == 2


@pytest.mark.asyncio
async def test_base_url_variants_collapse_into_one_client(monkeypatch):
    _install_mock_transport(monkeypatch, _ok_response)
    # trailing slash + scheme/host case must collapse to the same pooled client
    await call_llm(
        _cfg("http://provider.example/v1/"), [{"role": "user", "content": "x"}]
    )
    await call_llm(
        _cfg("http://Provider.Example/V1"), [{"role": "user", "content": "x"}]
    )
    assert _registry.created_count == 1


@pytest.mark.asyncio
async def test_request_url_has_no_double_slash(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _ok_response(request)

    _install_mock_transport(monkeypatch, handler)
    # base_url ends in '/' — the old f"{base_url}/chat/completions" produced a
    # double slash; normalization must prevent that.
    await call_llm(
        _cfg("http://provider.example/v1/"), [{"role": "user", "content": "x"}]
    )
    assert seen, "no request observed"
    assert seen[0].endswith("/v1/chat/completions"), seen
    assert "//chat" not in seen[0], seen


def test_normalize_base_url_key_and_prefix():
    # httpx.URL lowercases the host (correct per HTTP — hosts are case-insensitive)
    # while preserving path case; both feed the canonical key/prefix.
    key_a, prefix_a = _normalize_base_url("https://api.x.com/v1/")
    key_b, prefix_b = _normalize_base_url("https://API.x.com/v1")
    assert key_a == key_b == "https://api.x.com/v1", (key_a, key_b)
    assert prefix_a == prefix_b == "https://api.x.com/v1", (prefix_a, prefix_b)
    assert not prefix_a.endswith("/"), "prefix must not keep a trailing slash"
    # path case is preserved (paths may be case-sensitive on some origins)
    _, prefix_path = _normalize_base_url("https://api.x.com/V1/Chat")
    assert prefix_path == "https://api.x.com/V1/Chat", prefix_path


# ─── header / credentials isolation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_authorization_bleed_across_api_keys(monkeypatch):
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        return _ok_response(request)

    _install_mock_transport(monkeypatch, handler)
    # same provider, different keys → shared client, per-request Authorization
    await call_llm(_cfg(api_key="key-A"), [{"role": "user", "content": "x"}])
    await call_llm(_cfg(api_key="key-B"), [{"role": "user", "content": "x"}])
    await call_llm(_cfg(api_key="key-A"), [{"role": "user", "content": "x"}])
    assert _registry.created_count == 1, "same base_url shares one client"
    assert seen_auth == ["Bearer key-A", "Bearer key-B", "Bearer key-A"], seen_auth


@pytest.mark.asyncio
async def test_authorization_not_set_on_pooled_client_headers(monkeypatch):
    """The pooled client must carry NO Authorization header; it is per-request only.

    Inspects the actual ``httpx.AsyncClient.headers`` on the stored entry — this
    is the structural guarantee behind the header-isolation claim (httpcore pools
    by origin, never by Authorization).
    """
    _install_mock_transport(monkeypatch, _ok_response)
    await call_llm(_cfg(api_key="secret"), [{"role": "user", "content": "x"}])
    assert _registry.active_client_count() == 1
    entry = next(iter(_registry._entries.values()))
    assert "authorization" not in entry.client.headers, dict(entry.client.headers)
    assert "Authorization" not in entry.client.headers


# ─── concurrency ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_client(monkeypatch):
    _install_mock_transport(monkeypatch, _ok_response)
    cfg = _cfg()
    await asyncio.gather(
        *(call_llm(cfg, [{"role": "user", "content": "x"}]) for _ in range(16))
    )
    assert _registry.created_count == 1, "concurrent first-calls must not race-create"
    assert _registry.active_client_count() == 1


# ─── shutdown ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aclose_all_closes_clients_and_is_idempotent(monkeypatch):
    _install_mock_transport(monkeypatch, _ok_response)
    await call_llm(_cfg(), [{"role": "user", "content": "x"}])
    assert _registry.active_client_count() == 1
    entry = next(iter(_registry._entries.values()))
    assert not entry.client.is_closed

    await close_llm_http_clients()
    assert _registry.active_client_count() == 0
    assert entry.client.is_closed, (
        "aclose must really close the client on the live loop"
    )

    # idempotent
    await close_llm_http_clients()
    assert _registry.active_client_count() == 0

    # reusable after shutdown: a new call lazily re-creates a client
    await call_llm(_cfg(), [{"role": "user", "content": "x"}])
    assert _registry.active_client_count() == 1
    await close_llm_http_clients()


# ─── streaming cancel/release ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_stream_does_not_poison_pooled_client(monkeypatch):
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not body.get("stream"):
            # non-stream follow-up after the cancel (same pooled client)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
                },
            )

        async def gen():
            yield (
                b"data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": "tok"}, "finish_reason": None}]}
                ).encode()
                + b"\n\n"
            )
            # simulated model latency: the body parks mid-stream
            await asyncio.wait_for(release.wait(), timeout=10.0)
            yield b"data: [DONE]\n\n"

        return httpx.Response(
            200, content=gen(), headers={"content-type": "text/event-stream"}
        )

    _install_mock_transport(monkeypatch, handler)
    cfg = _cfg()
    first_token_seen: list[bool] = []

    async def consume():
        # Fully iterate (do NOT break): after the first token the body parks at
        # ``release.wait()``, so the consumer suspends mid-stream — exactly where
        # a client disconnect delivers cancellation.
        async for _event in call_llm_stream(cfg, [{"role": "user", "content": "x"}]):
            first_token_seen.append(True)

    task = asyncio.create_task(consume())
    # Let the consumer read the first token and then park inside the blocked body.
    await asyncio.sleep(0.2)
    assert first_token_seen, "consumer should have read the first token before cancel"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # MockTransport replaces httpcore's connection pool, so this can prove "the
    # pooled client is not poisoned (next request succeeds)" but NOT "the
    # connection was released" (there is no real connection). The real-socket
    # release proof lives in
    # ``test_cancelled_stream_releases_connection_over_real_socket`` below.
    # The client's transport is fixed at creation, so this follow-up hits the
    # SAME handler/client (no swap).
    resp = await call_llm(cfg, [{"role": "user", "content": "again"}])
    assert resp["choices"][0]["message"]["content"] == "ok"
    assert _registry.created_count == 1, "cancelled stream must not create a new client"
    release.set()


# ─── streaming cancel: real-socket connection release proof ──────────────────


async def _streaming_keepalive_server(
    release: asyncio.Event, events: list[str]
) -> asyncio.base_events.Server:
    """Local HTTP/1.1 server proving connection lifecycle on a real socket.

    Non-stream requests get a keep-alive JSON response. Stream requests get a
    chunked SSE body that writes one token then parks at ``release`` (simulated
    model latency). It appends ``"accept"`` per new TCP connection and
    ``"client_closed"`` when the client closes the socket mid-stream — the
    observable signal that httpx released the cancelled connection.
    """

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        events.append("accept")
        try:
            while True:
                request_line = await reader.readline()
                if not request_line:
                    break
                content_length = 0
                while True:
                    header = await reader.readline()
                    if header in (b"\r\n", b"\n", b""):
                        break
                    if header.lower().startswith(b"content-length:"):
                        content_length = int(header.split(b":", 1)[1].strip())
                raw = (
                    await reader.readexactly(content_length) if content_length else b""
                )
                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    payload = {}

                if not payload.get("stream"):
                    body = b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'
                    response = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + body
                    )
                    writer.write(response)
                    await writer.drain()
                    continue

                # Streaming branch: chunked SSE, one token, then park.
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n"
                    b"Connection: keep-alive\r\n\r\n"
                )
                token = (
                    b"data: "
                    + json.dumps(
                        {
                            "choices": [
                                {"delta": {"content": "tok"}, "finish_reason": None}
                            ]
                        }
                    ).encode()
                    + b"\n\n"
                )
                writer.write(b"%x\r\n" % len(token) + token + b"\r\n")
                await writer.drain()

                # Detect client-side close concurrently with the park. When httpx
                # releases the cancelled connection it closes the socket → read EOF.
                client_closed = asyncio.Event()

                async def _watch_close():
                    try:
                        while True:
                            data = await reader.read(1024)
                            if not data:
                                break
                    except Exception:  # noqa: BLE001 — any read error means closed
                        pass
                    client_closed.set()

                watch_task = asyncio.create_task(_watch_close())
                close_wait = asyncio.create_task(client_closed.wait())
                release_wait = asyncio.create_task(release.wait())
                await asyncio.wait(
                    {close_wait, release_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=10.0,
                )
                if client_closed.is_set():
                    events.append("client_closed")
                for pending in (watch_task, close_wait, release_wait):
                    pending.cancel()
                break  # the streaming connection ends here either way
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    return await asyncio.start_server(_handle, "127.0.0.1", 0)


@pytest.mark.asyncio
async def test_cancelled_stream_releases_connection_over_real_socket():
    """A cancelled stream must RELEASE the connection (close the socket), not
    hold it open or leak it onto the pool dirty.

    MockTransport cannot prove this (it bypasses the connection pool). This
    stands up a real HTTP/1.1 server whose streaming body parks mid-flight, then
    cancels the consumer and asserts the server observes the socket close
    (httpx released the connection) — plus the pooled client stays usable.
    """
    release = asyncio.Event()
    events: list[str] = []
    server = await _streaming_keepalive_server(release, events)
    port = server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    cfg = LLMConfig(base_url=base, model="m", api_key="k")
    try:
        # Wait for the streaming request to be accepted and the first token
        # delivered, so the consumer is parked inside the blocked body.
        first_token: list[bool] = []

        async def consume():
            async for _event in call_llm_stream(
                cfg, [{"role": "user", "content": "x"}]
            ):
                first_token.append(True)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        assert first_token, "consumer should have read the first token before cancel"
        assert events.count("accept") == 1, events

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The server must observe the client closing the socket → connection
        # released (not leaked / not held open by a dangling generator).
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 2.0
        while "client_closed" not in events and loop.time() < deadline:
            await asyncio.sleep(0.05)
        assert "client_closed" in events, (
            f"cancelled stream did not release the connection (no socket close "
            f"observed); events={events}"
        )

        # The pooled client is NOT poisoned: a follow-up request on the same
        # client succeeds (created_count stays 1).
        resp = await call_llm(cfg, [{"role": "user", "content": "again"}])
        assert resp["choices"][0]["message"]["content"] == "ok"
        assert _registry.created_count == 1, _registry.created_count

        release.set()
    finally:
        release.set()
        # Close the pooled client first: its keep-alive connections are held open
        # by httpx's pool, so without this the server's keep-alive handlers never
        # see EOF and ``server.wait_closed()`` would block. Guard wait_closed with
        # a timeout so a parked handler can never hang the suite.
        try:
            await close_llm_http_clients()
        except Exception:  # noqa: BLE001 — teardown
            pass
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


# ─── cross-loop safety ───────────────────────────────────────────────────────


def test_clients_do_not_cross_event_loops(monkeypatch):
    """Per-function loop scope invariant: a client bound to loop A must never be
    reused on loop B (that raises "Future attached to a different loop"). Two
    distinct ``asyncio.run`` loops acquire sequentially; the second must get a
    fresh client bound to its own loop.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response(request)

    def _run_once() -> None:
        async def main() -> None:
            await call_llm(_cfg(), [{"role": "user", "content": "x"}])

        asyncio.run(main())

    _install_mock_transport(monkeypatch, handler)
    _run_once()  # loop A
    created_after_a = _registry.created_count
    assert created_after_a == 1

    _run_once()  # loop B (new event loop); A is now closed
    # A brand-new client was created for loop B; the stale A-entry was discarded
    # (not await-closed — it is bound to the now-closed loop A).
    assert _registry.created_count == created_after_a + 1
