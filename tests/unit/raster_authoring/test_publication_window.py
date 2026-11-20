"""G26 P2 — 出版窗口渲染：地理对齐 / 部分相交 / nodata / 冻结身份。

与瓦片服务同语义（窗口读、冻结 stretch、全通道有效才绘制），但目标网格是
出版画布的一个窗口。已知答案栅格 + 像素级断言（不是「存在 PNG 就算过」）。
"""
from __future__ import annotations

import io
import os

import numpy as np
import pytest
from PIL import Image
from rasterio.transform import from_origin

from app.schemas.raster_spec import RasterExpressionError
from app.services.raster_authoring.publication_window import (
    invalidate_publication_window_cache,
    render_publication_window,
)
from tests.unit.raster_authoring.helpers import write_tif


@pytest.fixture(autouse=True)
def _clear_window_cache():
    invalidate_publication_window_cache()
    yield
    invalidate_publication_window_cache()


def _spec_payload(bands=(1,), mode="single", stretch=(0.0, 400.0),
                  colormap=None, data_revision="fp:test"):
    from app.schemas.raster_spec import RasterExpressionSpec, RasterStretchBounds

    spec = RasterExpressionSpec(
        mode=mode, bands=list(bands),
        stretch=RasterStretchBounds(min=stretch[0], max=stretch[1]),
        colormap=colormap, data_revision=data_revision,
    ).finalize()
    return spec.model_dump(mode="json")


def _stat_key(path):
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


def _png_pixels(png_bytes):
    return np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))


def _regional_tif(owner_root, *, name="regional.tif", crs="EPSG:4326",
                  values=None, nodata=None, bands=1):
    """10..20°E / 15..25°N（南向北行序由 from_origin 语义决定）。"""
    return write_tif(
        owner_root / name, bands=bands, width=8, height=8,
        dtype="float32", nodata=nodata, values=values, crs=crs,
        transform=from_origin(10.0, 25.0, 10.0 / 8, 10.0 / 8),
    )


