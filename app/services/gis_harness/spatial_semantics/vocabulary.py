"""SpatialSemantics 词汇表 —— 封闭词表与理由码（GOAL 01，schema v1）。

纪律（与 measurement.py / method_use_contract.py 同源）：

- **封闭词表**：本模块是空间语义契约全部枚举值的单一权威；新增值 = 契约
  变更，须 +schema_version；
- **复用既有权威**：``crs_source`` 四态与 data_preparation/spatial_receipt
  一字不差（declared/source/assumed/rfc7946_default，另加 unknown 兜底）；
  ``crs_class`` 四态来自 crs_safety.classify_crs；单位换算基准来自
  measurement.CANONICAL_UNITS —— 本模块不建第二注册表；
- **理由码（SSC_ 码族）**：稳定契约码（测试断言依赖），文案可演进；
  每个码带默认 severity（disclosure/warning/degraded/blocking）与
  是否可自动修复的授权分级。

不做的事：不复制 GCJ-02/BD-09 数学（utils/coord_transform 是权威）、
不做 CRS 分类（crs_safety 是权威）、不做字段量纲（measurement 是权威）。
"""
from __future__ import annotations

from typing import Dict, Tuple

# ── CRS 事实来源（复用 data_preparation/spatial_receipt.py:107 四态）───────
CRS_SOURCE_VOCABULARY: Tuple[str, ...] = (
    "declared",          # GeoJSON crs member / ref descriptor 显式声明
    "source",            # 数据源元数据带回（服务端管线）
    "assumed",           # 启发式假设（文件名/上下文）—— 禁止作为重投影依据
    "rfc7946_default",   # 未声明但坐标呈度样 —— RFC 7946 §4 规范缺省（有披露）
    "unknown",           # 无声明且结构不可判 —— 不猜
)

# ── 轴序 ────────────────────────────────────────────────────────────────────
AXIS_ORDER_VOCABULARY: Tuple[str, ...] = ("lon_lat", "lat_lon", "unknown")

# ── 单位（名称引用 measurement.CANONICAL_UNITS 键；此处只封口径枚举）────────
DISTANCE_UNIT_VOCABULARY: Tuple[str, ...] = ("m", "km")
AREA_UNIT_VOCABULARY: Tuple[str, ...] = ("m2", "km2", "ha")
DENOMINATOR_VOCABULARY: Tuple[str, ...] = ("none", "area", "population", "count")
DENSITY_DENOMINATOR_UNIT: Dict[str, str] = {
    # 请求口径 → 分母单位（area 族；population 族分母单位是人，不换算）
    "per_km2": "km2",
    "per_ha": "ha",
    "per_m2": "m2",
    "per_10k_persons": "population",
}

# ── 计算模式 ────────────────────────────────────────────────────────────────
CALCULATION_MODE_VOCABULARY: Tuple[str, ...] = (
    "projected_local",   # 单带局部度量投影（UTM/UPS/合格局部度量）
    "projected_polar",   # 极方位度量投影（EPSG:3413/3031）
    "geodesic",          # WGS84 椭球测地（大跨度/跨带）
    "raster_grid",       # 栅格管线自管
    "unknown",           # 证据不足 —— 不猜
)

# ── 尺度层级（由数据 bbox 跨度推断，描述性）────────────────────────────────
SCALE_LEVEL_VOCABULARY: Tuple[str, ...] = (
    "local",             # < 50 km（单 UTM 带内无忧）
    "regional",          # 50–500 km（跨带风险开始）
    "continental",       # 500–5000 km（必须测地或多带）
    "global",            # > 5000 km
    "unknown",
)

# ── 空间精度（由坐标小数位/栅格分辨率推断）──────────────────────────────────
SPATIAL_PRECISION_VOCABULARY: Tuple[str, ...] = (
    "fine",              # ~1 m 量级（≥5 位小数度）
    "medium",            # ~10–100 m（3–4 位）
    "coarse",            # > 1 km（≤2 位）
    "unknown",
)

