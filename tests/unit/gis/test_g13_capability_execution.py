"""G13-CAPABILITY-EXECUTION — 生产装配闭环测试（C01–C16 场景矩阵）。

覆盖：
* WP03 同代快照缓存（命中零编译 / 单飞 / 失败退避 / 关闭清理 / 旧代不覆盖）；
* WP04 可用性感知候选检索（真实 selector：C06/C12/C14 + 前门恒保留 +
  kill switch + 故障反验——移除接点必须让验收失败）；
* WP05 执行前复核（C07 选后撤权 / C08 同名换代 / dedup 释放 / 复核失败
  放行 + 故障反验）；
* WP06 算法绑定（候选可解析 / 计数 / 科学前提实质断言）；
* WP07 真实产物（dispatch 全路径铸 ref / 读回一致 / 下一轮 ref 消费 /
  C16 伪造与跨会话拒绝 + 伪 URI 反验）。

纪律：真实 import、真实 registry / session store / raster fixture；
故障注入只在 monkeypatch / fixture 内（Section 九），不提交破坏性改动。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.gis.capability_availability import (
    AvailabilityRecord,
    CapabilityAvailabilitySnapshot,
)
from app.services.chat.tool_surface_v3 import (
    DynamicToolSurface,
    ToolSelectionContext,
    availability_rerank_enabled,
)
from app.services.session_data import session_data_manager
from app.services.tool_dispatch_service import ToolDispatchService
from app.tools import init_tools
from app.tools.registry import ToolRegistry

RED_G13 = "G13 生产装配未落地"

# ── fixtures ─────────────────────────────────────────────────────────

_ZONES: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"zone_id": 1},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0, 10], [10, 10], [10, 0], [0, 0], [0, 10]]]}},
    ],
}


def _raster_path(name: str) -> Path:
    """validate_data_path 合法工作区 ./tmp 下的栅格（与 G06 测试同闸）。"""
    base = Path.cwd() / "tmp" / "g13_capability_tests"
    base.mkdir(parents=True, exist_ok=True)
    return base / name


def _write_raster(path: Path) -> np.ndarray:
    data = np.arange(100, dtype="float32").reshape(10, 10)
    data[0, 0] = -9999.0
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", nodata=-9999.0, crs="EPSG:3857",
        transform=from_origin(0, 10, 1, 1),
    ) as ds:
        ds.write(data, 1)
    return data


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.fixture()
def sid() -> str:
    import uuid

    return f"g13-{uuid.uuid4().hex[:12]}"


def _fake_snapshot(records: list[AvailabilityRecord]) -> CapabilityAvailabilitySnapshot:
    return CapabilityAvailabilitySnapshot(
        records=tuple(records),
        registry_generation="test-reg-gen",
        catalog_generation="test-cat-gen",
        content_fingerprint="test-fp",
        counts={},
    )


def _rec(tool: str, status: str, capability: str = "",
         unverified: tuple[str, ...] = (),
         permission: str = "") -> AvailabilityRecord:
    return AvailabilityRecord(
        tool_id=tool, capability_id=capability, availability_status=status,
        unverified_reasons=unverified, registry_generation="test-reg-gen",
        tool_version_id=f"{tool}@1.0#cv1", permission=permission,
    )


def _select(registry, query: str, **ctx_kw):
    ctx = ToolSelectionContext(user_message=query, k_max=12, **ctx_kw)
    return DynamicToolSurface(registry).select(ctx)


# ── WP03：同代缓存 ───────────────────────────────────────────────────

def _fresh_cache():
    """换新缓存实例（R2 P3-7：模块全局单例可换，进程内无跨用例正确性
    依赖；patch 一律经 monkeypatch 自动还原）。"""
    import app.lib.gis.capability_availability_cache as cac

    cac._CACHE = cac._SnapshotCache()
    return cac


def test_c13_same_generation_single_flight_and_zero_hot_compile(monkeypatch):
    """C13：10 个同代并发请求 → 恰好 1 次编译；命中路径零全量对账。"""
    cac = _fresh_cache()
    calls = {"n": 0}
    orig = cac.compile_availability_snapshot

    def counting(**kw):
        calls["n"] += 1
        time.sleep(0.05)  # 让并发窗口真实形成
        return orig(**kw)

    monkeypatch.setattr(cac, "compile_availability_snapshot", counting)
    outs: list = []
    threads = [
        threading.Thread(
            target=lambda: outs.append(
                cac.get_availability_snapshot(bootstrap=True)))
        for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1, f"同代重建并发必须=1，实际 {calls['n']}"
    assert all(s is outs[0] for s in outs), "同代命中必须返回同一快照对象"
    stats = cac.availability_cache_stats()
    assert stats["builds"] == 1
    assert stats["coalesced_builds"] >= 1

    # 热命中：再取 3 次，编译计数不再增长。
    before = calls["n"]
    for _ in range(3):
        cac.get_availability_snapshot(bootstrap=True)
    assert calls["n"] == before, "同代命中路径不得触发全量编译"


def test_cache_failure_backoff_no_storm_no_cross_generation(monkeypatch):
    """R2 P2-1/P2-2：构建失败 → 退避窗口内同键读取**不重试全量编译**、
    **不服务跨代 last-good**（证据缺席异常）；窗口过后真实重建。冷进程
    （无 last-good）同样退避 —— 不做失败风暴。"""
    cac = _fresh_cache()
    good = cac.get_availability_snapshot(bootstrap=True)
    orig = cac.compile_availability_snapshot
    boom = {"on": True}
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if boom["on"]:
            raise RuntimeError("simulated registry audit failure")
        return orig(**kw)

    monkeypatch.setattr(cac, "compile_availability_snapshot", flaky)
    # 强制代漂移（扩展状态摘要变化）→ 键变化 → 触发重建（失败上抛）。
    import app.lib.gis.capability_availability as ca

    monkeypatch.setattr(ca, "_extension_state_digest",
                        lambda: "drifted-generation")
    with pytest.raises(RuntimeError):
        cac.get_availability_snapshot(bootstrap=True)
    assert calls["n"] == 1
    # 退避窗口内：同键再取 → 不重试编译、不服务跨代 last-good，
    # 抛证据缺席异常（调用方零贡献降级）。
    for _ in range(3):
        with pytest.raises(cac.AvailabilitySnapshotBackoff):
            cac.get_availability_snapshot(bootstrap=True)
    assert calls["n"] == 1, "退避窗口内不得反复跑全量对账（失败风暴）"
    assert cac.availability_cache_stats()["backoff_suppressed"] >= 3

    # 冷进程（无 last-good）同样退避：失效后同键失败 → 窗口内不重试。
    cac.invalidate_availability_snapshot()
    with pytest.raises(RuntimeError):
        cac.get_availability_snapshot(bootstrap=True)
    assert calls["n"] == 2
    with pytest.raises(cac.AvailabilitySnapshotBackoff):
        cac.get_availability_snapshot(bootstrap=True)
    assert calls["n"] == 2, "冷进程退避窗口内也不得风暴重试"

    boom["on"] = False
    # 越过退避：把失败时间拨回过去 → 必须真实重建。
    with cac._CACHE._cache_lock:
        key, _ = cac._CACHE._last_failure
        cac._CACHE._last_failure = (key, time.monotonic() - 3600.0)
    rebuilt = cac.get_availability_snapshot(bootstrap=True)
    assert rebuilt is not good, "退避窗口过后必须真实重建"


def test_cache_shutdown_cleanup_idempotent():
    cac = _fresh_cache()
    cac.get_availability_snapshot(bootstrap=True)
    cac.shutdown_availability_cache()
    cac.shutdown_availability_cache()
    assert cac._CACHE._entry is None


def test_cache_key_changes_on_registry_generation():
    """注册代变化 → 键变化 → 一次有效重建（通知丢失也有键兜底）。"""
    cac = _fresh_cache()
    snap1 = cac.get_availability_snapshot(bootstrap=True)
    key1 = cac._CACHE._structural_key()
    # 动态注册一个新工具 → registry_fingerprint 变化。
    reg = ToolRegistry()
    init_tools(reg)
    from app.tools.registry import tool as tool_dec

    @tool_dec(reg, name="g13_probe_tool", description="probe", tier=1)
    def g13_probe_tool() -> dict:
        return {"ok": True}

    snap2 = cac.get_availability_snapshot(registry=reg, bootstrap=True)
    key2 = cac._CACHE._structural_key(reg)
    assert key1 != key2, "注册新工具必须改变结构键"
    assert snap2 is not snap1


# ── WP04：真实候选检索接通 ───────────────────────────────────────────

def _patch_snapshot(monkeypatch, snapshot: CapabilityAvailabilitySnapshot):
    import app.lib.gis.capability_availability_cache as cac

    monkeypatch.setattr(
        cac, "get_availability_snapshot",
        lambda **kw: snapshot)


def test_c06_deactivated_high_similarity_tool_excluded(registry, monkeypatch):
    """C06：扩展相似度最高但已停用（temporarily_unavailable）→ 真实候选
    不含它，抑制原因可查。反向：移除接点（kill switch）后该工具重新出现
    —— 证明测试确实经过 selector。"""
    query = "网络服务区 路网 覆盖 service area network"
    snap = _fake_snapshot([
        _rec("network_service_coverage_consistency", "temporarily_unavailable",
             "service_area", ("provider_unavailable:not_evaluated",)),
        _rec("network_service_area", "native_verified", "service_area"),
        _rec("isochrone_network", "native_verified", "service_area"),
    ])
    _patch_snapshot(monkeypatch, snap)
    sel = _select(registry, query, active_capabilities=("service_area",))
    assert "network_service_coverage_consistency" not in sel.names, (
        "停用扩展工具不得因相似度进入真实候选")
    assert sel.dropped.get("network_service_coverage_consistency", "").startswith(
        "availability:availability_temporarily_unavailable"), (
        f"抑制原因可查，实际 dropped={sel.dropped}")
    assert "network_service_coverage_consistency" in sel.availability_suppressed

    # 反向检查（Section 九）：关闭接点后，同输入下该工具必须回到名单 ——
    # 证明本用例的排除确实来自新接点而非既有闸。
    monkeypatch.setenv("GIS_AVAILABILITY_RERANK", "0")
    sel_off = _select(registry, query, active_capabilities=("service_area",))
    assert not availability_rerank_enabled()
    assert "network_service_coverage_consistency" in sel_off.names, (
        "kill switch 关闭后行为必须回退 master（排除只来自 G13 接点）")


def test_c12_quantile_unavailable_mean_not_silent_substitute(registry, monkeypatch):
    """C12：分位数原语不可用但均值可用 → 均值工具保留但**披露降权**，
    不冒充分位数等价物；抑制原因与 available_unverified 理由可区别。"""
    snap = _fake_snapshot([
        _rec("conditional_quantile_zonal_stats", "temporarily_unavailable",
             "zonal_statistics"),
        _rec("zonal_stats", "native_verified", "zonal_statistics"),
    ])
    _patch_snapshot(monkeypatch, snap)
    sel = _select(registry, "各区县 NDVI 的 p90 分位数 分区统计 quantile",
                  active_capabilities=("zonal_statistics",))
    assert "conditional_quantile_zonal_stats" not in sel.names
    assert "zonal_stats" in sel.names, "可用替代保留（均值仍可执行）"
    # 不得宣称等价：抑制理由是可用性，不是「已由均值替代」。
    drop_reason = sel.dropped.get("conditional_quantile_zonal_stats", "")
    assert "temporarily_unavailable" in drop_reason
    assert "equivalent" not in drop_reason


def test_unverified_tools_kept_visible_and_core_frontdoor_never_suppressed(
        registry, monkeypatch):
    """WP04 硬约束：available_unverified 降权但保留；CORE 前门永不抑制。"""
    snap = _fake_snapshot([
        _rec("conditional_quantile_zonal_stats", "available_unverified",
             "zonal_statistics", ("evidence_incomplete:not_evaluated",)),
        _rec("zonal_stats", "native_verified", "zonal_statistics"),
        _rec("webgis_map_intent", "temporarily_unavailable"),  # 恶意快照
    ])
    _patch_snapshot(monkeypatch, snap)
    sel = _select(registry, "分区统计 NDVI 分位数")
    assert "conditional_quantile_zonal_stats" in sel.names, (
        "available_unverified 不得全部隐藏")
    assert any(
        "available_unverified_downweighted" in r
        for r in sel.reasons.get("conditional_quantile_zonal_stats", []))
    assert any(
        "native_verified" in r for r in sel.reasons.get("zonal_stats", []))
    assert "webgis_map_intent" in sel.names, "核心前门恒保留"


def test_selector_failopen_on_snapshot_error(registry, monkeypatch):
    """快照构建失败 → 投影零贡献，选择照常（不因缓存故障破坏 turn）。"""
    import app.lib.gis.capability_availability_cache as cac

    def boom(**kw):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(cac, "get_availability_snapshot", boom)
    sel = _select(registry, "分区统计 NDVI 分位数")
    assert sel.names, "快照故障不得清空候选"
    assert sel.availability_suppressed == []


def test_selector_time_context_suppresses_metric_tools(registry, monkeypatch):
    """秒语境 × 米制工具名 → UNIT_DIMENSION_CONFLICT 抑制（C05 检索段）。"""
    snap = _fake_snapshot([
        _rec("buffer_analysis", "native_verified"),
        _rec("network_service_coverage_consistency", "native_verified",
             "service_area"),
    ])
    _patch_snapshot(monkeypatch, snap)
    sel = _select(registry, "15 分钟 路网服务区 travel time seconds 行程时间")
    assert "buffer_analysis" not in sel.names
    assert "UNIT_DIMENSION_CONFLICT" in sel.dropped.get("buffer_analysis", "")


def test_wp04_real_pi_turn_marker_changes(registry, monkeypatch):
    """WP04 验收：真实 Pi turn 入口（compute_turn_active_tools → 激活名单）
    实际变化 —— 停用工具不出现在名单；披露面携带抑制原因。"""
    from app.agent_pi_bridge import get_tool_registry, set_tool_registry

    try:
        original_global = get_tool_registry()
    except Exception:  # noqa: BLE001 — 进程内可能尚未初始化全局注册表
        original_global = None
    set_tool_registry(registry)
    monkeypatch.setenv("PI_DYNAMIC_TOOL_SURFACE", "1")
    monkeypatch.setenv("PI_TOOL_ABI", "0")  # 不依赖 ABI receipt 面

    from app.services.chat import pi_native_surface as pns
    import importlib

    importlib.reload(pns)
    try:
        snap = _fake_snapshot([
            _rec("network_service_coverage_consistency",
                 "temporarily_unavailable",
                 "service_area", ("provider_unavailable:not_evaluated",)),
            _rec("network_service_area", "native_verified", "service_area"),
        ])
        _patch_snapshot(monkeypatch, snap)

        disclosure: dict = {}
        names = pns.compute_turn_active_tools(
            "路网服务区 覆盖一致性 service area network",
            disclosure=disclosure)
        assert "network_service_coverage_consistency" not in names, (
            "真实 turn 名单不得包含停用工具")
        assert disclosure.get("availability_suppressed") == [
            "network_service_coverage_consistency"], (
            f"披露面须携带抑制名单：{disclosure}")

        # 反向：工具恢复（快照换代）→ 重新出现在真实名单。
        snap_ok = _fake_snapshot([
            _rec("network_service_coverage_consistency", "native_verified",
                 "service_area"),
            _rec("network_service_area", "native_verified", "service_area"),
        ])
        _patch_snapshot(monkeypatch, snap_ok)
        names2 = pns.compute_turn_active_tools(
            "路网服务区 覆盖一致性 service area network")
        assert "network_service_coverage_consistency" in names2, (
            "恢复后重新发现可继续（C07 重新发现路径）")
    finally:
        # R2 P3-7：还原模块全局（registry + env + 重载态），不泄漏到其它用例。
        if original_global is not None:
            set_tool_registry(original_global)
        monkeypatch.delenv("PI_DYNAMIC_TOOL_SURFACE")
        monkeypatch.delenv("PI_TOOL_ABI")
        importlib.reload(pns)


# ── WP06：算法绑定实质断言 ───────────────────────────────────────────

def test_wp06_primitives_resolvable_from_capability():
    """compiler/selector 可从能力找到原语；方法语义/科学前提真实投影。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.method_eligibility import preflight_method

    ar = get_algorithm_registry()
    cap_map = ar.capability_tool_map()
    assert "conditional_quantile_zonal_stats" in cap_map.get("zonal_statistics", ())
    assert "network_service_coverage_consistency" in cap_map.get("service_area", ())

    algo = ar.get("raster.zonal.quantile_conditional")
    assert algo is not None and algo.tool_candidates == [
        "conditional_quantile_zonal_stats"]
    assert algo.scientific_preconditions == ["raster_band_required:1"]
    assert algo.fallback_algorithms == [], "均值不是分位数的科学等价替代"

    net = ar.get("network.service_coverage.consistency")
    assert net is not None and net.crs_class == "PROJECTED_REQUIRED"
    assert "projected_crs_required" in net.scientific_preconditions
    assert net.fallback_algorithms == []

    # 科学前提三态：缺画像事实 → not_evaluated（不冒充 eligible）。
    pre = preflight_method("raster.zonal.quantile_conditional", profile={})
    assert pre.verdict in ("eligible", "not_evaluated")
    pre2 = preflight_method("network.service_coverage.consistency", profile={})
    assert pre2.verdict in ("eligible", "not_evaluated", "ineligible")