class TestFullOverlapAlignment:
    def test_view_equals_raster_extent_pixel_grid(self, owner_root):
        """视图=栅格范围、同分辨率：角像元值经冻结 stretch 后逐角命中。"""
        rows, cols = np.mgrid[0:8, 0:8]
        values = (rows * 10 + cols).astype("float32")  # 0..70
        path = _regional_tif(owner_root, values=values[None])
        res = render_publication_window(
            str(path), _stat_key(path), _spec_payload(),
            [10.0, 15.0, 20.0, 25.0], 8, 8,
        )
        assert res.overlap == "full" and res.width == 8 and res.height == 8
        # bounds 回投 ≈ 栅格范围（取整松弛 ≤ 半像元）
        assert res.bounds_wsen[0] == pytest.approx(10.0, abs=0.05)
        assert res.bounds_wsen[3] == pytest.approx(25.0, abs=0.05)
        px = _png_pixels(res.png_bytes)
        # 值域 0..77（row*10+col）；西北角 0 → 0，东北 (0,7)=7 → 4，
        # 东南 (7,7)=77 → 49（冻结 stretch 0..400 的已知答案）。
        assert px[0, 0, 0] == 0 and px[0, 0, 3] == 255
        assert px[0, 7, 0] == pytest.approx(round(7 / 400 * 255), abs=2)
        assert px[7, 7, 0] == pytest.approx(round(77 / 400 * 255), abs=2)

    def test_left_half_view_only_reads_left_half(self, owner_root):
        """部分相交：视图只盖左半 → 输出 bounds=左半；右半像元绝不入图。"""
        rows, cols = np.mgrid[0:8, 0:8]
        values = (rows * 10 + cols).astype("float32")
        path = _regional_tif(owner_root, values=values[None])
        res = render_publication_window(
            str(path), _stat_key(path), _spec_payload(),
            [10.0, 15.0, 15.0, 25.0], 8, 8,  # 只盖西半（10..15°E）
        )
        assert res.overlap in ("partial", "full")
        assert res.bounds_wsen[0] >= 9.9 and res.bounds_wsen[2] <= 15.6
        px = _png_pixels(res.png_bytes)
        # 西半最大值 col=3（左半窗口含取整余量，但绝不超过 col≈4 的值域）
        half_max = round(7 * 10 + 4 / 400 * 255)
        valid = px[..., 3] > 0
        assert valid.any()
        assert int(px[..., 0][valid].max()) <= half_max + 3

    def test_3857_source_aligns_through_transform(self, owner_root):
        """EPSG:3857 源 → 同一 4326 视图的像元值与 4326 源一致（重投影正确性）。"""
        rows, cols = np.mgrid[0:8, 0:8]
        values = (rows * 10 + cols).astype("float32")
        p4326 = _regional_tif(owner_root, values=values[None], name="a4326.tif")
        r4326 = render_publication_window(
            str(p4326), _stat_key(p4326), _spec_payload(),
            [10.0, 15.0, 20.0, 25.0], 32, 32,
        )
        # 同一几何在 3857 网格上重建（from_origin 单位=米）
        from rasterio.warp import transform as proj_pts
        xs = np.linspace(10.0, 20.0, 9)
        ys = np.linspace(25.0, 15.0, 9)
        mx, my = proj_pts("EPSG:4326", "EPSG:3857", list(xs), list(ys))
        resx = (mx[1] - mx[0])
        resy = (my[0] - my[1])
        values3857 = np.repeat(values[None], 1, axis=0)
        p3857 = write_tif(
            owner_root / "a3857.tif", bands=1, width=8, height=8,
            dtype="float32", values=values3857, crs="EPSG:3857",
            transform=from_origin(mx[0], my[0], resx, resy),
        )
        r3857 = render_publication_window(
            str(p3857), _stat_key(p3857), _spec_payload(),
            [10.0, 15.0, 20.0, 25.0], 32, 32,
        )
        a = _png_pixels(r4326.png_bytes)
        b = _png_pixels(r3857.png_bytes)
        # 两个投影链的角/中心像元灰度一致（容差：重采样核差异）
        for (y, x) in [(1, 1), (16, 16), (30, 30), (1, 30), (30, 1)]:
            assert abs(int(a[y, x, 0]) - int(b[y, x, 0])) <= 8, (y, x, a[y, x], b[y, x])
            assert a[y, x, 3] == b[y, x, 3] == 255


class TestExoticCrs:
    def test_unwarpable_crs_typed_reject_not_silent(self, owner_root):
        """A05：可解析但 GDAL 无法 warp 的 CRS → typed 拒绝（诚实失败，
        不静默、不错位输出）。"""
        import rasterio
        from rasterio.transform import from_origin

        wkt = (
            'PROJCS["FakeOblique",GEOGCS["GCS_WGS_1984",'
            'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]],'
            'PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]],'
            'PROJECTION["Hotine_Oblique_Mercator_Two_Point_Natural_Origin"],'
            'PARAMETER["False_Easting",0],PARAMETER["False_Northing",0],'
            'UNIT["Meter",1]]')
        rows, cols = np.mgrid[0:8, 0:8]
        vals = (rows * 10 + cols).astype("float32")[None]
        path = owner_root / "oblique.tif"
        with rasterio.open(
            path, "w", driver="GTiff", width=8, height=8, count=1,
            dtype="float32", crs=wkt,
            transform=from_origin(10.0, 25.0, 1.25, 1.25),
        ) as dst:
            dst.write(vals)
        with pytest.raises(RasterExpressionError) as ei:
            render_publication_window(
                str(path), _stat_key(path), _spec_payload(),
                [10.0, 15.0, 20.0, 25.0], 64, 64,
            )
        assert ei.value.code in ("INVALID_CRS", "RENDER_FAILED"), ei.value.code


