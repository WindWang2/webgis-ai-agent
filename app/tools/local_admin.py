import json
import logging
import geopandas as gpd
from app.tools.registry import ToolRegistry, tool
from app.lib.geo_processor.core import GeoAnalysisResult
from typing import Any

logger = logging.getLogger(__name__)

# Map administrative levels to file names
LEVEL_MAP = {
    "country": "data/admin_division/1. Country/country.shp",
    "province": "data/admin_division/2. Province/province.shp",
    "city": "data/admin_division/3. City/city.shp",
    "district": "data/admin_division/4. District/district.shp"
}

def register_local_admin_tools(registry: ToolRegistry):
    @tool(registry, name="get_local_admin_boundary",
           description=(
               "本地行政边界查询：从本地 SHP 数据获取行政区划边界。"
               "✅ 用于：中国境内行政区边界的首选——本地矢量库，最快最稳，"
               "如『获取成都市轮廓』。"
               "\n❌ 不要用于：非中国境内数据——此时回退在线工具 get_admin_division。"
           ),
           param_descriptions={
               "name": "行政区名称，如'成都市'、'锦江区'",
               "level": "级别: 'country', 'province', 'city', 'district'",
           })
    def get_local_admin_boundary(name: str, level: str = "district") -> dict:
        filepath = LEVEL_MAP.get(level)
        if not filepath:
            return {"error": f"不支持的级别: {level}"}
            
        try:
            gdf = gpd.read_file(filepath, encoding='utf-8')
            # 尝试根据名称列过滤（通常字段名为 'name' 或 'Name'）
            name_col = "name" if "name" in gdf.columns else "Name"
            result = gdf[gdf[name_col].str.contains(name, na=False)]
            
            if result.empty:
                return {"error": f"未找到名为 '{name}' 的行政区"}
                
            return {
                "type": "FeatureCollection",
                "features": json.loads(result.to_json())["features"],
                "count": len(result)
            }
        except Exception as e:
            logger.error(f"Local SHP query failed: {e}")
            return {"error": str(e)}

    @tool(registry, name="get_local_child_districts",
           description="本地下级行政区查询：获取指定城市或省份下的所有下级行政区边界。比在线 API 更快。",
           param_descriptions={
               "parent_name": "上级行政区名称，如'成都市'、'四川省'",
               "parent_level": "上级级别: 'province', 'city'",
           })
    def get_local_child_districts(parent_name: str, parent_level: str = "city") -> dict:
        # 子级统一从 district.shp 中查询
        filepath = LEVEL_MAP.get("district")
        try:
            gdf = gpd.read_file(filepath, encoding='utf-8')
            # 根据上级级别选择过滤列
            filter_col = "ct_name" if parent_level == "city" else "pr_name"
            if filter_col not in gdf.columns:
                # Fallback to fuzzy search if columns are different
                filter_col = "ct_name" if "ct_name" in gdf.columns else "name"
                
            result = gdf[gdf[filter_col].str.contains(parent_name, na=False)]
            
            if result.empty:
                return {"error": f"未找到 '{parent_name}' 下的子级行政区"}
                
            return {
                "type": "FeatureCollection",
                "features": json.loads(result.to_json())["features"],
                "count": len(result)
            }
        except Exception as e:
            logger.error(f"Local child districts query failed: {e}")
            return {"error": str(e)}