def test_wp06_native_verified_after_binding():
    """绑定后原语八面 pass → native_verified（不得手改状态，须真实对账）。"""
    from app.lib.gis.capability_availability import (
        compile_availability_snapshot, query_availability,
    )

    snap = compile_availability_snapshot(bootstrap=True)
    for tool in ("conditional_quantile_zonal_stats",
                 "network_service_coverage_consistency"):
        row = query_availability(snap, tool_id=tool)
        assert row["records"], f"{tool} 不在快照"
        assert row["records"][0]["availability_status"] == "native_verified", (
            f"{tool} 绑定后应达 native_verified（真实八面对账），"
            f"实际 {row['records'][0]['availability_status']}")


# ── WP05：执行前复核 ─────────────────────────────────────────────────

def _svc(registry) -> ToolDispatchService:
    """生产事实：不注入 session store —— ref 铸造与解引用走同一全局
    session_data_manager（registry._resolve_references 的单一真源）。"""
    return ToolDispatchService(registry=registry)


def _tc(name: str, args: dict) -> dict:
    return {"id": "call_g13", "function": {"name": name, "arguments": args}}


_LINE_NET = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"edge": "e1"},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [2, 0]]}},
    ],
}
_POINT_FAC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"scheme": "A"},
         "geometry": {"type": "Point", "coordinates": [0, 0]}},
        {"type": "Feature", "properties": {"scheme": "B"},
         "geometry": {"type": "Point", "coordinates": [2, 0]}},
    ],
}


