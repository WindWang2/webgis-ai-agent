"""可用底图供应商目录 (后端 single source of truth)。

前端 `frontend/lib/providers.ts` 是渲染端真相；这里是 LLM 感知端真相。
两边的 name 必须严格一致（switch_base_layer 工具靠它精确匹配）。
名字漂移时，把这份目录补齐即可，无需改其他逻辑。
"""
from typing import TypedDict


class BaseLayerInfo(TypedDict):
    name: str
    keywords: list[str]


# name 与 frontend/lib/providers.ts 必须严格一致
BASE_LAYER_CATALOG: list[BaseLayerInfo] = [
    {"name": "Carto 浅色", "keywords": ["浅色", "light", "白色", "亮色"]},
    {"name": "Carto 深色", "keywords": ["深色", "dark", "黑色", "暗色"]},
    {"name": "OSM 地图", "keywords": ["osm", "街道", "地图", "street"]},
    {"name": "ESRI 影像", "keywords": ["影像", "卫星", "satellite", "航拍", "鸟瞰"]},
    {"name": "ESRI 地形", "keywords": ["地形", "topo", "晕渲", "terrain"]},
    {"name": "OpenTopoMap", "keywords": ["opentopomap", "山体", "等高线"]},
    {"name": "高德影像", "keywords": ["高德影像", "amap img", "高德卫"]},
    {"name": "高德矢量", "keywords": ["高德矢量", "amap vec", "高德街"]},
    {"name": "天地图矢量", "keywords": ["天地图矢量", "天地图", "tianditu vec", "tianditu"]},
    {"name": "天地图影像", "keywords": ["天地图影像", "天地图卫星", "天地图卫", "tianditu img", "tianditu satellite"]},
]


def get_base_layer_names() -> list[str]:
    return [b["name"] for b in BASE_LAYER_CATALOG]


def format_base_layer_catalog() -> str:
    """单行紧凑文本：把所有底图名以 / 分隔，关键字以括号附后。"""
    parts = []
    for b in BASE_LAYER_CATALOG:
        kw = "/".join(b["keywords"][:3])  # 最多 3 个关键字够 LLM 匹配
        parts.append(f"{b['name']}({kw})")
    return " | ".join(parts)
