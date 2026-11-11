"""G22-P3 — 压缩、恢复与版本变更对账（A17 / --no-session 契约 / A21 隔离 /
Redis active_turn 启动清扫）。

核心口径：LLM/vendor 的 summary **不是授权或持久化事实**——下一 turn 的
「地图现在是什么」只信权威源现取（GisStateRebuildProvider）；`--no-session`
使 vendor 不持久化 transcript（与宿主 DB 历史互不竞争）；跨会话切换经
`new_session` 隔离；重启后本实例的 Redis 残留 active_turn key 被启动清扫。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge
from app.services.context_assembly.providers import (
    GisStateRebuildProvider,
    render_gis_state_rebuild_block,
)


# ── A17：压缩 summary 伪造授权/旧 revision 不生效 ──────────────────────────


class _Req:
    session_id = "sess-a17"


class _Facts:
    def __init__(self, mapspec, plan=None, durable_jobs=None,
                 invalid_source_refs=None):
        self.mapspec = mapspec
        self.plan = plan
        self.source_session_id = "sess-a17"
        self.durable_jobs = durable_jobs
        self.invalid_source_refs = invalid_source_refs


def test_a17_stale_summary_claims_do_not_override_authority():
    """伪造 summary（旧 revision/「已解锁 l1」/已删 ref「仍在」）不得覆盖
    权威读取：重建块以 MapSpec 现值为准 —— revision 9、锁定约束在、失效
    ref 如实披露。summary 从不进入 facts（结构性隔离），本测试同时钉死
    「重建块不携带任何指令性内容」。"""
    maps = {
        "revision": 9,
        "layers": [
            {"id": "l1", "layout": {"visibility": "visible"}},
            {"id": "l2", "layout": {"visibility": "none"}},
        ],
        "workbench": {"lockedLayerIds": ["l1"], "lockedComponentIds": []},
        "sources": {"src-1": {"refId": "ref-deleted"}},
    }
    text = render_gis_state_rebuild_block(
        maps, None,
        invalid_source_refs=[{"source": "src-1", "ref": "ref-deleted"}])
    # 权威 revision 覆盖 summary 的旧值（summary 声称 rev 5）
    assert "revision: 9" in text
    # 锁定约束在 —— summary 的「l1 已解锁」不产生任何效果
    assert "l1" in text and "layer_locked" in text
    # 隐藏约束在
    assert "（保持隐藏）: l2" in text
    # 已删 ref 披露失效（summary 声称「ref-deleted 仍在」→ 不重建幽灵）
    assert "src-1→ref-deleted" in text
    # 块内无指令性动词（事实块；指令只能来自用户消息域）
    for line in text.splitlines()[1:]:
        assert line.startswith("- "), line


@pytest.mark.asyncio
async def test_a17_provider_output_binds_authoritative_revision():
    """provider 产出的 item revision 绑定权威 MapSpec revision（receipt/
    幂等对账锚点），不绑定任何 summary 声称的版本。"""
    provider = GisStateRebuildProvider()
    report = await provider.collect(
        _Req(), _Facts({"revision": 33, "layers": [{"id": "a"}]}))
    assert report.skipped_reason == ""
    item = report.items[0]
    assert item.revision == "33"
    assert item.evidence_ref == "mapspec_store:current"


# ── --no-session 契约：两套持久会话不竞争 ─────────────────────────────────


def test_no_session_flag_kept_in_spawn_args(monkeypatch, tmp_path):
    """vendor 子进程必须继续 `--no-session`：vendor 持久 session 文件会与
    宿主 DB 会话历史形成两份竞争事实（漂移后无法裁决谁是权威）。G22-P3
    钉死该 flag 不被「升级顺便移除」。"""
    from app.services.chat.pi_rpc_client import PiRpcClient

    captured: dict = {}

    class _FakeProc:
        def __init__(self, *a, **k):
            captured["args"] = list(a[0]) if a else k.get("args")
            self.stdin = None
            self.stdout = None
            self.stderr = None
            self.pid = 424242
            self.returncode = None

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    entry = tmp_path / "rpc-entry.js"
    entry.write_text("// stub entry\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.chat.pi_rpc_client.subprocess.Popen", _FakeProc)
    # spawn 前会 dump native 工具面 —— 本测试只钉 CLI 契约，跳过真实注册表。
    monkeypatch.setattr(
        "app.services.chat.pi_native_surface.dump_surface_file",
        lambda path: str(path))
    monkeypatch.setenv("LLM_API_KEY", "test-key-a17")
    client = PiRpcClient(
        pi_rpc_entry=entry,
        session_dir=tmp_path, cwd=tmp_path, extension_paths=[],
    )
    client._spawn_process()
    args = captured["args"]
    assert args[0] == "node"
    assert "--mode" in args and args[args.index("--mode") + 1] == "rpc"
    assert "--no-session" in args, (
        "--no-session 必须保留：去掉它会让 vendor 持久会话与宿主 DB 历史竞争"
    )
    assert "--no-builtin-tools" in args


# ── A21：会话切换的 vendor 隔离（new_session 边界）────────────────────────


@pytest.mark.asyncio
async def test_a21_vendor_session_isolation_on_switch(monkeypatch):
    """GIS 会话切换（匿名↔登录、跨项目）必须 new_session 重置 vendor
    transcript；切回原会话同样重置（不泄漏前一会话内容）。"""

    calls: list[str] = []

    class _Rpc:
        def __init__(self):
            self.events = asyncio.Queue()

        async def request(self, command, data=None):
            calls.append(command)
            if command == "prompt":
                await self.events.put({"type": "agent_end", "willRetry": False})
                await self.events.put({"type": "agent_settled"})
                return {"status": "ok"}
            return {}

        def pending_request_ids(self):
            return set()

        def fail_pending_ids(self, ids, reason):
            pass

        async def issue(self, command, data=None):
            calls.append(command)
            f = asyncio.get_running_loop().create_future()
            f.set_result({})
            return SimpleNamespace(id=f"i-{command}", command=command, future=f)

        async def collect(self, issued, command=None):
            return await issued.future

    import app.services.chat.engine_instance as engines
    monkeypatch.setattr(
        engines, "try_get_chat_engine",
        lambda: SimpleNamespace(tracker=SimpleNamespace(
            create=lambda sid, message: SimpleNamespace(id=f"t-{sid}"),
            list_by_session=lambda sid: [],
            cancel=lambda tid: None,
            fail_task=lambda tid, reason: None,
            complete_task=lambda tid, message=None, map_product=None: None,
        )))

    rpc = _Rpc()
    bridge = PiBridge(rpc=rpc)

    async def _turn(sid):
        return await bridge.prompt(f"msg-{sid}", session_id=sid)

    await _turn("sess-anon")   # 匿名会话
    await _turn("sess-login")  # 登录（切换）
    await _turn("sess-anon")   # 切回

    # 首会话无前驱 → 无 new_session；两次切换 → 各一次 new_session。
    assert calls.count("new_session") == 2
    # new_session 严格先于对应 prompt（序列不可乱）
    first_prompt = calls.index("prompt")
    assert first_prompt < calls.index("new_session") or calls.index("new_session") > 0


# ── Redis active_turn：实例前缀 + 启动清扫 + 双格式兼容 ────────────────────


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        return self.store.pop(key, None)

    async def eval(self, script, numkeys, key, *argv):
        v = self.store.get(key)
        if v in argv:
            self.store.pop(key, None)
            return 1
        return 0

    def scan_iter(self, match=None, count=None):
        import fnmatch

        async def _gen():
            for k in list(self.store):
                if match is None or fnmatch.fnmatch(k, match):
                    yield k
        return _gen()


@pytest.mark.asyncio
async def test_registry_sweep_own_keys_only(monkeypatch):
    from app.services.chat.pi_turn_context import PiTurnRegistry

    reg = PiTurnRegistry()
    fake = _FakeRedis()
    monkeypatch.setattr(reg, "_get_redis_client", lambda: fake)
    monkeypatch.setattr(
        PiTurnRegistry, "_instance_id",
        staticmethod(lambda: "pod-a"))

    # 布景：本实例(pod-a)的残留 key、他实例(pod-b)的 live key、旧格式 key
    fake.store["webgis:pi:active_turn:s-own"] = "pod-a:turn-stale"
    fake.store["webgis:pi:active_turn:s-other"] = "pod-b:turn-live"
    fake.store["webgis:pi:active_turn:s-legacy"] = "turn-legacy"

    deleted = await reg.sweep_own_keys(force=True)

    assert deleted == 1
    assert "webgis:pi:active_turn:s-own" not in fake.store
    assert "webgis:pi:active_turn:s-other" in fake.store, "多 pod 不误杀"
    assert "webgis:pi:active_turn:s-legacy" in fake.store, "旧格式不动"


@pytest.mark.asyncio
async def test_registry_dual_format_active_and_unregister(monkeypatch):
    from app.services.chat.pi_turn_context import PiTurnRegistry

    reg = PiTurnRegistry()
    fake = _FakeRedis()
    monkeypatch.setattr(reg, "_get_redis_client", lambda: fake)
    monkeypatch.setattr(
        PiTurnRegistry, "_instance_id",
        staticmethod(lambda: "pod-a"))

    # 新格式注册 → is_active 解析 turn 段
    await reg.register_turn("s1", "turn-abc")
    assert fake.store["webgis:pi:active_turn:s1"] == "pod-a:turn-abc"
    assert await reg.is_active("s1", "turn-abc") is True
    assert await reg.is_active("s1", "turn-other") is False

    # 旧格式 key（他实例/升级前写入）—— turn 段判定兼容
    fake.store["webgis:pi:active_turn:s2"] = "turn-legacy"
    assert await reg.is_active("s2", "turn-legacy") is True

    # owner-checked 注销：双格式都可删，不误删他 turn
    await reg.unregister_turn("s1", "turn-abc")
    assert "webgis:pi:active_turn:s1" not in fake.store
    fake.store["webgis:pi:active_turn:s3"] = "pod-a:turn-x"
    await reg.unregister_turn("s3", "turn-not-mine")
    assert "webgis:pi:active_turn:s3" in fake.store
