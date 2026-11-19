"""V3 lifecycle：drain / 版本 pin / revoke 传播（ADR-0119 / Wave 13）。"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest


from app.extensions_platform.diagnostics import DiagnosticCode, has_errors
from app.extensions_platform.distribution import write_refresh_signal
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.tools.registry import ToolRegistry


def _worker_pack(tmp_path: Path, ext_id: str = "lc3.pack", call_timeout: float = 10.0) -> Path:
    ns, name = ext_id.split(".")
    pack = tmp_path / ext_id.replace(".", "_")
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": ext_id,
                "name": name,
                "namespace": ns,
                "version": "1.0.0",
                "api_version": "1.1.0",
                "entry_point": "main",
                "tools": [{"name": "slow", "description": "slow tool"}],
                "execution": {
                    "mode": "worker",
                    "startup_timeout_s": 15.0,
                    "call_timeout_s": call_timeout,
                },
            }
        )
    )
    (pack / "main.py").write_text(
        textwrap.dedent(
            """
            import time as _time

            from app.extensions_platform.sdk.tool import ToolExtensionSpec

            def activate(ctx):
                def slow(seconds=0):
                    if seconds:
                        _time.sleep(seconds)
                    return {"slept": seconds}
                ctx.register_tool(ToolExtensionSpec(
                    name="slow",
                    description="sleeps then returns",
                    func=slow,
                    parameters={"type": "object", "properties": {"seconds": {"type": "number"}}},
                ))
            """
        )
    )
    return pack


def _policy(tmp_path: Path, **kw) -> HostPolicy:
    return HostPolicy(
        roots=(tmp_path,),
        allow=frozenset({"lc3.pack"}),
        allow_local_untrusted_activation=True,
        **kw,
    )


def test_drain_waits_for_in_flight_then_deactivates(tmp_path):
    _worker_pack(tmp_path)
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=_policy(tmp_path))
    host.discover()
    diags = host.activate("lc3.pack")
    assert not has_errors(diags)
    import threading

    done = threading.Event()
    record = host.get_record("lc3.pack")

    def _call():
        try:
            record.worker.call("lc3_slow", {"seconds": 0.6}, timeout=5.0)
        finally:
            done.set()

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    time.sleep(0.2)  # 让 in-flight 确立
    assert record.worker.in_flight is True
    # drain=True：等 in-flight 清零后成功停用。
    deact = host.deactivate("lc3.pack", drain=True, drain_timeout_s=5.0)
    thread.join(timeout=5)
    assert not has_errors(deact), [d.message for d in deact]
    assert host.get_record("lc3.pack").state.value == "compatible"
    host.reset()


def test_drain_timeout_typed_and_refuses(tmp_path):
    _worker_pack(tmp_path)
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=_policy(tmp_path))
    host.discover()
    diags = host.activate("lc3.pack")
    assert not has_errors(diags)
    import threading

    record = host.get_record("lc3.pack")

    def _call():
        record.worker.call("lc3_slow", {"seconds": 1.2}, timeout=5.0)

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    time.sleep(0.2)
    assert record.worker.in_flight is True
    deact = host.deactivate("lc3.pack", drain=True, drain_timeout_s=0.3)
    thread.join(timeout=5)
    assert has_errors(deact)
    assert any(d.code == DiagnosticCode.DRAIN_TIMEOUT for d in deact)
    # 停用被拒：仍 active。
    assert host.get_record("lc3.pack").state.value == "active"
    host.reset()


def test_default_deactivate_still_refuses_in_flight(tmp_path):
    """默认 drain=False = V2 语义（OPERATION_IN_FLIGHT 立即拒绝）。"""
    _worker_pack(tmp_path)
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=_policy(tmp_path))
    host.discover()
    assert not has_errors(host.activate("lc3.pack"))
    import threading

    record = host.get_record("lc3.pack")

    def _call():
        record.worker.call("lc3_slow", {"seconds": 1.0}, timeout=5.0)

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    time.sleep(0.2)
    deact = host.deactivate("lc3.pack")
    thread.join(timeout=5)
    assert any(d.code == DiagnosticCode.OPERATION_IN_FLIGHT for d in deact)
    host.reset()


def test_version_pin_blocks_activation(tmp_path):
    _worker_pack(tmp_path)
    policy = _policy(tmp_path, version_pins={"lc3.pack": "9.9.9"})
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=policy)
    host.discover()
    diags = host.activate("lc3.pack")
    assert has_errors(diags)
    assert any(d.code == DiagnosticCode.VERSION_PINNED for d in diags), [
        d.to_dict() for d in diags
    ]
    host.reset()


def test_version_pin_allows_pinned_version(tmp_path):
    _worker_pack(tmp_path)
    policy = _policy(tmp_path, version_pins={"lc3.pack": "1.0.0"})
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=policy)
    host.discover()
    diags = host.activate("lc3.pack")
    assert not has_errors(diags), [d.message for d in diags]
    host.reset()


def _trust_store_file(tmp_path: Path, revoked: list[dict]) -> Path:
    path = tmp_path / "ts.json"
    path.write_text(
        json.dumps(
            {
                "publishers": {},
                "revoked": {"key_ids": [], "fingerprints": [], "packages": revoked},
            }
        )
    )
    return path


def test_refresh_revocations_quarantines_active_extension(tmp_path):
    ts = _trust_store_file(tmp_path, [])
    from app.extensions_platform.trust_store import TrustStore

    _worker_pack(tmp_path)
    trust = TrustStore.load(ts)
    host = ExtensionHost(
        tool_registry=ToolRegistry(),
        policy=_policy(tmp_path, trust_store=trust),
    )
    host.discover()
    diags = host.activate("lc3.pack")
    assert not has_errors(diags)
    # 运维吊销（trust store 文件更新 + generation 变化）。
    _trust_store_file(tmp_path, [{"id": "lc3.pack", "version": "1.0.0"}])
    # 手动失效 mtime 基线（模拟文件变化；mtime 粒度内也可能未变）。
    host._revocation_mtime = None
    quarantined = host.refresh_revocations()
    assert quarantined == ["lc3.pack"]
    record = host.get_record("lc3.pack")
    assert record.state is ExtensionState.QUARANTINED
    # 再次复查：已隔离不重复产出。
    host._revocation_mtime = None
    assert host.refresh_revocations() == []
    host.reset()


def test_refresh_revocations_noop_without_trust_store(tmp_path):
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=_policy(tmp_path))
    host.discover()
    assert host.refresh_revocations() == []
    host.reset()


def test_refresh_signal_changed_detection(tmp_path):
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=_policy(tmp_path))
    root = tmp_path / "install"
    assert host.refresh_signal_changed(root) is False  # 信号不存在
    write_refresh_signal(root, "install x")
    assert host.refresh_signal_changed(root) is True
    assert host.refresh_signal_changed(root) is False  # mtime 未变
    time.sleep(0.01)
    write_refresh_signal(root, "install y")
    assert host.refresh_signal_changed(root) is True


def test_refresh_revocations_removes_inprocess_projection(tmp_path):
    from app.extensions_platform.trust_store import TrustStore

    pack = _worker_pack(tmp_path)
    manifest_file = pack / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["execution"]["mode"] = "in_process"
    manifest_file.write_text(json.dumps(manifest))
    trust_file = _trust_store_file(tmp_path, [])
    registry = ToolRegistry()
    host = ExtensionHost(tool_registry=registry, policy=_policy(tmp_path, trust_store=TrustStore.load(trust_file)))
    try:
        host.discover()
        assert not has_errors(host.activate("lc3.pack"))
        assert registry.has("lc3_slow")
        _trust_store_file(tmp_path, [{"id": "lc3.pack", "version": "1.0.0"}])
        host._revocation_mtime = None
        assert host.refresh_revocations() == ["lc3.pack"]
        assert not registry.has("lc3_slow")
        assert host.get_record("lc3.pack").ledger is None
        assert host.get_record("lc3.pack").state is ExtensionState.QUARANTINED
    finally:
        host.reset()


# ── #2145 回归：reload/activate 吊销闸 + 并发 typed 拒绝 ──────────────────

def test_reload_refuses_quarantined_extension(tmp_path):
    """#2145：reload 走 unload→DISCOVERED→activate，旧行为会把隔离包
    （policy block）解除隔离重新激活。"""
    _worker_pack(tmp_path)
    policy = _policy(tmp_path, block=frozenset({"lc3.pack"}))
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=policy)
    host.discover()
    record = host.get_record("lc3.pack")
    assert record.state is ExtensionState.QUARANTINED
    diags = host.reload("lc3.pack")
    assert has_errors(diags)
    assert any(d.code is DiagnosticCode.TRUST_BLOCKED for d in diags), [
        d.to_dict() for d in diags
    ]
    assert host.get_record("lc3.pack").state is ExtensionState.QUARANTINED
    host.reset()


def test_reload_reruns_signature_revocation_gate(tmp_path):
    """#2145：discover 后吊销 → reload 必须重跑验签/吊销门（与 discover
    同缝），隔离并拒绝 —— 旧行为只查 allow/block，allowlist 内被吊销的
    包 reload 后复活。"""
    from app.extensions_platform.trust_store import TrustStore

    _worker_pack(tmp_path)
    ts = _trust_store_file(tmp_path, [])
    trust = TrustStore.load(ts)
    host = ExtensionHost(
        tool_registry=ToolRegistry(),
        policy=_policy(tmp_path, trust_store=trust),
    )
    host.discover()
    record = host.get_record("lc3.pack")
    assert record.state is ExtensionState.COMPATIBLE
    # 运维吊销：改宿主 policy 实际持有的 trust store 实例（discover 已从
    # 文件重载进 policy —— 等价文件重载语义）。
    object.__setattr__(
        host._policy.trust_store, "revoked_packages",
        (("lc3.pack", "1.0.0"),),
    )
    diags = host.reload("lc3.pack")
    assert host.get_record("lc3.pack").state is ExtensionState.QUARANTINED
    assert any(d.code is DiagnosticCode.PACKAGE_REVOKED for d in diags), [
        d.to_dict() for d in diags
    ]
    host.reset()


def test_activate_refuses_revoked_compatible_extension(tmp_path):
    """#2145：吊销窗口 —— refresh_revocations 只处理 ACTIVE/DEGRADED；
    discover 之后被吊销的 COMPATIBLE 包在 activate 时必须命中吊销复查。"""
    from app.extensions_platform.trust_store import TrustStore

    _worker_pack(tmp_path)
    ts = _trust_store_file(tmp_path, [])
    trust = TrustStore.load(ts)
    host = ExtensionHost(
        tool_registry=ToolRegistry(),
        policy=_policy(tmp_path, trust_store=trust),
    )
    host.discover()
    assert host.get_record("lc3.pack").state is ExtensionState.COMPATIBLE
    object.__setattr__(
        host._policy.trust_store, "revoked_packages",
        (("lc3.pack", "1.0.0"),),
    )
    diags = host.activate("lc3.pack")
    assert has_errors(diags)
    assert any(d.code is DiagnosticCode.PACKAGE_REVOKED for d in diags), [
        d.to_dict() for d in diags
    ]
    assert host.get_record("lc3.pack").state is ExtensionState.QUARANTINED
    host.reset()


def test_concurrent_worker_call_gets_typed_in_flight_refusal(tmp_path):
    """#2145：_request 不再全程持锁 —— 并发调用方拿到 typed
    OPERATION_IN_FLIGHT（旧行为：阻塞在锁上直到首个调用返回）。"""
    from app.extensions_platform.diagnostics import ExtensionPlatformError

    _worker_pack(tmp_path)
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=_policy(tmp_path))
    host.discover()
    assert not has_errors(host.activate("lc3.pack"))
    import threading

    record = host.get_record("lc3.pack")

    def _slow_call():
        record.worker.call("lc3_slow", {"seconds": 0.8}, timeout=5.0)

    thread = threading.Thread(target=_slow_call, daemon=True)
    thread.start()
    time.sleep(0.2)
    assert record.worker.in_flight is True
    started = time.monotonic()
    with pytest.raises(ExtensionPlatformError) as excinfo:
        record.worker.call("lc3_slow", {"seconds": 0.1}, timeout=5.0)
    elapsed = time.monotonic() - started
    assert excinfo.value.diagnostic.code is DiagnosticCode.OPERATION_IN_FLIGHT
    assert elapsed < 0.5, f"concurrent call blocked {elapsed:.2f}s (lock held)"
    thread.join(timeout=5)
    host.reset()
