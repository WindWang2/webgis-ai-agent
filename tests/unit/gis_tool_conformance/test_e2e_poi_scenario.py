"""G03 WP-D.13 离线 E2E 场景 A —— map_intent → POI 分析 → 热力/统计。

经**真实 ToolDispatchService.dispatch**（非 mock registry）走完整调度链：
dedup/守卫 → registry.dispatch（透明 ref 解引用）→ 错误归一化 → ref 铸造 →
MapSpec 图层授权 → slim_event/llm_payload 投影。

离线诚实边界（审计发现，如实入测）：

- ``query_local_poi`` 的主力数据源是本地 ``gd_pois.gpkg``（本机缺席），在线
  降级通道需 ``AMAP_API_KEY``（测试环境为空）。本场景把「离线数据缺席 →
  可解释失败（机器码 + correction_hint + dedup 槽释放）」作为链路的第一步
  断言——这是 G03 的主题（诚实失败面），而不是伪装成功。
- **审计发现**：``query_local_poi`` 声明 ``network=True`` 却不声明
  ``requires_credentials``（app/tools/local_stats.py:102-151），其在线降级
  实际消费 ``settings.AMAP_API_KEY``——descriptor 契约与真实凭证需求漂移，
  8 面对账的 dispatch 面披露为 "no credential/permission requirements"。
  本文件钉住现状（不迁就、不改产品代码）。
- POI 载荷由本地 fixture（合成小学点集，确定性坐标）经 session ref 注入，
  驱动真实热力/统计工具。

会话沙箱：注入 ``MemorySessionStore`` 并替换链路各消费面的
``session_data_manager`` 绑定 + 关闭 settings.USE_REDIS——零 Redis、零网络、
进程内确定性。
"""
from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

import pytest

from app.services.session_data import MemorySessionStore
from app.services.tool_dispatch_service import ToolDispatchService
from app.tools.catalog_discovery_tools import register_catalog_discovery_tools
from app.tools.local_stats import register_local_stats_tools
from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools

_VERDICTS = {"pass", "warning", "fail", "not_evaluated"}
_FACES = {
    "registration", "search", "planner", "pi_native",
    "proxy", "dispatch", "result", "science",
}

#: dispatch 链路上按模块名绑定了全局 session_data_manager 的消费面。
#: 会话沙箱把它们全部指向同一个进程内 MemorySessionStore（有该属性的
#: 才 patch；缺席说明重构后不再消费，跳过即可）。
_SESSION_DATA_CONSUMERS = (
    "app.services.session_data",
    "app.tools.registry",
    "app.services.tool_dispatch_service",
    "app.services.mapspec.lifecycle_engine",
    "app.services.mapspec.store",
    "app.services.mapspec_store",
    "app.services.mapspec.mutation_handlers",
    "app.services.mapspec.mutation_primitives",
    "app.services.artifact_registry",
    "app.services.render_work_supply",
    "app.services.session_plan",
    "app.lib.gis.analysis_reuse",
    "app.services.cartography_runtime",
)


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    r = ToolRegistry()
    register_local_stats_tools(r)
    register_spatial_tools(r)
    register_catalog_discovery_tools(r)
    return r


@pytest.fixture
def sandbox(registry, monkeypatch):
    """进程内会话沙箱：MemorySessionStore + USE_REDIS 关 + bridge 指向本 registry。"""
    monkeypatch.setattr("app.core.config.settings.USE_REDIS", False)
    store = MemorySessionStore()
    for module_name in _SESSION_DATA_CONSUMERS:
        module = importlib.import_module(module_name)
        if hasattr(module, "session_data_manager"):
            monkeypatch.setattr(module, "session_data_manager", store)
    import app.agent_pi_bridge as bridge

    monkeypatch.setattr(bridge, "try_get_tool_registry", lambda: registry)
    service = ToolDispatchService(registry=registry, session_data=store)
    return SimpleNamespace(store=store, service=service, registry=registry)


def _tc(name: str, args: dict) -> dict:
    return {
        "id": "call_" + uuid.uuid4().hex[:8],
        "function": {"name": name, "arguments": args},
    }


