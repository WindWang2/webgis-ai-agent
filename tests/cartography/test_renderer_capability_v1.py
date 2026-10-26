"""RendererCapabilityV1 声明面测试（Goal 03，ADR-0225）。

锁定：单一真相引用（layer_capability / component_renderers 不复制）、
schema 词表全对账（source types ⊆ mapspec_schema union；layer types ==
词表）、required capabilities 裁决（unsupported 不静默放行）、fingerprint
稳定性 + 诚实红线（#1617 已合并：渲染端可执行表达
``semantic_zoom_representation = supported: true``；artifact 模式
``generalization`` 无供给链仍 = false，绝不虚报）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography import renderer_capability as rc
from app.lib.cartography.layer_capability import LAYER_TYPE_SUPPORT
from app.lib.cartography.mapspec_schema import (
    DataFabricMapSpecSource,
    MapSpecModelSource,
    GeoJSONMapSpecSource,
    RasterDemMapSpecSource,
    RasterMapSpecSource,
    VectorMapSpecSource,
)

pytestmark = pytest.mark.cartography


class TestManifestSingleSource:
    def test_layer_types_reference_layer_capability(self):
        manifest = rc.capability_manifest()
        assert manifest["layer_types"] == {
            t: s.level for t, s in sorted(LAYER_TYPE_SUPPORT.items())
        }

    def test_component_types_from_registry(self):
        from app.lib.cartography.component_renderers import (
            get_component_renderer_registry,
        )

        assert rc.component_type_keys() == sorted(
            get_component_renderer_registry().all_types
        )

    def test_schema_source_vocab_covered(self):
        """schema 源 union 的每一个 type literal 都必须有声明（词表对账）。"""
        literals = set()
        for model in (
            GeoJSONMapSpecSource, VectorMapSpecSource, RasterMapSpecSource,
            RasterDemMapSpecSource, MapSpecModelSource,
        ):
            field = model.model_fields["type"]
            literals.update(field.annotation.__args__)
        literals.update(DataFabricMapSpecSource.model_fields["type"].annotation.__args__)
        declared = set(rc.SOURCE_TYPE_SUPPORT)
        assert literals == declared, (
            f"schema vocab {sorted(literals - declared)} undeclared; "
            f"extra {sorted(declared - literals)}"
        )

    def test_fingerprint_deterministic(self):
        assert rc.capability_fingerprint() == rc.capability_fingerprint(
            rc.capability_manifest()
        )

    def test_fingerprint_reacts_to_change(self):
        m = rc.capability_manifest()
        m["features"]["terrain"] = not m["features"]["terrain"]
        assert rc.capability_fingerprint(m) != rc.capability_fingerprint()


class TestPendingCapabilityHonesty:
    """红线：只把已实现声明为 supported（#1617 已合并，2026-10-18）。"""

    def test_renderer_executable_representation_supported(self):
        # v1.6 渲染端可执行模式端到端落地（后端生产方 + 前端 compiler/
        # runtime 消费）—— capability 面不得再锁在旧值。
        assert rc.FEATURE_SUPPORT["semantic_zoom_representation"]["supported"] is True

    def test_artifact_generalization_not_supported(self):
        # artifact 模式（aggregate_grid/generalized）需预计算工件 ——
        # master 无供给链，绝不虚报。
        assert rc.FEATURE_SUPPORT["generalization"]["supported"] is False

    def test_unsupported_required_capability_fails_check(self):
        verdict = rc.check_required_capabilities(["generalization"])
        assert verdict["supported"] is False
        assert "generalization" in verdict["unsupported"]

    def test_supported_required_capability_passes_check(self):
        verdict = rc.check_required_capabilities(["semantic_zoom_representation"])
        assert verdict["supported"] is True
        assert verdict["unsupported"] == []

    def test_degraded_partial_source(self):
        verdict = rc.check_required_capabilities(["data_fabric"])
        assert verdict["supported"] is True
        assert verdict["degraded"] == ["data_fabric"]


class TestRequiredCapabilityVerdict:
    def test_mixed_keys(self):
        verdict = rc.check_required_capabilities([
            "circle", "geojson", "component:legend", "heatmap", "wms", "bogus",
        ])
        assert verdict["unsupported"] == ["bogus", "wms"]
        assert verdict["supported"] is False

    def test_component_unknown_unsupported(self):
        verdict = rc.check_required_capabilities(["component:nonexistent"])
        assert verdict["supported"] is False

    def test_empty_ok(self):
        assert rc.check_required_capabilities([]) == {
            "supported": True, "unsupported": [], "degraded": [],
        }


class TestDesignSystemExport:
    def test_manifest_rides_design_system(self):
        from app.lib.cartography.design_system import build_design_system_manifest

        manifest = build_design_system_manifest()
        rcm = manifest["renderCapabilityManifest"]
        assert rcm["schema_version"] == rc.CAPABILITY_SCHEMA_VERSION
        assert rcm["fingerprint"] == rc.capability_fingerprint()
        assert rcm["source_types"]["geojson"] == "full"


if __name__ == "__main__":
    pytest.main([__file__])
