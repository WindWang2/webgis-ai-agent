"""Pi 工具面指标 + 回归门测试（ADR-0180 D5 / typed-tool-surface M3/T9）。

- 计数器语义：invalid_tool_name / schema_validation_rejected /
  proxy_wrapped / direct_surface / last surface projection；
- /api/v1/metrics/digest 的 additive ``pi_surface`` 段；
- T9 回归门：golden 任务面质量（必达工具 / tier-3 零泄漏 / 字节预算）
  + gate 快速拒绝（不进 wave 排队路径的可观测证明 = 有界时延）。
"""
import time

import pytest

from app.services.chat import pi_surface_metrics as psm


@pytest.fixture(autouse=True)
def _reset_metrics():
    psm.reset_for_tests()
    yield
    psm.reset_for_tests()


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_counter_semantics_and_rate():
    psm.record_invalid_tool_name("nope_tool", reason="unknown")
    psm.record_invalid_tool_name("nope_tool", reason="unknown")
    psm.record_validation_reject("heatmap_data", [{"path": "geojson"}])
    psm.record_surface_call(proxied=True, tool="heatmap_data")
    psm.record_surface_call(proxied=False, tool="webgis_map_intent")
    psm.record_surface_call(proxied=False, tool="query_local_poi")
    snap = psm.snapshot()
    assert snap["invalid_tool_name"] == 2
    assert snap["schema_validation_rejected"] == 1
    assert snap["proxy_wrapped"] == 1
    assert snap["direct_surface"] == 2
    assert snap["surface_calls_total"] == 3
    assert snap["proxy_fallback_rate"] == round(1 / 3, 4)
    assert snap["recent_rejects"][-1]["kind"] == "schema_validation_rejected"
    assert snap["recent_rejects"][-1]["issues"] == ["geojson"]


def test_last_surface_projection_last_wins():
    psm.record_surface_projection(surface_bytes=1000, budget=4096, dropped=2, dynamic_count=12)
    psm.record_surface_projection(surface_bytes=2000, budget=8192, dropped=0, dynamic_count=20)
    snap = psm.snapshot()
    assert snap["last_surface_projection"]["surface_bytes"] == 2000
    assert snap["last_surface_projection"]["dynamic_count"] == 20


def test_metrics_digest_carries_pi_surface_section():
    from fastapi.testclient import TestClient

    from app.core.auth import require_admin
    from app.main import app

    psm.record_surface_call(proxied=True, tool="heatmap_data")
    client = TestClient(app)
    app.dependency_overrides[require_admin] = lambda: {"user_id": "a", "role": "admin"}
    try:
        resp = client.get("/api/v1/metrics/digest")
        assert resp.status_code == 200
        body = resp.json()
        assert "pi_surface" in body
        assert body["pi_surface"]["proxy_wrapped"] == 1
        assert "proxy_fallback_rate" in body["pi_surface"]
    finally:
        app.dependency_overrides.pop(require_admin, None)


# ---------------------------------------------------------------------------
# T9 回归门：golden 任务面质量 + gate 时延上界
# ---------------------------------------------------------------------------

_GOLDEN_SURFACE_CASES = [
    # (query, must_have_any, forbidden_all)
    ("做一份成都市小学密度热力图", {"heatmap_data", "query_local_poi"}, {"create_new_skill"}),
    ("查一下成都的 POI 和行政区划边界", {"query_local_poi", "get_local_admin_boundary"}, set()),
    ("把地图导出成图片", {"finalize_display"}, set()),
]


def test_golden_surface_quality_gate(monkeypatch, registry):
    """面质量门：必达工具可达、tier-3 零泄漏、字节预算内（真实 registry）。"""
    from app.agent_pi_bridge import set_tool_registry

    set_tool_registry(registry)
    monkeypatch.delenv("PI_SURFACE_BYTE_BUDGET", raising=False)
    for query, must_any, forbidden in _GOLDEN_SURFACE_CASES:
        disclosure: dict = {}
        names = set(_compute(monkeypatch, query, disclosure))
        assert names.isdisjoint(forbidden), f"{query}: forbidden leaked {names & forbidden}"
        hit = names & must_any
        assert hit, f"{query}: none of {must_any} reachable (surface={sorted(names)[:8]}...)"
        # tier-3 零泄漏红线
        for n in names:
            assert int(registry.descriptor(n).tier) < 3
        info = disclosure
        if "surface_budget" in info:
            assert info["surface_bytes"] <= info["surface_budget"]


def _compute(monkeypatch, message, disclosure):
    from app.services.chat.pi_native_surface import compute_turn_active_tools

    return compute_turn_active_tools(message, disclosure=disclosure)


def test_gate_reject_latency_bounded(registry):
    """闸快速拒绝：非法参数在有界时延内被拒（未进 wave 排队/ref 解析）。"""
    from app.services.chat.pi_input_gate import validate_pi_tool_arguments

    big_args = {
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i * 0.001, i * 0.002]},
             "properties": {"id": i}} for i in range(2000)
        ]},
        "hallucinated_param": True,
    }
    t0 = time.perf_counter()
    report = validate_pi_tool_arguments(registry, "heatmap_data", big_args)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert report is not None, "unknown-field 应被闸拦截"
    assert elapsed_ms < 250, f"gate took {elapsed_ms:.0f}ms — 应远低于 wave 排队成本"


def test_gate_accept_latency_bounded_small_args(registry):
    from app.services.chat.pi_input_gate import validate_pi_tool_arguments

    t0 = time.perf_counter()
    for _ in range(50):
        assert validate_pi_tool_arguments(
            registry, "query_local_poi",
            {"district": "成都市", "subtype": "小学", "hallucinated": 1},
        ) is not None  # unknown-field 拒绝路径
        assert validate_pi_tool_arguments(
            registry, "query_local_poi", {"district": "成都市", "subtype": "小学"}
        ) is None  # 通过路径
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 2000, f"100 次 gate 共 {elapsed_ms:.0f}ms — 单次应亚毫秒级"
