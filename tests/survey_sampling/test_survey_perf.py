"""survey_sampling 性能基线（WP6）：帧构建/选样/估计全链计时 + 峰值 RSS。

风格（cartography perf 同款）：确定性合成数据；实测数字 print 进 stdout
（账本引用）；只设病态兜底阈值（不设严格 CI 计时闸，机器差异敏感）。

场景：
- 1e6 像元分类图（1600×625，3 层不等面积）帧构建 + 60 样本选样 + 估计；
- 2×10⁷ 预算守卫拒绝路径计时（typed 拒绝必须先于大分配）；
- 峰值 RSS（resource.getrusage ru_maxrss 增量）。
"""
from __future__ import annotations

import resource
import time
from pathlib import Path

import numpy as np
import pytest

from app.lib.geo_raster.reader import RasterReader
from app.lib.survey_sampling import estimation as est
from app.lib.survey_sampling.design import (
    SurveyDesign,
    StratumSpec,
    preview_sample_size,
)
from app.lib.survey_sampling.frame import build_frame
from app.lib.survey_sampling.selection import draw_samples

CLASS_DICT = {1: "forest", 2: "crop", 3: "water"}


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _write_cls(path: Path, width: int, height: int, seed: int = 5) -> Path:
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(seed)
    data = rng.choice([1, 2, 3], size=(height, width),
                      p=[0.5, 0.3, 0.2]).astype(np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height,
                       count=1, dtype="uint8", crs="EPSG:6933",
                       transform=from_origin(500000.0, 5200000.0,
                                             100.0, 100.0)) as dst:
        dst.write(data, 1)
    return path


def test_frame_sample_estimate_perf_budget() -> None:
    """1e6 像元全链：<30s（病态兜底；实测打印）。"""
    tmp = Path("/tmp") / f"survey-perf-{time.time_ns()}"
    tmp.mkdir()
    try:
        path = _write_cls(tmp / "cls.tif", 1600, 625)
        rss0 = _rss_mb()
        t0 = time.perf_counter()
        with RasterReader.open(str(path)) as reader:
            frame = build_frame(reader, band=1, class_dictionary=dict(CLASS_DICT),
                                nodata=None, crs="EPSG:6933")
        t_frame = time.perf_counter() - t0
        assert frame.payload["total_units"] == 1_000_000

        strata = [StratumSpec(stratum_id=sid, N_units=int(info["N_units"]),
                              area=float(info["area"]))
                  for sid, info in frame.payload["strata"]]
        t1 = time.perf_counter()
        preview = preview_sample_size(strata, strategy="equal", n_total=60)
        design = SurveyDesign(
            design_id="perf", design_type="stratified_srs",
            frame_ref=frame.digest, frame_crs="EPSG:6933",
            strata=strata, allocation=preview["allocation"],
            backup_per_stratum=5, seed=42, target_time="2026-11",
            objective="perf", max_nonresponse_rate=0.2,
            source_version="fp", class_dictionary_version="{}",
            total_area=float(frame.payload["total_area"]),
        ).with_digest()
        units = draw_samples(design, frame)
        t_draw = time.perf_counter() - t1

        # 模拟 100% 响应的合成标签（性能场景，非科学场景）
        H = len(strata)
        counts = np.zeros((H, H), dtype=np.int64)
        for u in units:
            if u.is_backup:
                continue
            h = [s.stratum_id for s in strata].index(u.stratum_id)
            counts[h, (h + 1) % H] += 1          # 全错的确定性标签
        t2 = time.perf_counter()
        res = est.stratified_estimate(
            est.StratifiedCounts(
                strata=[s.stratum_id for s in strata],
                ref_classes=[s.stratum_id for s in strata],
                W=np.array([s.area / sum(x.area for x in strata)
                            for s in strata]),
                N=np.array([s.N_units for s in strata], dtype=np.float64),
                counts=counts,
                n_planned=np.array([preview["allocation"][s.stratum_id]
                                    for s in strata], dtype=np.int64),
                excluded=np.zeros(H, dtype=np.int64)),
            total_area=float(frame.payload["total_area"]))
        t_est = time.perf_counter() - t2

        rss1 = _rss_mb()
        print(f"[survey perf] 1e6 cells: frame={t_frame:.2f}s "
              f"design+draw={t_draw:.3f}s estimate={t_est * 1000:.1f}ms "
              f"peakRSS+={rss1 - rss0:.0f}MB")
        assert t_frame < 30.0, f"frame build too slow: {t_frame:.1f}s"
        assert t_draw < 10.0, f"draw too slow: {t_draw:.1f}s"
        assert t_est < 1.0, f"estimate too slow: {t_est:.2f}s"
        assert res.status == "supported"
    finally:
        for p in tmp.glob("*.tif"):
            p.unlink()
        tmp.rmdir()


def test_frame_budget_guard_rejects_early() -> None:
    """超预算框：typed 拒绝先于任何大分配（拒绝路径 <5s）。"""
    from app.lib.gis.scientific_errors import ResourceScaleMismatch

    class _HugeMeta:
        width = 200_000
        height = 200_000                      # 4e10 cells ≫ 扫描守卫
        count = 1
        dtype = "uint8"
        crs = "EPSG:6933"
        nodata = None
        transform = [100.0, 0.0, 500000.0, 0.0, -100.0, 5200000.0]
        fingerprint = ""

    class _HugeReader:
        uri = "in-memory://huge"

        def metadata(self):
            return _HugeMeta()

        def read_window(self, window, band=1):
            raise AssertionError("must reject before any block read")

    t0 = time.perf_counter()
    with pytest.raises(ResourceScaleMismatch):
        build_frame(_HugeReader(), band=1,
                    class_dictionary=dict(CLASS_DICT), nodata=None,
                    crs="EPSG:6933")
    dt = time.perf_counter() - t0
    print(f"[survey perf] 4e10-cell guard rejection: {dt * 1000:.1f}ms")
    assert dt < 5.0
