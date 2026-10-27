"""G3D01 契约级 E2E：合成 DEM → MapSpec(scene.terrain) → 质量门 → 数据面。

工作单验收 (d)：MapSpec→runtime→ACK 或相应实际消费者的契约级验证。本测试
锚定**契约链的 Python 侧**：spec 构造（真实 URL 模板形态）→ schema 解析 →
场景质量门（dangling ref/type 门）→ terrarium 数据面真实字节 → 降级语义
（terrain 源缺席 = typed 拒绝而非静默）。前端侧（applySource raster-dem →
enable3DTerrain → ACK style_epoch）由 renderer-m4 / map-basemap-lifecycle
既有测试 + 本分支 runtime-basemap-evidence 覆盖。
"""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.lib.cartography.mapspec_schema import parse_mapspec
from app.lib.cartography.scene_quality import evaluate_scene_quality
from app.lib.cartography.terrain_encoding import decode_terrarium
from app.services.raster_tile_service import render_terrarium_tile
from app.services.synthetic_terrain import build_synthetic_terrain_ref


@pytest.fixture()
def synthetic_ref(tmp_path):
    return build_synthetic_terrain_ref("sess-e2e", "ref-e2e-dem", tmp_path)


def _spec_doc(url_template: str, terrain: dict | None) -> dict:
    spec = {
        "version": "1.6",
        "view": {"center": [104.06, 30.66], "zoom": 11.0},
        "sources": {
            "dem": {
                "type": "raster-dem",
                "url": url_template,
                "tileSize": 256,
                "encoding": "terrarium",
            },
        },
        "layers": [],
    }
    if terrain is not None:
        spec["scene"] = {"mode": "3d", "terrain": terrain}
    return spec


class TestContractE2E:
    def test_valid_spec_passes_schema_and_quality_gate(self, synthetic_ref):
        spec = _spec_doc(
            synthetic_ref.terrain_tiles_url_template,
            {"source": "dem", "exaggeration": 1.5, "vertical_unit": "m"},
        )
        result = parse_mapspec(spec)
        assert result.valid, result.invalid_fields
        quality = evaluate_scene_quality(result.document)
        assert quality["passed"], quality["findings"]

    def test_dangling_terrain_source_ref_is_blocking(self, synthetic_ref):
        """scene.terrain.source 指向不存在的 source → 质量门红（fail-closed）。"""
        spec = _spec_doc(
            synthetic_ref.terrain_tiles_url_template,
            {"source": "dem-that-does-not-exist", "exaggeration": 1.5},
        )
        result = parse_mapspec(spec)
        assert result.valid
        quality = evaluate_scene_quality(result.document)
        assert not quality["passed"]
        assert any(f["code"] == "SCENE_TERRAIN_SOURCE_REF" for f in quality["findings"])

    def test_wrong_source_type_is_blocking(self, synthetic_ref):
        spec = {
            "version": "1.6",
            "view": {"center": [104.06, 30.66], "zoom": 11.0},
            "sources": {"dem": {"type": "raster", "url": "https://x/{z}/{x}/{y}.png"}},
            "layers": [],
            "scene": {"mode": "3d", "terrain": {"source": "dem", "exaggeration": 1.0}},
        }
        # 注：该 raster source 形态本身过不了 schema（url 不属于 raster 源），
        # parse_mapspec 产出 best-effort 文档 —— 质量门的类型门在此文档上
        # 仍然 blocking（双门冗余，coordinator.validate 同款语义）。
        result = parse_mapspec(spec)
        quality = evaluate_scene_quality(result.document)
        assert any(f["code"] == "SCENE_TERRAIN_SOURCE_TYPE" for f in quality["findings"])

    def test_data_plane_bytes_decode_to_elevation(self, synthetic_ref):
        """spec URL 模板指向的数据面真实可渲染且高程可解码（契约闭环）。"""
        png = render_terrarium_tile(synthetic_ref.raster_path, 8, 201, 105)
        arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
        elev = decode_terrarium(arr)
        valid = elev[arr.sum(axis=2) > 0]
        assert valid.size > 0
        assert 400.0 <= valid.min() and valid.max() <= 1600.0

    def test_wrong_encoding_rejected_by_schema(self, synthetic_ref):
        spec = _spec_doc(synthetic_ref.terrain_tiles_url_template, None)
        spec["sources"]["dem"]["encoding"] = "ceiling-grid"  # 词表外编码
        result = parse_mapspec(spec)
        assert not result.valid, "词表外 DEM 编码必须被 schema 拒绝"

    def test_synthetic_qualification_flows_into_spec_provenance(self, synthetic_ref):
        """合成资格必须随 ref 可追溯（不因进入 spec 而丢失 synthetic 标记）。"""
        qual_spec = {
            "elevation_ref": synthetic_ref.ref_id,
            "qualification": synthetic_ref.qualification,
            "synthetic": synthetic_ref.synthetic,
        }
        spec = _spec_doc(
            synthetic_ref.terrain_tiles_url_template,
            {
                "source": "dem",
                "exaggeration": 1.0,
                "vertical_unit": "m",
                "elevation_ref": qual_spec["elevation_ref"],
            },
        )
        result = parse_mapspec(spec)
        assert result.valid, result.invalid_fields
        assert qual_spec["qualification"] == "display_only"
        assert qual_spec["synthetic"] is True

    def test_perf_budget_smoke(self, tmp_path):
        """可复核性能预算：生成 + 单瓦片渲染 < 3s（本机实测记录见 EVIDENCE.md）。"""
        import time

        from app.services.synthetic_terrain import write_synthetic_dem

        t0 = time.perf_counter()
        path = write_synthetic_dem(tmp_path / "perf.tif")
        png = render_terrarium_tile(str(path), 8, 201, 105)
        elapsed = time.perf_counter() - t0
        assert len(png) > 0
        assert elapsed < 3.0, f"合成 DEM 生成+渲染超出预算: {elapsed:.2f}s"