class TestRotatedAffine:
    def test_rotated_hot_pixel_lands_at_projected_position(self, owner_root):
        """旋转 affine（review P1-1 回归）：热像元必须落在其地理投影位置，
        空角由 coverage 透明（不得 north-up 重建错位 ~1/3 画幅）。"""
        import rasterio
        from rasterio.transform import Affine, from_origin

        # 100×100 网格绕像幅中心旋转 25°（像素空间）；单热像元 (60, 40)
        size, res = 100, 0.1
        base = from_origin(10.0, 25.0, res, res)
        half = size / 2.0
        rot = base * (
            Affine.translation(half, half) * Affine.rotation(25.0)
            * Affine.translation(-half, -half))
        values = np.full((size, size), -9999.0, dtype="float32")
        hot_col, hot_row = 60, 40
        values[hot_row, hot_col] = 500.0
        path = owner_root / "rotated.tif"
        with rasterio.open(
            path, "w", driver="GTiff", width=size, height=size, count=1,
            dtype="float32", crs="EPSG:4326", transform=rot, nodata=-9999.0,
        ) as dst:
            dst.write(values, 1)
        # nearest：单热像元不被双线性核涂抹（质心 = 投影位置 ±1px）；
        # style_id 事后不可改（篡改检测）—— 从构造期带 nearest。
        from app.schemas.raster_spec import RasterExpressionSpec, RasterStretchBounds

        spec = RasterExpressionSpec(
            mode="single", bands=[1],
            stretch=RasterStretchBounds(min=0.0, max=500.0),
            resampling="nearest", data_revision="fp:test",
        ).finalize().model_dump(mode="json")
        res = render_publication_window(
            str(path), _stat_key(path), spec,
            [9.0, 14.0, 21.0, 26.0], 120, 120,
        )
        assert res.overlap in ("full", "partial")
        px = _png_pixels(res.png_bytes)
        # 热像元地理坐标 → **结果图像素**（图幅自身 3857 包络；y 轴线性于
        # merc-y 而非 lat —— 断言口径与渲染网格同构）。
        from rasterio.warp import transform as _proj_pts

        gx, gy = rot * (hot_col + 0.5, hot_row + 0.5)
        (mx,), (my,) = _proj_pts("EPSG:4326", "EPSG:3857", [gx], [gy])
        b = res.bounds_3857
        ex = (mx - b[0]) / (b[2] - b[0]) * res.width
        ey = (b[3] - my) / (b[3] - b[1]) * res.height
        a = px[:, :, 3]
        lit = np.argwhere(a > 0)
        assert len(lit) > 0, "旋转源必须有可见像元"
        ys, xs = lit[:, 0], lit[:, 1]
        # 可见像元质心 ≈ 热像元投影位置（±3px：nearest 像元归属 + 包络取整）
        assert abs(xs.mean() - ex) <= 3.0, (xs.mean(), ex)
        assert abs(ys.mean() - ey) <= 3.0, (ys.mean(), ey)
        # 旋转包络空角必须透明（coverage 不满覆盖）
        assert a.mean() < 0.98, "空角不得被涂满（coverage 需真实覆盖）"


class TestNoOverlapAndNodata:
    def test_disjoint_view_returns_transparent_not_edge_pixels(self, owner_root):
        """完全不相交：透明 1×1 + overlap=none —— 绝不夹边缘像元。"""
        rows, cols = np.mgrid[0:8, 0:8]
        path = _regional_tif(owner_root, values=(rows * 10 + cols).astype("float32")[None])
        res = render_publication_window(
            str(path), _stat_key(path), _spec_payload(),
            [100.0, 50.0, 110.0, 60.0], 8, 8,  # 完全不相交
        )
        assert res.overlap == "none" and res.all_nodata
        px = _png_pixels(res.png_bytes)
        assert px.shape == (1, 1, 4) and px[0, 0, 3] == 0
        assert "no_overlap_with_view" in res.notes

    def test_nodata_pixels_transparent(self, owner_root):
        rows, cols = np.mgrid[0:8, 0:8]
        values = (rows * 10 + cols).astype("float32")
        values[:, :4] = -9999.0  # 左半 nodata
        path = _regional_tif(owner_root, values=values[None], nodata=-9999.0)
        res = render_publication_window(
            str(path), _stat_key(path), _spec_payload(),
            [10.0, 15.0, 20.0, 25.0], 16, 16,
        )
        px = _png_pixels(res.png_bytes)
        assert px[:16, :8, 3].max() == 0, "nodata 半区必须透明"
        assert px[:16, 8:, 3].min() == 255, "有效半区必须不透明"

    def test_rgb_all_channels_valid_rule(self, owner_root):
        """RGB：任一通道 nodata → 该像元透明（黑色填充不冒充实测）。"""
        rows, cols = np.mgrid[0:8, 0:8]
        b1 = (rows * 10 + cols).astype("float32")
        b2 = b1.copy()
        b3 = b1.copy()
        b3[2:6, 2:6] = -9999.0  # 只污染 B 通道的中央块
        values = np.stack([b1, b2, b3])
        path = _regional_tif(owner_root, values=values, nodata=-9999.0, bands=3)
        res = render_publication_window(
            str(path), _stat_key(path),
            _spec_payload(bands=(1, 2, 3), mode="rgb"),
            [10.0, 15.0, 20.0, 25.0], 16, 16,
        )
        px = _png_pixels(res.png_bytes)
        # 源 b3[2:6,2:6] 的中央块在 16×16 网格 ≈ rows/cols 4..12（bilinear
        # 边缘内缩）：角 (2,2) 有效、中央 (8,8) 透明。
        assert px[2, 2, 3] == 255, "角落必须有效"
        assert px[8, 8, 3] == 0, "B 通道 nodata 的中央块必须透明"