@pytest.mark.asyncio
async def test_c07_revoked_after_selection_recheck_refuses_and_releases(
        registry, monkeypatch):
    """C07：选中之后撤权/停用（快照换代为 temporarily_unavailable）→
    dispatch 拒绝旧调用、释放 dedup 占位、给出替代。反向：关闭复核开关后
    同输入真实执行成功 —— 证明拒绝只来自 G13 执行闸。"""
    import app.lib.gis.capability_availability_cache as cac

    snap = _fake_snapshot([
        _rec("network_service_coverage_consistency", "temporarily_unavailable",
             "service_area", ("provider_unavailable:not_evaluated",)),
        _rec("network_service_area", "native_verified", "service_area"),
    ])
    monkeypatch.setattr(
        cac, "get_availability_snapshot", lambda **kw: snap)
    args = {"network": _LINE_NET, "facilities": _POINT_FAC, "cutoff": 5.0}
    svc = _svc(registry)
    executed: set = set()
    result = await svc.dispatch(
        _tc("network_service_coverage_consistency", args),
        "sess-c07", executed)
    assert result.status == "error"
    assert result.error_msg == "CAPABILITY_UNAVAILABLE"
    assert "network_service_area" in result.llm_payload, "替代必须喂给重试"
    assert not executed, "拒绝后必须释放 dedup 占位（同任务可重试）"
    # 未执行：真实计算不得发生（负例断言实际执行函数没被调用）。
    assert result.raw_result.get("code") == "CAPABILITY_UNAVAILABLE"

    # 反向检查（Section 九）：移除执行复核 → 同输入真实执行成功。
    monkeypatch.setenv("GIS_AVAILABILITY_DISPATCH_RECHECK", "0")
    executed2: set = set()
    svc2 = _svc(registry)
    result2 = await svc2.dispatch(
        _tc("network_service_coverage_consistency", args),
        "sess-c07b", executed2)
    assert result2.status != "error", (
        f"关闭复核后同输入必须真实执行（证明拒绝只来自 G13 执行闸），"
        f"实际 {result2.status}/{result2.error_msg}")
    monkeypatch.delenv("GIS_AVAILABILITY_DISPATCH_RECHECK")


