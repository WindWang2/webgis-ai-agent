"""PublicationScaleContract.v1（paper_scale.v1）—— 目标纸面比例尺制图综合契约。

定位（GOAL 04 / 多尺度制图综合 20261124，与既有 M1 缩放带档案互补不重叠）：

- 既有 ``ScaleRepresentationProfile``（profile.py）是**屏幕 zoom 带**驱动的
  全域表达计划；本契约是**固定纸面比例尺**（1:50万 + A4 横版 + DPI）驱动的
  出版综合计划输入面。两者经 ``equivalent_display_zoom`` 单一换算点衔接：
  纸面契约可复用既有综合引擎（generalize.py 的 band_zoom 通道），
  但消除/聚合/网络取舍阈值以纸面可辨尺寸为准（不是 zoom 带缺省）。
- **纯数据 + 纯函数**：零 IO、零时钟；同输入恒同输出。
- **单位纪律**：比例尺分母 1:N 在纸面 mm 的唯一换算
  ``ground_m_per_mm = N / 1000``；像素级换算必须经显式 DPI；
  地理坐标（度）不当米用 —— 一切容差出口都是 ``meters_ground``。
- **投影诚实（fail-honest）**：Web Mercator 下比例尺随纬度变化
  （cos(φ) 因子）—— 契约锚定 ``scale_reference_lat``，南北边缘变化比
  超阈值时生成披露而非静默当均匀；非 Mercator 投影声明进入契约但比例尺
  换算标 ``not_evaluated``，绝不猜测投影变形。
- **硬约束**：用户点名保留（feature ids / 词表值 / 重点区域 bbox）是
  hard constraint；综合消除/取舍永不让步（user-wins D8 同纪律）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.lib.cartography.multiscale.profile import (
    EARTH_CIRCUMFERENCE_M,
    DEFAULT_SCREEN_DPI,
    _INCH_IN_METERS,
)

#: 契约版本（schema_version 字段值；演进 additive 递增）。
PAPER_SCALE_CONTRACT_VERSION = 1

SCHEMA_VERSION = "paper_scale.v1"

#: 分母合法域（1:100 到 1:1 亿；出域 = 调用方契约错误）。
MIN_DENOMINATOR = 100
MAX_DENOMINATOR = 100_000_000

#: DPI 合法域（出域 fail-closed，与 scale_rules 纪律一致）。
MIN_DPI = 36.0
MAX_DPI = 2400.0

#: 纸面页尺寸上界（与 print_layout.MAX_PAGE_MM 同源，不抬高旧上限）。
MAX_PAGE_MM = 1200.0

#: Mercator 纬度域（与 profile.display_meters_per_pixel 同钳制）。
_MERCATOR_LAT_MAX = 85.0511287798066

#: 南北边缘比例尺变化比超过该阈值 → 生成强制披露（真实变形，不静默）。
SCALE_VARIATION_DISCLOSURE_RATIO = 0.05

#: 投影声明词表。``web_mercator``：比例尺换算可执行（含纬度变化披露）；
#: ``epsg:4326``：局部等距近似通道（同 generalize 引擎的披露纪律）；
#: 其余 CRS 仅声明、换算 ``not_evaluated``。
PROJECTION_DECLARATIONS = ("web_mercator", "epsg:4326")

#: 地图用途词表 → 综合策略缺省（用途决定取舍激进度与网络硬约束）。
MAP_PURPOSES: Tuple[str, ...] = (
    "overview",             # 总览/态势：取舍最激进，行政骨架优先
    "reference",            # 参考图：均衡
    "planning",             # 规划/决策：面单元语义优先，保持紧凑
    "navigation",           # 导航/路网：网络连通性为硬约束
    "thematic_exhibition",  # 专题表达：专题量纲守恒为硬约束
)

#: 用途 → 网络连通性是否硬约束（navigation 恒硬；其余可降级披露）。
NETWORK_HARD_PURPOSES = frozenset({"navigation"})

#: 用途 → 专题量纲守恒是否硬约束（专题表达图恒硬：图上计数/总量必须与
#: 原始数据可对账 —— 消除/聚合只能 display-only，不替换统计口径）。
THEMATIC_CONSERVATION_HARD_PURPOSES = frozenset({"thematic_exhibition"})

#: 用途 → 取舍激进度（kept 秩的放大因子语义；供 compile 层换算，本模块
#: 只声明档位不解释具体秩）。
SELECTION_AGGRESSIVENESS: Dict[str, str] = {
    "overview": "high",
    "reference": "medium",
    "planning": "medium",
    "navigation": "low",
    "thematic_exhibition": "low",
}

#: ISO A 系纸（纵向 mm；横向交换）。自定义纸张必须显式给 mm 且 ≤ 上界。
ISO_A_PAGE_MM: Dict[str, Tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
}

PAGE_ORIENTATIONS = ("portrait", "landscape")

#: 缺省最小可辨尺寸（纸面 mm；制图惯例量级）。
DEFAULT_MIN_VISIBLE_LINE_MM = 0.3     # 线划可辨宽
DEFAULT_MIN_VISIBLE_AREA_MM2 = 2.0    # 面斑最小可辨面积（约 1.4×1.4mm）
DEFAULT_MIN_VISIBLE_SEPARATION_MM = 0.2  # 要素间最小可辨间隔

#: 缺省容差系数：auto 容差 = 系数 × 最小可辨间隔的地面尺寸（亚可辨简化
#: —— 位移低于纸面可辨阈值，视觉无损）。
AUTO_TOLERANCE_FACTOR = 0.5

#: 硬约束种类词表。
HARD_CONSTRAINT_KINDS = (
    "keep_feature_ids",   # 显式要素 id（与 FeatureSelectionPolicy.mandatory 同源）
    "keep_values",        # 重要性词表值恒保留（如「县城」所在 admin 级）
    "keep_region",        # 重点区域 bbox（内部要素不被消除）
)

#: 校验错误码前缀。
_ERR = "psc."


# ── 换算（唯一出口；消费方禁止自算混口径）────────────────────────────────


def ground_m_per_mm(denominator: int) -> float:
    """1:N 纸面 1mm 的地面米数（唯一换算点；分母越域返回 nan 之外的
    fail-closed 值 —— 调用方先过 validate；本函数不抛，便于纯函数链）。"""
    n = float(denominator)
    if not (MIN_DENOMINATOR <= n <= MAX_DENOMINATOR):
        return float("nan")
    return n / 1000.0


def ground_m_per_pixel(denominator: int, dpi: float) -> float:
    """1:N × DPI → 地面米/输出像素（出域 fail-closed nan）。"""
    n = float(denominator)
    if not (MIN_DENOMINATOR <= n <= MAX_DENOMINATOR):
        return float("nan")
    if not (isinstance(dpi, (int, float)) and not isinstance(dpi, bool)
            and MIN_DPI <= float(dpi) <= MAX_DPI):
        return float("nan")
    return n * (_INCH_IN_METERS / float(dpi))


def equivalent_display_zoom(
    denominator: int, latitude: float = 0.0,
    *, dpi: float = DEFAULT_SCREEN_DPI,
) -> Optional[float]:
    """纸面比例尺 → 等效 MapLibre display zoom（512px 基准，纬度锚定）。

    语义：该 zoom 下屏幕显示分辨率（米/显示像素）等于纸面输出分辨率。
    这是纸面契约复用既有 zoom 带引擎（generalize band_zoom 通道）的
    **单一衔接点** —— 除本函数外不互推 zoom/比例尺。
    """
    mpp = ground_m_per_pixel(denominator, dpi)
    if math.isnan(mpp):
        return None
    lat = min(_MERCATOR_LAT_MAX, max(-_MERCATOR_LAT_MAX, float(latitude)))
    num = EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat))
    if mpp <= 0 or num <= 0:
        return None
    return math.log2(num / (512.0 * mpp))


def scale_denominator_at_latitude(
    denominator: int, latitude: float,
    *, reference_lat: float = 0.0,
) -> Optional[int]:
    """Web Mercator 真实变形：锚定分母在 latitude 处的实际分母（1:N'）。

    N'(φ) = N · cos(φ) / cos(φ_ref)（评审 R1-P1-2 修正方向：Mercator 局部
    放大因子 sec(φ) ⇒ 图上 1mm 覆盖的地面距离 ∝ cos(φ) —— 与同包
    profile.display_meters_per_pixel 的 m/px ∝ cos(lat) 同向；φ=φ_ref 处
    N'=N）。纬度越域返回 None（fail-honest）。
    """
    n = float(denominator)
    if not (MIN_DENOMINATOR <= n <= MAX_DENOMINATOR):
        return None
    lat = float(latitude)
    ref = float(reference_lat)
    if not (-_MERCATOR_LAT_MAX <= lat <= _MERCATOR_LAT_MAX):
        return None
    if not (-_MERCATOR_LAT_MAX <= ref <= _MERCATOR_LAT_MAX):
        return None
    cos_lat = math.cos(math.radians(lat))
    cos_ref = math.cos(math.radians(ref))
    if cos_lat <= 0 or cos_ref <= 0:
        return None
    return max(1, int(round(n * cos_lat / cos_ref)))


# ── 契约模型 ──────────────────────────────────────────────────────────────


class _ContractModel(BaseModel):
    """round-trip 保真基类（repo 纪律：extra=allow，未知键不丢）。"""

    model_config = ConfigDict(extra="allow")


class PageSpec(_ContractModel):
    """纸面页规格（mm 显式；ISO 词表或 custom）。"""

    format_id: str = "A4"
    orientation: str = "landscape"
    width_mm: float = 297.0
    height_mm: float = 210.0
    dpi: float = 300.0

    @model_validator(mode="after")
    def _derive_dims(self) -> "PageSpec":
        fid = str(self.format_id)
        if fid in ISO_A_PAGE_MM:
            short, long_ = ISO_A_PAGE_MM[fid]
            if self.orientation == "landscape":
                self.width_mm, self.height_mm = long_, short
            else:
                self.width_mm, self.height_mm = short, long_
        # custom：显式 mm 保留；dpi 原值保留（越域由 validate_contract
        # 报错，不静默改写 —— 评审 R1-P3：改写后校验永远捕不到原值）
        return self


class FrameViewport(_ContractModel):
    """图框（页内地图视窗）mm 几何 + 锚定纬度。"""

    width_mm: float = 260.0
    height_mm: float = 170.0
    scale_reference_lat: float = 0.0
    #: 数据 bbox（经纬度 [minx,miny,maxx,maxy]；用于比例尺变化披露与
    #: keep_region 判定；缺席 = 披露降级为锚点单点）。
    data_bbox: Optional[List[float]] = None


class ErrorBudget(_ContractModel):
    """综合误差预算（全部 meters_ground / 相对比；auto 缺省派生自纸面）。"""

    #: auto 容差 = AUTO_TOLERANCE_FACTOR × min_separation_mm × m/mm；
    #: 显式 tolerance_m 覆盖 auto。
    tolerance_m: Optional[float] = None
    max_displacement_m: Optional[float] = None
    max_area_error_ratio: float = 0.02
    min_visible_line_mm: float = DEFAULT_MIN_VISIBLE_LINE_MM
    min_visible_area_mm2: float = DEFAULT_MIN_VISIBLE_AREA_MM2
    min_visible_separation_mm: float = DEFAULT_MIN_VISIBLE_SEPARATION_MM


class HardConstraint(_ContractModel):
    """用户显式保留（hard constraint；综合永不让步）。"""

    kind: str  # keep_feature_ids | keep_values | keep_region
    feature_ids: List[str] = Field(default_factory=list)
    values: List[str] = Field(default_factory=list)
    importance_field: Optional[str] = None
    region_bbox: Optional[List[float]] = None


class PublicationScaleContract(_ContractModel):
    """目标纸面比例尺综合契约（一份 = 一次出版综合的完整输入面）。"""

    schema_version: str = SCHEMA_VERSION
    contract_version: int = PAPER_SCALE_CONTRACT_VERSION
    contract_id: str = ""
    target_denominator: int
    purpose: str = "reference"
    projection: str = "web_mercator"
    page: PageSpec = Field(default_factory=PageSpec)
    frame: FrameViewport = Field(default_factory=FrameViewport)
    error_budget: ErrorBudget = Field(default_factory=ErrorBudget)
    hard_constraints: List[HardConstraint] = Field(default_factory=list)
    theme: Optional[str] = None
    audience: Optional[str] = None
    generated_by: str = ""


# ── 派生视图（纯函数）────────────────────────────────────────────────────


def resolve_tolerance_m(contract: PublicationScaleContract) -> float:
    """容差裁决：显式 tolerance_m 优先；否则 auto（亚可辨简化）。

    auto = AUTO_TOLERANCE_FACTOR × min_separation_mm × ground_m_per_mm，
    即简化位移低于纸面可辨阈值。分母越域 → 0.0（不综合，fail-closed）。
    """
    explicit = contract.error_budget.tolerance_m
    if explicit is not None:
        return max(0.0, float(explicit))
    m_per_mm = ground_m_per_mm(contract.target_denominator)
    if math.isnan(m_per_mm):
        return 0.0
    return max(0.0, AUTO_TOLERANCE_FACTOR
               * float(contract.error_budget.min_visible_separation_mm)
               * m_per_mm)


def minimum_mappable_area_m2(contract: PublicationScaleContract) -> float:
    """目标尺度最小可辨面斑（米²）：min_area_mm2 × (m/mm)²。

    面消除（eliminate）的缺省阈值来源；显式策略可覆盖。分母越域 → inf
    （一切面都不可辨是荒谬结论 → 调用方应先 validate）。
    """
    m_per_mm = ground_m_per_mm(contract.target_denominator)
    if math.isnan(m_per_mm):
        return float("inf")
    return float(contract.error_budget.min_visible_area_mm2) * m_per_mm * m_per_mm


def minimum_mappable_length_m(contract: PublicationScaleContract) -> float:
    """目标尺度最小可辨线长（米）：min_line_mm × m/mm。"""
    m_per_mm = ground_m_per_mm(contract.target_denominator)
    if math.isnan(m_per_mm):
        return float("inf")
    return float(contract.error_budget.min_visible_line_mm) * m_per_mm


def mandatory_ids_of(contract: PublicationScaleContract) -> List[str]:
    """硬约束 → 恒保留要素 id 并集（保序去重）。"""
    out: List[str] = []
    for c in contract.hard_constraints:
        if c.kind == "keep_feature_ids":
            for fid in c.feature_ids:
                if fid not in out:
                    out.append(fid)
    return out


def mandatory_values_of(contract: PublicationScaleContract) -> Dict[str, List[str]]:
    """硬约束 → 字段 → 恒保留取值并集（keep_values）。"""
    out: Dict[str, List[str]] = {}
    for c in contract.hard_constraints:
        if c.kind == "keep_values" and c.importance_field:
            bucket = out.setdefault(c.importance_field, [])
            for v in c.values:
                sv = str(v)
                if sv not in bucket:
                    bucket.append(sv)
    return out


def keep_region_of(contract: PublicationScaleContract) -> Optional[List[float]]:
    """硬约束 → 重点区域 bbox（多个取并集外接框；无则 None）。"""
    boxes = [
        c.region_bbox for c in contract.hard_constraints
        if c.kind == "keep_region"
        and isinstance(c.region_bbox, list) and len(c.region_bbox) == 4
    ]
    if not boxes:
        return None
    xs0, ys0, xs1, ys1 = [], [], [], []
    for b in boxes:
        try:
            xs0.append(float(b[0]))
            ys0.append(float(b[1]))
            xs1.append(float(b[2]))
            ys1.append(float(b[3]))
        except (TypeError, ValueError):
            continue
    if not xs0:
        return None
    return [min(xs0), min(ys0), max(xs1), max(ys1)]


def frame_ground_extent_m(contract: PublicationScaleContract) -> Optional[float]:
    """图框地面覆盖（米²）= frame w×h mm × (m/mm)²（越域 None）。"""
    m_per_mm = ground_m_per_mm(contract.target_denominator)
    if math.isnan(m_per_mm):
        return None
    return float(contract.frame.width_mm) * float(contract.frame.height_mm) \
        * m_per_mm * m_per_mm


def scale_variation_report(
    contract: PublicationScaleContract,
) -> Dict[str, Any]:
    """Web Mercator 纬度变形披露面（真实比例尺≠名义比例尺的诚实面）。

    - 锚点分母（scale_reference_lat 处）恒名义分母（cos 锚定=1 的定义）；
    - data_bbox 在场 → 南北边缘实际分母 + 变化比；变化比超阈值 →
      ``disclosure_required=True``；
    - 非 web_mercator 投影 → ``evaluated=False``（not_evaluated，不猜）。
    """
    out: Dict[str, Any] = {
        "evaluated": False,
        "projection": contract.projection,
        "scale_reference_lat": contract.frame.scale_reference_lat,
        "denominator_at_reference": None,
        "denominator_at_north_edge": None,
        "denominator_at_south_edge": None,
        "variation_ratio": None,
        "disclosure_required": False,
    }
    if contract.projection != "web_mercator":
        return out
    out["evaluated"] = True
    out["denominator_at_reference"] = int(contract.target_denominator)
    bbox = contract.frame.data_bbox
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return out
    try:
        north, south = float(bbox[3]), float(bbox[1])
    except (TypeError, ValueError):
        return out
    ref_lat = float(contract.frame.scale_reference_lat)
    d_n = scale_denominator_at_latitude(
        contract.target_denominator, north, reference_lat=ref_lat)
    d_s = scale_denominator_at_latitude(
        contract.target_denominator, south, reference_lat=ref_lat)
    out["denominator_at_north_edge"] = d_n
    out["denominator_at_south_edge"] = d_s
    if d_n and d_s:
        ratio = abs(d_n - d_s) / max(d_n, d_s)
        out["variation_ratio"] = round(ratio, 6)
        out["disclosure_required"] = (
            ratio >= SCALE_VARIATION_DISCLOSURE_RATIO)
    return out


def strategy_hints_of(contract: PublicationScaleContract) -> Dict[str, Any]:
    """用途 → 综合策略缺省提示（compile 层消费；本模块只声明不执行）。"""
    return {
        "selection_aggressiveness": SELECTION_AGGRESSIVENESS.get(
            contract.purpose, "medium"),
        "network_connectivity_hard": contract.purpose in NETWORK_HARD_PURPOSES,
        "thematic_conservation_hard": contract.purpose in THEMATIC_CONSERVATION_HARD_PURPOSES,
        "auto_tolerance_m": resolve_tolerance_m(contract),
        "min_area_m2": minimum_mappable_area_m2(contract),
        "min_line_length_m": minimum_mappable_length_m(contract),
    }


def contract_fingerprint(contract: PublicationScaleContract) -> str:
    """契约内容指纹（确定性；不含 contract_id/generated_by）。"""
    import hashlib
    import json

    payload = contract.model_dump(mode="json", exclude_none=True)
    for key in ("contract_id", "generated_by"):
        payload.pop(key, None)
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "psc-sha256:" + hashlib.sha256(blob).hexdigest()[:16]


# ── 校验 ─────────────────────────────────────────────────────────────────


def validate_contract(contract: PublicationScaleContract) -> List[str]:
    """契约结构合法性 → 错误码列表（空 = 合法）。"""
    errors: List[str] = []
    n = int(contract.target_denominator)
    if not (MIN_DENOMINATOR <= n <= MAX_DENOMINATOR):
        errors.append(f"{_ERR}denominator_out_of_range:{n}")
    if contract.purpose not in MAP_PURPOSES:
        errors.append(f"{_ERR}unknown_purpose:{contract.purpose}")
    if contract.projection not in PROJECTION_DECLARATIONS:
        errors.append(f"{_ERR}unknown_projection:{contract.projection}")
    page = contract.page
    fid = str(page.format_id)
    if fid not in ISO_A_PAGE_MM:
        # custom 纸：显式 mm 且在上界内
        if not (0 < float(page.width_mm) <= MAX_PAGE_MM
                and 0 < float(page.height_mm) <= MAX_PAGE_MM):
            errors.append(f"{_ERR}page_size_out_of_range:{fid}")
    if page.orientation not in PAGE_ORIENTATIONS:
        errors.append(f"{_ERR}unknown_orientation:{page.orientation}")
    if not (MIN_DPI <= float(page.dpi) <= MAX_DPI):
        errors.append(f"{_ERR}dpi_out_of_range:{page.dpi}")
    frame = contract.frame
    if not (0 < float(frame.width_mm) <= MAX_PAGE_MM
            and 0 < float(frame.height_mm) <= MAX_PAGE_MM):
        errors.append(f"{_ERR}frame_size_out_of_range")
    # 图框不得超过纸面（留边距为 0 也算贴边合法）
    if not errors or f"{_ERR}page_size" not in "".join(errors):
        if float(frame.width_mm) > float(page.width_mm) + 1e-9 or \
                float(frame.height_mm) > float(page.height_mm) + 1e-9:
            errors.append(f"{_ERR}frame_exceeds_page")
    bbox = frame.data_bbox
    if bbox is not None:
        if not (isinstance(bbox, list) and len(bbox) == 4):
            errors.append(f"{_ERR}data_bbox_shape")
        else:
            try:
                minx, miny, maxx, maxy = (float(v) for v in bbox)
                if not (minx < maxx and miny < maxy):
                    errors.append(f"{_ERR}data_bbox_empty")
                if not all(-180.0 <= v <= 180.0 for v in (minx, maxx)) or \
                        not all(-90.0 <= v <= 90.0 for v in (miny, maxy)):
                    errors.append(f"{_ERR}data_bbox_out_of_geo_domain")
            except (TypeError, ValueError):
                errors.append(f"{_ERR}data_bbox_non_numeric")
    budget = contract.error_budget
    if budget.tolerance_m is not None and float(budget.tolerance_m) < 0:
        errors.append(f"{_ERR}tolerance_negative")
    if budget.max_displacement_m is not None and float(budget.max_displacement_m) < 0:
        errors.append(f"{_ERR}displacement_negative")
    if not (0.0 <= float(budget.max_area_error_ratio) <= 1.0):
        errors.append(f"{_ERR}area_error_ratio_out_of_range")
    for key, val in (("min_line", budget.min_visible_line_mm),
                     ("area", budget.min_visible_area_mm2),
                     ("separation", budget.min_visible_separation_mm)):
        try:
            if float(val) <= 0:
                errors.append(f"{_ERR}min_visible_nonpositive:{key}")
        except (TypeError, ValueError):
            errors.append(f"{_ERR}min_visible_non_numeric:{key}")
    for c in contract.hard_constraints:
        if c.kind not in HARD_CONSTRAINT_KINDS:
            errors.append(f"{_ERR}unknown_constraint_kind:{c.kind}")
            continue
        if c.kind == "keep_feature_ids" and not c.feature_ids:
            errors.append(f"{_ERR}constraint_empty_ids")
        if c.kind == "keep_values":
            if not c.values:
                errors.append(f"{_ERR}constraint_empty_values")
            if not c.importance_field:
                errors.append(f"{_ERR}constraint_missing_field")
        if c.kind == "keep_region":
            if not (isinstance(c.region_bbox, list) and len(c.region_bbox) == 4):
                errors.append(f"{_ERR}constraint_region_shape")
    return errors


# ── 工具入参解析（纯函数；Pi 工具层消费）───────────────────────────────


_DENOM_SUFFIXES: Tuple[Tuple[str, float], ...] = (
    ("亿", 100_000_000.0), ("万", 10_000.0), ("k", 1_000.0), ("千", 1_000.0),
)


def parse_denominator_text(text: str) -> Optional[int]:
    """「1:50万」/「50万」/「1:500000」/「1:500,000」 → 分母 int。

    无法解析 → None（调用方结构化拒绝，不猜）。出域 → None。
    """
    s = str(text).strip().replace(",", "").replace("：", ":").replace(" ", "")
    if ":" in s:
        s = s.split(":", 1)[1]
    elif s.startswith("1/"):
        s = s[2:]
    s = s.strip()
    if not s:
        return None
    multiplier = 1.0
    for suffix, factor in _DENOM_SUFFIXES:
        if s.endswith(suffix):
            multiplier = factor
            s = s[: -len(suffix)]
            break
    try:
        value = float(s)
    except ValueError:
        return None
    if value <= 0:
        return None
    n = int(round(value * multiplier))
    if not (MIN_DENOMINATOR <= n <= MAX_DENOMINATOR):
        return None
    return n


def parse_page_text(text: str) -> Optional[Tuple[str, str]]:
    """「A4横版」/「A3 portrait」/「a4 横向」 → (format_id, orientation)。

    无法解析 → None。
    """
    s = str(text).strip().lower().replace(" ", "").replace("_", "")
    if not s:
        return None
    orientation = ""
    for token, canon in (
        ("横版", "landscape"), ("横向", "landscape"), ("landscape", "landscape"),
        ("竖版", "portrait"), ("纵向", "portrait"), ("portrait", "portrait"),
    ):
        if token in s:
            orientation = canon
            s = s.replace(token, "")
            break
    fmt = s.upper()
    if fmt not in ISO_A_PAGE_MM:
        return None
    if not orientation:
        orientation = "landscape" if (
            ISO_A_PAGE_MM[fmt][1] > ISO_A_PAGE_MM[fmt][0]) else "portrait"
    return fmt, orientation


__all__ = [
    "SCHEMA_VERSION",
    "PAPER_SCALE_CONTRACT_VERSION",
    "MIN_DENOMINATOR",
    "MAX_DENOMINATOR",
    "MIN_DPI",
    "MAX_DPI",
    "MAX_PAGE_MM",
    "PROJECTION_DECLARATIONS",
    "MAP_PURPOSES",
    "NETWORK_HARD_PURPOSES",
    "THEMATIC_CONSERVATION_HARD_PURPOSES",
    "SELECTION_AGGRESSIVENESS",
    "ISO_A_PAGE_MM",
    "PAGE_ORIENTATIONS",
    "HARD_CONSTRAINT_KINDS",
    "SCALE_VARIATION_DISCLOSURE_RATIO",
    "AUTO_TOLERANCE_FACTOR",
    "PageSpec",
    "FrameViewport",
    "ErrorBudget",
    "HardConstraint",
    "PublicationScaleContract",
    "ground_m_per_mm",
    "ground_m_per_pixel",
    "equivalent_display_zoom",
    "scale_denominator_at_latitude",
    "resolve_tolerance_m",
    "minimum_mappable_area_m2",
    "minimum_mappable_length_m",
    "mandatory_ids_of",
    "mandatory_values_of",
    "keep_region_of",
    "frame_ground_extent_m",
    "scale_variation_report",
    "strategy_hints_of",
    "contract_fingerprint",
    "validate_contract",
    "parse_denominator_text",
    "parse_page_text",
]
