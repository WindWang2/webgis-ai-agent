"""本地行政边界查询（ChinaAdminDivisonSHP 四级 SHP 数据）。

数据契约（README）：
- 目录 ``<LOCAL_GEODATA_DIR>/ChinaAdminDivisonSHP/{1. Country..4. District}/``
- 字段 ``adcode`` + ``pr_name``/``ct_name``/``dt_name`` 前缀体系；
- 坐标系 WGS84 壳、实为 GCJ-02（高德系偏移）——默认原样返回，``to_wgs84``
  显式转换（与 amap 工具链同坐标系语义）。

性能：每 level 的 GeoDataFrame 进程内只读一次（district ~3k 行），
查询在内存过滤；旧实现每次调用重读 SHP 且路径依赖 CWD。
"""
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np
import geopandas as gpd
from shapely.ops import transform as _shapely_transform

from app.core.config import settings
from app.tools.registry import ToolRegistry, tool
from app.utils.coord_transform import gcj02_to_wgs84_array

logger = logging.getLogger(__name__)

LEVELS = ("country", "province", "city", "district")

_LEVEL_FILES = {
    "country": ("1. Country", "country.shp"),
    "province": ("2. Province", "province.shp"),
    "city": ("3. City", "city.shp"),
    "district": ("4. District", "district.shp"),
}

# 每级自身的名称列（README 的 cn/pr/ct/dt 前缀），回退通用候选。
_LEVEL_NAME_COL = {
    "country": ("cn_name", "name", "Name"),
    "province": ("pr_name", "name", "Name"),
    "city": ("ct_name", "name", "Name"),
    "district": ("dt_name", "name", "Name"),
}

# 真实数据的行政编码列同样带级别前缀（ct_adcode/dt_adcode...），裸 adcode
# 作为回退（老版数据集/测试 fixture）。
_LEVEL_ADCODE_COL = {
    "country": ("cn_adcode", "adcode"),
    "province": ("pr_adcode", "adcode"),
    "city": ("ct_adcode", "adcode"),
    "district": ("dt_adcode", "adcode"),
}

_gdf_cache: Dict[str, Optional[gpd.GeoDataFrame]] = {}
_cache_lock = threading.Lock()


def _admin_root() -> Optional[Path]:
    """数据根目录：优先 settings.LOCAL_GEODATA_DIR，回退仓库 data/ 旧布局。"""
    raw = (settings.LOCAL_GEODATA_DIR or "").strip()
    if raw:
        root = Path(raw).expanduser() / "ChinaAdminDivisonSHP"
        if root.is_dir():
            return root
    legacy = Path(__file__).resolve().parents[2] / "data" / "admin_division"
    if legacy.is_dir():
        return legacy
    return None


def _level_path(level: str) -> Optional[Path]:
    root = _admin_root()
    if root is None:
        return None
    sub, fname = _LEVEL_FILES[level]
    path = root / sub / fname
    return path if path.exists() else None


def admin_data_available() -> bool:
    return _level_path("district") is not None