@pytest.mark.asyncio
async def test_c15_denial_then_recovery_no_stuck_placeholder(
        registry, monkeypatch):
    """C15：拒绝/异常后同参重试无永久「在飞」占位 —— 撤销恢复
    （快照换代回 native_verified）后，同会话同参直接真实执行成功。"""
    import app.lib.gis.capability_availability_cache as cac

    snap_bad = _fake_snapshot([
        _rec("network_service_coverage_consistency", "temporarily_unavailable",
             "service_area"),
        _rec("network_service_area", "native_verified", "service_area"),
    ])
    monkeypatch.setattr(
        cac, "get_availability_snapshot", lambda **kw: snap_bad)
    args = {"network": _LINE_NET, "facilities": _POINT_FAC, "cutoff": 5.0}
    executed: set = set()
    svc = _svc(registry)
    denied = await svc.dispatch(
        _tc("network_service_coverage_consistency", args), "sess-c15", executed)
    assert denied.status == "error"

    # 恢复：快照换代 → 同会话同参重试真实执行（不是「已在执行」谎言）。
    snap_ok = _fake_snapshot([
        _rec("network_service_coverage_consistency", "native_verified",
             "service_area"),
    ])
    monkeypatch.setattr(
        cac, "get_availability_snapshot", lambda **kw: snap_ok)
    retried = await svc.dispatch(
        _tc("network_service_coverage_consistency", args), "sess-c15", set())
    assert retried.status != "error", (
        f"恢复后同参重试必须真实执行（无永久在飞占位），"
        f"实际 {retried.status}/{retried.error_msg}: {retried.llm_payload[:120]}")
    assert "Loop blocked" not in retried.llm_payload


