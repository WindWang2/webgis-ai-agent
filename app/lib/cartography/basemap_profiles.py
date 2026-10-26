"""底图与高程源治理画像（G3D01 / ADR-0197 + ADR-0199 增补）。

单一事实源：``frontend/lib/providers.ts``（渲染端）与
``app/core/base_layers.py``（LLM 感知端）的**手工双真相**在此收口 ——
本模块以 typed catalog 为权威，经
``app.lib.cartography.basemap_profile_projection`` 生成
``frontend/lib/basemap-profiles.generated.ts``，由双侧契约测试锁定
（pytest 再生成幂等 + vitest ``TILE_PROVIDERS`` 一致性），名字/署名/许可
漂移在 CI 即红，而非运行期静默分叉。

与 data fabric ``SourceDefinition``（数据集源域）刻意分域：本模块治理
**渲染资源**（底图/地形瓦片），字段惯例对齐（license/auth env-key-only/
verified/缓存策略），但不进入 ``config/sources/*.yaml`` 注册表。

数据诚信红线（fail-closed，model_validator 层）：
- 无 license / 无 attribution 的 provider 不许进目录；
- ``synthetic`` 高程源资格必须是 ``display_only``（合成数据绝不冒充
  分析级输入）；
- ``analysis_grade`` 默认资格仅允许会话内受控源（远端公共混合源
  垂直基准不统一，见 ELEVATION_SOURCE_PROFILES 注释）；
- 凭据只存**环境变量名**（``credentials_env``），目录内出现任何
  secret 形值即拒绝（与 ``source_registry._validate_secrets`` 同纪律）。

许可事实核验记录（2026-10-26，逐条 fetch 官方条款页）：
- OpenFreeMap：项目 MIT；样式 hyperknot/openfreemap-styles MIT；数据
  OSM（ODbL）；公共实例免 key、无请求上限、商用允许；署名
  "OpenFreeMap © OpenMapTiles Data from OpenStreetMap"；公共实例离线
  下载限 3000 tiles / 50 MB / 4 并发。
- AWS Terrain Tiles（Mapzen/joerd）：署名为多源清单（SRTM/3DEP/
  GMTED2010/ETOPO1 + 各国数据集），见 ``attribution_detail``；垂直基准
  混合（Skadi 为 EGM96，其余随源）→ 默认资格 display_only。
"""
from __future__ import annotations

import re
from typing import Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 渲染资源目录的 ABI 版本（投影生成物携带，双侧契约测试锚点）。
BASEMAP_PROFILES_VERSION = "1"

BASEMAP_KINDS = ("raster_xyz", "vector_style", "wmts_rest")
ELEVATION_KINDS = ("session_ref", "remote_terrarium_xyz", "synthetic_local")
QUALIFICATIONS = ("analysis_grade", "display_only", "unavailable", "unknown")
DEPLOYMENTS = ("cloud", "cloud+air_gapped", "air_gapped_only")

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


