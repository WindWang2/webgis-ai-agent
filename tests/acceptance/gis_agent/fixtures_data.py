"""G05 离线确定性合成数据工厂（全部 simulated，非实测）。

体积有界：点/面 fixture ≤ 64 要素、栅格 ≤ 32×32（<10KB）。空间范围取
成都一带 bbox 仅作空间参考；所有命名与 note 都显式标注「模拟/合成」，
不得暗示实测成都学校/行政区数据。

工厂键（DatasetSpec.dataset_kind）：
- school_points          模拟小学点（12 点，含 district/name 字段）
- school_points_sparse   样本不足（3 点 —— 热力资格 fail 的科学负例）
- school_points_dense    多点（40 点，热力资格充足）
- no_coord_table         无经纬度字段（仅有名称/地址属性行 → 澄清/拒绝例）
- district_polygons      模拟行政区面（5 面，含 name/population 面积可求）
- wrong_crs_points       坐标为 GCJ 假定/投影系错配（超成都 bbox 的平面坐标）
- unit_conflict_points   数值字段单位冲突（enrollment 学生数 vs area 亩/公顷混标）
- temporal_school_points 带时间字段（opened_year 三期）
- raster_nir / raster_red  模拟波段栅格（float32 32×32，NDVI 分子/分母）
- raster_nodata          带 nodata 像元的栅格（-9999 标记）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

#: 成都一带参考 bbox（WGS84；仅空间参考，数据为合成）。
CHENGDU_BBOX = (103.95, 30.55, 104.15, 30.70)

_WGS84 = {"type": "name", "properties": {"name": "EPSG:4326"}}


def _fc(features: list, crs: Dict[str, Any] | None = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if crs is not None:
        out["crs"] = crs
    return out


def _pt(lon: float, lat: float, props: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": props,
    }


def school_points() -> Dict[str, Any]:
    """模拟成都小学点位（合成；12 点，跨 5 个模拟区）。"""
    districts = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区"]
    feats = []
    for i in range(12):
        lon = CHENGDU_BBOX[0] + 0.02 + (i % 6) * 0.028
        lat = CHENGDU_BBOX[1] + 0.03 + (i // 6) * 0.06
        feats.append(_pt(lon, lat, {
            "name": f"模拟小学-{i:02d}",
            "district": districts[i % len(districts)],
            "enrollment": 300 + i * 47,          # 模拟在校生数（人）
            "_simulated": True,
        }))
    return _fc(feats, _WGS84)


def school_points_sparse() -> Dict[str, Any]:
    fc = school_points()
    fc["features"] = fc["features"][:3]          # 低于热力最小样本量
    return fc


def school_points_dense() -> Dict[str, Any]:
    districts = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区"]
    feats = []
    for i in range(40):
        lon = CHENGDU_BBOX[0] + 0.01 + (i % 10) * 0.017
        lat = CHENGDU_BBOX[1] + 0.025 + (i // 10) * 0.032
        feats.append(_pt(lon, lat, {
            "name": f"模拟小学-D{i:02d}",
            "district": districts[i % len(districts)],
            "_simulated": True,
        }))
    return _fc(feats, _WGS84)


def no_coord_table() -> Dict[str, Any]:
    """「表格数据没有经纬度」负例：features 无 geometry，仅属性。"""
    rows = [
        {"name": "模拟小学-A", "address": "模拟路 1 号", "students": 520},
        {"name": "模拟小学-B", "address": "模拟路 2 号", "students": 610},
        {"name": "模拟小学-C", "address": "模拟路 3 号", "students": 430},
    ]
    return {
        "type": "FeatureCollection",
        "note": "simulated table rows without coordinates",
        "features": [
            {"type": "Feature", "geometry": None,
             "properties": {**r, "_simulated": True}}
            for r in rows
        ],
    }


def district_polygons() -> Dict[str, Any]:
    """模拟行政区面（5 个合成四边形，覆盖点位范围；含人口分母字段）。"""
    x0, y0, x1, y1 = CHENGDU_BBOX
    names = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区"]
    pops = [69_000, 83_000, 71_000, 91_000, 95_000]        # 模拟常住人口
    areas_km2 = [6.0, 6.6, 7.2, 7.8, 8.4]                  # 模拟面积（km²）
    dx = (x1 - x0) / len(names)
    feats = []
    for i, name in enumerate(names):
        ax, bx = x0 + i * dx, x0 + (i + 1) * dx
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [round(ax, 6), round(y0, 6)], [round(bx, 6), round(y0, 6)],
                [round(bx, 6), round(y1, 6)], [round(ax, 6), round(y1, 6)],
                [round(ax, 6), round(y0, 6)],
            ]]},
            "properties": {
                "name": name,
                "population": pops[i],
                "area_km2": areas_km2[i],
                "_simulated": True,
            },
        })
    return _fc(feats, _WGS84)


def wrong_crs_points() -> Dict[str, Any]:
    """错误 CRS 负例：坐标是某投影系平面坐标（数值远超 WGS84 经纬度域）。"""
    feats = []
    for i in range(8):
        feats.append(_pt(482_000.0 + i * 350.0, 3_386_000.0 + i * 120.0, {
            "name": f"模拟点位-CRS{i}", "_simulated": True,
        }))
    # 声明 EPSG:4326 但坐标是米制 —— CRS/坐标域矛盾（科学负例）
    return _fc(feats, _WGS84)


def unit_conflict_points() -> Dict[str, Any]:
    fc = school_points()
    for i, f in enumerate(fc["features"]):
        # 同名字段两种单位混标（亩 vs 公顷 ×15.2）——单位一致性负例
        f["properties"]["campus_area"] = 20.0 + i if i % 2 == 0 else (20.0 + i) / 15.2
        f["properties"]["campus_area_unit"] = "mu" if i % 2 == 0 else "hectare"
    return fc


def temporal_school_points() -> Dict[str, Any]:
    districts = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区"]
    feats = []
    for year in (2015, 2020, 2025):
        for i in range(6):
            lon = CHENGDU_BBOX[0] + 0.02 + i * 0.028
            lat = CHENGDU_BBOX[1] + 0.03 + (year - 2015) * 0.04
            feats.append(_pt(lon, lat, {
                "name": f"模拟小学-{year}-{i}",
                "opened_year": year,
                "district": districts[i % len(districts)],
                "_simulated": True,
            }))
    return _fc(feats, _WGS84)


# ── 栅格（写盘工厂；返回相对 ./tmp 的路径）───────────────────────────────

def _write_tif(path: Path, data: "list", nodata=None) -> str:
    import rasterio
    from rasterio.transform import from_origin
    import numpy as np

    arr = np.array(data, dtype="float32")
    height, width = arr.shape
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height,
        count=1, dtype="float32", crs="EPSG:4326",
        transform=from_origin(CHENGDU_BBOX[2], CHENGDU_BBOX[3], 0.005, 0.005),
        nodata=nodata,
    ) as dst:
        dst.write(arr, 1)
    return str(path)


def _band_matrix(n: int, base: float, spread: float) -> "list":
    """确定性 32×32 波段矩阵（行列式样、无随机源）。"""
    return [
        [round(base + spread * ((r * 7 + c * 3) % 17) / 17.0, 4)
         for c in range(32)]
        for r in range(32)
    ]


def raster_nir(tmp_root: Path) -> str:
    return _write_tif(tmp_root / "g05_sim_nir.tif", _band_matrix(32, 0.35, 0.45))


def raster_red(tmp_root: Path) -> str:
    return _write_tif(tmp_root / "g05_sim_red.tif", _band_matrix(32, 0.12, 0.30))


def raster_nodata(tmp_root: Path) -> str:
    m = _band_matrix(32, 0.4, 0.4)
    for r in range(0, 32, 2):                     # 交错行 nodata（≈50% 缺测）
        for c in range(0, 32, 2):
            m[r][c] = -9999.0
    return _write_tif(tmp_root / "g05_sim_nodata.tif", m, nodata=-9999.0)


#: dataset_kind → (载体类型, 工厂)。geojson = 会话 ref 存储；raster = ./tmp 文件。
def hazard_points() -> Dict[str, Any]:
    """goal-02 F28：模拟危险源点（化工厂 2 处，落在学区范围内）。"""
    x0, y0, x1, y1 = CHENGDU_BBOX
    feats = []
    for i, (dx, dy) in enumerate([(0.05, 0.04), (0.09, 0.07)]):
        feats.append(_pt(x0 + dx, y0 + dy, {
            "name": f"模拟化工厂-{i + 1}",
            "hazard_class": "major",
            "_simulated": True,
        }))
    return _fc(feats, _WGS84)


def road_network() -> Dict[str, Any]:
    """goal-02 F28：模拟路网（覆盖学区的纵横网格道路 LineString）。"""
    x0, y0, x1, y1 = CHENGDU_BBOX
    feats = []
    for i in range(5):            # 纵路
        x = x0 + 0.01 + i * (x1 - x0 - 0.02) / 4
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [
                [round(x, 6), y0 + 0.01], [round(x, 6), y1 - 0.01]]},
            "properties": {"road_id": f"v{i}", "kind": "primary",
                           "_simulated": True},
        })
    for j in range(4):            # 横路
        y = y0 + 0.02 + j * (y1 - y0 - 0.04) / 3
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [
                [x0 + 0.01, round(y, 6)], [x1 - 0.01, round(y, 6)]]},
            "properties": {"road_id": f"h{j}", "kind": "primary",
                           "_simulated": True},
        })
    return _fc(feats, _WGS84)


VECTOR_FACTORIES = {
    "school_points": school_points,
    "hazard_points": hazard_points,
    "road_network": road_network,
    "school_points_sparse": school_points_sparse,
    "school_points_dense": school_points_dense,
    "no_coord_table": no_coord_table,
    "district_polygons": district_polygons,
    "wrong_crs_points": wrong_crs_points,
    "unit_conflict_points": unit_conflict_points,
    "temporal_school_points": temporal_school_points,
}

def _dem_cone_matrix(n: int = 61, nodata_patch: bool = False) -> "list":
    """确定性锥形山体 DEM（61×61，无随机源；nodata_patch = 中央 5×5 洞）。

    投影 CRS（EPSG:32648）下 cell 10 m、UL 原点 (500000, 4000000)——
    与场景 F27 观察点/折线坐标同一坐标系。模拟合成数据（simulated）。
    """
    import math as _math

    out = []
    for r in range(n):
        row = []
        for c in range(n):
            if nodata_patch and 28 <= r <= 32 and 28 <= c <= 32:
                row.append(-9999.0)
            else:
                d = _math.hypot(r - 30, c - 30)
                row.append(round(max(0.0, 100.0 - 2.0 * d), 4))
        out.append(row)
    return out


def _write_dem_tif(path: Path, matrix: "list", nodata=None) -> str:
    import rasterio
    from rasterio.transform import from_origin
    import numpy as np

    arr = np.array(matrix, dtype="float32")
    height, width = arr.shape
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height,
        count=1, dtype="float32", crs="EPSG:32648",
        transform=from_origin(500000.0, 4_000_000.0, 10.0, 10.0),
        nodata=nodata,
    ) as dst:
        dst.write(arr, 1)
    return str(path)


def raster_dem_cone(tmp_root: Path) -> str:
    """G3D-V2-C F27：合成锥形 DEM（正例分析数据资格）。"""
    return _write_dem_tif(tmp_root / "g05_sim_dem_cone.tif", _dem_cone_matrix())


def raster_dem_nodata(tmp_root: Path) -> str:
    """G3D-V2-C F27：中央 nodata 洞的锥形 DEM（负例：观察点落洞）。"""
    return _write_dem_tif(
        tmp_root / "g05_sim_dem_nodata.tif",
        _dem_cone_matrix(nodata_patch=True),
        nodata=-9999.0,
    )


RASTER_FACTORIES = {
    "raster_nir": raster_nir,
    "raster_red": raster_red,
    "raster_nodata": raster_nodata,
    "raster_dem_cone": raster_dem_cone,
    "raster_dem_nodata": raster_dem_nodata,
}

ALL_DATASET_KINDS = sorted({*VECTOR_FACTORIES, *RASTER_FACTORIES})

_SIMULATED_NOTE = "模拟合成数据（simulated；非实测成都学校/行政区）"


def dataset_note(kind: str) -> str:
    return f"{kind}: {_SIMULATED_NOTE}"


def vector_fixture_json(kind: str) -> str:
    """确定性序列化（pin/审计用；sorted keys + 中文原样）。"""
    if kind not in VECTOR_FACTORIES:
        raise KeyError(kind)
    return json.dumps(VECTOR_FACTORIES[kind](), ensure_ascii=False, sort_keys=True)


def make_raster(kind: str, tmp_root: Path) -> Tuple[str, str]:
    """返回 (绝对路径, dispatch 用路径)。

    validate_data_path 允许 cwd 下 ``./tmp`` 工作区（绝对路径 realpath 校验），
    因此 dispatch 用绝对路径；相对路径会被拼到 ./data 下导致错位。
    """
    if kind not in RASTER_FACTORIES:
        raise KeyError(kind)
    abs_path = RASTER_FACTORIES[kind](tmp_root)
    return abs_path, str(Path(abs_path).resolve())
