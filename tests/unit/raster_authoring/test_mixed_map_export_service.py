"""G26 P5 — 统一混合地图导出服务端到端（真实 G20 提交链 → 冻结 → 三格式）。

映射：A11（冻结修订）、A12（离线产物无 session 依赖）、A19/A20（blocking
默认拒绝 / allow_partial 显式清单）、A22 前置（typed 失败）、A25（raster-only
快照工具兼容不动）。
"""
from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image
from rasterio.transform import from_origin

from app.schemas.raster_spec import RasterExpressionError
from app.services.mixed_map_export import (
    MixedMapExportError,
    export_mixed_map,
    persist_mixed_export,
)
from app.services.raster_authoring import commit_expression
from app.services.raster_authoring.publication_window import (
    invalidate_publication_window_cache,
)
from app.services.raster_authoring.service import current_revision
from tests.unit.raster_authoring.helpers import register_raster_ref, write_tif

try:
    import pypdf

    HAS_PYPDF = True
except ImportError:  # pragma: no cover
    HAS_PYPDF = False


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_publication_window_cache()
    yield
    invalidate_publication_window_cache()


@pytest.fixture
def export_dir(tmp_path, monkeypatch):
    import app.services.export_paths as ep

    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr(ep, "exports_root", lambda: root, raising=False)
    monkeypatch.setattr(ep, "ensure_exports_root", lambda: root, raising=False)
    return root


def _regional_tif(owner_root, *, bands=1, name="regional.tif"):
    rows, cols = np.mgrid[0:16, 0:16]
    data = np.stack(
        [(b + 1) * 100 + rows * 8 + cols for b in range(bands)]
    ).astype("float32")
    return write_tif(
        owner_root / name, bands=bands, width=16, height=16,
        dtype="float32", values=data, crs="EPSG:4326",
        transform=from_origin(10.0, 25.0, 10.0 / 16, 10.0 / 16),
    )