# ── 范围重叠状态（AOI × 数据 bbox）──────────────────────────────────────────
SCOPE_OVERLAP_VOCABULARY: Tuple[str, ...] = (
    "contains",          # 数据 ⊆ AOI（AOI 是研究区，数据可能只覆盖部分）
    "intersects",        # 部分重叠
    "disjoint",          # 完全不相交 —— 空间连接/聚合必然空
    "unknown",           # 任一侧无几何证据
    "empty",             # AOI 无有效范围
)

# ── 判定状态（五值 + not_evaluated，与科学门 pass/warning/not_evaluated 对齐）─
VERDICT_VOCABULARY: Tuple[str, ...] = (
    "pass",
    "pass_with_disclosure",
    "degraded_with_disclosure",
    "requires_clarification",
    "blocked",
    "not_evaluated",
)

# ── 修复建议授权分级 ────────────────────────────────────────────────────────
REPAIR_AUTHORIZATION_VOCABULARY: Tuple[str, ...] = (
    "explicit_required",          # 须用户/上层显式授权（默认）
    "deterministic_safe",         # 双方显式声明的 EPSG↔EPSG 可逆变换，可自动
)


# ── 理由码（稳定契约；severity 是默认建议，validate 可按操作升级）──────────
class ReasonCode:
    code: str
    severity: str      # disclosure | warning | degraded | blocking
    detail: str

    def __init__(self, code: str, severity: str, detail: str) -> None:
        self.code = code
        self.severity = severity
        self.detail = detail