def _load_level(level: str) -> Optional[gpd.GeoDataFrame]:
    """按 level 懒加载并缓存（None 也缓存：数据缺失时避免反复探测磁盘）。"""
    if level in _gdf_cache:
        return _gdf_cache[level]
    with _cache_lock:
        if level in _gdf_cache:
            return _gdf_cache[level]
        path = _level_path(level)
        gdf: Optional[gpd.GeoDataFrame] = None
        if path is not None:
            try:
                gdf = gpd.read_file(path, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 - 查询工具必须可观测地降级
                logger.error("[local_admin] read %s failed: %s", path, exc)
                gdf = None
        _gdf_cache[level] = gdf
        return gdf


def _reset_cache_for_tests() -> None:
    with _cache_lock:
        _gdf_cache.clear()


def _name_column(gdf: gpd.GeoDataFrame, level: str) -> Optional[str]:
    for col in _LEVEL_NAME_COL[level]:
        if col in gdf.columns:
            return col
    return None


def _gcj02_to_wgs84_geometry(geom: Any) -> Any:
    """对单个几何做 GCJ-02 → WGS84 逐顶点转换（向量实现）。"""

    def _fn(x, y, z=None):
        nx, ny = gcj02_to_wgs84_array(
            np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        )
        return (nx, ny) if z is None else (nx, ny, z)

    return _shapely_transform(_fn, geom)


def _project_result(gdf: gpd.GeoDataFrame, *, to_wgs84: bool, simplified: bool) -> gpd.GeoDataFrame:
    if simplified:
        # ~0.001° ≈ 100m：保留政区轮廓形态，抑制全国/省级边界的 MB 级 payload。
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.simplify(0.001, preserve_topology=True)
    if to_wgs84:
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.apply(_gcj02_to_wgs84_geometry)
    return gdf


def _to_feature_collection(
    gdf: gpd.GeoDataFrame, *, note: str, crs: Optional[str] = None
) -> Dict[str, Any]:
    payload = json.loads(gdf.to_json())
    minx, miny, maxx, maxy = (float(v) for v in gdf.total_bounds)
    out: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": payload["features"],
        "count": len(gdf),
        "crs_note": note,
        "total_bounds": [minx, miny, maxx, maxy],
    }
    # G-2（#866）：机器可读的 crs 成员——下游 gdf_from_features/to_utm_gdf
    # 只认 crs 成员（#599/#813 契约），此前坐标系语义只停留在 crs_note 字符串
    # 里，GCJ-02 边界与 WGS84 POI 叠加时 ~100-600m 偏移无法被自动归一。
    if crs:
        out["crs"] = crs
    return out


_CRS_NOTE_GCJ = "GCJ-02（高德系偏移坐标，与 amap 数据同系）"
_CRS_NOTE_WGS = "WGS84（已从 GCJ-02 转换，近似迭代精度 ~1m）"