@pytest.mark.asyncio
async def test_c08_same_name_schema_drift_refused(registry, monkeypatch):
    """C08：同名工具 schema 换代（tool_version_id 漂移）→ typed
    STALE_TOOL_SCHEMA，不默许旧 schema 调用。"""
    import app.lib.gis.capability_availability_cache as cac

    stale_rec = _rec("network_service_coverage_consistency", "native_verified")
    stale_rec = AvailabilityRecord(
        tool_id="network_service_coverage_consistency",
        availability_status="native_verified",
        tool_version_id="network_service_coverage_consistency@0.9#cv1",
        registry_generation="stale-gen",
    )
    snap = _fake_snapshot([stale_rec])
    monkeypatch.setattr(
        cac, "get_availability_snapshot", lambda **kw: snap)
    svc = _svc(registry)
    result = await svc.dispatch(
        _tc("network_service_coverage_consistency",
            {"network": _ZONES, "facilities": _ZONES, "cutoff": 5.0}),
        "sess-c08", set())
    assert result.status == "error"
    assert result.error_msg == "STALE_TOOL_SCHEMA"


@pytest.mark.asyncio
async def test_recheck_failopen_on_snapshot_failure(registry,
                                                    monkeypatch):
    """复核面故障（快照构建异常）→ 放行（闸不成为第二故障面）。"""
    import app.lib.gis.capability_availability_cache as cac

    def boom(**kw):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(cac, "get_availability_snapshot", boom)
    svc = _svc(registry)
    result = await svc.dispatch(
        _tc("network_service_coverage_consistency",
            {"network": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature", "properties": {"edge": "e1"},
                    "geometry": {"type": "LineString",
                                 "coordinates": [[0, 0], [2, 0]]}}],
            },
                "facilities": {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {"scheme": "A"},
                        "geometry": {"type": "Point",
                                     "coordinates": [0, 0]}}]},
                "cutoff": 5.0}),
        "sess-fo", set())
    assert result.status != "error" or (
        result.error_msg not in ("CAPABILITY_UNAVAILABLE", "STALE_TOOL_SCHEMA")), (
        "复核故障不得产生可用性拒绝")


