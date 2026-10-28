"""底图/高程源治理画像的 kernel 契约（G3D01 下沉，ADR-0216 分层）。

app.core.base_layers 需要 BASEMAP_PROVIDER_PROFILES / iter_llm_catalog 而
app.core 禁止依赖 app.lib.* —— 画像模型与常量由此 contracts 层承载（单一
事实源不变：app.lib.cartography.basemap_profiles 原样 re-export，既有
import 面零破坏）。
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 渲染资源目录的 ABI 版本（投影生成物携带，双侧契约测试锚点）。
BASEMAP_PROFILES_VERSION = "1"

BASEMAP_KINDS = ("raster_xyz", "vector_style", "wmts_rest")

#: 环境变量名合法性（只允许名字，任何像 secret 的值在此即拒绝）。
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: 可用性评估的 closed reason codes（评估器绝不返回词表外值）。
AVAILABILITY_REASONS = (
    "ok",
    "missing_env_gate",
    "missing_credentials",
    "deployment_not_permitted",
)

AvailabilityState = Literal["available", "unavailable"]


class ProfileValidationError(ValueError):
    """目录/画像违反治理不变量（fail-closed，import 期即抛）。"""




class CachePolicy(BaseModel):
    """缓存/再分发策略 —— 许可条款的机器可读投影。"""

    model_config = ConfigDict(extra="forbid")

    browser_cache_allowed: bool = True
    server_proxy_cache_allowed: bool = False
    offline_bulk_download: Literal["allowed", "restricted", "forbidden"] = "forbidden"
    offline_note: Optional[str] = None




class BasemapProviderProfile(BaseModel):
    """一个底图供应商的完整治理画像。"""

    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    #: 与 frontend TILE_PROVIDERS[].name 严格一致（switch_base_layer 匹配键）。
    name: str = Field(min_length=1)
    kind: Literal["raster_xyz", "vector_style", "wmts_rest"]
    #: 前端 MapStyleOption.type 的直接投影（避免 kind→type 双侧推断分叉）。
    map_style_type: Literal["raster", "vector"]
    #: URL 模板。静态远程条目必须 https；env 注入条目用 ``{env:VAR}`` 占位。
    url: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    license: str = Field(min_length=1)
    license_url: Optional[str] = None
    tos_url: Optional[str] = None
    max_zoom: int = Field(ge=0, le=24)
    crs: str = "EPSG:3857"
    coverage: str = "global"
    requires_credentials: bool = False
    #: 只存环境变量**名**（NEXT_PUBLIC_TIANDITU_TOKEN 等），绝不存值。
    credentials_env: Optional[str] = None
    #: 存在性门（如 NEXT_PUBLIC_LOCAL_BASEMAP_URL）—— 未设置即诚实缺席。
    env_gate: Optional[str] = None
    deployment: Literal["cloud", "cloud+air_gapped", "air_gapped_only"] = "cloud"
    cache_policy: CachePolicy
    keywords: list[str] = Field(min_length=1)
    #: 是否进入 LLM 感知目录（base_layers.BASE_LAYER_CATALOG 派生源）。
    llm_visible: bool = True
    notes: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url_https_or_env(cls, v: str) -> str:
        # review R1 P2-4：https 前缀（内部可含 {env:VAR} 占位，如天地图
        # token），或**整段** {env:VAR} 注入模板（local-xyz）。部分含
        # {env:} 的 http URL 不豁免 —— 否则 `http://evil/{env:X}` 绕过。
        import re as _re
        if v.startswith("https://"):
            return v
        if _re.fullmatch(r"\{env:[A-Z][A-Z0-9_]*\}", v):
            return v
        raise ProfileValidationError(
            f"basemap url 必须是 https（可内嵌 {{env:VAR}} 占位）或整段 {{env:VAR}} 注入模板，拒绝: {v!r}"
        )

    @field_validator("credentials_env", "env_gate")
    @classmethod
    def _env_names_only(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _ENV_NAME_RE.match(v):
            raise ProfileValidationError(
                f"环境变量名非法（只允许名字，禁止内联值）: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _credential_consistency(self) -> "BasemapProviderProfile":
        if self.requires_credentials and not self.credentials_env:
            raise ProfileValidationError(
                f"{self.provider_id}: requires_credentials 必须声明 credentials_env"
            )
        if self.credentials_env and not self.requires_credentials:
            raise ProfileValidationError(
                f"{self.provider_id}: 声明 credentials_env 必须同时 requires_credentials"
            )
        if self.deployment == "air_gapped_only" and not self.env_gate:
            raise ProfileValidationError(
                f"{self.provider_id}: air_gapped_only 底图必须有 env_gate（离线资源"
                "由部署方显式提供，不允许默认存在）"
            )
        return self




_OPENFREEMAP_ATTRIBUTION = (
    "© OpenFreeMap © OpenMapTiles Data from OpenStreetMap"
)
_OPENFREEMAP_TOS = "https://openfreemap.org/"


BASEMAP_PROVIDER_PROFILES: tuple[BasemapProviderProfile, ...] = (
    # --- 开源矢量样式（免 key、无请求上限、商用允许） ---
    BasemapProviderProfile(
        provider_id="openfreemap-libre",
        name="OpenFreeMap Libre",
        kind="vector_style",
        map_style_type="vector",
        url="https://openfreemap.org/style/libre",
        attribution=_OPENFREEMAP_ATTRIBUTION,
        license="MIT styles / OSM data ODbL-1.0",
        license_url="https://github.com/hyperknot/openfreemap-styles",
        tos_url=_OPENFREEMAP_TOS,
        max_zoom=14,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="restricted",
            offline_note=(
                "公共实例单次下载 ≤3000 tiles / 50 MB / 4 并发（官方 FAQ）；"
                "更大规模请自托管（官方提供全星球 MBTiles/PMTiles 下载）"
            ),
        ),
        keywords=["openfreemap", "libre", "开源矢量", "开源底图", "free"],
    ),
    BasemapProviderProfile(
        provider_id="openfreemap-positron",
        name="OpenFreeMap Positron",
        kind="vector_style",
        map_style_type="vector",
        url="https://openfreemap.org/style/positron",
        attribution=_OPENFREEMAP_ATTRIBUTION,
        license="MIT styles / OSM data ODbL-1.0",
        license_url="https://github.com/hyperknot/openfreemap-styles",
        tos_url=_OPENFREEMAP_TOS,
        max_zoom=14,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="restricted",
            offline_note="同 openfreemap-libre（公共实例下载限额）",
        ),
        keywords=["openfreemap", "positron", "开源浅色", "浅色矢量", "学术"],
    ),
    BasemapProviderProfile(
        provider_id="openfreemap-bright",
        name="OpenFreeMap Bright",
        kind="vector_style",
        map_style_type="vector",
        url="https://openfreemap.org/style/bright",
        attribution=_OPENFREEMAP_ATTRIBUTION,
        license="MIT styles / OSM data ODbL-1.0",
        license_url="https://github.com/hyperknot/openfreemap-styles",
        tos_url=_OPENFREEMAP_TOS,
        max_zoom=14,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="restricted",
            offline_note="同 openfreemap-libre（公共实例下载限额）",
        ),
        keywords=["openfreemap", "bright", "开源彩色", "彩色矢量"],
    ),
    # --- Carto（矢量 GL style + raster @2x） ---
    BasemapProviderProfile(
        provider_id="carto-positron",
        name="Carto Positron 矢量",
        kind="vector_style",
        map_style_type="vector",
        url="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        attribution="© OpenStreetMap contributors © CARTO",
        license="ODbL-1.0 (data) / CARTO ToS",
        license_url="https://carto.com/attribution/",
        tos_url="https://carto.com/legal/terms-of-service/",
        max_zoom=14,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["carto-positron", "positron", "浅色矢量", "学术", "矢量底图"],
    ),
    BasemapProviderProfile(
        provider_id="carto-dark-vec",
        name="Carto Dark Matter 矢量",
        kind="vector_style",
        map_style_type="vector",
        url="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        attribution="© OpenStreetMap contributors © CARTO",
        license="ODbL-1.0 (data) / CARTO ToS",
        license_url="https://carto.com/attribution/",
        tos_url="https://carto.com/legal/terms-of-service/",
        max_zoom=14,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["carto-dark-vec", "dark-matter", "深色矢量", "夜间矢量", "大屏"],
    ),
    BasemapProviderProfile(
        provider_id="carto-light",
        name="Carto 浅色",
        kind="raster_xyz",
        map_style_type="raster",
        url="https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
        attribution="© OpenStreetMap contributors © CARTO",
        license="ODbL-1.0 (data) / CARTO ToS",
        license_url="https://carto.com/attribution/",
        tos_url="https://carto.com/legal/terms-of-service/",
        max_zoom=20,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["浅色", "light", "白色", "亮色"],
    ),
    BasemapProviderProfile(
        provider_id="carto-dark",
        name="Carto 深色",
        kind="raster_xyz",
        map_style_type="raster",
        url="https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
        attribution="© OpenStreetMap contributors © CARTO",
        license="ODbL-1.0 (data) / CARTO ToS",
        license_url="https://carto.com/attribution/",
        tos_url="https://carto.com/legal/terms-of-service/",
        max_zoom=20,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["深色", "dark", "黑色", "暗色"],
    ),
    # --- OSM 标准瓦片（政策：重负载应用应使用其他源；仅轻量/演示） ---
    BasemapProviderProfile(
        provider_id="osm",
        name="OSM 地图",
        kind="raster_xyz",
        map_style_type="raster",
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        license="ODbL-1.0",
        license_url="https://opendatacommons.org/licenses/odbl/",
        tos_url="https://operations.osmfoundation.org/policies/tiles/",
        max_zoom=19,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
            offline_note="OSMF 瓦片政策：重负载/批量使用被明确禁止（需切换其他源）",
        ),
        keywords=["osm", "街道", "地图", "street"],
    ),
    BasemapProviderProfile(
        provider_id="esri-img",
        name="ESRI 影像",
        kind="raster_xyz",
        map_style_type="raster",
        url=(
            "https://services.arcgisonline.com/arcgis/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attribution="Esri, Maxar, Earthstar Geographics",
        license="Esri Terms of Service (attribution required)",
        license_url="https://www.esri.com/en-us/legal/terms",
        max_zoom=19,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["影像", "卫星", "satellite", "航拍", "鸟瞰"],
    ),
    BasemapProviderProfile(
        provider_id="esri-topo",
        name="ESRI 地形",
        kind="raster_xyz",
        map_style_type="raster",
        url=(
            "https://services.arcgisonline.com/arcgis/rest/services/"
            "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
        ),
        attribution="Esri, USGS, NOAA | Esri, HERE, Garmin",
        license="Esri Terms of Service (attribution required)",
        license_url="https://www.esri.com/en-us/legal/terms",
        max_zoom=19,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["地形", "topo", "晕渲", "terrain"],
    ),
    BasemapProviderProfile(
        provider_id="opentopomap",
        name="OpenTopoMap",
        kind="raster_xyz",
        map_style_type="raster",
        url="https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)",
        license="CC-BY-SA 3.0 (tiles) / ODbL-1.0 (data)",
        license_url="https://opentopomap.org/about",
        max_zoom=17,
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["opentopomap", "山体", "等高线"],
    ),
    BasemapProviderProfile(
        provider_id="amap-img",
        name="高德影像",
        kind="raster_xyz",
        map_style_type="raster",
        url="https://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
        attribution="© 高德地图 AutoNavi",
        license="高德地图服务条款（禁止批量抓取/存储）",
        license_url="https://lbs.amap.com/pages/terms/",
        max_zoom=18,
        coverage="CN",
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["高德影像", "amap img", "高德卫"],
        notes="GCJ-02 加偏坐标：与 WGS-84 图层叠加存在 ~50-500m 系统偏移（叠加分析前需纠偏）",
    ),
    BasemapProviderProfile(
        provider_id="amap-vec",
        name="高德矢量",
        kind="raster_xyz",
        map_style_type="raster",
        url=(
            "https://webrd01.is.autonavi.com/appmaptile"
            "?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}"
        ),
        attribution="© 高德地图 AutoNavi",
        license="高德地图服务条款（禁止批量抓取/存储）",
        license_url="https://lbs.amap.com/pages/terms/",
        max_zoom=18,
        coverage="CN",
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["高德矢量", "amap vec", "高德街"],
        notes="GCJ-02 加偏坐标：与 WGS-84 图层叠加存在 ~50-500m 系统偏移（叠加分析前需纠偏）",
    ),
    # --- 天地图（凭据 env-key-only；token 缺席 = 诚实缺席，前端不注册） ---
    BasemapProviderProfile(
        provider_id="tianditu-vec",
        name="天地图矢量",
        kind="wmts_rest",
        map_style_type="raster",
        url=(
            "https://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
            "&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
            "&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk={env:NEXT_PUBLIC_TIANDITU_TOKEN}"
        ),
        attribution="© 国家地理信息公共服务平台 天地图",
        license="天地图服务条款（需授权 token）",
        license_url="https://www.tianditu.gov.cn/",
        max_zoom=18,
        coverage="CN",
        requires_credentials=True,
        credentials_env="NEXT_PUBLIC_TIANDITU_TOKEN",
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["天地图矢量", "天地图", "tianditu vec", "tianditu"],
    ),
    BasemapProviderProfile(
        provider_id="tianditu-img",
        name="天地图影像",
        kind="wmts_rest",
        map_style_type="raster",
        url=(
            "https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
            "&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
            "&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk={env:NEXT_PUBLIC_TIANDITU_TOKEN}"
        ),
        attribution="© 国家地理信息公共服务平台 天地图",
        license="天地图服务条款（需授权 token）",
        license_url="https://www.tianditu.gov.cn/",
        max_zoom=18,
        coverage="CN",
        requires_credentials=True,
        credentials_env="NEXT_PUBLIC_TIANDITU_TOKEN",
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=False,
            offline_bulk_download="forbidden",
        ),
        keywords=["天地图影像", "天地图卫星", "天地图卫", "tianditu img", "tianditu satellite"],
    ),
    # --- 本地/内网 XYZ（ADR-0197；PMTiles 自托管经后端路由派生 XYZ 也走此门） ---
    BasemapProviderProfile(
        provider_id="local-xyz",
        name="本地底图",
        kind="raster_xyz",
        map_style_type="raster",
        url="{env:NEXT_PUBLIC_LOCAL_BASEMAP_URL}",
        attribution="内网/本地瓦片服务",
        license="由部署方提供（自托管资源，许可随部署）",
        max_zoom=22,
        env_gate="NEXT_PUBLIC_LOCAL_BASEMAP_URL",
        deployment="air_gapped_only",
        cache_policy=CachePolicy(
            browser_cache_allowed=True,
            server_proxy_cache_allowed=True,
            offline_bulk_download="allowed",
        ),
        keywords=["本地底图", "内网底图", "local", "offline", "local-xyz"],
        llm_visible=False,
        notes="部署方自托管（本地 XYZ 或经 PMTiles→XYZ 路由派生）；air_gapped 唯一底图路径",
    ),
)


def iter_llm_catalog() -> list[dict[str, list[str]]]:
    """LLM 感知目录派生（base_layers.BASE_LAYER_CATALOG 的上游）。"""
    return [
        {"name": p.name, "keywords": list(p.keywords)}
        for p in BASEMAP_PROVIDER_PROFILES
        if p.llm_visible
    ]