class ElevationSourceProfile(BaseModel):
    """一个高程/DEM 渲染源的治理画像。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    kind: Literal["session_ref", "remote_terrarium_xyz", "synthetic_local"]
    encoding: Literal["terrarium", "mapbox"]
    max_zoom: int = Field(ge=0, le=22)
    vertical_unit: Literal["m"] = "m"
    #: 混合源（如 AWS 公共 terrarium）垂直基准不统一 → None + note。
    vertical_datum: Optional[str] = None
    url: Optional[str] = None
    attribution: str = Field(min_length=1)
    #: 导出/打印面应展示的完整署名清单（许可要求的全文）。
    attribution_detail: Optional[str] = None
    license: str = Field(min_length=1)
    license_url: Optional[str] = None
    synthetic: bool = False
    qualification: Literal["analysis_grade", "display_only", "unavailable", "unknown"]
    deployment: Literal["cloud", "cloud+air_gapped", "air_gapped_only"] = "cloud"
    notes: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url_https(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("https://"):
            raise ProfileValidationError(f"高程源 url 必须 https: {v!r}")
        return v

    @model_validator(mode="after")
    def _qualification_invariants(self) -> "ElevationSourceProfile":
        if self.synthetic and self.qualification != "display_only":
            raise ProfileValidationError(
                f"{self.source_id}: synthetic 高程源资格必须是 display_only"
                "（合成数据绝不冒充分析级输入）"
            )
        if self.qualification == "analysis_grade":
            if self.kind != "session_ref":
                raise ProfileValidationError(
                    f"{self.source_id}: analysis_grade 默认资格仅允许会话内受控源"
                    "（远端公共混合源垂直基准/来源不统一，不可默认授予）"
                )
            if not self.vertical_datum:
                raise ProfileValidationError(
                    f"{self.source_id}: analysis_grade 必须声明 vertical_datum"
                )
        if self.kind == "remote_terrarium_xyz" and not self.url:
            raise ProfileValidationError(
                f"{self.source_id}: remote_terrarium_xyz 必须携带 url 模板"
            )
        if self.kind == "synthetic_local" and self.qualification == "analysis_grade":
            raise ProfileValidationError(  # pragma: no cover - 被 synthetic 分支前置拦截
                f"{self.source_id}: synthetic 与 analysis_grade 互斥"
            )
        return self


class TerrainCapability(BaseModel):
    """渲染端地形能力画像（与 renderer/mapspec 契约逐项对齐的静态投影）。"""

    model_config = ConfigDict(extra="forbid")

    supports_terrain: bool = True
    supported_encodings: tuple[str, ...] = ("terrarium", "mapbox")
    #: 与 mapspec_schema.MAX_TERRAIN_EXAGGERATION 同锚。
    max_exaggeration: float = 10.0
    default_maxzoom: int = 14
    default_tile_size: int = 256
    #: ADR-0199 D3/D5：地形源必须绑定 spec 内 raster-dem source，
    #: 失败证据（scene_terrain_unavailable）必经披露通道。
    requires_spec_bound_source: bool = True
    spec_binding: str = "scene.terrain.source -> sources[*].type == raster-dem"


#: 渲染端地形能力（单一常量；变更须同步 renderer/tests —— 投影锁兜底）。
TERRAIN_CAPABILITY = TerrainCapability()


#: 前端地形兜底源 id（收编 renderer.ts 硬编码 AWS URL —— D6）。
ELEVATION_FALLBACK_ID = "aws-terrarium-public"

_OPENFREEMAP_ATTRIBUTION = (
    "© OpenFreeMap © OpenMapTiles Data from OpenStreetMap"
)
_OPENFREEMAP_TOS = "https://openfreemap.org/"

#: AWS Terrain Tiles（joerd）署名全文（docs/attribution.md 要求的清单，
#: 导出面使用；地图内嵌署名用上方紧凑串 + 链接）。
_AWS_TERRAIN_ATTRIBUTION_DETAIL = (
    "ArcticDEM: DigitalGlobe/NASA/NSF; Australia: © Commonwealth of Australia "
    "(Geoscience Australia) 2017; Austria: © offene Daten Österreichs; Canada: "
    "Open Government Licence – Canada; Europe: Copernicus EU-DEM; ETOPO1: NOAA; "
    "Mexico: INEGI; New Zealand: LINZ; Norway: © Kartverket; UK: © Environment "
    "Agency 2015; US 3DEP/GMTED2010/SRTM: U.S. Geological Survey."
)

#: 目录本体 —— 每条都对应可核验的许可事实（模块 docstring 记录核验日期）。
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

#: 高程源目录（静态条目）。会话 ref 路径是机制而非静态源，资格经
#: G01/G02 数据资格管线逐 ref 评定，不在此伪造静态资格。
ELEVATION_SOURCE_PROFILES: tuple[ElevationSourceProfile, ...] = (
    ElevationSourceProfile(
        source_id=ELEVATION_FALLBACK_ID,
        kind="remote_terrarium_xyz",
        encoding="terrarium",
        max_zoom=15,
        vertical_datum=None,
        url="https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
        attribution="Terrain: SRTM/3DEP/GMTED2010/ETOPO1 © USGS/NOAA + national datasets",
        attribution_detail=_AWS_TERRAIN_ATTRIBUTION_DETAIL,
        license="Mapzen Terrain Tiles on AWS Open Data (per-source licenses)",
        license_url=(
            "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"
        ),
        qualification="display_only",
        notes=(
            "z0-15（官方范围）；垂直基准随源混合（Skadi=EGM96，其余各异）——渲染兜底可用，"
            "绝不默认投给科学分析；air_gapped 部署禁用（浏览器直连外网）"
        ),
    ),
    ElevationSourceProfile(
        source_id="synthetic-demo-dem",
        kind="synthetic_local",
        encoding="terrarium",
        max_zoom=14,
        vertical_datum=None,
        attribution="Synthetic demo terrain (deterministic fixture, not real elevation)",
        license="本仓库生成（无外部数据）",
        synthetic=True,
        qualification="display_only",
        deployment="cloud+air_gapped",
        notes=(
            "确定性合成 DEM（app/services/synthetic_terrain.py）——仅演示"
            "三维地形管线；synthetic=True 强制 display_only，任何分析用途拒绝"
        ),
    ),
)


def _validate_catalogs() -> None:
    """目录级不变量（import 期执行）：id 唯一、名称唯一、凭据绝不内联。"""
    ids: set[str] = set()
    names: set[str] = set()
    for p in BASEMAP_PROVIDER_PROFILES:
        if p.provider_id in ids:
            raise ProfileValidationError(f"重复 provider_id: {p.provider_id}")
        if p.name in names:
            raise ProfileValidationError(f"重复 provider name: {p.name}")
        ids.add(p.provider_id)
        names.add(p.name)
    eids: set[str] = set()
    for e in ELEVATION_SOURCE_PROFILES:
        if e.source_id in eids:
            raise ProfileValidationError(f"重复 elevation source_id: {e.source_id}")
        eids.add(e.source_id)
    if ELEVATION_FALLBACK_ID not in eids:
        raise ProfileValidationError(
            f"ELEVATION_FALLBACK_ID {ELEVATION_FALLBACK_ID!r} 不在高程目录中"
        )


_validate_catalogs()


def get_basemap_profile(provider_id: str) -> Optional[BasemapProviderProfile]:
    for p in BASEMAP_PROVIDER_PROFILES:
        if p.provider_id == provider_id:
            return p
    return None


def get_elevation_profile(source_id: str) -> Optional[ElevationSourceProfile]:
    for e in ELEVATION_SOURCE_PROFILES:
        if e.source_id == source_id:
            return e
    return None


class Availability(BaseModel):
    """可用性判定（closed reason codes，绝不静默成功）。"""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    state: AvailabilityState
    reason_code: Literal[
        "ok", "missing_env_gate", "missing_credentials", "deployment_not_permitted"
    ]
    detail: Optional[str] = None


def evaluate_basemap_availability(
    profile: BasemapProviderProfile,
    env: Mapping[str, str],
    deployment: str = "cloud",
) -> Availability:
    """纯函数可用性评估（env 仅取名字索引，绝不求值/记录 secret）。

    ``deployment``: "cloud" | "air_gapped"（与 DEPLOYMENT_PROFILE 二值对齐）。
    """
    if profile.env_gate and not (env.get(profile.env_gate) or "").strip():
        return Availability(
            provider_id=profile.provider_id,
            state="unavailable",
            reason_code="missing_env_gate",
            detail=f"未配置 {profile.env_gate}（诚实缺席，不注册必然失败的源）",
        )
    if profile.requires_credentials and not (
        env.get(profile.credentials_env or "") or ""
    ).strip():
        return Availability(
            provider_id=profile.provider_id,
            state="unavailable",
            reason_code="missing_credentials",
            detail=f"未配置凭据 {profile.credentials_env}",
        )
    if deployment == "air_gapped" and profile.deployment == "cloud":
        return Availability(
            provider_id=profile.provider_id,
            state="unavailable",
            reason_code="deployment_not_permitted",
            detail="air_gapped 部署仅允许本地/内网底图（ADR-0197）",
        )
    return Availability(
        provider_id=profile.provider_id, state="available", reason_code="ok"
    )


def evaluate_elevation_availability(
    profile: ElevationSourceProfile,
    env: Mapping[str, str],
    deployment: str = "cloud",
) -> Availability:
    if deployment == "air_gapped" and profile.deployment == "cloud":
        return Availability(
            provider_id=profile.source_id,
            state="unavailable",
            reason_code="deployment_not_permitted",
            detail="air_gapped 部署禁用外网高程兜底（会话内 DEM 不受影响）",
        )
    return Availability(
        provider_id=profile.source_id, state="available", reason_code="ok"
    )


def default_elevation_fallback(
    deployment: str = "cloud",
) -> Optional[ElevationSourceProfile]:
    """前端地形兜底源解析（D6）：cloud = AWS terrarium（受治理），air_gapped = None。

    None 表示"无兜底"——调用方必须显式跳过地形（typed 降级），而不是
    回退到某个未治理 URL。
    """
    if deployment == "air_gapped":
        return None
    profile = get_elevation_profile(ELEVATION_FALLBACK_ID)
    assert profile is not None  # _validate_catalogs 已保证
    return profile


def iter_llm_catalog() -> list[dict[str, list[str]]]:
    """LLM 感知目录派生（base_layers.BASE_LAYER_CATALOG 的上游）。"""
    return [
        {"name": p.name, "keywords": list(p.keywords)}
        for p in BASEMAP_PROVIDER_PROFILES
        if p.llm_visible
    ]


__all__ = [
    "BASEMAP_PROVIDER_PROFILES",
    "ELEVATION_SOURCE_PROFILES",
    "TERRAIN_CAPABILITY",
    "ELEVATION_FALLBACK_ID",
    "BASEMAP_PROFILES_VERSION",
    "AVAILABILITY_REASONS",
    "Availability",
    "AvailabilityState",
    "BasemapProviderProfile",
    "CachePolicy",
    "ElevationSourceProfile",
    "ProfileValidationError",
    "TerrainCapability",
    "default_elevation_fallback",
    "evaluate_basemap_availability",
    "evaluate_elevation_availability",
    "get_basemap_profile",
    "get_elevation_profile",
    "iter_llm_catalog",
]
