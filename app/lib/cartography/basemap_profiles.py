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

# ADR-0216 分层修复（#1835 合并时清偿 #1771 的 core→lib 违背）：画像模型与
# 常量下沉 app/contracts/basemap_profiles.py（kernel），本模块 re-export
# 保持既有 import 面零破坏。
from app.contracts.basemap_profiles import (  # noqa: F401
    AVAILABILITY_REASONS,
    BASEMAP_KINDS,
    BASEMAP_PROFILES_VERSION,
    AvailabilityState,
    BasemapProviderProfile,
    CachePolicy,
    ProfileValidationError,
    BASEMAP_PROVIDER_PROFILES,
    iter_llm_catalog,
)

class ElevationSourceProfile(BaseModel):
    """一个高程/地形数据源的治理画像。"""

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