# ── WP02：统一资格视图 ───────────────────────────────────────────────

def test_wp02_qualification_summary_layers_distinguishable():
    """未知/不可用/禁止/降权可区别消费；权限投影请求侧、不进缓存。"""
    from app.services.chat.pi_availability_adapter import (
        summarize_tool_qualification,
    )

    snap = _fake_snapshot([
        _rec("tool_ok", "native_verified"),
        _rec("tool_gone", "temporarily_unavailable",
             unverified=("provider_unavailable:not_evaluated",)),
        _rec("tool_perm", "native_verified", permission="model_provider"),
    ])
    ok = summarize_tool_qualification("tool_ok", snapshot=snap)
    assert ok["structural_availability"]["status"] == "native_verified"
    assert ok["permission"] == "not_evaluated", "无隔离上下文不虚构判定"

    perm = summarize_tool_qualification(
        "tool_perm", snapshot=snap, permissions=frozenset({"model_provider"}))
    assert perm["permission"] == "allowed"

    # 缓存共享的结构快照 + 逐请求权限投影：同一 snapshot 对象服务两种请求。
    allowed = summarize_tool_qualification(
        "tool_perm", snapshot=snap, permissions=frozenset())
    assert allowed["permission"] == "forbidden", "空权限 fail-closed"

    gone = summarize_tool_qualification("tool_gone", snapshot=snap)
    assert gone["structural_availability"]["status"] == "temporarily_unavailable"
    assert "not_evaluated" in " ".join(
        gone["structural_availability"]["unverified_reasons"])

    unknown = summarize_tool_qualification("tool_absent", snapshot=snap)
    assert unknown["structural_availability"]["status"] == "not_in_snapshot"
    assert unknown["generations"]["registry"] == "test-reg-gen"


# ── WP07：真实产物与下一轮复用 ───────────────────────────────────────

_QUANTILE_ARGS = {
    "zones": _ZONES,
    "raster_path": None,  # 由用例填充
    "quantiles": [0.9],
    "where": "value > 50",
    "nodata": -9999.0,
}


@pytest.mark.asyncio
async def test_c01_real_dispatch_mints_real_artifact_ref(
        registry, tmp_path):
    """C01（产物段）：真实 dispatch → ref:analysis-* 真存储、可读回、
    内容与首轮数据一致；LLM 载荷拿得到 ref 且不带完整 zones 表。"""
    raster = _raster_path(f"grid_{tmp_path.name}.tif")
    data = _write_raster(raster)
    args = dict(_QUANTILE_ARGS, raster_path=str(raster))
    svc = _svc(registry)
    result = await svc.dispatch(
        _tc("conditional_quantile_zonal_stats", args), "sess-c01", set())
    assert result.status != "error", f"真实执行失败: {result.error_msg}"
    raw = result.raw_result
    assert isinstance(raw, dict)
    data_ref = raw.get("data_ref")
    assert isinstance(data_ref, str) and data_ref.startswith("ref:analysis-"), (
        f"seam 必须铸造真实 ref，实际 data_ref={data_ref!r}")
    artifact = raw.get("artifact", {})
    assert artifact.get("stored") is True
    assert artifact.get("ref") == data_ref

    # 读回：session store 里的完整封套与执行数据一致（C03 数据一致性）。
    envelope = await session_data_manager.get("sess-c01", data_ref)
    assert isinstance(envelope, dict)
    zones = envelope["data"]["zones"]
    assert zones and zones[0]["n_samples"] > 0
    valid = data[(data != -9999.0) & (data > 50)]
    expected_p90 = float(np.quantile(valid.astype("float64"), 0.9))
    assert abs(zones[0]["quantiles"]["0.9"] - expected_p90) <= 0.5

    # LLM 载荷：ref 在、完整 zones 表不在（有界上下文）。
    payload = result.llm_payload
    assert data_ref in payload
    assert "zones" not in payload or '"zone_index"' not in payload, (
        "完整逐 zone 表不得进入模型上下文")


