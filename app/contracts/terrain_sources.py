"""会话 terrain 源契约（G3D-V2-A A1/A6 跨方 schema 发布点）。

本模块只发布**数据合同**：摄入期一锤定音的资格 receipt、可用性摘要的
response 形状。消费方：

- 渲染/发现（B 方向、前端数据目录）：经 `/layers/terrain-sources` 与
  TileJSON 端点消费，勿建第二真相；
- 科学分析门（C 方向、get_3d_capabilities 后续对接）：
  `qualification != "unavailable"` 的会话源才是 raster-dem 绑定候选，
  `analysis_grade` 才可进量测输入 —— 本 schema 不实现对方的消费端。

资格词表与 ``app.lib.cartography.basemap_profiles.ElevationSourceProfile``
的 ``qualification`` 枚举逐字对齐（不并表、不另造词）。

诚实红线（写入理由就必须可编程消费）：
- 未知垂直基准/单位 → display_only 或 unavailable，绝不静默换算；
- synthetic 标记永久跟随（SYNTHETIC_DEM tag → display_only）；
- 每条不合格因素都是 ``QualificationReason(code, detail)``，不是自由文本。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: 摄入 receipt 资格（严格 3 值 —— validate_dem 只产这三者）。
TerrainQualification = Literal["analysis_grade", "display_only", "unavailable"]

#: 投影面资格（含上游画像词表的 "unknown"：无 receipt 的非摄入 raster ref
#: TileJSON 扩展块等投影场景使用；摄入 receipt 永不产 unknown）。
ProjectedTerrainQualification = Literal["analysis_grade", "display_only", "unavailable", "unknown"]

#: 垂直高程序序的已知可换算单位（换算因子见 terrain_ingest._UNIT_FACTORS）。
DeclaredVerticalUnit = Literal["m", "ft", "us_survey_ft"]


class QualificationReason(BaseModel):
    """一条可编程消费的不合格/警告因素。"""

    model_config = ConfigDict(extra="forbid")

    #: 机器码（全词表见 terrain_ingest 模块 docstring 与测试锁定）。
    code: str = Field(min_length=1)
    #: 人读细节（含 remediation 提示；不含绝对路径/token 等敏感信息）。
    detail: str = Field(min_length=1)


class UnitConversionEvidence(BaseModel):
    """非米单位 → 米的显式换算证据（DECISIONS D2：不静默换算）。"""

    model_config = ConfigDict(extra="forbid")

    from_unit: DeclaredVerticalUnit
    #: 精确换算因子（ft=0.3048，US survey ft=1200/3937）。
    factor: float
    #: 换算输入文件 sha256（源 GeoTIFF）。
    input_sha256: str
    #: 换算输出文件 sha256（归一化副本 .dem-m.tif）。
    output_sha256: str


class TerrainSourceReceipt(BaseModel):
    """DEM 摄入回执 —— 摄入期一锤定音，渲染/发现不再变义。"""

    model_config = ConfigDict(extra="forbid")

    qualification: TerrainQualification
    reasons: tuple[QualificationReason, ...] = ()

    # —— 空间身份 ——
    crs: Optional[str] = None
    bbox_epsg4326: Optional[tuple[float, float, float, float]] = None
    width: int = 0
    height: int = 0
    #: 源网格地面采样间距（米；地理 CRS 按中心纬度近似换算）。
    ground_sample_distance_m: Optional[float] = None

    # —— 垂直参考 ——
    #: 归一化后恒为 "m"（非米单位经换算副本）。
    vertical_unit: Literal["m"] = "m"
    vertical_unit_source: Literal["declared", "geotiff_tag", "assumed"] = "assumed"
    vertical_datum: Optional[str] = None
    vertical_datum_source: Literal["declared", "geotiff_tag", "unavailable"] = "unavailable"
    unit_conversion: Optional[UnitConversionEvidence] = None

    # —— 数据质量事实（只记录，不掩盖） ——
    nodata: Optional[float] = None
    nodata_hole_fraction: float = 0.0
    non_finite_pixels: int = 0
    outlier_pixels: int = 0
    #: 判定 outlier 的米制包络（plausible elevation envelope）。
    outlier_envelope_m: tuple[float, float] = (-500.0, 8850.0)
    elevation_min_m: Optional[float] = None
    elevation_max_m: Optional[float] = None

    # —— 溯源 ——
    synthetic: bool = False
    content_sha256: str = ""
    registered_at: str = ""


class SessionTerrainSourceEntry(BaseModel):
    """可用性摘要中的单条会话 DEM 源。"""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    name: str
    receipt: TerrainSourceReceipt
    #: terrarium XYZ 模板（相对站点根；匿名会话由 API 侧追加 token）。
    tiles_url_template: str
    tilejson_url: str


class ElevationProfileAvailability(BaseModel):
    """静态高程画像的部署期可用性投影（摘要 API 的静态腿）。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    kind: str
    encoding: str
    qualification: ProjectedTerrainQualification
    synthetic: bool
    vertical_datum: Optional[str] = None
    attribution: str
    deployment_available: bool
    #: 不可用原因（air_gapped 过滤等），可编程消费的短码。
    unavailable_reason: Optional[str] = None


class MapResourceAvailabilitySummary(BaseModel):
    """`GET /layers/terrain-sources` 的 response 形状（契约锁定）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    elevation_profiles: tuple[ElevationProfileAvailability, ...] = ()
    session_terrain_sources: tuple[SessionTerrainSourceEntry, ...] = ()
    #: 3D 地形可用性总判（对接 get_3d_capabilities 的语义，不实现其消费端）。
    terrain_3d_available: bool = False
    terrain_3d_status: Literal["available", "available_with_limits", "unavailable"] = "unavailable"
    terrain_3d_reason_code: str = "NO_TERRAIN_SOURCE"
    terrain_3d_detail: str = ""


__all__ = [
    "DeclaredVerticalUnit",
    "ElevationProfileAvailability",
    "MapResourceAvailabilitySummary",
    "QualificationReason",
    "SessionTerrainSourceEntry",
    "ProjectedTerrainQualification",
    "TerrainQualification",
    "TerrainSourceReceipt",
    "UnitConversionEvidence",
]
