"""WP2（GOAL-20261127-01）unit：provider 失败观测通道。

覆盖：
- parse_provider_observation 纯解析（有界、越界拒收、ANSI/bytes/垃圾行）；
- PiRpcClient stderr 读循环 → 注入 sink（假 proc 驱动，不 spawn）；
- PiBridge._on_provider_observation → 路由健康表回写（G2 闭合；
  归属锚=回读确认模型，未确认不冒认）；
- classify_retry_error_text 有界文本分类；
- stream_prompt 泵：auto_retry 事件 → 健康表兜底回写（结构化通道）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.services.chat.pi_rpc_client import parse_provider_observation
from app.services.chat.model_runtime.health import get_llm_provider_health


# ── parse_provider_observation ──────────────────────────────────────


def test_parse_valid_line_with_retry_after():
    obs = parse_provider_observation(
        "[webgis-tools][provider-obs] status=429 retry_after=2.5")
    assert obs == {"status": 429, "retry_after": 2.5}


def test_parse_valid_line_missing_retry_after():
    obs = parse_provider_observation(
        "[webgis-tools][provider-obs] status=503 retry_after=-")
    assert obs == {"status": 503, "retry_after": None}


def test_parse_accepts_bytes_and_ansi():
    obs = parse_provider_observation(
        b"\x1b[31m[webgis-tools][provider-obs] status=500\x1b[0m")
    assert obs == {"status": 500, "retry_after": None}


def test_parse_rejects_success_and_garbage():
    assert parse_provider_observation("[webgis-tools][provider-obs] status=200") is None
    assert parse_provider_observation("[webgis-tools] provider_response status=429") is None
    assert parse_provider_observation("Error: something else") is None
    assert parse_provider_observation("") is None


def test_parse_rejects_out_of_range_status_and_retry_after():
    assert parse_provider_observation(
        "[webgis-tools][provider-obs] status=600") is None
    assert parse_provider_observation(
        "[webgis-tools][provider-obs] status=399") is None
    # retry-after 越界（>1h）→ 丢弃该字段但保留 status
    obs = parse_provider_observation(
        "[webgis-tools][provider-obs] status=429 retry_after=7200")
    assert obs == {"status": 429, "retry_after": None}


# ── stderr 读循环 → sink（假 proc，不 spawn） ────────────────────────


class _FakeStderr:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else b""


@pytest.mark.asyncio
async def test_stderr_reader_fires_provider_sink():
    from app.services.chat.pi_rpc_client import PiRpcClient

    client = PiRpcClient()
    seen = []
    client.set_provider_observation_sink(seen.append)
    proc = SimpleNamespace(stderr=_FakeStderr([
        b"[webgis-tools][provider-obs] status=429 retry_after=1\n",
        b"Warning: unrelated diagnostic\n",
        b"[webgis-tools][provider-obs] status=503 retry_after=-\n",
    ]))
    await asyncio.wait_for(client._read_stderr(proc), timeout=5)
    assert [o["status"] for o in seen] == [429, 503]
    # 无 sink 时读循环不受影响（诊断环照常）
    client2 = PiRpcClient()
    proc2 = SimpleNamespace(stderr=_FakeStderr(
        [b"[webgis-tools][provider-obs] status=500\n"]))
    await asyncio.wait_for(client2._read_stderr(proc2), timeout=5)


# ── 桥接：观测 → 健康表 ─────────────────────────────────────────────


class _SinklessRpc:
    """最小 rpc 桩：接受 sink 注册、暴露 spawn_model_id。"""

    def __init__(self):
        self.sinks = []
        self.spawn_model_id = "m-spawn"

    def set_provider_observation_sink(self, sink):
        self.sinks.append(sink)

    def set_ui_request_sink(self, sink):
        pass


@pytest.fixture
def clean_health():
    health = get_llm_provider_health()
    health.reset()
    yield health
    health.reset()


def _bridge(rpc):
    from app.agent_pi_bridge import PiBridge

    return PiBridge(rpc=rpc)


def test_bridge_observation_records_failure_with_confirmed_model(clean_health):
    rpc = _SinklessRpc()
    bridge = _bridge(rpc)
    assert bridge._last_confirmed_model == ""
    # 未确认（空）→ 按 spawn 基模型归属
    bridge._on_provider_observation({"status": 429, "retry_after": 2.0})
    snap = clean_health.snapshot()
    assert snap["webgis/m-spawn"]["rate_limited_recently"] is True

    # 回读确认后 → 按确认模型归属
    bridge._last_confirmed_provider = "webgis"
    bridge._last_confirmed_model = "m-confirmed"
    bridge._on_provider_observation({"status": 503, "retry_after": None})
    snap = clean_health.snapshot()
    assert "webgis/m-confirmed" in snap


def test_bridge_observation_ignores_invalid_and_unattributable(clean_health):
    rpc = _SinklessRpc()
    bridge = _bridge(rpc)
    rpc.spawn_model_id = ""
    bridge._on_provider_observation({"status": 429, "retry_after": None})  # 无归属
    bridge._on_provider_observation({"status": 200, "retry_after": None})  # 越界
    assert clean_health.snapshot() == {}


def test_classify_retry_error_text_vocab():
    from app.agent_pi_bridge import PiBridge

    assert PiBridge.classify_retry_error_text("HTTP 429 too many requests") == "rate_limit"
    assert PiBridge.classify_retry_error_text("503 service unavailable") == "provider_unavailable"
    assert PiBridge.classify_retry_error_text("socket hang up (ECONNRESET)") == "timeout"
    assert PiBridge.classify_retry_error_text("weird provider text") == "unknown"


@pytest.mark.asyncio
async def test_retry_health_observe_records(clean_health):
    rpc = _SinklessRpc()
    bridge = _bridge(rpc)
    bridge._observe_retry_health(model="m-x", failure_kind="rate_limit")
    snap = clean_health.snapshot()
    assert snap["webgis/m-x"]["rate_limited_recently"] is True
    bridge._observe_retry_success("m-x")
    # success 清除失败计数（recent_rate_limit 位不在 record_success 清除，
    # 但 consecutive/cooldown 清零 —— 用 total_success 证明成功已记）
    snap2 = clean_health.snapshot()
    assert snap2["webgis/m-x"]["total_success"] >= 1
    assert snap2["webgis/m-x"]["consecutive_failures"] == 0


# ── stream 泵：auto_retry 事件 → 健康表（结构化通道） ────────────────


@pytest_asyncio.fixture
async def db_factory(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import database as db_mod
    from app.models.db_model import Base

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'obs.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=eng, expire_on_commit=False)

    import contextlib

    @contextlib.asynccontextmanager
    async def _patched():
        async with fac() as db:
            yield db

    monkeypatch.setattr(db_mod, "async_db_session", _patched)
    yield fac
    await eng.dispose()


def _catalog_env(monkeypatch, *, base="m-base"):
    monkeypatch.setattr(
        "app.services.chat.model_config._runtime_overrides", {}, raising=False)
    monkeypatch.setattr("app.core.config.settings.LLM_MODEL", base, raising=False)
    monkeypatch.setattr(
        "app.core.config.settings.SESSION_MODEL_CATALOG_EXTRA", "", raising=False)
    monkeypatch.setattr("app.core.config.settings.LLM_API_KEY", "k-test", raising=False)


def _make_agent_settled():
    return {"type": "agent_settled", "aborted": False}


def _make_auto_retry_start(attempt=1, error="HTTP 429 too many requests"):
    return {
        "type": "auto_retry_start", "attempt": attempt, "maxAttempts": 3,
        "delayMs": 100, "errorMessage": error,
    }


def _make_auto_retry_end(success=True, attempt=1):
    return {"type": "auto_retry_end", "success": success, "attempt": attempt}


@pytest.mark.asyncio
async def test_stream_auto_retry_events_feed_health(db_factory, monkeypatch, clean_health):
    _catalog_env(monkeypatch)
    rpc = SimpleNamespace(
        calls=[], spawn_epoch=1, runtime_epoch="e", process_died=False,
        spawn_model_id="m-base",
        events=asyncio.Queue(),
    )

    async def _request(cmd, data=None):
        rpc.calls.append((cmd, dict(data or {})))
        if cmd == "prompt":
            await rpc.events.put(_make_auto_retry_start(error="HTTP 429 rate limit"))
            await rpc.events.put(_make_auto_retry_end(success=True))
            await rpc.events.put(_make_agent_settled())
            return {}
        if cmd == "get_state":
            return {"model": {"id": "m-base", "provider": "webgis"}, "thinkingLevel": "off"}
        return {}

    rpc.request = _request
    from app.agent_pi_bridge import PiBridge

    bridge = PiBridge(rpc=rpc)
    events = []
    async for ev in bridge.stream_prompt("hi", session_id="conv-obs"):
        events.append(ev)
    assert events[-1].startswith("event: done")
    snap = clean_health.snapshot()
    # 429 → 模型键进健康表（recent_rate_limit 位随后续语义）；重试成功已回写
    assert "webgis/m-base" in snap