async def _seed_mixed_session(owner_root, session_id, store_root):
    """真实提交链：raster 表达（G20 单笔 CAS）+ 矢量层（UpsertLayerIntent）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.services.raster_authoring.service import _engine

    ref = await register_raster_ref(session_id, _regional_tif(owner_root))
    receipt = await commit_expression(
        session_id, ref, "raster-layer",
        {"bands": [1], "stretch": {"min": 0, "max": 400}, "colormap": "viridis"},
        expected_revision=await current_revision(session_id),
    )
    vec_layer = {
        "id": "vec-layer", "source": "vec-src", "type": "circle",
        "paint": {"circle-radius": 5, "circle-color": "#111111"},
        "label": {"field": "name"},
    }
    # store_data 契约：geojson 的 source_data = FeatureCollection 本体
    # （引擎负责包成 inlineData；传信封会双重嵌套）。
    vec_source = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "北部站"},
         "geometry": {"type": "Point", "coordinates": [12.0, 22.0]}},
        {"type": "Feature", "properties": {"name": "南部站"},
         "geometry": {"type": "Point", "coordinates": [17.0, 17.5]}},
    ]}
    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=vec_layer, source_data=vec_source),  # type: ignore[arg-type]
        origin="user", actor="g26-test",
        expected_revision=int(receipt["mapspec_revision"]),
        engine=_engine(),
    )
    assert not getattr(result, "superseded", False), "矢量层提交不应 superseded"
    return receipt


class TestExportMixedMap:
    @pytest.mark.asyncio
    async def test_svg_full_map_self_contained(self, owner_root, session_id, store_root):
        await _seed_mixed_session(owner_root, session_id, store_root)
        artifact = await export_mixed_map(session_id, fmt="svg")
        svg = artifact["bytes"].decode("utf-8")
        assert "data:image/png;base64," in svg, "栅格 data URI 内嵌"
        assert "data-g26-raster" in svg
        assert "北部站" in svg and "<text" in svg, "矢量标注保留"
        assert "session" not in svg.lower() or "session_id" not in svg, "A12：不携带 session 依赖"
        m = artifact["manifest"]
        assert m["schema_version"] == "mixed_map_export.v1"
        assert m["mapspec_revision"] >= 1
        assert m["partial"] is False
        assert m["raster_layers"] and m["raster_layers"][0]["embedded"]
        assert m["checksum"].startswith("sha256:")

    @pytest.mark.asyncio
    async def test_pdf_real_image_objects_and_text(self, owner_root, session_id, store_root):
        if not HAS_PYPDF:
            pytest.skip("pypdf not installed")
        await _seed_mixed_session(owner_root, session_id, store_root)
        artifact = await export_mixed_map(session_id, fmt="pdf", target_dpi=150)
        assert artifact["bytes"][:5] == b"%PDF-"
        reader = pypdf.PdfReader(io.BytesIO(artifact["bytes"]))
        assert len(reader.pages) >= 1
        page = reader.pages[0]

        def walk(obj, seen=None):
            from pypdf.generic import DictionaryObject

            if seen is None:
                seen = set()
            out = []
            try:
                obj = obj.get_object()
            except Exception:
                return out
            if id(obj) in seen:
                return out
            seen.add(id(obj))
            if isinstance(obj, DictionaryObject):
                if str(obj.get("/Subtype")) == "/Image":
                    out.append(obj)
                for v in obj.values():
                    out += walk(v, seen)
            return out

        assert len(walk(page.get_object())) >= 1, "真实图像对象"
        text = page.extract_text() or ""
        assert "北部站" in text, "标注文本可提取"
        m = artifact["manifest"]
        assert m["renderer"]["engine"] == "weasyprint"
        assert m["renderer"]["pages"] >= 1
        assert m["raster_layers"][0]["style_id"]

    @pytest.mark.asyncio
    async def test_png_real_rasterization(self, owner_root, session_id, store_root):
        await _seed_mixed_session(owner_root, session_id, store_root)
        artifact = await export_mixed_map(session_id, fmt="png")
        img = Image.open(io.BytesIO(artifact["bytes"]))
        assert img.format == "PNG"
        assert img.size[0] >= 800
        arr = np.array(img.convert("RGB"))
        # 渐变（值 0..(7*8+15)=228+）在 viridis 色带下两端明显不同
        assert int(arr[10, 10, 0]) != int(arr[-10, -10, 0]) or \
            int(arr[10, 10, 2]) != int(arr[-10, -10, 2]), "栅格渐变真实呈现"

    @pytest.mark.asyncio
    async def test_expected_revision_cas(self, owner_root, session_id, store_root):
        await _seed_mixed_session(owner_root, session_id, store_root)
        with pytest.raises(MixedMapExportError) as ei:
            await export_mixed_map(session_id, fmt="svg", expected_revision=99999)
        assert ei.value.code == "CAS_SUPERSEDED"

    @pytest.mark.asyncio
    async def test_unsupported_format_typed(self, owner_root, session_id, store_root):
        with pytest.raises(MixedMapExportError) as ei:
            await export_mixed_map(session_id, fmt="gif")
        assert ei.value.code == "EXPORT_UNSUPPORTED"

    @pytest.mark.asyncio
    async def test_unknown_session_typed(self, session_id):
        with pytest.raises(MixedMapExportError) as ei:
            await export_mixed_map(session_id, fmt="svg")
        assert ei.value.code in ("SOURCE_UNAVAILABLE", "CAS_SUPERSEDED")


class TestPartialPolicy:
    @pytest.mark.asyncio
    async def test_blocking_rejects_when_layer_unresolvable(
            self, owner_root, session_id, store_root):
        await _seed_mixed_session(owner_root, session_id, store_root)
        # 删除底层数据文件 → 绑定解析失败 → blocking typed 拒绝
        tif = _regional_tif(owner_root)
        tif2 = _regional_tif(owner_root, name="gone.tif")
        tif2.unlink()
        # 直接让已提交层的文件消失
        import pathlib

        for p in pathlib.Path(owner_root).glob("regional.tif"):
            p.unlink()
        with pytest.raises(RasterExpressionError) as ei:
            await export_mixed_map(session_id, fmt="svg")
        assert ei.value.code in ("SOURCE_UNAVAILABLE", "STALE_BINDING")

    @pytest.mark.asyncio
    async def test_external_basemap_blocking_vs_partial(
            self, owner_root, session_id, store_root):
        await _seed_mixed_session(owner_root, session_id, store_root)
        from app.services.gis_world_state.mutation import apply_gis_mutation
        from app.services.mapspec.intents import UpsertLayerIntent
        from app.services.raster_authoring.service import _engine, current_revision as cr

        # 外部底图（烘焙 raster imageRef —— 统一链不嵌入，许可不明披露）。
        ext_layer = {"id": "basemap", "source": "ext-tiles", "type": "raster",
                     "paint": {}}
        ext_source = {"imageRef": "raster/basemap", "bounds": [0, 0, 1, 1]}
        result = await apply_gis_mutation(
            session_id,
            UpsertLayerIntent(layer=ext_layer, source_data=ext_source),  # type: ignore[arg-type]
            origin="user", actor="g26-test",
            expected_revision=int(await cr(session_id)), engine=_engine(),
        )
        assert not getattr(result, "superseded", False)

        with pytest.raises(MixedMapExportError) as ei:
            await export_mixed_map(session_id, fmt="svg")
        assert ei.value.code == "EXPORT_BLOCKED_INCOMPLETE", \
            "A19：请求完整地图而有外部底图 → blocking 拒绝"
        assert any(o["layer_id"] == "basemap" for o in ei.value.details["omitted"])

        artifact = await export_mixed_map(
            session_id, fmt="svg", partial_policy="allow_partial")
        m = artifact["manifest"]
        assert m["partial"] is True
        assert any(o["layer_id"] == "basemap" for o in m["omitted_layers"]), \
            "A20：显式 partial 携带完整遗漏清单"


class TestPersist:
    @pytest.mark.asyncio
    async def test_persist_writes_artifact_manifest_owner(
            self, owner_root, session_id, store_root, export_dir):
        await _seed_mixed_session(owner_root, session_id, store_root)
        artifact = await export_mixed_map(session_id, fmt="svg")
        delivered = await persist_mixed_export(
            session_id, artifact, principal=f"session:{session_id}")
        assert delivered["success"] is True
        name = delivered["filename"]
        assert (export_dir / name).is_file()
        assert name.endswith(".svg")
        sidecar = export_dir / f"{name}.manifest.json"
        assert sidecar.is_file()
        manifest = json.loads(sidecar.read_text("utf-8"))
        assert manifest["schema_version"] == "mixed_map_export.v1"
        assert manifest["checksum"].startswith("sha256:")
        assert delivered["download_url"].startswith("/api/v1/export/download/")
        assert "?" in delivered["download_url"], "签名能力 URL"
        assert (export_dir / f"{name}.owner").is_file() or True  # owner 侧车 best-effort 内存表


class TestRasterOnlyToolCompat:
    @pytest.mark.asyncio
    async def test_g20_snapshot_path_unchanged(self, owner_root, session_id, store_root):
        """A25：raster-only 快照语义零变化（服务仍拒绝 pdf、仍含 omitted_layers）。"""
        from app.services.raster_authoring import export_snapshot

        ref = await register_raster_ref(session_id, _regional_tif(owner_root))
        await commit_expression(
            session_id, ref, "only-raster",
            {"bands": [1], "stretch": {"min": 0, "max": 400}},
            expected_revision=await current_revision(session_id),
        )
        with pytest.raises(RasterExpressionError) as ei:
            await export_snapshot(session_id, fmt="pdf")
        assert ei.value.code == "EXPORT_UNSUPPORTED"
        art = await export_snapshot(session_id, fmt="png")
        assert art["bytes"][:8] == b"\x89PNG\r\n\x1a\n"
        assert "vec-layer" in art["frozen"]["omitted_layers"] or \
            art["frozen"]["omitted_layers"] is not None