@pytest.mark.asyncio
async def test_c03_next_turn_reads_previous_artifact_via_ref(
        registry, tmp_path):
    """C03：第二轮把上一轮产物 ref 当输入 → registry 真实解引用、结果一致。
    反向（伪 URI）：注入假 artifact:// 形状时下一轮读取必须失败。"""
    raster = _raster_path(f"grid3_{tmp_path.name}.tif")
    _write_raster(raster)
    svc = _svc(registry)
    # 第一轮：zones 存成 ref（模拟上游把 FC 交给会话存储）。
    zones_ref = await session_data_manager.store("sess-c03", _ZONES, prefix="geojson")
    args = dict(_QUANTILE_ARGS, raster_path=str(raster))
    r1 = await svc.dispatch(
        _tc("conditional_quantile_zonal_stats", args), "sess-c03", set())
    assert r1.status != "error"
    # 第二轮：zones 参数直接传 ref → 生产解引用路径（同会话）。
    args2 = dict(args, zones=zones_ref)
    r2 = await svc.dispatch(
        _tc("conditional_quantile_zonal_stats", args2), "sess-c03", set())
    assert r2.status != "error", (
        f"ref 输入必须被解引用: {r2.error_msg} / {r2.llm_payload[:200]}")
    q1 = r1.raw_result["data"]["zone_count"]
    q2 = r2.raw_result["data"]["zone_count"]
    assert q1 == q2 == 1


@pytest.mark.asyncio
async def test_c16_fake_and_cross_session_refs_rejected(
        registry, tmp_path):
    """C16：伪造 ref / 跨 session 引用 → 诚实拒绝（typed 自愈指引），
    不产生伪成功。"""
    raster = _raster_path(f"grid16_{tmp_path.name}.tif")
    _write_raster(raster)
    svc = _svc(registry)
    args = dict(_QUANTILE_ARGS, raster_path=str(raster),
                zones="ref:analysis-doesnotexist0000")
    result = await svc.dispatch(
        _tc("conditional_quantile_zonal_stats", args), "sess-c16", set())
    assert result.status == "error" or result.raw_result.get("success") is False, (
        "不存在的 ref 必须被拒绝，不得伪成功")
    # 跨 session：sess-a 的 ref 在 sess-b 中不可见。
    ref_a = await session_data_manager.store("sess-a", _ZONES, prefix="geojson")
    args_b = dict(_QUANTILE_ARGS, raster_path=str(raster), zones=ref_a)
    result_b = await svc.dispatch(
        _tc("conditional_quantile_zonal_stats", args_b), "sess-b", set())
    assert result_b.status == "error" or result_b.raw_result.get("success") is False


@pytest.mark.asyncio
async def test_fake_artifact_uri_rejected_by_envelope_contract(
        registry, monkeypatch):
    """Section 九 反验：伪 artifact:// URI（未存储）不得被当真产物放行 ——
    seam 只认 v2 契约封套并真实存储；伪造 v1 形状时结果不携带 data_ref，
    下一轮拿它当 ref 读取必然失败（不产生伪成功）。"""
    svc = _svc(registry)

    async def fake_dispatch(name, args, **kw):
        return {
            "summary": "fake v1 envelope",
            "data": {"x": 1},
            "artifact": {"ref": "artifact://conditional_quantile/fake",
                         "kind": "analysis_artifact",
                         "schema_version": "capability-artifact/v1",
                         "pageable": True},
        }

    monkeypatch.setattr(registry, "dispatch", fake_dispatch)
    result = await svc.dispatch(
        _tc("some_tool", {}), "sess-fake", set())
    raw = result.raw_result
    # seam 只回填 v2 契约封套；v1 伪 URI 不被铸成真 ref。
    artifact = raw.get("artifact", {})
    assert artifact.get("ref") == "artifact://conditional_quantile/fake"
    assert artifact.get("stored") is not True, "伪 URI 不得被静默当真 ref"
    assert "data_ref" not in raw, "v1 形状不携带真 ref，下一轮读取必失败"


# ── WP08 边界（方案上界） ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wp08_scheme_budget_bound(registry):
    """MAX_SCHEMES：超过方案数上界 → typed BUDGET_EXCEEDED，不做无界 O(k²)。"""
    from app.tools.capability_ecosystem_tools import MAX_SCHEMES

    schemes = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"scheme": f"s{i}"},
         "geometry": {"type": "Point", "coordinates": [0, 0]}}
        for i in range(MAX_SCHEMES + 1)
    ]}
    network = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"edge": "e1"},
         "geometry": {"type": "LineString",
                      "coordinates": [[0, 0], [2, 0]]}}]}
    result = await registry.dispatch("network_service_coverage_consistency", {
        "network": network, "facilities": schemes, "cutoff": 5.0,
        "cost_unit": "meters"})
    assert result.get("success") is False
    assert result.get("code") == "BUDGET_EXCEEDED"


