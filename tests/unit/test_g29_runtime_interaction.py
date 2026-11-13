"""G29 单元层：原生 UI 协议 / 封套 / 交互生命周期 / 单向写出。

覆盖（WP02/WP03/WP04/WP05/WP07/WP11 的无子进程面）：

- pi_ui_protocol：解析/互斥/界；未知方法/坏 id 拒绝。
- PiRpcClient.send_ui_response：单向写出（不登记 pending future）、进程
  不在 → PiRpcError；issue 写锁共存（不撕裂 JSON 行 —— 以锁串行验证）。
- pi_interaction_envelope：签名/篡改/过期；选项表精确反解（不做宽松匹配）；
  可得证据分类（timed_out/cancelled vs 用户确认「否」）。
- RuntimeInteractionService：归属登记、双标签页幂等、白名单拒绝、过期、
  失效、等待记账、SSE 投影事件、pending 上限。
- webgis_user_decision / webgis_layer_upsert 裸名分支（catalog stub）。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.chat.pi_ui_protocol import (
    UiProtocolError,
    build_ui_response,
    parse_ui_request,
)
from app.services.chat.pi_runtime_interaction import (
    InteractionState,
    RuntimeInteractionService,
    reset_interaction_service,
)


# ── 协议层 ──────────────────────────────────────────────────────────────


def test_parse_select_request():
    req = parse_ui_request({
        "type": "extension_ui_request", "id": "uuid-1", "method": "select",
        "title": "选择数据版本", "options": ["A（2020）", "B（2024）"],
        "timeout": 10000,
    })
    assert req.is_dialog and req.method == "select"
    assert req.options == ["A（2020）", "B（2024）"]
    assert req.native_timeout_ms == 10000
    assert req.dialog_default_value() is None


def test_parse_editor_has_no_native_timeout():
    req = parse_ui_request({
        "type": "extension_ui_request", "id": "uuid-4", "method": "editor",
        "title": "编辑标题", "prefill": "草案\n第二行",
    })
    assert req.method == "editor"
    assert req.native_timeout_ms is None
    assert req.dialog_default_value() is None


def test_parse_confirm_default_is_false():
    req = parse_ui_request({
        "type": "extension_ui_request", "id": "u", "method": "confirm",
        "title": "确认", "message": "继续？",
    })
    assert req.dialog_default_value() is False


def test_parse_fire_and_forget_notify():
    req = parse_ui_request({
        "type": "extension_ui_request", "id": "u2", "method": "notify",
        "message": "done", "notifyType": "warning",
    })
    assert not req.is_dialog
    assert req.notify_type == "warning"


@pytest.mark.parametrize("payload,code", [
    ({"type": "extension_ui_request", "id": "u", "method": "multi_select"}, "UI_UNKNOWN_METHOD"),
    ({"type": "extension_ui_request", "method": "select", "options": ["a"]}, "UI_BAD_ID"),
    ({"type": "extension_ui_request", "id": "u", "method": "select", "options": []}, "UI_BAD_OPTIONS"),
    ({"type": "extension_ui_request", "id": "u", "method": "select",
      "options": [f"opt{i}" for i in range(99)]}, "UI_TOO_MANY_OPTIONS"),
    ({"type": "other", "id": "u", "method": "select", "options": ["a"]}, "UI_WRONG_TYPE"),
])
def test_parse_rejects_bad_payloads(payload, code):
    with pytest.raises(UiProtocolError) as ei:
        parse_ui_request(payload)
    assert ei.value.code == code


def test_build_ui_response_mutex():
    with pytest.raises(UiProtocolError):
        build_ui_response("u", value="a", confirmed=True)
    with pytest.raises(UiProtocolError):
        build_ui_response("u")  # 三态全空
    ok = build_ui_response("u", cancelled=True)
    assert ok == {"type": "extension_ui_response", "id": "u", "cancelled": True}
    ok2 = build_ui_response("u", confirmed=False)
    assert ok2["confirmed"] is False


# ── 单向写出（不登记 pending future） ──────────────────────────────────


@pytest.mark.asyncio
async def test_send_ui_response_is_one_way():
    from app.services.chat.pi_rpc_client import PiRpcClient, PiRpcError

    client = PiRpcClient()
    proc = MagicMock()
    proc.stdin = MagicMock()
    client._process = proc

    await client.send_ui_response(build_ui_response("uuid-9", value="A"))
    assert client._pending_requests == {}, "单向回答绝不能登记 pending future"
    written = proc.stdin.write.call_args[0][0]
    payload = json.loads(written if isinstance(written, str) else written.decode())
    assert payload == {"type": "extension_ui_response", "id": "uuid-9", "value": "A"}

    client._process = None
    with pytest.raises(PiRpcError):
        await client.send_ui_response(build_ui_response("x", cancelled=True))


@pytest.mark.asyncio
async def test_ui_request_sink_routes_and_enriches():
    """reader sink 分支：extension_ui_request 进 sink（不进旧两分支）。"""
    from app.services.chat.pi_rpc_client import PiRpcClient

    client = PiRpcClient()
    service = reset_interaction_service()
    bridge = SimpleNamespace(_current_turn=None)
    client.set_ui_request_sink(service.make_sink(client, bridge))

    # 无 turn 归属 → orphan 登记（仍展示，submit 会因 turn 校验拒绝）。
    client._ui_request_sink({
        "type": "extension_ui_request", "id": "n1", "method": "select",
        "title": "Q", "options": ["a", "b"],
    })
    assert service._metrics["orphan"] == 1
    # 合成事件已投递（供 SSE mapper 翻译）。
    event = client.events.get_nowait()
    assert event["type"] == "extension_ui_request"
    payload = event["_webgis_interaction"]
    assert payload["method"] == "select"
    assert payload["options"] == ["a", "b"]
    assert payload["interaction_id"].startswith("iax-")


# ── 封套 ────────────────────────────────────────────────────────────────


def _mint(service, **kw):
    """铸造并返回 (token, awaiting_entry) —— entry 模拟 native 配对后记录携带的封套全文。"""
    from app.services.chat import pi_interaction_envelope as env

    defaults = dict(
        tool_name="webgis_layer_upsert", kind="select", title="选数据",
        options=[{"display": "人口（2020）", "value": "ref:a"},
                 {"display": "人口（2024）", "value": "ref:b"}],
        session_id="sess-1", turn_id="turn-1", tool_call_id="tc-1",
    )
    defaults.update(kw)
    with patch.object(env, "current_tool_context", lambda: ("", "", "")):
        token, error = env.mint_envelope(**defaults)
    assert error is None, error
    queue = service._awaiting_envelopes.get(("sess-1", "turn-1"), [])
    entry = dict(queue[-1]) if queue else {}
    return token, entry


@pytest.fixture()
def fresh_service():
    return reset_interaction_service()


_requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="cross-language envelope test needs node"
)


@_requires_node
def test_envelope_signature_matches_node_verifier(fresh_service):
    """跨语言签名一致性（review P2-3）：Python 铸造 → Node verifyEnvelope 验签。

    直接以子进程加载生产扩展模块（app/extensions/webgis-tools/index.mjs 的
    export），同一 bridge secret 下逐字段（含中文/嵌套 business/选项表）必须
    双向一致 —— canonical JSON 递归键序 + 内层 sha256 + 外层 HMAC 的完整管线。
    """
    import subprocess as _sp

    from app.core.bridge_secret import get_bridge_secret
    from app.services.chat import pi_interaction_envelope as _env

    token, err = _env.mint_envelope(
        tool_name="webgis_layer_upsert",
        kind="select",
        title="选择数据版本：人口数据（同名歧义）",
        message="两个合法版本，范围/日期不同",
        options=[
            {"display": "人口数据（版本日期 2020-05-01；范围 [104.00,30.60,104.05,30.61]）", "value": "ref:geojson-aaaa"},
            {"display": "人口数据（版本日期 2024-05-01；范围 [110.00,30.60,110.07,30.61]）", "value": "ref:geojson-bbbb"},
        ],
        timeout_ms=600000,
        business={"target": "dataset_choice", "dataset_name": "人口数据"},
        session_id="sess-xlang",
        turn_id="turn-xlang",
        tool_call_id="tc-xlang",
    )
    assert err is None

    script = (
        "import('file://" + str(Path(__file__).resolve().parents[2] / "app" / "extensions" / "webgis-tools" / "index.mjs")
        + "').then((m) => {"
        "  const fs = require('node:fs');"
        "  const env = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));"
        "  process.stdout.write(m.verifyEnvelope(env) ? 'OK' : 'FAIL');"
        "}).catch((e) => { process.stderr.write(String(e)); process.exit(2); });"
    )
    import tempfile as _tf
    import os as _os

    with _tf.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(token, fh, ensure_ascii=False)
        token_path = fh.name
    try:
        proc = _sp.run(
            ["node", "-e", script, token_path],
            capture_output=True, text=True, timeout=30,
            env={**_os.environ, "WEBGIS_BRIDGE_SECRET": get_bridge_secret()},
        )
        assert proc.returncode == 0, proc.stderr[:300]
        assert proc.stdout.strip() == "OK", (
            f"Node verifyEnvelope rejected server-minted envelope: {proc.stdout!r} {proc.stderr[:300]}"
        )
        # 篡改负例：改一个嵌套 business 值后 Node 必须拒绝。
        tampered = dict(token)
        tampered["business"] = {**token["business"], "dataset_name": "伪造"}
        with open(token_path, "w", encoding="utf-8") as fh:
            json.dump(tampered, fh, ensure_ascii=False)
        proc2 = _sp.run(
            ["node", "-e", script, token_path],
            capture_output=True, text=True, timeout=30,
            env={**_os.environ, "WEBGIS_BRIDGE_SECRET": get_bridge_secret()},
        )
        assert proc2.stdout.strip() == "FAIL", "tampered envelope must fail Node verification"
    finally:
        _os.unlink(token_path)


def test_envelope_sign_verify_roundtrip(fresh_service):
    from app.services.chat.pi_interaction_envelope import verify_envelope

    token, _entry = _mint(fresh_service)
    assert verify_envelope(token) is not None
    tampered = dict(token)
    tampered["options"] = ["人口（2020）", "人口（2024）", "人口（2030）"]
    assert verify_envelope(tampered) is None, "篡改载荷必须过不了签名"
    expired = dict(token)
    expired["exp"] = int(time.time()) - 10
    expired["sig"] = token["sig"]
    assert verify_envelope(expired) is None


def test_envelope_turn_cap(fresh_service):
    from app.services.chat.pi_interaction_envelope import mint_envelope

    with patch(
        "app.services.chat.pi_interaction_envelope.current_tool_context",
        lambda: ("sess-1", "turn-cap", "tc"),
    ):
        t1, e1 = mint_envelope(tool_name="t", kind="confirm", title="q1")
        t2, e2 = mint_envelope(tool_name="t", kind="confirm", title="q2")
        t3, e3 = mint_envelope(tool_name="t", kind="confirm", title="q3")
    assert e1 is None and e2 is None
    assert e3 == "ENVELOPE_TURN_CAP"


def test_consume_answer_reverse_lookup_strict(fresh_service):
    from app.services.chat.pi_interaction_envelope import consume_answer

    token, entry = _mint(fresh_service)
    outcome = consume_answer(
        session_id="sess-1", turn_id="turn-1", tool_call_id="tc-1",
        envelope_id=token["envelope_id"], native_id="n1",
        answer_kind="value", display="人口（2020）",
        envelope={**entry, "interaction_id": ""},
    )
    assert outcome["ok"]
    assert outcome["domain"]["value"] == "ref:a"
    # 不在选项表内的回答 —— 精确拒绝，绝不宽松匹配。
    outcome2 = consume_answer(
        session_id="sess-1", turn_id="turn-1", tool_call_id="tc-1",
        envelope_id=token["envelope_id"], native_id="n1",
        answer_kind="value", display="人口",
        envelope={**entry, "interaction_id": ""},
    )
    assert outcome2["ok"] is False and outcome2["code"] == "ANSWER_NOT_IN_OPTIONS"
    # 记录已回收（无封套全文）—— NOT_PENDING（一次性消费语义）。
    outcome3 = consume_answer(
        session_id="sess-1", turn_id="turn-1", tool_call_id="tc-1",
        envelope_id=token["envelope_id"], native_id="n1",
        answer_kind="value", display="人口（2020）",
        envelope=None,
    )
    assert outcome3["ok"] is False and outcome3["code"] == "ENVELOPE_NOT_PENDING"


def test_consume_answer_timeout_vs_user_no(fresh_service):
    """vendor confirm 超时默认 false ≠ 用户明确选「否」—— 按宿主记录分类。"""
    from app.services.chat.pi_interaction_envelope import consume_answer

    with patch(
        "app.services.chat.pi_interaction_envelope.current_tool_context",
        lambda: ("s", "t", "tc"),
    ):
        from app.services.chat.pi_interaction_envelope import mint_envelope

        token, err = mint_envelope(tool_name="t", kind="confirm", title="确认草案")
    assert err is None
    entry_a = dict(fresh_service._awaiting_envelopes.get(("s", "t"), [{}])[-1])
    # 场景 A：宿主记录 timed_out（用户没提交）→ 超时语义，不采用 false。
    out = consume_answer(
        session_id="s", turn_id="t", tool_call_id="tc",
        envelope_id=token["envelope_id"], native_id="n",
        answer_kind="confirmed", display="false",
        interaction_state="timed_out", envelope={**entry_a, "interaction_id": ""},
    )
    assert out["code"] == "TIMED_OUT" and "未采用" in out["content"]
    # 场景 B：宿主记录 answer_written（用户真的提交了）→ 用户决定语义。
    with patch(
        "app.services.chat.pi_interaction_envelope.current_tool_context",
        lambda: ("s", "t2", "tc"),
    ):
        token2, _ = mint_envelope(tool_name="t", kind="confirm", title="确认草案")
    entry_b = dict(fresh_service._awaiting_envelopes.get(("s", "t2"), [{}])[-1])
    out2 = consume_answer(
        session_id="s", turn_id="t2", tool_call_id="tc",
        envelope_id=token2["envelope_id"], native_id="n",
        answer_kind="confirmed", display="false",
        interaction_state="answer_written", envelope={**entry_b, "interaction_id": ""},
    )
    assert out2["code"] == "ANSWERED" and out2["domain"]["confirmed"] is False


# ── 生命周期 ────────────────────────────────────────────────────────────


class _FakeRpc:
    def __init__(self):
        self.events_queue = asyncio.Queue()
        self.sent = []
        self.alive = True

    @property
    def events(self):
        return self.events_queue

    def is_alive(self):
        return self.alive

    async def send_ui_response(self, response):
        if not self.alive:
            raise RuntimeError("pipe gone")
        self.sent.append(response)


def _make_record(service: RuntimeInteractionService, rpc=None, *, native_timeout_ms=None):
    """经 sink 登记一条带 turn 归属的 dialog 记录并返回 (record, rpc, bridge)。"""
    rpc = rpc or _FakeRpc()
    entry = SimpleNamespace(session_id="sess-A", turn_id="turn-A", bridge=None)
    bridge = SimpleNamespace(_current_turn=entry)
    sink = service.make_sink(rpc, bridge)
    payload = {
        "type": "extension_ui_request", "id": "nat-1", "method": "select",
        "title": "同名数据", "options": ["人口（2020）", "人口（2024）"],
    }
    if native_timeout_ms:
        payload["timeout"] = native_timeout_ms
    sink(payload)
    record = next(
        r for r in service._by_id.values() if r.native_id == "nat-1"
    )
    record.rpc = rpc
    return record, rpc, bridge, entry


def _resolver_with(entry, rpc):
    from app.agent_pi_bridge import _ActiveTurnEntry  # noqa: F401 — 类型一致性说明
    def resolver(record):
        current = entry
        if current is None or current.turn_id != record.turn_id:
            return None, False
        return (rpc, rpc.is_alive()) if rpc.is_alive() else (None, False)
    return resolver


@pytest.mark.asyncio
async def test_submit_answer_once_then_idempotent(fresh_service):
    record, rpc, bridge, entry = _make_record(fresh_service)
    resolver = _resolver_with(entry, rpc)

    out1 = await fresh_service.submit_answer(
        "sess-A", record.interaction_id, {"value": "人口（2024）"}, resolver,
    )
    assert out1["result"] == "accepted"
    assert out1["state"] == "answer_written"
    assert rpc.sent == [{"type": "extension_ui_response", "id": "nat-1", "value": "人口（2024）"}]

    # 第二个标签页重复提交（不同答案）→ 幂等返回，不再写 stdin。
    out2 = await fresh_service.submit_answer(
        "sess-A", record.interaction_id, {"value": "人口（2020）"}, resolver,
    )
    assert out2["result"] == "already_answered"
    assert len(rpc.sent) == 1

    # 观察到当前任务继续 → resolved（一次）。
    fresh_service.observe_turn_activity("turn-A", "event:tool_execution_end")
    assert record.state is InteractionState.RESOLVED
    assert record.resolve_evidence.startswith("event:")


@pytest.mark.asyncio
async def test_submit_answer_whitelist_rejects_foreign_value(fresh_service):
    record, rpc, bridge, entry = _make_record(fresh_service)
    out = await fresh_service.submit_answer(
        "sess-A", record.interaction_id, {"value": "未知选项"}, _resolver_with(entry, rpc),
    )
    assert out["result"] == "rejected" and out["reason"] == "ANSWER_INVALID"
    assert rpc.sent == [], "白名单外的值绝不写出"
    assert record.state is InteractionState.PENDING, "拒绝不污染状态"


@pytest.mark.asyncio
async def test_submit_answer_cross_session_rejected(fresh_service):
    record, rpc, bridge, entry = _make_record(fresh_service)
    out = await fresh_service.submit_answer(
        "sess-OTHER", record.interaction_id, {"value": "人口（2020）"},
        _resolver_with(entry, rpc),
    )
    assert out["result"] == "rejected" and out["reason"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_worker_gone_invalidates(fresh_service):
    record, rpc, bridge, entry = _make_record(fresh_service)
    rpc.alive = False
    out = await fresh_service.submit_answer(
        "sess-A", record.interaction_id, {"value": "人口（2020）"}, _resolver_with(entry, rpc),
    )
    assert out["result"] == "rejected" and out["reason"] == "WORKER_GONE"
    assert record.state is InteractionState.INVALIDATED


@pytest.mark.asyncio
async def test_native_timeout_marks_timed_out_without_write(fresh_service):
    record, rpc, bridge, entry = _make_record(fresh_service, native_timeout_ms=3000)
    # 推进时钟越过 deadline + 宽限。
    record.deadline_monotonic = time.monotonic() - 1
    expired = fresh_service.expire_due("sess-A")
    assert record.state is InteractionState.TIMED_OUT
    assert record.resolve_evidence == "native-timeout-default"
    assert expired and expired[0]["state"] == "timed_out"
    # 迟到回答：幂等返回 timed_out，绝不写 stdin（vendor 已自收口）。
    out = await fresh_service.submit_answer(
        "sess-A", record.interaction_id, {"value": "人口（2020）"}, _resolver_with(entry, rpc),
    )
    assert out["result"] == "duplicate" and out["state"] == "timed_out"
    assert rpc.sent == []


@pytest.mark.asyncio
async def test_invalidate_turn_kills_pending(fresh_service):
    record, rpc, bridge, entry = _make_record(fresh_service)
    count = fresh_service.invalidate_turn("sess-A", "turn-A", "turn-teardown")
    assert count == 1
    assert record.state is InteractionState.INVALIDATED
    out = await fresh_service.submit_answer(
        "sess-A", record.interaction_id, {"value": "人口（2020）"}, _resolver_with(entry, rpc),
    )
    assert out["result"] == "duplicate" and out["state"] == "invalidated"


def test_waiting_for_user_budget_hook(fresh_service):
    record, rpc, bridge, entry = _make_record(fresh_service)
    assert fresh_service.waiting_for_user("turn-A") is True
    assert fresh_service.waiting_for_user("turn-other") is False
    fresh_service.observe_turn_activity("turn-A", "event:message_update")
    # 仍 pending（未作答）→ 等待记账继续。
    assert fresh_service.waiting_for_user("turn-A") is True


@pytest.mark.asyncio
async def test_session_pending_cap_cancels_excess(fresh_service):
    """超过每会话 pending 上限的第 4 条 native dialog：合法取消收口
    （deferred 写出经 flush 落 stdin —— sink 本身零 I/O）。"""
    sent = []
    rpc = _FakeRpc()
    rpc.sent = sent
    entry = SimpleNamespace(session_id="sess-cap", turn_id="turn-cap", bridge=None)
    bridge = SimpleNamespace(_current_turn=entry)
    sink = fresh_service.make_sink(rpc, bridge)
    for i in range(4):
        sink({
            "type": "extension_ui_request", "id": f"n{i}", "method": "confirm",
            "title": f"q{i}",
        })
    assert fresh_service._metrics["cap_rejected"] >= 1
    # 上限拒收的那条在 flush 后写出 cancelled:true（不悬挂 vendor promise）。
    flushed = await fresh_service.flush_deferred_writes()
    assert flushed >= 1
    cancelled = [r for r in sent if r.get("cancelled")]
    assert len(cancelled) >= 1


def test_fire_projection_bounded_merge(fresh_service):
    rpc = _FakeRpc()
    entry = SimpleNamespace(session_id="s-fire", turn_id="t", bridge=None)
    bridge = SimpleNamespace(_current_turn=entry)
    sink = fresh_service.make_sink(rpc, bridge)
    base = {"type": "extension_ui_request", "id": "x", "method": "notify", "message": "m"}
    sink(dict(base))
    # 1s 合并窗内的后续 notify 被折叠。
    sink(dict(base, message="m2"))
    events = [rpc.events.get_nowait() for _ in range(rpc.events.qsize())]
    notifies = [e for e in events if e["type"] == "extension_ui_fire"]
    assert len(notifies) == 1
    view = fresh_service.session_view("s-fire")
    assert view["fire"]["notifies"][0]["message"] == "m"


# ── 工具面 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_decision_tool_mints_envelope(fresh_service):
    from app.tools.registry import ToolRegistry
    from app.tools.user_decision_tools import register_user_decision_tools

    registry = ToolRegistry()
    register_user_decision_tools(registry)
    with patch(
        "app.services.chat.pi_interaction_envelope.current_tool_context",
        lambda: ("s", "t", "tc-ud"),
    ):
        outcome = await registry.dispatch(
            "webgis_user_decision",
            {
                "kind": "editor",
                "title": "核对地图标题",
                "prefill": "2020年人口分布",
                "target": "mapspec_title",
            },
            session_id="s",
        )
    result = getattr(outcome, "raw_result", outcome)
    assert result.get("success") is True
    assert "webgis_interaction_request" in result


@pytest.mark.asyncio
async def test_layer_upsert_bare_name_ambiguity_returns_envelope(fresh_service):
    """同名 ≥2 → 决策封套（Journey A 发起点）。"""
    from app.tools.cartography_tools import register_mapspec_cartography_tools
    from app.tools.registry import ToolRegistry

    def catalog_entry(i, date, ext):
        return SimpleNamespace(
            entry_id=f"ref:geojson-{i}", scope="session", name="人口数据",
            extent=ext, feature_count=100 + i, updated_at=date, created_at=date,
        )
    fake_result = SimpleNamespace(
        entries=[
            catalog_entry(1, "2020-05-01", [100.0, 30.0, 104.0, 32.0]),
            catalog_entry(2, "2024-05-01", [110.0, 30.0, 114.0, 32.0]),
        ],
        total_matched=2, truncated=False, sources_queried=[],
    )

    class _FakeCatalog:
        async def search(self, **kw):
            return fake_result

    registry = ToolRegistry()
    register_mapspec_cartography_tools(registry)
    with patch(
        "app.services.data_catalog.catalog.get_data_catalog", lambda: _FakeCatalog(),
    ), patch(
        "app.services.chat.pi_interaction_envelope.current_tool_context",
        lambda: ("s2", "t2", "tc-layer"),
    ):
        outcome = await registry.dispatch(
            "webgis_layer_upsert",
            {
                "layer": {"id": "pop", "type": "circle"},
                "source_data": "人口数据",
            },
            session_id="s2",
        )
    result = getattr(outcome, "raw_result", outcome)
    token = result.get("webgis_interaction_request")
    assert token, f"应返回决策封套：{result}"
    displays = token["options"]
    assert any("2020" in d for d in displays) and any("2024" in d for d in displays)
    assert any("110.00" in d or "100.00" in d for d in displays), "范围应入显示名以区分"


@pytest.mark.asyncio
async def test_layer_upsert_url_name_unchanged(fresh_service):
    """URL/path 形态的裸串保持既有语义（不进目录解析、不发问）。"""
    from app.tools.cartography_tools import _looks_like_url_or_path

    assert _looks_like_url_or_path("https://example.com/a.geojson")
    assert _looks_like_url_or_path("/data/local.gpkg")
    assert _looks_like_url_or_path("人口数据") is False
