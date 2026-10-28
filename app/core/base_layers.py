"""可用底图供应商目录 (后端 LLM 感知端)。

G3D01 起，本模块**派生自** ``app/lib/cartography/basemap_profiles.py``
的 typed 治理目录（单一事实源）：name/keywords/license/attribution 漂移
由 ``tests/cartography/test_basemap_profile_projection.py`` 与前端
``basemap-profiles.contract.test.ts`` 双侧契约锁拦截，不再手工同步。
前端 ``frontend/lib/providers.ts`` 仍是渲染端真相（env 门控逻辑在手写端），
两侧一致性同样由契约测试保证。
"""
from typing import TypedDict

# ADR-0216 分层：画像 kernel 契约在 app/contracts（app.core 禁依赖 app.lib）。
from app.contracts.basemap_profiles import (
    BASEMAP_PROVIDER_PROFILES,
    iter_llm_catalog,
)


class BaseLayerInfo(TypedDict):
    name: str
    keywords: list[str]


# 派生自 basemap_profiles.BASEMAP_PROVIDER_PROFILES（llm_visible 过滤）。
# 本目录 = LLM 可提议的底图全集；前端按 env/部署 profile 决定渲染注册。
BASE_LAYER_CATALOG: list[BaseLayerInfo] = iter_llm_catalog()


def get_base_layer_names() -> list[str]:
    return [b["name"] for b in BASE_LAYER_CATALOG]


def _providers_by_id() -> dict[str, str]:
    """providerId → canonical name（id 与前端 TILE_PROVIDERS.id 对齐）。

    注意（review R2 P2-2）：本映射**不过滤** llm_visible（local-xyz 在册）——
    它服务于模板种子 providerId 解析（历史载荷可能携带 local-xyz）；前端
    未配置 env 时 switch_base_layer 会显式 target_not_found（诚实失败），
    与 BASE_LAYER_CATALOG 的 llm_visible 过滤刻意不对称。
    """
    return {p.provider_id: p.name for p in BASEMAP_PROVIDER_PROFILES}


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
