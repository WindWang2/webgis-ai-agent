"""V6（ADR-0120 W3）TS 投影契约：幂等 + 契约面锁定。

- types.generated.ts 必须与当前 schema 投影 byte 级一致（防手改生成物/
  防 schema 改动后忘记再生成 —— 与 component-catalog 同纪律）。
- SCHEMA_EXPORT_MODELS 导出面变化必须显式更新本测试。
"""
from pathlib import Path

from app.lib.cartography.ts_projection import (
    OUTPUT,
    emit_typescript,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestTsProjectionContract:
    def test_committed_file_matches_current_projection(self):
        assert OUTPUT.exists(), "types.generated.ts 不存在 —— 运行 `python -m app.lib.cartography.ts_projection`"
        committed = OUTPUT.read_text(encoding="utf-8")
        assert committed == emit_typescript(), (
            "types.generated.ts 与 schema 投影漂移 —— 改了 mapspec_schema.py "
            "后必须再生成（禁止手改生成文件）"
        )

    def test_projection_is_deterministic(self):
        assert emit_typescript() == emit_typescript()

    def test_generated_file_has_do_not_edit_header(self):
        content = OUTPUT.read_text(encoding="utf-8")
        assert "DO NOT EDIT" in content
        assert "AUTO-GENERATED" in content

    def test_spine_names_present(self):
        """re-export 面（types.ts `export *`）依赖的核心名字必须在场。"""
        content = emit_typescript()
        for name in (
            "MapSpec",
            "MapSpecView",
            "MapSpecSource",
            "GeoJSONMapSpecSource",
            "VectorMapSpecSource",
            "RasterMapSpecSource",
            "DataFabricMapSpecSource",
            "MapSpecLayer",
            "MapSpecLayerPaint",
            "MapSpecLayerLayout",
            "MapSpecLayerLabel",
            "MapSpecLayoutConfig",
            "MapSpecLegendConfig",
            "MapSpecControlConfig",
            "MapSpecComponent",
            "ComponentPlacement",
            "MapThresholds",
            "MapSpecFrame",
            "LayerOverride",
            "FramePageSize",
            "MapLabelConfig",
            "ClusterSourceConfig",
            "StyleMethod",
        ):
            assert f"export interface {name}" in content or f"export type {name}" in content, name

    def test_v6_additive_surface_in_projection(self):
        """V6 additive（frames/labels）必须出现在 TS 投影中。"""
        content = emit_typescript()
        assert "frames?:" in content
        assert "labels?:" in content
        assert 'collision?: "deterministic"' in content
