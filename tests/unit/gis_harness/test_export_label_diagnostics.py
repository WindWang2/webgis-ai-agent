"""Track B（M13）：导出标注确定性 findings 的 critique 接线测试。

- 条件门：无 deterministic collision / 无导出声明 / 无 symbol 层 → 零
  finding（诚实缺席）；
- budget 超限 → export_label_budget_exceeded warning；
- 抑制过半 → export_label_suppressed warning；
- 长文本截断 → export_label_truncated info；
- critique_map_state 聚合面包含该检查且不超 12 条上限。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.map_critique import (
    C_EXPORT_LABEL_BUDGET,
    C_EXPORT_LABEL_SUPPRESSED,
    C_EXPORT_LABEL_TRUNCATED,
    check_export_label_diagnostics,
    critique_map_state,
)
from app.services.mapspec_to_svg import MAX_SVG_LABEL_CHARS


def _spec(n_features=30, collision=True, exports=("png",), name="点", same_pos=True):
    features = []
    for i in range(n_features):
        coord = [116.0, 39.0] if same_pos else [116.0 + (i % 5) * 0.02, 39.0 + (i // 5) * 0.02]
        nm = ("超长学校名称" * 20)[:MAX_SVG_LABEL_CHARS + 10] if i == 0 and name == "long" else f"点{i:02d}"
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coord},
            "properties": {"name": nm},
        })
    # 扩边背景要素（数据范围撑大，标签投影入画布）
    features.insert(0, {"type": "Feature", "geometry": {"type": "Point", "coordinates": [115.8, 38.8]}, "properties": {}})
    features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.8, 39.6]}, "properties": {}})
    spec = {
        "version": "1.1",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"pts": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": features}}},
        "layers": [{
            "id": "sym", "source": "pts", "type": "symbol",
            "layout": {"text-field": "{name}", "text-size": 14},
            "paint": {"text-color": "#111"},
        }],
        "exports": list(exports),
    }
    if collision:
        spec["layout"] = {"labels": {"collision": "deterministic"}}
    return spec


def _codes(findings):
    return [f.code for f in findings]


class TestCheckExportLabelDiagnostics:
    def test_no_collision_mode_no_findings(self):
        assert check_export_label_diagnostics(_spec(collision=False)) == []

    def test_no_export_declaration_no_findings(self):
        assert check_export_label_diagnostics(_spec(exports=())) == []

    def test_budget_exceeded_finding(self, monkeypatch):
        spec = _spec(n_features=30)
        # 编译器以别名持有预算缺省 —— patch 编译器模块内的引用才生效。
        monkeypatch.setattr("app.services.mapspec_to_svg._MAX_LABELS_PER_EXPORT", 5)
        findings = check_export_label_diagnostics(spec)
        codes = _codes(findings)
        assert C_EXPORT_LABEL_BUDGET in codes
        budget = [f for f in findings if f.code == C_EXPORT_LABEL_BUDGET][0]
        assert budget.severity == "warning"
        assert budget.target == "labels"

    def test_suppressed_majority_finding(self):
        # 30 同位点 → 大量抑制（抑制数 > 放置数一半）
        findings = check_export_label_diagnostics(_spec(n_features=30))
        codes = _codes(findings)
        assert C_EXPORT_LABEL_SUPPRESSED in codes
        sup = [f for f in findings if f.code == C_EXPORT_LABEL_SUPPRESSED][0]
        assert "suppressed" in sup.detail

    def test_truncated_long_name_info(self):
        findings = check_export_label_diagnostics(_spec(n_features=8, name="long", same_pos=False))
        codes = _codes(findings)
        assert C_EXPORT_LABEL_TRUNCATED in codes
        tr = [f for f in findings if f.code == C_EXPORT_LABEL_TRUNCATED][0]
        assert tr.severity == "info"

    def test_compile_failure_is_silent_absence(self, monkeypatch):
        import app.services.gis_harness.map_critique as mc

        def _boom(*a, **k):
            raise RuntimeError("compile down")

        monkeypatch.setattr(
            "app.services.mapspec_to_svg.compile_mapspec_to_svg_detailed", _boom
        )
        assert check_export_label_diagnostics(_spec()) == []


class TestCritiqueAggregation:
    def test_export_label_findings_flow_into_critique(self):
        chapter = {
            "layers": [{"layer_id": "sym", "render_complete": True, "feature_count": 30}],
        }
        findings = critique_map_state(chapter, _spec(n_features=30), None)
        codes = _codes(findings)
        assert C_EXPORT_LABEL_SUPPRESSED in codes
        assert len(findings) <= 12

    def test_critique_without_spec_labels_unchanged(self):
        chapter = {"layers": [{"layer_id": "x", "render_complete": True, "feature_count": 1}]}
        findings = critique_map_state(chapter, None, None)
        assert _codes(findings) == []
