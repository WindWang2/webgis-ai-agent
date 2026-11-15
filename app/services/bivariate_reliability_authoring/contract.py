"""G41 有版本的成对/可靠性作者计划（WP02）。

闭合词表 + ``extra="forbid"``：任意 paint JSON / 任意「confidence 数字」都
不能直通。两种核心模式：

* ``bivariate_matrix``  —— 两数值字段的成对分级矩阵（2×2 / 3×3）；
* ``reliability_dual_channel`` —— 估计值主色 + 有来源误差/区间的第二通道；
* ``revert`` —— 清除本域表达，回到本域作者决定前的完整快照（单步）。

两类模式共享：层/源身份（service 锚定）、源版本 + 内容指纹、每轴单位与
测量说明、方法/断点、缺失与 clip 政策、输入证据来源、计划 digest 与算法
版本。计划不携带任何要素数据，也不携带色值之外的任意样式对象。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.bivariate_reliability_authoring.errors import AuthoringError

#: 计划合同版本；未知版本一律拒绝（不猜测降级）。
PLAN_SCHEMA_VERSION = 1
#: 算法/语义版本。任何分级、转置、度量或映射语义变更都必须递增。
ALGORITHM_VERSION = "g41-authoring.v1"

MODES = ("bivariate_matrix", "reliability_dual_channel", "revert")
BOUNDARY_RULES = ("upper_inclusive", "legacy_lower_inclusive")
AXIS_METHODS = ("quantiles", "equal_interval", "manual")
MATRIX_SIZES = (2, 3)
MEASURE_KINDS = (
    "standard_error", "variance", "confidence_interval", "prediction_interval",
)
MEASURE_SOURCES = ("author_declared", "analysis_evidence")
RELIABILITY_DIRECTIONS = ("higher_less_reliable", "higher_more_reliable")
VISUAL_CHANNELS = ("opacity", "stroke_width")
CLIP_POLICIES = ("clamp", "reject")
ESTIMATE_METHODS = ("quantiles", "equal_interval")

#: 资源硬帽（超限明确拒绝，不静默截断后称完整）。
MAX_SOURCE_FEATURES = 250_000
MAX_BREAKS = 2          # n<=3 → 最多 2 个内部断点
MAX_SCATTER_POINTS = 400
MAX_HISTOGRAM_BINS = 24
MAX_DERIVED_BYTES = 48 * 1024 * 1024
MAX_LABEL_LEN = 64
MAX_UNIT_LEN = 32

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex(value: str, *, field: str, index: Optional[int] = None) -> str:
    if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
        raise AuthoringError(
            "INVALID_COLOR",
            f"{field} 必须是 #rrggbb 十六进制颜色，收到 {value!r}",
            field=field, index=index,
            correction_hint="使用 #rrggbb（如 #1f77b4）。",
        )
    return value.lower()


def _finite(value: Any, *, field: str, index: Optional[int] = None) -> float:
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthoringError(
            "NON_FINITE_VALUE", f"{field} 必须是有限数值，收到 {value!r}",
            field=field, index=index)
    f = float(value)
    if not math.isfinite(f):
        raise AuthoringError(
            "NON_FINITE_VALUE", f"{field} 不得为 NaN/Inf", field=field, index=index)
    return f


class SourceAnchor(BaseModel):
    """预览/提交时的源身份锚（防「预览用 A 源、应用时源已变」）。"""

    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(min_length=1, max_length=128)
    content_revision: int = Field(ge=1)          # 0 不是强版本，不接受
    content_fingerprint: Optional[str] = Field(default=None, max_length=128)


class AxisSpec(BaseModel):
    """单轴作者合同：字段、单位/测量说明、方法与断点。

    单位各自独立：两轴不同单位合法（B07），声明单位**不等于**做单位换算。
    ``unit=""`` 表示「未声明」，图例必须写「单位未声明」而不是猜测。
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=MAX_LABEL_LEN)
    unit: str = Field(default="", max_length=MAX_UNIT_LEN)
    measurement_note: str = Field(default="", max_length=160)
    method: Literal["quantiles", "equal_interval", "manual"] = "quantiles"
    #: 仅 manual 使用：n-1 个**内部**断点，严格递增、有限；不得自动排序。
    breaks: Optional[List[float]] = None

    @field_validator("breaks")
    @classmethod
    def _check_breaks(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return None
        if not v:
            raise AuthoringError(
                "INVALID_BREAKS", "manual 断点不得为空", field="breaks")
        vals = [_finite(x, field="breaks", index=i) for i, x in enumerate(v)]
        for i in range(1, len(vals)):
            if not vals[i - 1] < vals[i]:
                raise AuthoringError(
                    "BREAKS_NOT_STRICTLY_INCREASING",
                    f"断点必须严格递增：第 {i-1} 个 {vals[i-1]!r} ≥ 第 {i} 个 {vals[i]!r}；"
                    "服务端不自动排序，相等断点会产生空类",
                    field="breaks", index=i)
        return vals


class BivariateSpec(BaseModel):
    """双变量矩阵合同。行=B、列=A，行主序；高低方向与箭头随合同显式给出。"""

    model_config = ConfigDict(extra="forbid")

    n: Literal[2, 3] = 3
    axis_a: AxisSpec
    axis_b: AxisSpec
    #: 库存色阵名（BIVARIATE_MATRICES）；与 colors 二选一，不得同时给出。
    matrix_id: Optional[str] = Field(default=None, max_length=64)
    #: 自定义非对称色阵：必须给满 n*n 个 #rrggbb，行主序（B11）。
    colors: Optional[List[str]] = None
    boundary_rule: Literal["upper_inclusive", "legacy_lower_inclusive"] = "upper_inclusive"
    #: 交换两轴：同步转置类别索引、色阵、联合计数与图例（不只是换标题）。
    swap_axes: bool = False
    #: 纵向反转：格序、数值端点、paint 类别与箭头同步反转。
    flip_rows: bool = False
    #: 缺失政策：独立表达，不画最低格、不当 0。
    missing_label: str = Field(default="无数据", max_length=32)
    missing_color: str = Field(default="#cccccc", min_length=7, max_length=7)
    #: 有界散点样本（只用于检查配对/分布；计数仍为全量精确）。
    scatter_sample_size: int = Field(default=200, ge=0, le=MAX_SCATTER_POINTS)
    histogram_bins: int = Field(default=12, ge=2, le=MAX_HISTOGRAM_BINS)

    @field_validator("missing_color")
    @classmethod
    def _missing_color(cls, v: str) -> str:
        return _hex(v, field="missing_color")

    @field_validator("colors")
    @classmethod
    def _colors(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        return [_hex(c, field="colors", index=i) for i, c in enumerate(v)]

    @model_validator(mode="before")
    @classmethod
    def _precheck_axis_breaks(cls, data: Any) -> Any:
        """错误必须落到具体轴/断点索引，而不是笼统的 ``breaks``。"""
        if not isinstance(data, dict):
            return data
        for axis_name in ("axis_a", "axis_b"):
            axis = data.get(axis_name)
            if not isinstance(axis, dict) or axis.get("method") != "manual":
                continue
            br = axis.get("breaks")
            if not isinstance(br, list):
                continue
            for i in range(1, len(br)):
                a, b = br[i - 1], br[i]
                if (isinstance(a, (int, float)) and isinstance(b, (int, float))
                        and not isinstance(a, bool) and not isinstance(b, bool)
                        and not (a < b)):
                    raise AuthoringError(
                        "BREAKS_NOT_STRICTLY_INCREASING",
                        f"{axis_name} 断点必须严格递增：第 {i-1} 个 {a!r} ≥ "
                        f"第 {i} 个 {b!r}；服务端不自动排序",
                        field=f"{axis_name}.breaks", index=i,
                        correction_hint="输入严格递增的有限数值；相等断点会产生空类。")
        return data

    @model_validator(mode="after")
    def _shape(self) -> "BivariateSpec":
        if self.matrix_id and self.colors:
            raise AuthoringError(
                "AMBIGUOUS_COLOR_SOURCE",
                "matrix_id 与 colors 只能给一个（避免两处色值权威）", field="colors")
        if self.colors is not None and len(self.colors) != self.n * self.n:
            raise AuthoringError(
                "COLOR_COUNT_MISMATCH",
                f"自定义色阵需要恰好 {self.n*self.n} 个颜色，收到 {len(self.colors)}；"
                "颜色不足一律拒绝，不夹取、不循环复用",
                field="colors",
                correction_hint=f"提供 {self.n*self.n} 个 #rrggbb，或改用 matrix_id。")
        for name, axis in (("axis_a", self.axis_a), ("axis_b", self.axis_b)):
            if axis.method == "manual":
                needed = self.n - 1
                if axis.breaks is None or len(axis.breaks) != needed:
                    raise AuthoringError(
                        "MANUAL_BREAK_COUNT_MISMATCH",
                        f"{name} 的 manual 方法需要恰好 {needed} 个内部断点，"
                        f"收到 {len(axis.breaks or [])} 个",
                        field=f"{name}.breaks")
            elif axis.breaks is not None:
                raise AuthoringError(
                    "MODE_FIELD_MISMATCH",
                    f"{name}.breaks 只允许 manual 方法使用（自动方法由完整源计算）",
                    field=f"{name}.breaks")
        if self.axis_a.field == self.axis_b.field:
            raise AuthoringError(
                "SAME_FIELD_TWICE",
                "两轴不得使用同一字段——那不是双变量表达，只会生成伪共现",
                field="axis_b.field",
                correction_hint="选择第二个真正不同的数值字段。")
        return self


class MeasureBinding(BaseModel):
    """不确定度量与来源资格（WP06）。

    * ``standard_error`` / ``variance``：来自 ``value_field``（逐要素）。
    * ``confidence_interval`` / ``prediction_interval``：来自 ``low_field``
      与 ``high_field``，``level`` 必须有合法来源，区间不必包含估计值。
    * ``source="author_declared"`` 明确标记为**用户声明**，不是独立验证的
      统计推断；``analysis_evidence`` 必须给出可复核的 ``evidence_ref``。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "standard_error", "variance", "confidence_interval", "prediction_interval"
    ]
    #: SE/方差字段（逐要素，与估计同一已授权源内已对齐）。
    value_field: Optional[str] = Field(default=None, max_length=128)
    low_field: Optional[str] = Field(default=None, max_length=128)
    high_field: Optional[str] = Field(default=None, max_length=128)
    #: 区间置信水平，0<level<1；CI/PI 必填，绝不自动填 0.95。
    level: Optional[float] = None
    unit: str = Field(default="", max_length=MAX_UNIT_LEN)
    source: Literal["author_declared", "analysis_evidence"] = "author_declared"
    evidence_ref: Optional[str] = Field(default=None, max_length=128)
    evidence_note: str = Field(default="", max_length=200)
    method_note: str = Field(default="", max_length=200)
    #: 区间是否允许不含当前估计 / 非对称（由来源模型合同决定）。
    allow_noncentered: bool = True
    require_interval_contains_estimate: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "MeasureBinding":
        interval = self.kind in ("confidence_interval", "prediction_interval")
        if interval:
            if not (self.low_field and self.high_field):
                raise AuthoringError(
                    "MISSING_INTERVAL_FIELDS",
                    f"{self.kind} 需要 low_field 与 high_field",
                    field="low_field",
                    correction_hint="提供区间上下界字段；只有单个数字不能构成区间。")
            if self.value_field:
                raise AuthoringError(
                    "MODE_FIELD_MISMATCH",
                    "区间模式不接受 value_field（宽度由上下界相减得出）",
                    field="value_field")
            if self.level is None:
                raise AuthoringError(
                    "MISSING_INTERVAL_LEVEL",
                    f"{self.kind} 必须提供合法 level（0<level<1）；不会自动填 95%",
                    field="level",
                    correction_hint="从分析结果 uncertainty evidence 或来源模型合同读取 level。")
            if not (0.0 < float(self.level) < 1.0):
                raise AuthoringError(
                    "INVALID_INTERVAL_LEVEL",
                    f"level 必须在 (0,1) 内，收到 {self.level!r}",
                    field="level")
        else:
            if not self.value_field:
                raise AuthoringError(
                    "MISSING_MEASURE_FIELD",
                    f"{self.kind} 需要 value_field（逐要素误差字段）",
                    field="value_field")
            if self.low_field or self.high_field:
                raise AuthoringError(
                    "MODE_FIELD_MISMATCH",
                    f"{self.kind} 不接受区间字段", field="low_field")
            if self.level is not None:
                raise AuthoringError(
                    "MODE_FIELD_MISMATCH",
                    f"{self.kind} 没有区间 level 语义", field="level")
        if self.source == "analysis_evidence" and not self.evidence_ref:
            raise AuthoringError(
                "MISSING_EVIDENCE_REF",
                "声明 analysis_evidence 必须给出可复核的 evidence_ref",
                field="evidence_ref")
        if self.source == "author_declared" and not (
                self.method_note or self.evidence_note):
            raise AuthoringError(
                "AUTHOR_DECLARATION_UNEXPLAINED",
                "author_declared 必须给出 method_note / evidence_note 说明误差"
                "字段的来源与含义；不能只凭字段名把它当作统计推断",
                field="method_note",
                correction_hint="写明误差由哪个模型/测量流程产生，或改用 analysis_evidence + evidence_ref。")
        if self.low_field and self.high_field and self.low_field == self.high_field:
            raise AuthoringError(
                "DEGENERATE_INTERVAL",
                "区间上下界不得是同一字段（宽度恒为 0 不是区间证据）",
                field="high_field")
        return self


class VisualPolicy(BaseModel):
    """第二通道的有界视觉映射（WP07）。

    归一化仅用于视觉映射；min/max 归一结果不是 95% 置信度。``unknown``
    是独立输出，缺失误差不当 0、不当最高可靠性，也不重复乘 alpha。
    """

    model_config = ConfigDict(extra="forbid")

    channel: Literal["opacity", "stroke_width"] = "opacity"
    direction: Literal["higher_less_reliable", "higher_more_reliable"] = "higher_less_reliable"
    #: 误差量值域（与度量单位一致）；必须 min < max。
    min_value: float = Field(ge=0)
    max_value: float = Field(gt=0)
    #: 输出上下界；opacity ∈ (0,1]，stroke_width ∈ (0,25]。
    min_output: float = Field(gt=0)
    max_output: float = Field(gt=0)
    #: 误差缺失/无效时的独立视觉输出（不等于 min 或 max）。
    unknown_output: float = Field(gt=0)
    #: 误差**超出映射值域**（clip=clamp 时）的视觉输出。未声明则复用
    #: ``unknown_output``，并在图例披露「越界与缺失共用中性输出，仅计数分列」——
    #: 允许作者区分，但不允许悄悄把越界画成某个有效等级。
    out_of_domain_output: Optional[float] = Field(default=None, gt=0)
    buckets: int = Field(default=5, ge=2, le=8)
    clip: Literal["clamp", "reject"] = "clamp"
    unknown_label: str = Field(default="误差缺失/未知", max_length=48)

    @model_validator(mode="after")
    def _shape(self) -> "VisualPolicy":
        _finite(self.min_value, field="min_value")
        _finite(self.max_value, field="max_value")
        _finite(self.min_output, field="min_output")
        _finite(self.max_output, field="max_output")
        _finite(self.unknown_output, field="unknown_output")
        if self.out_of_domain_output is not None:
            _finite(self.out_of_domain_output, field="out_of_domain_output")
        if not self.min_value < self.max_value:
            raise AuthoringError(
                "INVALID_VISUAL_DOMAIN",
                "视觉映射值域必须 min_value < max_value（同值域无法表达差异）",
                field="min_value")
        if not self.min_output <= self.max_output:
            raise AuthoringError(
                "INVALID_VISUAL_RANGE", "视觉输出必须 min_output ≤ max_output",
                field="min_output")
        if self.channel == "opacity":
            for name, v in (("min_output", self.min_output),
                            ("max_output", self.max_output),
                            ("unknown_output", self.unknown_output),
                            ("out_of_domain_output",
                             self.out_of_domain_output)):
                if v is None:
                    continue
                if not (0.05 <= v <= 1.0):
                    raise AuthoringError(
                        "INVALID_VISUAL_RANGE",
                        f"opacity 输出必须在 [0.05,1.0] 内，{name}={v!r}",
                        field=name)
        elif self.channel == "stroke_width":
            for name, v in (("min_output", self.min_output),
                            ("max_output", self.max_output),
                            ("unknown_output", self.unknown_output),
                            ("out_of_domain_output",
                             self.out_of_domain_output)):
                if v is None:
                    continue
                if not (0.1 <= v <= 25.0):
                    raise AuthoringError(
                        "INVALID_VISUAL_RANGE",
                        f"stroke_width 输出必须在 [0.1,25] 内，{name}={v!r}",
                        field=name)
        return self


class ReliabilitySpec(BaseModel):
    """可靠性双通道合同：估计主色 + 有来源误差/区间的第二视觉通道。"""

    model_config = ConfigDict(extra="forbid")

    estimate_field: str = Field(min_length=1, max_length=128)
    estimate_label: str = Field(default="", max_length=MAX_LABEL_LEN)
    estimate_unit: str = Field(default="", max_length=MAX_UNIT_LEN)
    estimate_method: Literal["quantiles", "equal_interval"] = "quantiles"
    estimate_k: int = Field(default=5, ge=2, le=7)
    estimate_palette: str = Field(default="YlOrRd", max_length=48)
    measure: MeasureBinding
    visual: VisualPolicy
    boundary_rule: Literal["upper_inclusive", "legacy_lower_inclusive"] = "upper_inclusive"
    missing_label: str = Field(default="估计缺失", max_length=32)
    scatter_sample_size: int = Field(default=200, ge=0, le=MAX_SCATTER_POINTS)

    @model_validator(mode="after")
    def _shape(self) -> "ReliabilitySpec":
        # 估计主色由 ``thematic_spec._graduated_to_step`` 投影为 MapLibre step
        # (值 >= 阈值归上类)，无法承载 legacy 的「恰等断点归下类」语义。
        # 半生效字段会让图例区间与 paint 分级相反 —— 明确拒绝而非静默忽略。
        if self.boundary_rule != "upper_inclusive":
            raise AuthoringError(
                "UNSUPPORTED_RELIABILITY_BOUNDARY_RULE",
                "估计主色只支持 upper_inclusive（与 MapLibre step 一致）；"
                "legacy_lower_inclusive 会使图例区间与实际 paint 分级相反",
                field="boundary_rule",
                correction_hint="改用 upper_inclusive，或走 bivariate_matrix 模式。"
                                " 该模式把边界规则烘焙进派生类字段，图例与 paint 同源。")
        m = self.measure
        for f in (m.value_field, m.low_field, m.high_field):
            if f and f == self.estimate_field:
                raise AuthoringError(
                    "MEASURE_FIELD_IS_ESTIMATE",
                    "误差/区间字段不得与估计字段相同（估计值不是自己的误差）",
                    field="measure.value_field")
        # 方差是平方单位：仅声明单位不等于做了换算；此处强制显式说明。
        if m.kind == "variance" and not m.unit:
            raise AuthoringError(
                "VARIANCE_UNIT_REQUIRED",
                "variance 是平方单位，必须显式声明 unit（如 m²）；不得当作 SD 使用",
                field="measure.unit",
                correction_hint="声明平方单位，或改用 standard_error。")
        return self


class AuthoringPlan(BaseModel):
    """作者计划（typed，extra=forbid——任意 paint JSON 不得直通）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=PLAN_SCHEMA_VERSION)
    layer_id: str = Field(min_length=1, max_length=200)
    mode: Literal["bivariate_matrix", "reliability_dual_channel", "revert"]
    bivariate: Optional[BivariateSpec] = None
    reliability: Optional[ReliabilitySpec] = None
    source_anchor: Optional[SourceAnchor] = None
    title: Optional[str] = Field(default=None, max_length=120)

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != PLAN_SCHEMA_VERSION:
            raise AuthoringError(
                "UNKNOWN_PLAN_VERSION",
                f"不支持的作者计划版本 {v}（当前 {PLAN_SCHEMA_VERSION}）",
                status_code=400)
        return v

    @model_validator(mode="after")
    def _mode_shape(self) -> "AuthoringPlan":
        if self.mode == "bivariate_matrix":
            if self.bivariate is None:
                raise AuthoringError("MISSING_BIVARIATE_SPEC",
                                     "bivariate_matrix 模式需要 bivariate 合同",
                                     field="bivariate")
            if self.reliability is not None:
                raise AuthoringError("MODE_FIELD_MISMATCH",
                                     "bivariate_matrix 模式不接受 reliability",
                                     field="reliability")
        elif self.mode == "reliability_dual_channel":
            if self.reliability is None:
                raise AuthoringError("MISSING_RELIABILITY_SPEC",
                                     "reliability_dual_channel 模式需要 reliability 合同",
                                     field="reliability")
            if self.bivariate is not None:
                raise AuthoringError("MODE_FIELD_MISMATCH",
                                     "reliability_dual_channel 模式不接受 bivariate",
                                     field="bivariate")
        else:  # revert
            if self.bivariate is not None or self.reliability is not None:
                raise AuthoringError("MODE_FIELD_MISMATCH",
                                     "revert 不接受任何新表达合同", field="bivariate")
        return self

    def plan_digest(self) -> str:
        """计划摘要指纹：内容寻址，含算法版本；显式排除 source_anchor
        （源身份单独核对，避免把两次不同源的相同计划混为同一预览）。"""
        payload = {
            "algorithm_version": ALGORITHM_VERSION,
            "plan": self.model_dump(mode="json", exclude_none=True,
                                    exclude={"source_anchor"}),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def candidate_digest(self, source_anchor: Dict[str, Any]) -> str:
        """预览候选指纹：计划 × 源代次 × 内容指纹。任何参数/源变化即失效。"""
        payload = {
            "algorithm_version": ALGORITHM_VERSION,
            "plan_digest": self.plan_digest(),
            "source": source_anchor,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
        return "g41c-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def parse_plan(request: Any) -> AuthoringPlan:
    """外部请求体 → typed 计划。形状错误 → 422 INVALID_PLAN（逐字段定位）。"""
    try:
        return AuthoringPlan.model_validate(request)
    except AuthoringError:
        raise
    except Exception as exc:
        raise AuthoringError(
            "INVALID_PLAN",
            f"作者计划形状无效：{exc}",
            status_code=422,
            correction_hint="按 INTERFACES.md 提供 typed plan 字段。",
        ) from exc