class TestFrozenIdentityAndBounds:
    def test_stat_key_mismatch_rejects(self, owner_root, tmp_path):
        rows, cols = np.mgrid[0:8, 0:8]
        path = _regional_tif(owner_root, values=(rows * 10 + cols).astype("float32")[None])
        stale_key = (1, 1)
        with pytest.raises(RasterExpressionError) as ei:
            render_publication_window(
                str(path), stale_key, _spec_payload(),
                [10.0, 15.0, 20.0, 25.0], 8, 8,
            )
        assert ei.value.code == "STALE_BINDING"

    def test_spec_tampering_rejected(self, owner_root):
        rows, cols = np.mgrid[0:8, 0:8]
        path = _regional_tif(owner_root, values=(rows * 10 + cols).astype("float32")[None])
        payload = _spec_payload()
        payload["style_id"] = "tampered"
        with pytest.raises(RasterExpressionError) as ei:
            render_publication_window(
                str(path), _stat_key(path), payload,
                [10.0, 15.0, 20.0, 25.0], 8, 8,
            )
        assert ei.value.code == "STALE_STYLE"

    def test_invalid_view_bounds_typed_reject(self, owner_root):
        rows, cols = np.mgrid[0:8, 0:8]
        path = _regional_tif(owner_root, values=(rows * 10 + cols).astype("float32")[None])
        with pytest.raises(RasterExpressionError) as ei:
            render_publication_window(
                str(path), _stat_key(path), _spec_payload(),
                [20.0, 15.0, 10.0, 25.0], 8, 8,
            )
        assert ei.value.code == "RENDER_FAILED"

    def test_max_side_truncation_disclosed(self, owner_root):
        rows, cols = np.mgrid[0:8, 0:8]
        path = _regional_tif(owner_root, values=(rows * 10 + cols).astype("float32")[None])
        res = render_publication_window(
            str(path), _stat_key(path), _spec_payload(),
            [10.0, 15.0, 20.0, 25.0], 4096, 4096,
        )
        assert res.width == 2048 and res.height == 2048
        assert res.truncated
        assert any("resolution_capped" in n for n in res.notes)

    def test_content_addressed_cache_hit(self, owner_root):
        rows, cols = np.mgrid[0:8, 0:8]
        path = _regional_tif(owner_root, values=(rows * 10 + cols).astype("float32")[None])
        args = (str(path), _stat_key(path), _spec_payload(),
                [10.0, 15.0, 20.0, 25.0], 64, 64)
        a = render_publication_window(*args)
        b = render_publication_window(*args)
        assert a.png_bytes == b.png_bytes, "同键窗口必须命中缓存（同 bytes）"
        # 不同窗口（视图不同）绝不串缓存
        c = render_publication_window(*args[:3], [11.0, 15.0, 20.0, 25.0], 64, 64)
        assert c.png_bytes != a.png_bytes
