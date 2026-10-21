"""H02 — 制图一致性面的性能预算（任务书 §3.F）。

确定性/结构性基准（median-of-N + 宽上限），非速度基准：
  - manifest 装配 100 / 1000 图层元数据（bounded 投影必须近线性且小系数）
  - 语义验证（verify_export_semantic_consistency）100 / 1000 图层
  - 反复导出稳定性（同输入 100 次构建逐字节一致 + 时间有界）
  - required 槽推导全 29 模板（注册表 O(1) 查找形状）

车道：-m perf（与既有 perf 基准同隔离策略，#664）。
"""
import statistics
import time
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.perf

from app.lib.cartography.product_manifest import (
    build_product_manifest,
    verify_export_semantic_consistency,
)


def _layer(i: int) -> Dict[str, Any]:
    return {
        "id": f"l{i}",
        "type": "fill",
        "source": "s1",
        "paint": {"color": {
            "method": "step", "field": "value", "default": "#fcae91",
            "stops": [[6, "#fb6a4a"], [12, "#cb181d"]],
        }},
        "legend_spec": {
            "type": "graduated", "field": "value", "method": "quantiles",
            "breaks": [0, 6, 12],
            "palette_colors": ["#fcae91", "#fb6a4a", "#cb181d"],
        },
        "filter": [">", ["get", "value"], i % 7],
    }


def _spec(n_layers: int) -> Dict[str, Any]:
    return {
        "version": "1.6",
        "view": {"zoom": 11, "center": [104.06, 30.66]},
        "sources": {"s1": {
            "type": "geojson", "ref": "ref:geojson:perf",
            "content_revision": 3, "data_fingerprint": "fp",
            "profile": {"feature_count": 1000},
        }},
        "layers": [_layer(i) for i in range(n_layers)],
        "layout": {
            "components": [
                {"id": "title", "type": "title", "enabled": True},
                {"id": "north_arrow", "type": "north_arrow", "enabled": True},
                {"id": "scale_bar", "type": "scale_bar", "enabled": True},
                {"id": "attribution", "type": "attribution", "enabled": True},
                {"id": "legend", "type": "legend", "enabled": True},
            ],
            "composition": {
                "template_id": "composition.standard_analysis",
                "template_version": "1",
                "component_abi_version": 1,
            },
        },
    }


@pytest.mark.parametrize("n_layers,upper_ms", [(100, 250), (1000, 2000)])
def test_manifest_assembly_budget(n_layers: int, upper_ms: float):
    spec = _spec(n_layers)
    timings: List[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        manifest = build_product_manifest(spec, export_format="png")
        timings.append((time.perf_counter() - t0) * 1000)
        assert len(manifest["layers"]) == min(n_layers, 128), (
            "manifest 图层投影必须有界（128 封顶），不随输入无界增长"
        )
    median = statistics.median(timings)
    assert median < upper_ms, (
        f"{n_layers} 层 manifest 装配中位数 {median:.1f}ms 超预算 {upper_ms}ms"
    )


@pytest.mark.parametrize("n_layers,upper_ms", [(100, 600), (1000, 4000)])
def test_verify_semantics_budget(n_layers: int, upper_ms: float):
    spec = _spec(n_layers)
    timings: List[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        report = verify_export_semantic_consistency(
            spec, export_format="png")
        timings.append((time.perf_counter() - t0) * 1000)
        assert report["status"] in ("pass", "warning")
    median = statistics.median(timings)
    assert median < upper_ms, (
        f"{n_layers} 层语义验证中位数 {median:.1f}ms 超预算 {upper_ms}ms"
    )


def test_repeated_export_manifest_stability():
    """反复导出稳定性：同输入 100 次构建逐字节一致 + 单次时间有界。"""
    spec = _spec(100)
    reference = None
    timings: List[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        manifest = build_product_manifest(spec, export_format="png")
        timings.append((time.perf_counter() - t0) * 1000)
        serialized = repr(manifest)
        if reference is None:
            reference = serialized
        else:
            assert serialized == reference, "manifest 非确定性（导出不可重放）"
    assert statistics.median(timings) < 500


def test_required_slot_resolution_all_templates_budget():
    """29 个组合模板的 required 槽推导：O(1) 注册表查找形状。"""
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    from app.services.gis_harness.completion.inputs import (
        resolve_required_component_slots,
    )

    reg = get_composition_template_registry()
    template_ids = sorted(t.id for t in reg.all_templates())
    assert len(template_ids) >= 29
    spec_tpl = {
        "layout": {"composition": {
            "template_id": "", "template_version": "1",
            "component_abi_version": 1,
        }}}
    timings: List[float] = []
    for tid in template_ids * 10:  # 290 次推导
        spec_tpl["layout"]["composition"]["template_id"] = tid
        t0 = time.perf_counter()
        slots = resolve_required_component_slots({}, spec_tpl)
        timings.append((time.perf_counter() - t0) * 1000)
        assert isinstance(slots, list)
    assert statistics.median(timings) < 5, (
        f"required 槽推导中位数 {statistics.median(timings):.3f}ms —— "
        "注册表查找应保持 O(1) 形状"
    )
