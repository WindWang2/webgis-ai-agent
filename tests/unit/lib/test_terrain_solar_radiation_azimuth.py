"""solar_radiation 方位项回归（#1720）—— 方位角约定统一到罗盘后的符号锁定。

``solar_radiation`` 的坡向必须与仓库既有 ESRI 罗盘约定一致（真相源
``app.services.rs.band_math.compute_aspect``：aspect = atan2(−dzdx, dzdy)，
0=北、顺时针；DEM row 0 = north、col 0 = west —— 见
``tests/unit/test_terrain_compass.py``）。旧实现写 ``atan2(−gx, −gy)`` 使
坡向整体南北镜像 180°（N↔S、NE↔SE、SW↔NW，E/W 重合）：朝阳坡欠照、
背阳坡过照。本文件用 8 个已知方位的等坡度斜坡钉死符号：辐照量必须随
|az_sun − aspect| 单调衰减，并与 hillshade / compute_aspect 交叉断言防漂移。
"""
import math

import numpy as np
import pytest

from app.lib.geo_analysis.terrain import hillshade_multiazimuth, solar_radiation
from app.services.rs.band_math import compute_aspect

CELL = 30.0
LAT = 45.0
DOY_SOLSTICE = 172      # 北半球夏至：δ ≈ +23.45°，正午太阳在南
DOY_EQUINOX = 81        # 春分附近：δ ≈ 0，代表方位 ≈ 正南

# 8 个罗盘方位（下坡向 = 坡面朝向，与 GIS aspect 同义）
_ASPECTS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0,
            "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}


def _declination(doy: int) -> float:
    """FAO-56 赤纬（与实现同式，仅测试侧独立复算）。"""
    return 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)


def _ramp(azimuth_deg: float, k: float = 0.35, n: int = 9) -> np.ndarray:
    """下坡向 = 罗盘方位 ``azimuth_deg`` 的等坡度平面（坡度 atan(k)）。

    ``xx`` 沿列（东）递增、``yy`` 沿行（**南**）递增 —— 仓库约定 row 0 =
    north、col 0 = west（band_math.compute_aspect / test_terrain_compass）。
    高度 = −k·(东向·sin A − 南向·cos A) 使下坡向恰为方位 A（与
    ``compute_aspect`` 判定的罗盘坡向一致，见 test_ramp_aspect_matches_band_math）。"""
    a = math.radians(azimuth_deg)
    xx, yy = np.meshgrid(np.arange(n) * CELL, np.arange(n) * CELL)
    return -k * (xx * math.sin(a) - yy * math.cos(a))


def _means(doy: int) -> dict:
    out = {}
    for name, az in _ASPECTS.items():
        sol, _ = solar_radiation(
            _ramp(az), CELL, latitude_deg=LAT, day_of_year=doy
        )
        out[name] = float(np.nanmean(sol))
    return out


def _order_by_sun_distance(doy: int) -> list:
    """按方位与代表方位 az_sun = π + δ 的角距从小到大排序。"""
    az_sun = math.pi + _declination(doy)
    def distance(name: str) -> float:
        d = abs(az_sun - math.radians(_ASPECTS[name])) % (2.0 * math.pi)
        return min(d, 2.0 * math.pi - d)
    return sorted(_ASPECTS, key=distance)


def test_azimuth_term_monotone_in_sun_distance():
    """方位项 = cos(az_sun − aspect)：角距越大辐照越少（等坡度平面）。"""
    means = _means(DOY_SOLSTICE)
    order = _order_by_sun_distance(DOY_SOLSTICE)
    # 夏至 45°N：代表方位 ≈ 203.4°（ SSW）→ SW 最朝阳、NE 最背阳
    assert order[0] == "SW" and order[-1] == "NE"
    values = [means[name] for name in order]
    assert all(a >= b - 1e-9 for a, b in zip(values, values[1:])), (order, means)
    # 旧实现（整体旋转 90°）会把极大挪到 SE、极小挪到 NW —— 反向排序即旧行为
    assert means["SW"] > means["NE"]
    assert means["S"] > means["N"]      # 夏至南坡朝阳、北坡背阳
    assert means["W"] > means["E"]      # 代表方位偏南偏西 → 西坡优于东坡


def test_equinox_south_facing_sunniest_north_facing_least():
    """春秋分 δ≈0 → 代表方位 ≈ 正南：南坡最晒、北坡最少（物理锚）。"""
    means = _means(DOY_EQUINOX)
    assert max(means, key=means.get) == "S"
    assert min(means, key=means.get) == "N"


def test_flat_dem_insolation_independent_of_azimuth():
    """零坡度下方位项不生效（cos β=1）→ 各方位斜坡与平面同值。"""
    flat = np.full((6, 6), 100.0)
    sol_flat, meta = solar_radiation(
        flat, CELL, latitude_deg=LAT, day_of_year=DOY_SOLSTICE
    )
    assert float(np.nanmean(sol_flat)) == pytest.approx(
        meta["extraterrestrial_ra"] * 0.75, rel=1e-4
    )


def test_ramp_aspect_matches_band_math():
    """#1720 交叉断言：_ramp 构造的坡向必须与真相源 compute_aspect 一致。

    旧 _ramp（yy 沿行递增却自称「北」递增）构造的是镜像 180° 的坡面，
    曾把 solar 的镜像行为钉死；此处对所有内部像元逐一比对罗盘坡向。"""
    for name, az in _ASPECTS.items():
        dem = _ramp(az, n=9)
        aspect = compute_aspect(dem, CELL)
        interior = aspect[2:-2, 2:-2]
        assert np.all(np.isfinite(interior)), name
        diff = np.abs((interior - az + 180.0) % 360.0 - 180.0)
        assert float(np.nanmax(diff)) < 1e-6, (name, float(np.nanmax(diff)))


def test_solar_agrees_with_hillshade_multiazimuth():
    """#1720 交叉断言：solar 的坡向符号与 hillshade_multiazimuth（=band_math
    compute_hillshade 复算）同向 —— 单一方位 180°（正南）光照下南坡亮、
    北坡暗，solar 的辐照排序必须同号。"""
    sun_az = 180.0
    shade = {}
    for name in _ASPECTS:
        hs, _ = hillshade_multiazimuth(
            _ramp(_ASPECTS[name]), CELL, altitude=45.0, azimuths=(sun_az,)
        )
        shade[name] = float(np.nanmean(hs))
    assert shade["S"] > shade["N"], shade
    means = _means(DOY_EQUINOX)
    # 春秋分代表方位 ≈ 正南：solar 的南/北优劣须与 hillshade 同向
    assert (means["S"] > means["N"]) == (shade["S"] > shade["N"])
    assert means["S"] > means["N"]