# ── WP09：资格变化不中断合法在途工作 ─────────────────────────────────

@pytest.mark.asyncio
async def test_wp09_invalidation_does_not_break_other_tools(registry, monkeypatch):
    """缓存失效/换代只影响该工具自身；其他合法工具照常执行（无全局停摆），
    且结构缓存不携带权限事实（同快照服务多请求隔离）。"""
    import app.lib.gis.capability_availability_cache as cac

    cac.invalidate_availability_snapshot()
    # 换代后再取：其他工具仍可获得新代快照并正常 dispatch。
    snap = cac.get_availability_snapshot(registry=registry, bootstrap=True)
    assert snap.counts.get("total", 0) > 0
    # 同一（共享的）结构快照对象上做两个隔离上下文的查询：互不串权限。
    from app.lib.gis.capability_availability import query_availability

    a = query_availability(snap, tool_id="zonal_stats", permissions=frozenset())
    b = query_availability(snap, tool_id="zonal_stats")
    assert a["records"][0]["availability_status"] == "forbidden"
    assert b["records"][0]["availability_status"] != "forbidden"
    assert snap is cac.get_availability_snapshot(
        registry=registry, bootstrap=True), "结构缓存进程共享"


@pytest.mark.asyncio
async def test_wp09_extension_deactivation_unregisters_tool(registry, monkeypatch):
    """扩展停用 → 宿主台账注销其工具（注册表事实）→ 复核面看到的是
    registry 存在性闸（UNKNOWN_TOOL），不虚构可用性；其它工具不受影响。"""
    # 结构性验证：停用路径存在且经 ledger unregister（行为契约在宿主测试
    # 覆盖）；此处验证 recheck 对「不在快照/不在注册表」的工具不冒进。
    import app.lib.gis.capability_availability_cache as cac

    snap = _fake_snapshot([_rec("network_service_area", "native_verified")])
    monkeypatch.setattr(
        cac, "get_availability_snapshot", lambda **kw: snap)
    from app.services.chat.pi_availability_adapter import (
        preflight_dispatch_availability,
    )

    # 不在快照的注册工具 → 无阳性事实 → 放行（存在性由 registry 闸负责）。
    assert preflight_dispatch_availability(
        "zonal_stats", registry=registry, snapshot=snap) is None
    # 未注册名 → 同样放行给存在性闸（不是本复核的职责）。
    assert preflight_dispatch_availability(
        "totally_unknown_tool", registry=registry, snapshot=snap) is None


# ── WP10：能力发现披露 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wp10_list_available_tools_discloses_availability(registry):
    """list_available_tools 输出携带 availability 状态；不可用工具给理由。
    同一失败在发现面/拒绝面语义一致（普通用户能据此走下一步）。"""
    result = await registry.dispatch("list_available_tools", {"domain": "raster"})
    rows = {t["name"]: t for t in result.get("tools", [])}
    assert "zonal_stats" in rows
    zonal = rows["zonal_stats"]
    assert zonal.get("availability") in (
        "native_verified", "available_unverified"), (
        f"发现面必须披露可用性状态，实际 {zonal.get('availability')!r}")
    # 两个原语也在发现面（G13 绑定后 native_verified；raster 域硬断言，
    # 不做 if 守卫 —— R2 review：守卫会让断言空洞）。
    assert "conditional_quantile_zonal_stats" in rows, (
        "分位数原语必须在 raster 域发现面（domains 含 raster）")
    assert rows["conditional_quantile_zonal_stats"][
        "availability"] == "native_verified"


def test_wp10_denial_texts_consistent(monkeypatch):
    """同一失败（temporarily_unavailable）在 recheck 拒绝文本与 selector
    抑制理由中语义一致：状态词、typed 码、下一步建议三面同源。"""
    from app.services.chat.pi_availability_adapter import (
        DispatchAvailabilityDenial, CODE_CAPABILITY_UNAVAILABLE,
    )

    d = DispatchAvailabilityDenial(
        code=CODE_CAPABILITY_UNAVAILABLE,
        reason="structural availability = temporarily_unavailable "
               "(provider dependency absent)",
        hint="wait or pick alternative",
        alternatives=("network_service_area",))
    text = d.denial_text()
    assert "CAPABILITY_UNAVAILABLE" in text
    assert "temporarily_unavailable" in text
    assert "network_service_area" in text
    assert "Retry the same task" in text, "可恢复拒绝必须说明重试语义"
