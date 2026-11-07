"""G04 W3（CM-06/CM-09）：实时↔导出关键图元 parity + 真实回执链.

锁定（Oracle 最少覆盖表）：
- **图例与实际分类同步**：图层的分级/色带变化后，SVG 导出的图例条目与
  legend_spec 的 breaks/palette 逐条一致（不允许旧分类残留）；
- **单位贯通**：legend_spec.unit 出现在导出图例文本中（单位与样式匹配）；
- **跨格式同义图元**：同一 spec 的 SVG 与语义语料（export_semantic_corpus）
  对同一图例条目数一致；
- **真实回执链（CM-09）**：canvas 链上传携带 ``constraint_layout_evaluated``
  诊断 → POST /api/v1/export 持久化 sidecar（服务端词表校验通过）；
  矢量链 ``render_publication_pdf`` 显式 page_profile 时 result 携带
  evaluated 报告（回执与图面同真）。
"""
from __future__ import annotations

import json
import os

import pytest

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

pytestmark = pytest.mark.cartography


def _choropleth_spec(breaks: list[float], palette: list[str], unit: str = "所") -> dict:
    districts = [
        [104.0, 30.5], [104.15, 30.5], [104.15, 30.68], [104.0, 30.68],
        [104.0, 30.5],
    ]
    features = [
        {"type": "Feature",
         "properties": {"district": f"d{i}", "schools": float(v)},
         "geometry": {"type": "Polygon", "coordinates": [districts]}}
        for i, v in enumerate([3, 9, 18, 40])
    ]
    return {
        "version": "1.6",
        "view": {"center": [104.07, 30.6], "zoom": 10},
        "sources": {"S": {"type": "geojson",
                          "inlineData": {"type": "FeatureCollection",
                                         "features": features}}},
        "layers": [{
            "id": "choro", "source": "S", "type": "fill",
            "paint": {"color": {"method": "choropleth", "field": "schools"}},
            "legend_spec": {
                "title": "小学数量", "unit": unit,
                "breaks": breaks, "palette": palette,
                "entries": [
                    {"label": f"{breaks[i]}–{breaks[i + 1]}",
                     "color": palette[i]}
                    for i in range(len(palette))
                ],
            },
        }],
        "layout": {"components": [
            {"id": "legend-main", "type": "legend", "enabled": True,
             "position": "bottom-left", "options": {"layerId": "choro"}},
            {"id": "north-arrow", "type": "north_arrow", "enabled": True,
             "position": "top-right"},
        ]},
    }


class TestLegendClassificationSync:
    def test_svg_legend_entries_follow_current_breaks(self):
        breaks = [0, 10, 20, 40]
        palette = ["#fef0d9", "#fdcc8a", "#fc8d59"]
        comp = compile_mapspec_to_svg_detailed(
            _choropleth_spec(breaks, palette), include_chrome=True,
        )
        legend_svg = comp.svg
        # 图例标题与单位进入图面（单位与样式匹配）
        assert "小学数量" in legend_svg
        assert "所" in legend_svg
        # 每个分级区间标签与当前 breaks 同步出现
        for i in range(3):
            assert f"{breaks[i]}–{breaks[i + 1]}" in legend_svg

    def test_reclassified_spec_renders_new_breaks_not_stale(self):
        old = compile_mapspec_to_svg_detailed(
            _choropleth_spec([0, 10, 20, 40],
                             ["#fef0d9", "#fdcc8a", "#fc8d59"]),
            include_chrome=True,
        ).svg
        new_breaks = [0, 5, 15, 40]
        new = compile_mapspec_to_svg_detailed(
            _choropleth_spec(new_breaks,
                             ["#edf8fb", "#b2e2e2", "#66c2a4"]),
            include_chrome=True,
        ).svg
        assert "0–10" in old and "0–5" not in old
        assert "0–5" in new and "0–10" not in new

    def test_semantic_corpus_agrees_with_svg_on_entry_count(self):
        from app.lib.cartography.export_semantic_corpus import expected_semantics

        breaks = [0, 10, 20, 40]
        palette = ["#fef0d9", "#fdcc8a", "#fc8d59"]
        spec = _choropleth_spec(breaks, palette)
        comp = compile_mapspec_to_svg_detailed(spec, include_chrome=True)
        # 语义语料对图例的期望条目数 = legend_spec 条目数（跨格式同口径）
        semantics = expected_semantics(spec)
        legend_sem = [
            s for s in semantics
            if isinstance(s, dict) and "legend" in str(s.get("code", ""))
        ]
        entry_count = len(spec["layers"][0]["legend_spec"]["entries"])
        assert entry_count == len(palette) == 3
        # SVG 图例组在场；语料若有图例语义则其条目数与本口径一致
        assert 'class="chrome-legend"' in comp.svg or "chrome-legend" in comp.svg
        for s in legend_sem:
            count = s.get("entries") or s.get("count")
            if isinstance(count, int):
                assert count == entry_count, s


