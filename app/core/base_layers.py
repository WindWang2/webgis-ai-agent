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
    {"name": "Carto Positron 矢量", "keywords": ["carto-positron", "positron", "浅色矢量", "学术", "矢量底图"]},
    {"name": "Carto Dark Matter 矢量", "keywords": ["carto-dark-vec", "dark-matter", "深色矢量", "夜间矢量", "大屏"]},
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


def _providers_by_id() -> dict[str, str]:
    """providerId → canonical name（id 与前端 TILE_PROVIDERS.id 对齐）。"""
    return {
        "carto-positron": "Carto Positron 矢量",
        "carto-dark-vec": "Carto Dark Matter 矢量",
        "carto-light": "Carto 浅色",
        "carto-dark": "Carto 深色",
        "osm": "OSM 地图",
        "esri-img": "ESRI 影像",
        "esri-topo": "ESRI 地形",
        "opentopomap": "OpenTopoMap",
        "amap-img": "高德影像",
        "amap-vec": "高德矢量",
        "tianditu-vec": "天地图矢量",
        "tianditu-img": "天地图影像",
    }


# 模板种子里的 providerId 别名 → 规范名（种子词汇与前端 TILE_PROVIDERS.id 有出入：
# esri-imagery、osm-standard、gaode-satellite、open-topo、carto-voyager 等）
_SEED_PROVIDER_ALIASES: dict[str, str] = {
    "esri-imagery": "ESRI 影像",
    "osm-standard": "OSM 地图",
    "gaode-satellite": "高德影像",
    "gaode-vec": "高德矢量",
    "open-topo": "OpenTopoMap",
    "carto-voyager": "Carto 浅色",
    "carto-positron-labels": "Carto Positron 矢量",
}


def resolve_provider_id_to_name(provider_id: str) -> str | None:
    """模板 providerId → 前端 TILE_PROVIDERS 规范名（#557 断点 2）。

    apply_template 的 basemap 分支必须发出 frontend `base_layer_change` 期望的
    ``params.name``（TILE_PROVIDERS[].name），而不是模板载荷里的 providerId。
    解析顺序：精确 id 映射 → 种子别名 → 目录模糊（子串/关键字）→ 意象关键字兜底；
    解析不出返回 None，交给调用方显式报错（绝不静默降级）。
    """
    pid = (provider_id or "").strip().lower()
    if not pid:
        return None
    by_id = _providers_by_id()
    if pid in by_id:
        return by_id[pid]
    if pid in _SEED_PROVIDER_ALIASES:
        return _SEED_PROVIDER_ALIASES[pid]

    for name in get_base_layer_names():
        low = name.lower()
        if low == pid or pid in low or low in pid:
            return name
    for info in BASE_LAYER_CATALOG:
        if any(pid in k.lower() or k.lower() in pid for k in info["keywords"]):
            return info["name"]
    if any(k in pid for k in ("影像", "卫星", "satellite", "imagery")):
        return "ESRI 影像"
    if any(k in pid for k in ("深色", "dark")):
        return "Carto 深色"
    if any(k in pid for k in ("地图", "osm", "street")):
        return "OSM 地图"
    return None


def format_base_layer_catalog() -> str:
    """单行紧凑文本：把所有底图名以 / 分隔，关键字以括号附后。"""
    parts = []
    for b in BASE_LAYER_CATALOG:
        kw = "/".join(b["keywords"][:3])  # 最多 3 个关键字够 LLM 匹配
        parts.append(f"{b['name']}({kw})")
    return " | ".join(parts)
