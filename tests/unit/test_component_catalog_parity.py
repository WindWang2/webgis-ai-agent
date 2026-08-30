"""Component catalog contract: backend registry ↔ checked-in frontend artifact.

scripts/export_component_catalog.py writes
frontend/lib/map-components/component-catalog.generated.json; this test
regenerates the payload and fails on drift, so the frontend parity test and
the backend registry can never silently diverge.
"""
import json
from pathlib import Path

import pytest

from app.lib.cartography.export_component_catalog import OUTPUT, build_catalog

pytestmark = pytest.mark.unit


def test_checked_in_catalog_matches_backend_registry():
    assert OUTPUT.exists(), (
        "frontend/lib/map-components/component-catalog.generated.json missing — "
        "run: python scripts/export_component_catalog.py"
    )
    checked_in = json.loads(OUTPUT.read_text(encoding="utf-8"))
    fresh = build_catalog()
    assert checked_in == fresh, (
        "component-catalog.generated.json is stale vs backend registry — "
        "re-run scripts/export_component_catalog.py"
    )


def test_catalog_renderer_required_types_are_renderable_family():
    catalog = build_catalog()
    by_type = {c["type"]: c for c in catalog["componentTypes"]}
    # 面板/图例/导航家族必须有 renderer（chart_panel / statistics_panel 是
    # 本轮闭环目标，不允许再出现"后端可启用、前端无渲染"的占位）
    for required in (
        "chart_panel", "statistics_panel", "legend", "continuous_colorbar",
        "categorical_legend", "north_arrow", "scale_bar", "title", "subtitle",
        "attribution",
    ):
        assert by_type[required]["rendererRequired"] is True
    # export/basemap 家族豁免（导出器侧/类型占位）
    for exempt in ("export_layout", "basemap"):
        assert by_type[exempt]["rendererRequired"] is False


def test_catalog_file_in_frontend_tree():
    # 契约文件必须在前端目录内（frontend 测试从相对路径读取）
    path = Path(OUTPUT)
    assert "frontend" in path.parts
    assert path.name == "component-catalog.generated.json"


def test_catalog_exports_renderer_and_exporter_support():
    """phase-2：目录携带 rendererSupport/exporterSupport 机器真值字段."""
    catalog = build_catalog()
    by_type = {c["type"]: c for c in catalog["componentTypes"]}
    # 与 component_renderers.py 矩阵一致的抽查（ADR-0081：legend 族
    # exporter 支持随 spec 组件导出落地）
    assert sorted(by_type["legend"]["exporterSupport"]) == ["pdf", "png", "svg"]
    assert by_type["title"]["rendererSupport"] == ["interactive"]
    assert sorted(by_type["title"]["exporterSupport"]) == ["pdf", "png", "svg"]
    assert by_type["graticule"]["rendererSupport"] == ["interactive"]  # P3 live 落地
    # v2 P1：inset_map 渲染器落地 —— native + rendererRequired
    assert by_type["inset_map"]["rendererSupport"] == ["interactive"]
    assert sorted(by_type["inset_map"]["exporterSupport"]) == ["pdf", "png", "svg"]
    assert by_type["inset_map"]["runtimeStatus"] == "native"
    assert by_type["inset_map"]["rendererRequired"] is True