def _school_fc(n: int = 40) -> dict:
    """确定性合成小学点集（成都一带 WGS84，n≥10 满足热力最小样本闸）。"""
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [104.05 + (i % 8) * 0.004, 30.65 + (i // 8) * 0.003],
            },
            "properties": {"name": f"synthetic_school_{i:02d}"},
        }
        for i in range(n)
    ]
    return {"type": "FeatureCollection", "features": features}


# ── 1. map_intent：意图入口（真实意图解析器，离线） ──────────────────


def test_map_intent_resolves_poi_distribution_family():
    from app.services.gis_harness.intent import resolve_map_request_intent

    intent = resolve_map_request_intent("成都的小学分布情况")
    assert intent.task == "distribution_overview"


# ── 2. POI 数据源缺席：可解释失败面 ─────────────────────────────────


async def test_poi_tool_offline_absence_is_explainable_failure(sandbox):
    """本地 POI 库缺席 + 无 API key → 结构化错误（机器码 + 自愈提示），
    绝不伪装成功；失败调用释放 dedup 占位（同参重试不被在飞谎言拦截）。"""
    executed: set = set()
    result = await sandbox.service.dispatch(
        _tc("query_local_poi", {"district": "成都市", "name_like": "小学"}),
        "sess-g03-poi-absent", executed)
    assert result.status == "error"
    assert result.geojson_ref is None
    assert result.error_msg, "错误分支必须有非空 error_msg"
    raw = result.raw_result
    assert isinstance(raw, dict)
    assert raw.get("code"), "错误面必须有机器码（泛化码也必须有）"
    assert raw.get("success") is False, "错误不得被静默改写为 success"
    assert raw.get("correction_hint"), "必须携带自愈提示（改用在线工具/入库）"
    assert not executed, "失败调用必须释放 dedup 占位"


async def test_unknown_tool_machine_code_not_success(sandbox):
    """未注册工具 → UNKNOWN_TOOL 机器码（错误吞噬检查的负例）。"""
    result = await sandbox.service.dispatch(
        _tc("definitely_not_a_tool", {}), "sess-g03-poi-unknown", set())
    assert result.status == "error"
    assert result.raw_result.get("code") == "UNKNOWN_TOOL"


# ── 3. 热力：本地 fixture → ref-first 产物 + MapSpec 图层证据 ────────


async def test_heatmap_chain_ref_first_with_mapspec_layer(sandbox):
    session = "sess-g03-poi-heat"
    source_ref = await sandbox.store.store(session, _school_fc(40), prefix="geojson")
    result = await sandbox.service.dispatch(
        _tc("heatmap_data", {"geojson": source_ref, "render_type": "native"}),
        session, set())

    assert result.status == "ok"
    # ref-first：新铸 geojson_ref（≠ 输入 ref），载荷不内联大 GeoJSON
    assert result.geojson_ref and result.geojson_ref.startswith("ref:geojson-")
    assert result.geojson_ref != source_ref
    assert '"coordinates"' not in result.llm_payload, (
        "LLM 载荷不得内联要素几何（ref 是唯一载体）")
    # MapSpec 图层证据：layer_id / 指纹 / add_layer 命令 / map_actions
    raw = result.raw_result
    assert raw.get("layer_id"), "矢量分析产物必须挂 MapSpec 图层"
    assert raw.get("mapspec_fingerprint", "").startswith("carto-sha256:")
    commands = raw.get("commands") or []
    assert any(c.get("command") == "add_layer" for c in commands)
    assert result.map_actions and result.map_actions[0].get("action_id")
    # slim_event（SSE 面）携带脱敏投影而非要素体
    assert result.slim_event.get("result_ref") == result.geojson_ref
    assert '"coordinates"' not in str(result.slim_event.get("mapspec"))


async def test_heatmap_insufficient_points_machine_code(sandbox):
    """5 点 < HEATMAP_MIN_POINTS → 工具侧确定性拒绝 + 结构化机器码。"""
    session = "sess-g03-poi-few"
    small_ref = await sandbox.store.store(session, _school_fc(5), prefix="geojson")
    result = await sandbox.service.dispatch(
        _tc("heatmap_data", {"geojson": small_ref, "render_type": "native"}),
        session, set())
    assert result.status == "error"
    assert result.raw_result.get("code") == "INSUFFICIENT_POINTS_FOR_HEATMAP"
    assert result.raw_result.get("correction_hint"), "定量护栏必须给替代路径提示"