def query_admin_boundary(
    level: str,
    name: Optional[str] = None,
    adcode: Optional[str] = None,
    *,
    to_wgs84: bool = False,
    simplified: bool = False,
) -> Dict[str, Any]:
    """共享查询实现：工具与 HTTP 路由共用。"""
    if level not in _LEVEL_FILES:
        return {"error": f"不支持的级别: {level}（可选 {', '.join(LEVELS)}）"}
    if not name and not adcode:
        return {"error": "name 与 adcode 至少提供一个"}
    gdf = _load_level(level)
    if gdf is None:
        # 自动在线降级回退：当 LOCAL_GEODATA_DIR 外部磁盘未挂载或本地 SHP 不存在时，通过在线高德/天地图 API 自动获取政区轮廓
        try:
            import httpx
            from shapely.geometry import Polygon, MultiPolygon
            key = settings.AMAP_API_KEY
            if key and (name or adcode):
                search_kw = name or str(adcode)
                resp = httpx.get(
                    "https://restapi.amap.com/v3/config/district",
                    params={
                        "key": key,
                        "keywords": search_kw,
                        "subdistrict": "0",
                        "extensions": "all",
                    },
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    districts = data.get("districts", [])
                    if districts and districts[0].get("polyline"):
                        polyline_str = districts[0]["polyline"]
                        polys = []
                        for poly_part in polyline_str.split("|"):
                            coords = []
                            for pt_str in poly_part.split(";"):
                                if "," in pt_str:
                                    x_s, y_s = pt_str.split(",")
                                    coords.append((float(x_s), float(y_s)))
                            if len(coords) >= 3:
                                polys.append(Polygon(coords))
                        if polys:
                            geom = MultiPolygon(polys) if len(polys) > 1 else polys[0]
                            gdf_online = gpd.GeoDataFrame(
                                [{"name": districts[0].get("name", name), "adcode": districts[0].get("adcode", adcode)}],
                                geometry=[geom],
                            )
                            gdf_online = _project_result(gdf_online, to_wgs84=to_wgs84, simplified=simplified)
                            out = _to_feature_collection(
                                gdf_online,
                                note=_CRS_NOTE_WGS if to_wgs84 else _CRS_NOTE_GCJ,
                                crs=None if to_wgs84 else "gcj02",
                            )
                            out["metadata"] = {**(out.get("metadata") or {}), "admin_level": level, "source": "amap_online_fallback"}
                            return out
        except Exception as exc:
            logger.warning("[local_admin] query_admin_boundary online fallback failed: %s", exc)

        return {
            "error": "本地行政区数据不可用（未找到 ChinaAdminDivisonSHP 或读取失败）",
            "correction_hint": "请设置 LOCAL_GEODATA_DIR 指向含 ChinaAdminDivisonSHP/ 的目录，"
            "或改用在线工具 get_admin_division。",
        }

    if adcode:
        col = next(
            (c for c in _LEVEL_ADCODE_COL[level] if c in gdf.columns), None
        )
        if col is None:
            return {"error": f"数据缺少行政编码列（尝试过 {_LEVEL_ADCODE_COL[level]}）"}
        result = gdf[gdf[col].astype(str).str.zfill(6) == str(adcode).zfill(6)]
        if result.empty:
            return {"error": f"未找到 adcode={adcode} 的行政区（level={level}）"}
    else:
        name_col = _name_column(gdf, level)
        if name_col is None:
            return {"error": f"数据缺少名称列（尝试过 {_LEVEL_NAME_COL[level]}）"}
        result = gdf[gdf[name_col].astype(str).str.contains(str(name), na=False, regex=False)]
        if result.empty:
            return {"error": f"未找到名为 '{name}' 的行政区（level={level}）"}

    result = _project_result(result, to_wgs84=to_wgs84, simplified=simplified)
    out = _to_feature_collection(
        result,
        note=_CRS_NOTE_WGS if to_wgs84 else _CRS_NOTE_GCJ,
        crs=None if to_wgs84 else "gcj02",
    )
    # 行政级别随载荷下传（converter 按级别定边界线宽：国界粗、区县界细）
    out["metadata"] = {**(out.get("metadata") or {}), "admin_level": level}
    return out


def query_child_districts(
    parent_name: str,
    parent_level: str = "city",
    *,
    to_wgs84: bool = False,
    simplified: bool = False,
) -> Dict[str, Any]:
    """子级统一从 district.shp 查询；父级列按 parent_level 选择。"""
    parent_col_map = {"city": "ct_name", "province": "pr_name"}
    filter_col = parent_col_map.get(parent_level)
    if filter_col is None:
        return {"error": f"parent_level 仅支持 {', '.join(parent_col_map)}"}
    gdf = _load_level("district")
    if gdf is None:
        # 在线高德下级行政区查询降级
        try:
            import httpx
            from shapely.geometry import Polygon, MultiPolygon
            key = settings.AMAP_API_KEY
            if key and parent_name:
                resp = httpx.get(
                    "https://restapi.amap.com/v3/config/district",
                    params={
                        "key": key,
                        "keywords": parent_name,
                        "subdistrict": "1",
                        "extensions": "all",
                    },
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    districts = data.get("districts", [])
                    if districts:
                        sub_districts = districts[0].get("districts", [])
                        features = []
                        for sub in sub_districts:
                            polyline_str = sub.get("polyline")
                            if polyline_str:
                                polys = []
                                for poly_part in polyline_str.split("|"):
                                    coords = []
                                    for pt_str in poly_part.split(";"):
                                        if "," in pt_str:
                                            x_s, y_s = pt_str.split(",")
                                            coords.append((float(x_s), float(y_s)))
                                    if len(coords) >= 3:
                                        polys.append(Polygon(coords))
                                if polys:
                                    geom = MultiPolygon(polys) if len(polys) > 1 else polys[0]
                                    features.append({
                                        "name": sub.get("name"),
                                        "adcode": sub.get("adcode"),
                                        "level": sub.get("level", "district"),
                                        "geometry": geom,
                                    })
                        if features:
                            gdf_online = gpd.GeoDataFrame(features, geometry="geometry")
                            gdf_online = _project_result(gdf_online, to_wgs84=to_wgs84, simplified=simplified)
                            out = _to_feature_collection(
                                gdf_online,
                                note=_CRS_NOTE_WGS if to_wgs84 else _CRS_NOTE_GCJ,
                                crs=None if to_wgs84 else "gcj02",
                            )
                            out["metadata"] = {**(out.get("metadata") or {}), "admin_level": "district", "source": "amap_online_fallback"}
                            return out
        except Exception as exc:
            logger.warning("[local_admin] query_child_districts online fallback failed: %s", exc)

        return {
            "error": "本地行政区数据不可用（district.shp 未找到）",
            "correction_hint": "请设置 LOCAL_GEODATA_DIR，或改用在线工具。",
        }
    if filter_col not in gdf.columns:
        # 列名不符时回退通用名称列做模糊匹配（仍按包含语义）。
        fallback = _name_column(gdf, "district") or "name"
        filter_col = fallback
    result = gdf[gdf[filter_col].astype(str).str.contains(str(parent_name), na=False, regex=False)]
    if result.empty:
        return {"error": f"未找到 '{parent_name}'（{parent_level}）下的区/县"}
    result = _project_result(result, to_wgs84=to_wgs84, simplified=simplified)
    out = _to_feature_collection(
        result,
        note=_CRS_NOTE_WGS if to_wgs84 else _CRS_NOTE_GCJ,
        crs=None if to_wgs84 else "gcj02",
    )
    # 子级查询恒为 district 级（区/县界）
    out["metadata"] = {**(out.get("metadata") or {}), "admin_level": "district"}
    return out


def register_local_admin_tools(registry: ToolRegistry):
    @tool(registry, tier=2, domains=["chinese"], name="get_local_admin_boundary",
          description=(
              "本地行政边界查询：从本地 SHP 数据获取中国行政区划边界。"
              "✅ 用于：中国境内行政区边界的首选——本地矢量库，最快最稳，"
              "如『获取成都市轮廓』；支持名称模糊或 adcode 精确查询。"
              "\n❌ 不要用于：非中国境内数据——此时回退在线工具 get_admin_division。"
              "坐标为 GCJ-02（与 amap 同系）；需要 WGS84 时传 to_wgs84=true。"
          ),
          param_descriptions={
              "name": "行政区名称（包含匹配），如'成都市'、'锦江区'",
              "level": "级别: 'country', 'province', 'city', 'district'",
              "adcode": "六位行政编码精确查询（与 name 二选一），如 '510100'",
              "to_wgs84": "true 时将 GCJ-02 转为 WGS84（默认 false 保持原生坐标系）",
              "simplified": "true 时简化几何（~100m 容差），省级以上大边界建议开启",
          })
    def get_local_admin_boundary(
        name: str = "",
        level: str = "district",
        adcode: Optional[Union[str, int]] = "",
        to_wgs84: bool = False,
        simplified: bool = False,
    ) -> dict:
        # LLM 常把编码传成数字（510104），此处归一为字符串
        adcode_s = "" if adcode is None else str(adcode).strip()
        return query_admin_boundary(
            level, name=name or None, adcode=adcode_s or None,
            to_wgs84=to_wgs84, simplified=simplified,
        )

    @tool(registry, tier=2, domains=["chinese"], name="get_local_child_districts",
          description="本地下级行政区查询：获取指定城市或省份下的所有区/县边界。比在线 API 更快。",
          param_descriptions={
              "parent_name": "上级行政区名称，如'成都市'、'四川省'",
              "parent_level": "上级级别: 'province', 'city'",
              "to_wgs84": "true 时将 GCJ-02 转为 WGS84（默认 false）",
              "simplified": "true 时简化几何（推荐，区县数量多）",
          })
    def get_local_child_districts(
        parent_name: str,
        parent_level: str = "city",
        to_wgs84: bool = False,
        simplified: bool = False,
    ) -> dict:
        return query_child_districts(
            parent_name, parent_level, to_wgs84=to_wgs84, simplified=simplified,
        )
