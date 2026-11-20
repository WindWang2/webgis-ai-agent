"""G39 采样域契约 — 词表、预算、typed 错误与计划/样本行模型。

公共条款：
- 词表封闭：站位模式、采样方法、无效原因、单位状态、距离模式全部
  显式声明，绝不从 dtype/index 猜语义。
- 预算先于分配：超限 typed 阻断（BUDGET_EXCEEDED），无旁路标志。
- 计划是可修订的持久事实（CAS）；样本行是执行产物，逐行携带来源
  locator 与无效分类，绝不删坏行后宣称 100% 覆盖。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

#: 采样算法合同版本（站位生成 + 插值内核 + 有效性口径）。语义变更时
#: 递增并写入 receipt.method_digest —— 同 plan 不同算法版本的执行不混用。
SAMPLING_ALGORITHM_VERSION = "g39.v1"

# ── 词表（封闭） ─────────────────────────────────────────────────────

STATION_MODE_SOURCE_POINTS = "source_points"
STATION_MODE_LINE_STATIONS = "line_stations"
STATION_MODES = frozenset({STATION_MODE_SOURCE_POINTS, STATION_MODE_LINE_STATIONS})

#: 值域语义：raw（源像元原值，无换算）| physical（raw×scale+offset 恰一次）
#: | interpolated（bilinear 估计，非原像元观测）。
VALUE_DOMAIN_RAW = "raw"
VALUE_DOMAIN_PHYSICAL = "physical"
VALUE_DOMAIN_INTERPOLATED = "interpolated"
VALUE_DOMAINS = frozenset(
    {VALUE_DOMAIN_RAW, VALUE_DOMAIN_PHYSICAL, VALUE_DOMAIN_INTERPOLATED}
)

#: 采样方法。source_pixel 与 G20 probe 同口径（floor 反算源像元）；
#: bilinear 为连续波段插值估计（保留支撑像元与权重）；renormalized 是
#: 显式不同的跨孔方法（带 support_ratio，非悄悄填洞）。
METHOD_SOURCE_PIXEL = "source_pixel"
METHOD_BILINEAR = "bilinear"
METHOD_BILINEAR_RENORM = "bilinear_renormalized"
SAMPLING_METHODS = frozenset(
    {METHOD_SOURCE_PIXEL, METHOD_BILINEAR, METHOD_BILINEAR_RENORM}
)
INTERPOLATING_METHODS = frozenset({METHOD_BILINEAR, METHOD_BILINEAR_RENORM})

#: 线路距离模式。geodesic = 椭球测地（pyproj.Geod）；planar_metric =
#: 投影平面米制（要求 CRS 单位为米）。两者都必须显式入计划 —— 不存在
#: 静默默认的「准确里程」。
DISTANCE_MODE_GEODESIC = "geodesic"
DISTANCE_MODE_PLANAR_METRIC = "planar_metric"
DISTANCE_MODES = frozenset({DISTANCE_MODE_GEODESIC, DISTANCE_MODE_PLANAR_METRIC})

#: 无效原因分类学（样本行保留，不删行）。
INVALID_OUTSIDE_EXTENT = "outside_extent"
INVALID_NODATA = "nodata"
INVALID_MASKED = "masked"
INVALID_NON_FINITE = "non_finite"
INVALID_MASKED_SUPPORT = "masked_support"        # 插值支撑像元无效（严格策略）
INVALID_TRANSFORM_FAILED = "transform_failed"    # 请求 CRS → 源 CRS 失败
INVALID_INSUFFICIENT_SUPPORT = "insufficient_support"  # renorm 支撑占比不足
INVALID_REASONS = frozenset({
    INVALID_OUTSIDE_EXTENT, INVALID_NODATA, INVALID_MASKED, INVALID_NON_FINITE,
    INVALID_MASKED_SUPPORT, INVALID_TRANSFORM_FAILED, INVALID_INSUFFICIENT_SUPPORT,
})

#: 单位状态（与 probe 同口径）。
UNIT_STATUS_DECLARED = "declared"
UNIT_STATUS_UNKNOWN = "unknown"

#: 波段解释来源：元数据 tag / 用户显式声明。dtype 不在其中 —— 整数
#: dtype 不是「分类」的证据，浮点 dtype 也不是「连续」的证据。
BAND_SEMANTICS_METADATA = "metadata"
BAND_SEMANTICS_DECLARED = "declared"


# ── 预算（先于分配 typed 阻断） ──────────────────────────────────────

MAX_SOURCES = 4                 #: 多源上限（每源自己的真实网格）
MAX_BANDS_PER_SOURCE = 8        #: 每源显式波段上限
MAX_TOTAL_BANDS = 16            #: 总波段上限
MAX_STATIONS = 50_000           #: 站位总数上限
MAX_VALUE_CELLS = 500_000       #: 站位 × (源, 波段) 值单元上限
MAX_BATCH_STATIONS = 2_000      #: 每批读取站位
MAX_PREVIEW_STATIONS = 64       #: 单次预览站位
MAX_SAMPLE_TABLE_ROWS = 500_000  #: 长表行上限（对齐 MAX_VALUE_CELLS）
#: 活跃读窗口字节预算（解码后数组驻留）
MAX_ACTIVE_READ_BYTES = 256 * 1024 * 1024
#: 本域临时/结果字节预算（长表 + 站位层 + 曲线）
MAX_DOMAIN_TEMP_BYTES = 512 * 1024 * 1024
#: 非分块源最低解码成本护栏：单波段整幅解码字节超过该值时，即使只请求
#: 一个点也按真实整图 I/O 预估并阻断（S18）。
MAX_STRIP_DECODE_BYTES = 256 * 1024 * 1024
#: 曲线抽稀点上限（显示用；完整数据在长表/站位 ref）
MAX_CURVE_POINTS_PER_BAND = 512
#: 站位层 GeoJSON 特征上限（与 MAX_STATIONS 一致）
MAX_STATION_LAYER_FEATURES = MAX_STATIONS
#: 表格分页
TABLE_PAGE_SIZE_DEFAULT = 50
TABLE_PAGE_SIZE_MAX = 500


class RasterSamplingError(Exception):
    """带稳定 code / 字段 / 修复指引的采样域 typed 错误（HTTP 与工具共用）。"""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        field: str = "",
        status_code: int = 422,
        details: Optional[Dict[str, Any]] = None,
        correction_hint: str = "",
    ) -> None:
        super().__init__(detail)
        self.code = code if code in SAMPLING_ERROR_CODES else "SAMPLING_FAILED"
        self.detail = detail
        self.field = field
        self.status_code = status_code
        self.details = details or {}
        self.correction_hint = correction_hint

    def to_payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": False,
            "code": self.code,
            "detail": self.detail,
        }
        if self.field:
            out["field"] = self.field
        if self.details:
            out["details"] = self.details
        if self.correction_hint:
            out["correction_hint"] = self.correction_hint
        return out


SAMPLING_ERROR_CODES = frozenset({
    "INVALID_PLAN", "PLAN_NOT_FOUND", "PLAN_REVISION_CONFLICT", "STALE_BINDING",
    "SOURCE_UNAVAILABLE", "SOURCE_NOT_NUMERIC_RASTER", "SOURCE_PATH_REJECTED",
    "SOURCE_MUTATED_DURING_READ", "SOURCE_BINDING_CHANGED",
    "NO_CRS", "INVALID_CRS", "BAND_OUT_OF_RANGE", "BAND_METADATA_UNAVAILABLE",
    "METHOD_INCOMPATIBLE", "INVALID_GEOMETRY", "UNSUPPORTED_GEODESY",
    "BUDGET_EXCEEDED", "PREVIEW_STALE", "EXECUTION_NOT_FOUND",
    "EXECUTION_NOT_RUNNING", "RESULTS_NOT_READY", "RESULTS_EXPIRED",
    "CONTENT_WRITE_FAILED", "READBACK_FAILED", "PUBLISH_FAILED",
    "SAMPLING_FAILED", "RECOVERY_REQUIRED", "UNAUTHORIZED",
})
#: 上面字符串集合先定义后使用的顺序问题：错误码表在类之后收紧 ——
#: `RasterSamplingError.__init__` 里对未知 code 回落 SAMPLING_FAILED。


# ── 计划模型（versioned、CAS 修订） ──────────────────────────────────

class SourceBinding(BaseModel):
    """单个数值栅格源在计划里的不可变绑定。

    ``data_revision`` / ``stat_fingerprint`` 是执行前快照事实（stat 于
    计划冻结时采集）；执行期每批重新 stat 校验，防 ABA 混读。
    """

    ref_id: str
    label: str = ""
    data_revision: str
    stat_fingerprint: str = ""
    bands: List[int] = Field(min_length=1)
    band_labels: Dict[str, str] = Field(default_factory=dict)
    band_units: Dict[str, str] = Field(default_factory=dict)
    #: categorical / continuous / undeclared；来源 band_semantics_source
    band_semantics: Dict[str, str] = Field(default_factory=dict)
    band_semantics_source: str = BAND_SEMANTICS_METADATA
    crs: Optional[str] = None

    def band_unit(self, band: int) -> str:
        return self.band_units.get(str(band)) or ""

    def band_label(self, band: int) -> str:
        return self.band_labels.get(str(band)) or f"band {band}"

    def band_semantic(self, band: int) -> str:
        v = self.band_semantics.get(str(band))
        return v if v in ("categorical", "continuous", "undeclared") else "undeclared"


class StationSpec(BaseModel):
    """站位来源与距离轴声明。"""

    mode: str  # STATION_MODES
    points_ref: str = ""
    lines_ref: str = ""
    #: 请求坐标系（站位几何的 CRS 声明；"source:<ref_id>" 表示某源网格坐标）
    crs: str = "EPSG:4326"
    distance_mode: str = DISTANCE_MODE_GEODESIC
    distance_unit: str = "m"
    #: line_stations 专用
    station_step: Optional[float] = None
    include_vertices: bool = True
    #: part 断口策略（固定：每 part 独立 chainage，断口如实展示）
    line_feature_id: str = ""
    line_part_ids: List[str] = Field(default_factory=list)


class BudgetSpec(BaseModel):
    """执行预算声明（分配前闸门依据；receipt 复述实际值）。"""

    max_stations: int = MAX_STATIONS
    max_value_cells: int = MAX_VALUE_CELLS
    max_batch_stations: int = MAX_BATCH_STATIONS
    max_active_read_bytes: int = MAX_ACTIVE_READ_BYTES
    max_domain_temp_bytes: int = MAX_DOMAIN_TEMP_BYTES


class SamplingPlan(BaseModel):
    """versioned 采样计划（持久；revision 递增、修改 CAS）。"""

    plan_id: str
    session_id: str
    owner: str = ""
    project_id: str = ""
    revision: int = 1
    name: str = ""
    mode: str  # STATION_MODES
    station_spec: StationSpec
    sources: List[SourceBinding] = Field(min_length=1)
    method: str = METHOD_SOURCE_PIXEL
    nodata_policy: str = "strict"  # strict | keep_invalid_rows（行保留，值无效）
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    algorithm_version: str = SAMPLING_ALGORITHM_VERSION
    #: 冻结的站位事实（准备阶段生成；plan 修订会使预览失效）
    station_digest: str = ""
    geometry_hash: str = ""
    #: 执行输入指纹（plan + 站位 + 源绑定 + 方法/算法版本）
    params_digest: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def source_by_ref(self, ref_id: str) -> Optional[SourceBinding]:
        return next((s for s in self.sources if s.ref_id == ref_id), None)


def compute_params_digest(
    *,
    plan: SamplingPlan,
    station_digest: str,
) -> str:
    """幂等键指纹：站位几何 + 源绑定 + 方法 + 算法版本（不含显示样式）。"""
    binding_facts = []
    for s in plan.sources:
        binding_facts.append({
            "ref_id": s.ref_id, "data_revision": s.data_revision,
            "stat_fingerprint": s.stat_fingerprint, "bands": sorted(s.bands),
            "semantics": s.band_semantics, "semantics_source": s.band_semantics_source,
        })
    raw = {
        "v": 1,
        "algorithm_version": plan.algorithm_version,
        "mode": plan.mode,
        "method": plan.method,
        "nodata_policy": plan.nodata_policy,
        "station": {
            "mode": plan.station_spec.mode, "crs": plan.station_spec.crs,
            "distance_mode": plan.station_spec.distance_mode,
            "station_step": plan.station_spec.station_step,
            "include_vertices": plan.station_spec.include_vertices,
        },
        "station_digest": station_digest,
        "sources": sorted(binding_facts, key=lambda x: x["ref_id"]),
    }
    import json

    blob = json.dumps(raw, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return "rsp:" + hashlib.sha256(blob).hexdigest()[:24]


# ── 样本行（长表 schema；复合键 sample_id × source × band） ──────────

class SampleRow(BaseModel):
    """一行 = 一个站位在一个 (source, band) 的采样事实。"""

    sample_id: str
    station_index: int
    #: 点模式父 locator（输入 feature id）或线路 part 定位
    parent_locator: str = ""
    source_ref: str
    band: int
    band_label: str = ""
    #: 站位几何定位（请求 CRS）
    station_x: float
    station_y: float
    station_crs: str
    #: 线路定位（点模式为空）
    part_id: str = ""
    chainage_m: Optional[float] = None
    #: 源网格定位
    source_x: float = 0.0
    source_y: float = 0.0
    row: Optional[int] = None
    col: Optional[int] = None
    #: 值（三域；无效行为 None + invalid_reason）
    raw_value: Optional[float] = None
    #: raw 整数超 JS 安全范围时的精确十进制串（无损编码；S33）
    raw_exact: Optional[str] = None
    physical_value: Optional[float] = None
    interpolated_value: Optional[float] = None
    value_domain: str = VALUE_DOMAIN_RAW
    method: str = METHOD_SOURCE_PIXEL
    unit: str = ""
    unit_status: str = UNIT_STATUS_UNKNOWN
    valid: bool = False
    invalid_reason: Optional[str] = None
    #: 插值支撑事实（bilinear）：四个 (row, col, weight)；renorm 附占比
    support_pixels: List[Dict[str, float]] = Field(default_factory=list)
    support_ratio: Optional[float] = None
    #: 来源版本（执行时实际读取的 data_revision）
    data_revision: str = ""

    def primary_value(self) -> Optional[float]:
        if self.value_domain == VALUE_DOMAIN_PHYSICAL:
            return self.physical_value
        if self.value_domain == VALUE_DOMAIN_INTERPOLATED:
            return self.interpolated_value
        return self.raw_value


class CoverageCounts(BaseModel):
    """覆盖率分母 = 请求站位 × (源, 波段) 完整矩阵。"""

    requested_cells: int = 0
    valid_cells: int = 0
    invalid_by_reason: Dict[str, int] = Field(default_factory=dict)
    stations_requested: int = 0
    stations_with_any_valid: int = 0

    def coverage_fraction(self) -> Optional[float]:
        if self.requested_cells <= 0:
            return None
        return self.valid_cells / self.requested_cells


class ExecutionReceipt(BaseModel):
    """执行收据（发布后才可见；恢复与验收的事实源）。"""

    execution_id: str
    plan_id: str
    plan_revision: int
    session_id: str
    params_digest: str
    algorithm_version: str
    method: str
    mode: str
    station_count: int
    source_revisions: Dict[str, str] = Field(default_factory=dict)
    geometry_hash: str = ""
    coverage: CoverageCounts = Field(default_factory=CoverageCounts)
    read_metrics: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    job_id: Optional[str] = None
    status: str = "completed"
    degraded: List[str] = Field(default_factory=list)
    completed_at: float = 0.0


def bounded_int(value: int, upper: int, *, field: str, what: str) -> int:
    if value <= 0 or value > upper:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED",
            f"{what} must be within 1..{upper}, got {value}",
            field=field, status_code=413,
        )
    return int(value)


def validate_sources(sources: Sequence[SourceBinding]) -> None:
    if not sources:
        raise RasterSamplingError("INVALID_PLAN", "at least one source is required",
                                  field="sources")
    if len(sources) > MAX_SOURCES:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED", f"at most {MAX_SOURCES} sources, got {len(sources)}",
            field="sources", status_code=413,
        )
    total = 0
    seen: set = set()
    for s in sources:
        if s.ref_id in seen:
            raise RasterSamplingError(
                "INVALID_PLAN", f"duplicate source ref {s.ref_id}", field="sources")
        seen.add(s.ref_id)
        if not (1 <= len(s.bands) <= MAX_BANDS_PER_SOURCE):
            raise RasterSamplingError(
                "BUDGET_EXCEEDED",
                f"source {s.ref_id}: 1..{MAX_BANDS_PER_SOURCE} bands, got {len(s.bands)}",
                field="bands", status_code=413,
            )
        total += len(s.bands)
    if total > MAX_TOTAL_BANDS:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED",
            f"at most {MAX_TOTAL_BANDS} total bands across sources, got {total}",
            field="bands", status_code=413,
        )