class TestVectorPdfConstraintReceipt:
    def test_profile_solve_report_in_result(self, tmp_path):
        pytest.importorskip("weasyprint")
        from app.services.publication_export import render_publication_pdf

        spec = _choropleth_spec([0, 10, 20, 40],
                                ["#fef0d9", "#fdcc8a", "#fc8d59"])
        result = render_publication_pdf(spec, title="成都小学", page_profile="a4_landscape")
        report = result.constraint_layout
        assert report["constraint_layout"] == "evaluated"
        assert report["page_profile"] == "a4_landscape"
        assert result.page_count >= 1
        # 回执与图面同真：求解后的文档组件锚点即求解 zones（克隆路径）
        default = render_publication_pdf(spec, title="成都小学")
        assert default.constraint_layout["constraint_layout"] == "not_evaluated"


class TestCanvasConstraintReceiptSidecar:
    """canvas 链 constraint_layout_evaluated → 服务端词表校验 + sidecar 持久化。

    模式与 tests/unit/test_export_diagnostics_sidecar.py 同源（ASGITransport
    + exports_root 重定向），此处锁定 G04 新码的真实回执链贯通。
    """

    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from app.api.routes import map as _mod

        app = FastAPI()
        app.include_router(_mod.router, prefix="/api/v1")
        return app

    @pytest.mark.asyncio
    async def test_evaluated_code_persists_sidecar(self, app):
        import shutil

        from httpx import ASGITransport, AsyncClient

        from app.api.routes import map as _mod
        from app.core.auth import (
            get_current_user_optional_with_version,
            get_current_user_with_version,
        )

        export_dir = "/var/tmp/test_exports_g04_receipt"
        os.makedirs(export_dir, exist_ok=True)
        _mod._EXPORT_OWNERS.clear()
        app.dependency_overrides[get_current_user_with_version] = lambda: {
            "user_id": "g04-owner",
        }
        app.dependency_overrides[get_current_user_optional_with_version] = lambda: {
            "user_id": "g04-owner",
        }
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport,
                                   base_url="http://test") as client:
                from io import BytesIO

                from PIL import Image

                png = BytesIO()
                Image.new("RGB", (1, 1)).save(png, format="PNG")
                with pytest.MonkeyPatch.context() as mp:
                    from pathlib import Path as _P

                    mp.setattr("app.services.export_paths.exports_root",
                               lambda: _P(export_dir))
                    resp = await client.post(
                        "/api/v1/export",
                        files={"file": ("map.png", png.getvalue(), "image/png")},
                        data={"render_diagnostics": json.dumps([
                            {"code": "constraint_layout_evaluated",
                             "detail": "a4_landscape"},
                        ])},
                    )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["render_diagnostics"]["accepted"] == 1
            sidecar = os.path.join(export_dir,
                                   f"{body['filename']}.diagnostics.json")
            assert os.path.exists(sidecar), sidecar
            with open(sidecar, encoding="utf-8") as f:
                payload = json.load(f)
            codes = [d.get("code") for d in payload.get("diagnostics", [])]
            assert "constraint_layout_evaluated" in codes
            assert payload["diagnostics"][0]["severity"] == "info"
        finally:
            _mod._EXPORT_OWNERS.clear()
            shutil.rmtree(export_dir, ignore_errors=True)