# ── 4. 统计：透明 ref 解引用 → 最近邻点格局 ─────────────────────────


async def test_statistics_consumes_ref_without_inline_payload(sandbox):
    session = "sess-g03-poi-stats"
    source_ref = await sandbox.store.store(session, _school_fc(40), prefix="geojson")
    result = await sandbox.service.dispatch(
        _tc("nearest_neighbor", {"geojson": source_ref}), session, set())
    assert result.status == "ok"
    raw = result.raw_result
    assert raw.get("success") is True
    data = raw.get("data") or {}
    assert {"mean_nearest_distance", "expected", "R", "pattern"} <= set(data)
    assert "Nearest Neighbor" in (raw.get("summary") or "")
    # inline_small 契约：统计结果不铸造 geojson_ref（无几何产物）
    assert result.geojson_ref is None


# ── 5. 跨会话隔离：ref 不串 ──────────────────────────────────────────


async def test_no_cross_session_ref_leak(sandbox):
    sess_a, sess_b = "sess-g03-poi-a", "sess-g03-poi-b"
    fc_a = _school_fc(40)
    ref_a = await sandbox.store.store(sess_a, fc_a, prefix="geojson")

    # 会话 B 引用会话 A 的 ref → 解引用失败（结构化错误），绝不泄漏载荷
    cross = await sandbox.service.dispatch(
        _tc("nearest_neighbor", {"geojson": ref_a}), sess_b, set())
    assert cross.status == "error"
    assert cross.raw_result.get("code") == "VALIDATION_ERROR"
    assert "无法找到引用数据" in (cross.error_msg or "")

    # 两会话各自产热力 → 铸出的 ref 必须互不相同且互不可见
    heat_a = await sandbox.service.dispatch(
        _tc("heatmap_data", {"geojson": ref_a, "render_type": "native"}),
        sess_a, set())
    ref_b = await sandbox.store.store(sess_b, _school_fc(40), prefix="geojson")
    heat_b = await sandbox.service.dispatch(
        _tc("heatmap_data", {"geojson": ref_b, "render_type": "native"}),
        sess_b, set())
    assert heat_a.status == "ok" and heat_b.status == "ok"
    assert heat_a.geojson_ref != heat_b.geojson_ref
    assert await sandbox.store.get(sess_b, heat_a.geojson_ref) is None
    assert await sandbox.store.get(sess_a, heat_b.geojson_ref) is None


# ── 6. 链路中经 dispatch 调 catalog_conformance 解释工具选择 ─────────


async def test_catalog_conformance_in_chain_explains_tool_choice(sandbox):
    """tier-2 只读披露工具经同一 dispatch chokepoint 调用：8 面完整 +
    裁决词表封闭；对 query_local_poi 的披露与其离线现实一致（warning 档）。"""
    result = await sandbox.service.dispatch(
        _tc("catalog_conformance", {"tool": "query_local_poi"}),
        "sess-g03-poi-catalog", set())
    assert result.status == "ok"
    raw = result.raw_result
    assert raw.get("tool") == "query_local_poi"
    assert raw.get("overall") in _VERDICTS
    faces = {f["face"]: f for f in raw.get("faces", [])}
    assert set(faces) == _FACES
    for verdict in faces.values():
        assert verdict["status"] in _VERDICTS
    # 现状锚（审计发现）：descriptor 未声明凭证（在线降级实际消费
    # AMAP_API_KEY）→ dispatch 面按声明判 pass；registration 面因
    # deterministic 缺席告警。钉住现状供漂移检测，不迁就。
    assert faces["registration"]["status"] == "warning"
    assert faces["registration"]["reason"] == "metadata_incomplete"
    assert faces["dispatch"]["reason"] == "gate_facts_coherent"
    assert "authoritative" in (raw.get("note") or "")


async def test_catalog_conformance_heatmap_tool_self_consistent(sandbox):
    """链路中的热力工具自身的对账健康度：无 fail 面。"""
    result = await sandbox.service.dispatch(
        _tc("catalog_conformance", {"tool": "heatmap_data"}),
        "sess-g03-poi-catalog2", set())
    assert result.status == "ok"
    raw = result.raw_result
    assert raw.get("overall") in _VERDICTS - {"fail"}
    faces = {f["face"]: f for f in raw.get("faces", [])}
    assert set(faces) == _FACES