SSC_REASON_CODES: Dict[str, ReasonCode] = {rc.code: rc for rc in (
    # — CRS 声明 —
    ReasonCode(
        "SSC_CRS_DECLARED", "disclosure",
        "数据 CRS 显式声明（{crs}）"),
    ReasonCode(
        "SSC_CRS_RFC7946_DEFAULT", "disclosure",
        "未声明 CRS 但坐标呈度样 —— 按 RFC 7946 规范缺省 WGS84 处理（非猜测，附披露）"),
    ReasonCode(
        "SSC_CRS_UNDECLARED_NON_DEGREE", "blocking",
        "未声明 CRS 且坐标非度样（越 WGS84 包络/投影样量级）—— 禁止默认 4326，须先 declare_crs"),
    ReasonCode(
        "SSC_CRS_ASSUMED", "warning",
        "CRS 为启发式假设（crs_source=assumed）—— 不得作为度量/重投影依据"),
    ReasonCode(
        "SSC_UNKNOWN_CRS", "warning",
        "CRS 未知且结构证据不足 —— 度量类操作按 requires_clarification 处理"),
    # — 轴序 —
    ReasonCode(
        "SSC_AXIS_ORDER_SUSPECT", "warning",
        "轴序疑似经纬倒置（{evidence}）—— 须确认 lon/lat 顺序"),
    # — 偏移帧（GCJ-02/BD-09）—
    ReasonCode(
        "SSC_DATUM_NORMALIZED", "disclosure",
        "声明偏移帧已归一：{chain}"),
    ReasonCode(
        "SSC_CHINA_OFFSET_FRAME_RISK", "warning",
        "AOI 位于中国且坐标帧未显式声明 —— 存在 GCJ-02/BD-09 偏移风险（不自动归一未声明数据）"),
    # — 投影/失真 —
    ReasonCode(
        "SSC_WEB_MERCATOR_SCALE_DISTORTION", "degraded",
        "Web Mercator（EPSG:3857）在中高纬尺度失真 ≈{distortion}—— 度量结论降级披露"),
    ReasonCode(
        "SSC_MULTIZONE_SPAN", "degraded",
        "数据经度跨度 {span}° 超单 UTM 带 —— 距离类建议测地；面积已走测地分母"),
    ReasonCode(
        "SSC_POLAR_PROJECTION_SELECTED", "disclosure",
        "极区（|lat|>{lat}°）—— 采用极方位度量投影 {crs}"),
    ReasonCode(
        "SSC_ANTIMERIDIAN_CROSSED", "disclosure",
        "范围跨 180° 经线 —— 选带按 rewrap，几何级拆分由分析入口负责"),
    # — 范围 —
    ReasonCode(
        "SSC_SCOPE_INVALID", "blocking",
        "请求范围无效（{reason}）—— min>max / 空 / 非数值"),
    ReasonCode(
        "SSC_SCOPE_DATA_CRS_MISMATCH", "requires_clarification",
        "AOI CRS（{scope_crs}）与数据 CRS（{data_crs}）不同且未声明换算 —— 拒绝混合 CRS 执行"),
    ReasonCode(
        "SSC_SCOPE_DISJOINT_FROM_DATA", "blocking",
        "AOI 与数据范围完全不相交 —— 空间连接/聚合结果必然为空"),
    # — 单位/分母 —
    ReasonCode(
        "SSC_DISTANCE_UNIT_CONVERTED", "disclosure",
        "距离参数单位换算：{chain}"),
    ReasonCode(
        "SSC_DISTANCE_UNIT_INVALID", "blocking",
        "距离参数单位（{unit}）不在词表 m/km —— 拒绝执行而非猜米"),
    ReasonCode(
        "SSC_DEGREE_DISTANCE_REQUEST", "blocking",
        "距离参数呈度级量值（≤360 且带小数）且单位未声明 —— 不得当米用，须显式单位"),
    ReasonCode(
        "SSC_AREA_UNIT_CONVERSION", "disclosure",
        "面积输出单位换算：{chain}"),
    ReasonCode(
        "SSC_DENOMINATOR_KIND_MISMATCH", "requires_clarification",
        "分母类型（{actual}）与请求口径（{requested}）不一致 —— 二选一：换分母或改口径"),
    ReasonCode(
        "SSC_DENOMINATOR_ZERO_ZONES", "disclosure",
        "{count} 个聚合单元分母为零/负 —— 比率置 None（绝不造 0/∞）"),
    ReasonCode(
        "SSC_DENSITY_UNIT_IMPLICIT", "warning",
        "密度值隐含分母（{implicit}）—— 已显式升格为 {explicit}"),
    # — 栅格 —
    ReasonCode(
        "SSC_RASTER_CELL_UNIT_MISMATCH", "degraded",
        "栅格分辨率单位（{raster_unit}）与请求距离单位（{request_unit}）不同 —— 换算因子 {factor} 附披露"),
    # — 几何 —
    ReasonCode(
        "SSC_GEOMETRY_INVALID", "blocking",
        "输入含无效几何（{reason}）—— 拒绝执行而非静默修复"),
    ReasonCode(
        "SSC_COORD_OUT_OF_ENVELOPE", "blocking",
        "声明地理 CRS 但坐标越 WGS84 包络（|x|>{x} 或 |y|>{y}）—— CRS 声明与数据矛盾"),
    # — 精度 —
    ReasonCode(
        "SSC_SPATIAL_PRECISION_LOW", "warning",
        "坐标精度粗糙（~{precision}）—— 与 {request} 量级请求不匹配"),
)}


def reason_code_metadata(code: str) -> ReasonCode:
    """理由码元数据（未知码 → disclosure 兜底，绝不虚构 blocking）。"""
    return SSC_REASON_CODES.get(code, ReasonCode(code, "disclosure", ""))


__all__ = [
    "CRS_SOURCE_VOCABULARY",
    "AXIS_ORDER_VOCABULARY",
    "DISTANCE_UNIT_VOCABULARY",
    "AREA_UNIT_VOCABULARY",
    "DENOMINATOR_VOCABULARY",
    "DENSITY_DENOMINATOR_UNIT",
    "CALCULATION_MODE_VOCABULARY",
    "SCALE_LEVEL_VOCABULARY",
    "SPATIAL_PRECISION_VOCABULARY",
    "SCOPE_OVERLAP_VOCABULARY",
    "VERDICT_VOCABULARY",
    "REPAIR_AUTHORIZATION_VOCABULARY",
    "SSC_REASON_CODES",
    "ReasonCode",
    "reason_code_metadata",
]
