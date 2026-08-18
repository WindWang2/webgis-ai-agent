"""本地 OSM 主题查询工具（依赖 manage.py osm-ingest 的预处理产物）。"""
import logging
from typing import Optional

from app.services.local_osm import THEME_SPECS, catalog, query_osm_features
from app.tools.registry import ToolExecutionPolicy, ToolRegistry, tool

logger = logging.getLogger(__name__)

_THEMES_HELP = "/".join(THEME_SPECS)


def register_local_osm_tools(registry: ToolRegistry):
    @tool(
        registry,
        name="query_local_osm",
        description=(
            f"本地 OSM 数据查询：按主题（{_THEMES_HELP}）在 bbox 范围内查询要素，"
            "支持名称与标签过滤。数据来自本地预处理 GPKG（离线、秒级响应）。"
            "✅ 用于：『春熙路周边的餐厅』『这个范围内的主干路』等中国境内"
            " POI/道路/铁路/水系查询——优先于在线 API。"
            "\n❌ 不要用于：未预处理过的主题（先用 get_local_osm_catalog 检查）、"
            "或中国境外的数据。"
            "返回坐标为 WGS84。"
        ),
        param_descriptions={
            "theme": f"主题: {', '.join(THEME_SPECS)}",
            "bbox": "WGS84 边界框 [minx,miny,maxx,maxy]（可由行政区边界 total_bounds 获得）",
            "name_like": "可选：名称包含匹配（中英文均可）",
            "tag": "可选：标签过滤，如 'amenity=restaurant'（仅值时模糊匹配）",
            "limit": "返回上限（默认 200，最大 2000）",
        },
        tier=2,
        domains=["osm", "dataset"],
        execution_policy=ToolExecutionPolicy.THREAD,
        timeout=120.0,
    )
    def query_local_osm(
        theme: str,
        bbox,
        name_like: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 200,
    ) -> dict:
        return query_osm_features(
            theme, bbox, name_like=name_like, tag=tag, limit=limit
        )

    @tool(
        registry,
        name="get_local_osm_catalog",
        description=(
            "本地 OSM 数据目录：查看已预处理的主题、行数与覆盖说明。"
            "✅ 用于：query_local_osm 之前确认主题可用性。"
        ),
        execution_policy=ToolExecutionPolicy.INLINE,
        tier=1,
        domains=["osm", "meta"],
    )
    def get_local_osm_catalog() -> dict:
        return catalog()
