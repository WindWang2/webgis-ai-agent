"""中国坐标系互转工具 (WGS84 ↔ GCJ-02 ↔ BD-09) & 通用 EPSG 重投影工具。

中国 GIS 的核心痛点：
- WGS84  — 国际标准、GPS、OSM、Sentinel 等遥感数据、Nominatim 反查
- GCJ-02 — 高德地图 / 腾讯地图 / 谷歌中国版的偏移坐标系（"火星坐标"）
- BD-09  — 百度地图的二次偏移坐标系

三套坐标系在同一城市内可错位 200-500m，混用直接造成图层错位、缓冲区分析失真。
本工具支持把整张 GeoJSON 图层（点/线/面）一次性平移到目标坐标系。
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from app.tools.registry import ToolRegistry, tool
from app.tools._utils import std_error_response
from app.utils.coord_transform import transform_geojson, normalize_chinese_crs
from app.lib.geo_processor.core import safe_parse

logger = logging.getLogger(__name__)

# The supported-Chinese-CRS set lives in the deep module (app.utils.coord_transform);
# this adapter delegates "what counts as a Chinese CRS" to normalize_chinese_crs
# rather than re-deriving the normalization and the set (Candidate #2).


def register_coord_transform_tools(registry: ToolRegistry):
    """注册中国坐标系互转工具。"""

    @tool(registry, name="transform_coordinates",
          tier=2, domains=["chinese"],
          description=(
              "中国坐标系互转 (WGS84 ↔ GCJ-02 ↔ BD-09)：批量把一张 GeoJSON 图层从一种坐标系平移到另一种。"
              "\n何时用：(1) 上传的数据来自高德 (GCJ-02) 但要叠加 OSM (WGS84) — 错位 ~300m；"
              "(2) 百度地图 (BD-09) POI 要与 Sentinel-2 (WGS84) 影像叠加；"
              "(3) 客户给的 Excel 坐标说不清是哪个系，看在地图上偏移方向反推后转回正常系。"
              "\n何时不用：(1) 数据本来就在同一坐标系 — 不要瞎转；"
              "(2) 用 Amap 自带工具 (search_poi、reverse_geocode_cn) — 它们返回 GCJ-02，前端基础已处理；"
              "(3) 中国境外的数据 — 函数会原样返回（GCJ-02/BD-09 偏移仅在国内生效）。"
              "\n关键约束：from_crs/to_crs ∈ {wgs84, gcj02, bd09}，**大小写不敏感**；"
              "支持 Point/LineString/Polygon/Multi* 及其 FeatureCollection 容器；"
              "保留 properties 与 z/m 维度。"
          ),
          param_descriptions={
              "geojson": "输入图层 GeoJSON 或引用(ref:xxx)",
              "from_crs": "源坐标系：'wgs84' | 'gcj02' | 'bd09'",
              "to_crs": "目标坐标系：'wgs84' | 'gcj02' | 'bd09'",
          })
    def transform_coordinates(geojson: Any, from_crs: str, to_crs: str) -> dict:
        # Policy gate: this tool is Chinese-CRS-only. normalize_chinese_crs is
        # the deep module's single authority for "what counts as a Chinese CRS";
        # the adapter keeps the policy (reject non-Chinese) and dict-shaping.
        src = normalize_chinese_crs(from_crs)
        dst = normalize_chinese_crs(to_crs)
        if src is None or dst is None:
            msg = (f"不支持的坐标系 from={from_crs} to={to_crs}。"
                   f"必须是 ['bd09', 'gcj02', 'wgs84'] 之一。")
            return std_error_response(
                msg,
                code="VALIDATION_ERROR",
                correction_hint="请将 from_crs/to_crs 改为 ['bd09', 'gcj02', 'wgs84'] 之一。",
            )

        data = safe_parse(geojson)
        if not data:
            return std_error_response(
                "无法解析输入 GeoJSON",
                code="VALIDATION_ERROR",
                correction_hint="请提供合法的 GeoJSON 对象、FeatureCollection 或 ref:xxx 引用。",
            )

        if src == dst:
            return {
                "success": True,
                "data": copy.deepcopy(data),
                "summary": f"源 = 目标坐标系 ({src})，原样返回。",
            }

        try:
            out = transform_geojson(data, src, dst)
            return {
                "success": True,
                "data": out,
                "summary": f"已将图层从 {src} 转换为 {dst}",
                "metadata": {"from_crs": src, "to_crs": dst},
            }
        except Exception as e:
            return std_error_response(
                f"坐标转换失败: {e}",
                code="TOOL_ERROR",
                error_type=type(e).__name__,
                correction_hint="请检查输入几何是否合法、坐标系参数是否匹配。",
            )


def register_epsg_transform_tools(registry: ToolRegistry):
    """Register general-purpose EPSG-to-EPSG reprojection tool."""

    @tool(registry, name="reproject_coordinates",
          tier=2,
          description=(
              "通用坐标参考系 (CRS) 转换：将 GeoJSON 图层从一种 EPSG 坐标系重投影到另一种。"
              "\n何时用：(1) 上传的 Shapefile/GPKG 使用了地方坐标系（如 CGCS2000 / EPSG:4490），"
              "需要转为 WGS84 (EPSG:4326) 以叠加底图；"
              "(2) 分析结果需要转到 UTM 投影以计算精确面积/距离；"
              "(3) 客户要求输出特定坐标系的成果。"
              "\n何时不用：(1) 中国坐标偏移 (WGS84↔GCJ-02↔BD-09) — 用 transform_coordinates；"
              "(2) 数据已经在目标 CRS — 不要重复投影。"
              "\n参数格式：EPSG 代码，如 'EPSG:4326'、'EPSG:32650'。"
          ),
          param_descriptions={
              "geojson": "输入图层 GeoJSON 或引用(ref:xxx)",
              "from_epsg": "源坐标系 EPSG 代码，如 'EPSG:4326'",
              "to_epsg": "目标坐标系 EPSG 代码，如 'EPSG:32650'",
          })
    def reproject_coordinates(geojson: Any, from_epsg: str, to_epsg: str) -> dict:
        data = safe_parse(geojson)
        if not data:
            return std_error_response(
                "无法解析输入 GeoJSON",
                code="VALIDATION_ERROR",
                correction_hint="请提供合法的 GeoJSON 对象、FeatureCollection 或 ref:xxx 引用。",
            )

        src_clean = (from_epsg or "").strip().upper()
        dst_clean = (to_epsg or "").strip().upper()

        if src_clean == dst_clean:
            return {
                "success": True,
                "data": copy.deepcopy(data),
                "summary": f"源 = 目标 CRS ({from_epsg})，原样返回。",
            }

        try:
            out = transform_geojson(data, from_epsg, to_epsg)
            return {
                "success": True,
                "data": out,
                "summary": f"已将图层从 {from_epsg} 重投影到 {to_epsg}",
                "metadata": {"from_epsg": from_epsg, "to_epsg": to_epsg},
            }
        except Exception as e:
            err = str(e).lower()
            if "crs" in err or "epsg" in err or "unsupported" in err:
                return std_error_response(
                    f"不支持的 CRS: {from_epsg} → {to_epsg} ({e})",
                    code="VALIDATION_ERROR",
                    error_type=type(e).__name__,
                    correction_hint=f"请使用合法的 EPSG 代码（如 EPSG:4326、EPSG:32650）。",
                )
            return std_error_response(
                f"重投影失败: {e}",
                code="TOOL_ERROR",
                error_type=type(e).__name__,
                correction_hint="请检查输入几何与坐标系参数是否合法。",
            )
