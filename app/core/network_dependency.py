"""NetworkDependencyCatalog — 机器可读的网络依赖清单（ADR-0197）。

单一事实源：系统每一类**出网依赖**登记为一条 ``NetworkDependency``，
含端点（env 可覆盖时动态解析）、调用面、离线替代、是否被运行时 egress
守卫覆盖。消费方：

- ``manage.py network-catalog``（JSON / 表格导出，部署审计用）
- ``manage.py preflight``（air-gapped 下逐依赖可用性判定）
- ``/api/v1/status/detailed`` 的 ``network_policy`` 组件（概要投影）
- SBOM / 离线资产 manifest 的网络面章节

诚实边界：``enforced_by_egress_guard=False`` 的依赖（pystac-client、
rasterio /vsicurl、DDGS、HF hub 下载、浏览器侧底图）不受 Python 运行时
守卫覆盖——它们由本清单登记 + preflight 在 air-gapped 下报 typed
unavailable，最终防线是部署网络层。工具面 ``network=True`` 声明经
``tool_network_matrix()`` 交叉引用，不在此重复登记。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CATALOG_SCHEMA_VERSION = 1

#: 依赖类别封闭词表（审计面：新类别必须显式登记进来）。
DEPENDENCY_CATEGORIES = frozenset({
    "llm",
    "geocoder",
    "basemap_tiles",
    "osm_data",
    "web_search",
    "stac_satellite",
    "remote_raster",
    "gov_portal",
    "stats_api",
    "object_store",
    "model_inference",
    "model_download",
    "data_fabric_remote",
    "frontend_only",
})


@dataclass(frozen=True)
class NetworkDependency:
    """一条出网依赖。端点可来自 Settings 字段、裸 env 键或静态常量。"""

    id: str
    category: str
    description: str
    #: 端点兜底显示（setting_key/env_key 均未配置时）
    endpoint: str
    setting_key: Optional[str] = None      # Settings 字段（pydantic）
    env_key: Optional[str] = None          # 第二配置面的裸 env 键
    constant_endpoint: str = ""
    call_sites: Tuple[str, ...] = field(default_factory=tuple)
    offline_alternative: str = ""
    #: True = 运行时 egress 守卫覆盖（拒绝即 typed unavailable）
    enforced_by_egress_guard: bool = True
    #: True = 端点指向本地/内网时离线可用（如私网 LLM / 内网 STAC）
    local_endpoint_usable: bool = True
    #: True = 无任何本地/内网替代（离线下必然 unavailable）
    public_only: bool = False
    #: 配置为 True 时该依赖声明离线可用（如 RAG_EMBEDDING_OFFLINE + 预热缓存）
    offline_toggle_key: Optional[str] = None

    def resolved_endpoint(self) -> str:
        if self.setting_key:
            from app.core.config import settings

            value = getattr(settings, self.setting_key, "") or ""
            if value:
                return value
        if self.env_key:
            import os

            value = (os.environ.get(self.env_key) or "").strip()
            if value:
                return value
        return self.constant_endpoint or self.endpoint

    def is_local_endpoint(self) -> bool:
        """无 DNS 的保守本地/内网判定（与 egress 同一函数，语义不漂移）。"""
        from app.core.egress import _is_private_host
        from urllib.parse import urlparse

        endpoint = self.resolved_endpoint()
        try:
            host = urlparse(endpoint).hostname
        except Exception:  # noqa: BLE001
            return False
        return bool(host) and _is_private_host(host.strip("[]").lower())

    def available_offline(self, profile: str) -> bool:
        """当前 profile 下该依赖是否可用。

        cloud = 全部可用（无守卫）。air_gapped：本地/内网端点可用（含
        LLM 指向内网、对象存储指向内网 MinIO 等）；声明 offline_toggle_key
        且已置真 → 可用（如 RAG 预热缓存）；其余 → unavailable。
        """
        if profile != "air_gapped":
            return True
        if self.offline_toggle_key:
            from app.core.config import settings

            if bool(getattr(settings, self.offline_toggle_key, False)):
                return True
        if self.local_endpoint_usable and self.is_local_endpoint():
            return True
        # 可改指内网的依赖（如内网 STAC/瓦片/统计 API）：公网端点离线下
        # 不可用，但运维可把端点指向内网服务恢复。
        return False

    def as_dict(self, profile: str = "cloud") -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "endpoint": self.resolved_endpoint(),
            "setting_key": self.setting_key,
            "env_key": self.env_key,
            "call_sites": list(self.call_sites),
            "offline_alternative": self.offline_alternative,
            "enforced_by_egress_guard": self.enforced_by_egress_guard,
            "local_endpoint": self.is_local_endpoint(),
            "available_offline": self.available_offline(profile),
        }


def _dependencies() -> Tuple[NetworkDependency, ...]:
    """静态登记（Phase 0 勘察产物固化；新出网面必须同步登记）。"""
    return (
        NetworkDependency(
            id="llm_chat",
            category="llm",
            description="主对话/规划 LLM（OpenAI 兼容 Chat Completions）",
            endpoint="",
            setting_key="LLM_BASE_URL",
            call_sites=(
                "app/services/chat/llm_client.py (httpx 池)",
                "app/services/chat/execution_engine.py",
                "app/api/routes/health.py::_check_llm",
                "app/api/routes/config.py::test_llm_connection",
            ),
            offline_alternative=(
                "LLM_BASE_URL 指向本地/内网 OpenAI 兼容端点（ollama/vllm/"
                "lmdeploy 等）；air-gapped 启动校验强制"
            ),
        ),
        NetworkDependency(
            id="llm_visual_judge",
            category="llm",
            description="制图视觉评审 VLM（OpenAI 兼容 / Gemini 形态）",
            endpoint="",
            env_key="CARTO_VISUAL_JUDGE_BASE_URL",
            call_sites=(
                "app/lib/harness/visual_judge/vlm_provider.py",
                "app/lib/harness/visual_evaluator.py",
            ),
            offline_alternative="CARTO_VISUAL_JUDGE_BASE_URL 指向本地 VLM 端点",
        ),
        NetworkDependency(
            id="geocoder_nominatim",
            category="geocoder",
            description="Nominatim 全球地理编码/逆编码",
            endpoint="",
            setting_key="NOMINATIM_URL",
            call_sites=(
                "app/tools/geocoding.py",
                "app/tools/osm.py",
                "app/services/viewport_naming.py",
            ),
            offline_alternative=(
                "LOCAL_GEODATA_DIR 行政区 SHP/GPKG（LOCAL_QUERY_FIRST 本地优先）"
                "；或自建内网 Nominatim 覆盖 NOMINATIM_URL"
            ),
        ),
        NetworkDependency(
            id="geocoder_amap",
            category="geocoder",
            description="高德 POI/行政区/路径（需 AMAP_API_KEY）",
            endpoint="https://restapi.amap.com",
            constant_endpoint="https://restapi.amap.com",
            call_sites=(
                "app/tools/chinese_maps/amap.py",
                "app/tools/local_admin.py",
                "app/tools/local_stats.py",
                "app/services/spatial_decision/target_resolver.py",
            ),
            offline_alternative="manage.py gd-poi-ingest 本地 POI + 本地行政区",
            public_only=True,
        ),
        NetworkDependency(
            id="geocoder_baidu",
            category="geocoder",
            description="百度地图 POI/地理编码（需 BAIDU_MAP_AK）",
            endpoint="https://api.map.baidu.com",
            constant_endpoint="https://api.map.baidu.com",
            call_sites=("app/tools/chinese_maps/providers/baidu.py",),
            offline_alternative="无（离线下 typed unavailable）",
            public_only=True,
        ),
        NetworkDependency(
            id="basemap_tianditu",
            category="basemap_tiles",
            description="天地图 WMTS 底图/注记（需 TIANDITU_TOKEN）",
            endpoint="https://api.tianditu.gov.cn",
            constant_endpoint="https://api.tianditu.gov.cn",
            call_sites=(
                "app/tools/chinese_maps/tianditu.py",
                "frontend/lib/providers.ts（浏览器侧）",
            ),
            offline_alternative="本地 XYZ/PMTiles 瓦片服务（内网部署）",
        ),
        NetworkDependency(
            id="osm_overpass",
            category="osm_data",
            description="Overpass API 在线 OSM 数据查询",
            endpoint="",
            setting_key="OVERPASS_API_URL",
            call_sites=("app/tools/osm.py",),
            offline_alternative="manage.py osm-ingest 预载本地 GPKG",
        ),
        NetworkDependency(
            id="web_search_qianfan",
            category="web_search",
            description="百度千帆 AI 搜索（需 BAIDU_QIANFAN_TOKEN）",
            endpoint="https://qianfan.baidubce.com",
            constant_endpoint="https://qianfan.baidubce.com",
            call_sites=("app/tools/web_crawler.py",),
            offline_alternative="无（离线下能力 typed unavailable）",
            public_only=True,
        ),
        NetworkDependency(
            id="web_search_ddgs",
            category="web_search",
            description="DuckDuckGo 网页检索（duckduckgo-search 库内部建连）",
            endpoint="https://duckduckgo.com",
            constant_endpoint="https://duckduckgo.com",
            call_sites=("app/tools/web_crawler.py（DDGS）",),
            offline_alternative="无",
            enforced_by_egress_guard=False,  # 第三方库内部建连，Python 层不可拦截
            public_only=True,
        ),
        NetworkDependency(
            id="stac_catalog",
            category="stac_satellite",
            description="STAC 影像目录检索（pystac-client 内部建连）",
            endpoint="",
            setting_key="STAC_API_URL",
            call_sites=(
                "app/services/rs/stac_client.py",
                "app/tools/remote_sensing.py",
            ),
            offline_alternative="本地 COG/栅格经 data-fabric local adapter",
            enforced_by_egress_guard=False,
        ),
        NetworkDependency(
            id="remote_raster_vsicurl",
            category="remote_raster",
            description="远程 COG/栅格按需读取（rasterio /vsicurl 内部建连）",
            endpoint="http(s):// 任意影像 href",
            constant_endpoint="",
            call_sites=(
                "app/lib/geo_raster/remote.py",
                "data_fabric cog_adapter",
            ),
            offline_alternative="本地栅格文件（DATA_FABRIC_LOCAL_FILE_ROOTS）",
            enforced_by_egress_guard=False,
        ),
        NetworkDependency(
            id="gov_open_data",
            category="gov_portal",
            description="政府数据开放平台检索（北京/上海/广东门户）",
            endpoint="config/sources/*_gov.yaml",
            constant_endpoint="",
            call_sites=("app/adapters/gov/gov_data_adapter.py",),
            offline_alternative="manage.py yearbook-ingest 本地年鉴数据",
        ),
        NetworkDependency(
            id="stats_apis",
            category="stats_api",
            description="统计 API（GBIF 物种 / World Bank 指标）",
            endpoint="config/sources/gbif_api.yaml, worldbank_api.yaml",
            constant_endpoint="",
            call_sites=("data_fabric/adapters/stats_api_adapter.py",),
            offline_alternative="无（离线下 typed unavailable）",
            public_only=True,
        ),
        NetworkDependency(
            id="object_store",
            category="object_store",
            description="S3/MinIO 对象存储产物面",
            endpoint="",
            env_key="WEBGIS_S3_ENDPOINT_URL",
            call_sites=("app/services/s3_blob_store.py",),
            offline_alternative=(
                "WEBGIS_OBJECT_STORE_BACKEND=filesystem（本地文件后端）"
            ),
        ),
        NetworkDependency(
            id="modelops_remote",
            category="model_inference",
            description="ModelOps 远程推理端点（MODELOPS_REMOTE_ALLOWLIST）",
            endpoint="allowlist 端点",
            constant_endpoint="",
            call_sites=("app/services/modelops/providers/remote_client.py",),
            offline_alternative="本地模型推理（modelops local provider）",
        ),
        NetworkDependency(
            id="rag_embedding_download",
            category="model_download",
            description="RAG embedding 模型 HuggingFace 下载",
            endpoint="https://huggingface.co",
            constant_endpoint="https://huggingface.co",
            call_sites=("app/services/rag/faiss_store.py",),
            offline_alternative=(
                "RAG_EMBEDDING_OFFLINE=true + 预热 HF 缓存 volume "
                "（docs/DEPLOYMENT-offline.md）"
            ),
            enforced_by_egress_guard=False,  # transformers 内部建连
            offline_toggle_key="RAG_EMBEDDING_OFFLINE",
        ),
        NetworkDependency(
            id="data_fabric_remote_sources",
            category="data_fabric_remote",
            description="用户登记的远程数据源（WFS/WMS/OGC API/ArcGIS/HTTP）",
            endpoint="DataSourceModel.endpoint（用户声明）",
            constant_endpoint="",
            call_sites=(
                "data_fabric/adapters/{wfs,wms_wmts,ogc_api,arcgis}.py",
            ),
            offline_alternative="本地源（geopackage/local_file/本地 pmtiles/cog）",
        ),
        NetworkDependency(
            id="frontend_basemap_tiles",
            category="frontend_only",
            description="浏览器侧底图/字形瓦片（Carto/OSM/ESRI 等，providers.ts）",
            endpoint="https://basemaps.cartocdn.com 等",
            constant_endpoint="https://basemaps.cartocdn.com",
            call_sites=(
                "frontend/lib/providers.ts",
                "frontend/lib/mapspec-compiler/compiler.ts（glyphs）",
            ),
            offline_alternative=(
                "本地 XYZ/PMTiles 底图 provider + NEXT_PUBLIC_MAP_GLYPHS_URL "
                "指向内网字形"
            ),
            enforced_by_egress_guard=False,  # 浏览器直连，服务端守卫不可达
            local_endpoint_usable=False,
        ),
    )


_CATALOG_CACHE: Optional[Tuple[NetworkDependency, ...]] = None


def network_dependencies() -> Tuple[NetworkDependency, ...]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        deps = _dependencies()
        _CATALOG_CACHE = tuple(sorted(deps, key=lambda d: d.id))
    return _CATALOG_CACHE


def reset_catalog_settings_cache() -> None:
    """Settings/env 变更后清缓存（测试/preflight 用）。"""
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def current_profile_and_mode() -> Tuple[str, str]:
    from app.core.config import settings

    return (
        (getattr(settings, "DEPLOYMENT_PROFILE", "cloud") or "cloud").strip().lower(),
        (getattr(settings, "NETWORK_EGRESS_MODE", "unrestricted") or "unrestricted").strip().lower(),
    )


def catalog_to_dict() -> Dict[str, Any]:
    """可 diff 的确定性序列化（部署审计 / SBOM 网络面章节）。"""
    profile, mode = current_profile_and_mode()
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "deployment_profile": profile,
        "egress_mode": mode,
        "dependencies": [
            d.as_dict(profile) for d in network_dependencies()
        ],
    }


def offline_capability_summary() -> Dict[str, Any]:
    """当前 profile 下逐依赖可用性 + 汇总计数（health/preflight 投影）。"""
    profile, mode = current_profile_and_mode()
    deps = [
        d.as_dict(profile)
        for d in network_dependencies()
    ]
    return {
        "profile": profile,
        "egress_mode": mode,
        "dependencies": deps,
        "counts": {
            "total": len(deps),
            "available_offline": sum(1 for d in deps if d["available_offline"]),
            "covered_by_egress_guard": sum(
                1 for d in deps if d["enforced_by_egress_guard"]),
        },
    }


# ── 工具面交叉矩阵 ──────────────────────────────────────────────────

#: network=True 工具的已知离线替代（保守静态映射；未列出 = 无替代）。
TOOL_OFFLINE_FALLBACKS: Dict[str, Tuple[str, str]] = {
    # tool_id: (fallback_id, 一句话说明)
    "geocode": ("local_admin_boundaries", "行政区 SHP/GPKG 本地解析"),
    "reverse_geocode": ("local_admin_boundaries", "行政区 SHP/GPKG 本地包含判定"),
    "osm_download": ("local_osm_gpkg", "manage.py osm-ingest 预载"),
    "osm_features": ("local_osm_gpkg", "manage.py osm-ingest 预载"),
    "search_poi": ("local_gd_pois", "manage.py gd-poi-ingest 本地 POI"),
    "admin_district": ("local_admin_boundaries", "本地行政区 SHP"),
    "admin_neighbors": ("local_admin_boundaries", "本地行政区 SHP"),
    "stats_county_data": ("local_yearbook", "manage.py yearbook-ingest 本地年鉴"),
    "gov_data_search": ("local_yearbook", "本地年鉴/已导入源"),
    "data_fabric_create_source": ("local_adapters", "geopackage/local_file/本地 pmtiles"),
    "data_fabric_list_sources": ("local_adapters", "本地源仍可列出"),
}


def tool_network_matrix(registry=None) -> Dict[str, Any]:
    """从工具面 ``network=True`` 声明生成交叉矩阵。

    registry 未注入（CLI 上下文/早期启动）时诚实降级：
    ``status=registry_unavailable``，不伪造空矩阵为"无网络工具"。
    """
    if registry is None:
        try:
            from app.agent_pi_bridge import get_tool_registry
            registry = get_tool_registry()
        except Exception:  # noqa: BLE001
            return {
                "status": "registry_unavailable",
                "network_tools": [],
                "counts": {"network_true": 0, "with_offline_fallback": 0},
            }
    tools: List[Dict[str, Any]] = []
    try:
        descriptors = registry.descriptors()
    except Exception:  # noqa: BLE001
        return {
            "status": "registry_unavailable",
            "network_tools": [],
            "counts": {"network_true": 0, "with_offline_fallback": 0},
        }
    for name in sorted(descriptors):
        desc = descriptors[name]
        if getattr(desc, "network", None) is not True:
            continue
        fallback = TOOL_OFFLINE_FALLBACKS.get(name)
        tools.append({
            "tool": name,
            "offline_fallback": fallback[0] if fallback else None,
            "fallback_description": fallback[1] if fallback else None,
        })
    return {
        "status": "ok",
        "network_tools": tools,
        "counts": {
            "network_true": len(tools),
            "with_offline_fallback": sum(1 for t in tools if t["offline_fallback"]),
        },
    }
