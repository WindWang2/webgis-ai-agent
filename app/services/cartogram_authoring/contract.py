"""G47 计划合同（schema_version=1，mode=dorling_circle）。

一个合法计划把"哪些区域、哪个数量字段、什么单位/语义、用哪个投影与质量门、
资源上限多少"钉死成可复算的输入。计划的任何变化都会改变 ``plan_digest``，
从而让旧候选/旧布局失效（不允许"参数变了但复用旧结果"）。

设计要点（与任务书 §三/§四对齐）：

* **数量语义必须由作者声明**：``unit`` 非空、``meaning ∈ count|amount``、
  ``aggregation=sum``。率/密度/单位混合被拒绝（``RATE_OR_MIXED_UNIT``），
  绝不"自动乘面积"或"取绝对值"。
* **布局坐标不是地理坐标**：``coordinate_space`` 恒为 ``diagram``；投影只用于
  由原几何算 anchor/邻接，绝不写进下游 GIS 分析。
* **质量门是合同的一部分**：面积相对误差、残余重叠、邻接保留都要在布局生成后
  由**独立** oracle 复核；达不到就是 ``QUALITY_GATE_FAILED``，不做 publication-ready。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services.cartogram_authoring.errors import AuthoringError

SCHEMA_VERSION = 1
ALGORITHM_ID = "dorling-circle"
#: 算法版本参与幂等键/布局身份；改求解或质量语义必须同步升版。
ALGORITHM_VERSION = "dorling.v1"
CITATION = "Dorling, D. (1996) Area Cartograms: Their Use and Creation, pp.32-35, fig.9a, app.B"

#: 硬帽（计划里只能更小，不能更大）。
MAX_FEATURES_HARD = 400
MAX_ITERATIONS_HARD = 4000
MAX_SECONDS_HARD = 60.0
MAX_ADJACENCY_EDGES_HARD = 8000
MAX_LABEL_LEN = 80
COORDINATE_SPACE = "diagram"

_DEFAULT_PROJECTION = "EPSG:3857"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_bool(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric quantity")
    return value


class QuantitySpec(_Spec):
    """数量字段 + 单位 + 语义。三者缺一不可，避免"看起来对"的自动猜测。"""

    field: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=24)
    meaning: Literal["count", "amount"]
    aggregation: Literal["sum"]

    @field_validator("field")
    @classmethod
    def _field(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("field must not be blank")
        return s

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("unit must not be blank")
        if s.lower() in {"rate", "ratio", "percent", "%", "per_capita", "density"}:
            raise ValueError(
                "rate/ratio/density 不是可加总量；面积 cartogram 需要 count/amount")
        return s


class ScopeSpec(_Spec):
    """可选的范围收窄；排除必须逐项给理由（发布说明不能继续声称全量）。"""

    include_ids: Optional[List[str]] = None
    exclude_ids: List[str] = Field(default_factory=list)
    exclude_reasons: Dict[str, str] = Field(default_factory=dict)

    @field_validator("include_ids")
    @classmethod
    def _include(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if not v:
            raise ValueError("include_ids 不能是空数组（要全量请省略该键）")
        if len(v) > MAX_FEATURES_HARD:
            raise ValueError(f"include_ids 超过硬帽 {MAX_FEATURES_HARD}")
        return v

    @field_validator("exclude_ids")
    @classmethod
    def _exclude(cls, v: List[str]) -> List[str]:
        if len(v) > MAX_FEATURES_HARD:
            raise ValueError(f"exclude_ids 超过硬帽 {MAX_FEATURES_HARD}")
        return v


class ProjectionSpec(_Spec):
    """平面准备用投影。默认 Web Mercator（仅用于布局准备，不用于面积测量）。"""

    crs: str = Field(default=_DEFAULT_PROJECTION, min_length=3, max_length=64)


class AreaScaleSpec(_Spec):
    """面积比例基准（diagram 单位）。

    ``max_radius`` 是最大圆的**目标半径**；``extent`` 是图面最长边。两者共同
    决定共同面积系数 ``k``（所有圆共享，逐圆 clamp 被禁止）。
    """

    max_radius: float = Field(default=36.0, gt=0.5, le=200.0)
    extent: float = Field(default=1000.0, gt=50.0, le=10000.0)

    @field_validator("max_radius")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("max_radius must be finite")
        return v


class AdjacencySpec(_Spec):
    """原邻接判定合同（共享边长度，不用点接触/最近邻臆造）。"""

    #: 坐标吸附容差（米，投影后）：小于该尺度的缝隙视为共边。
    snap_tolerance_m: float = Field(default=1.0, gt=0.0, le=5000.0)
    #: 共享边长度下限（米）：角点接触 ≈ 0，不得算邻接。
    min_shared_length_m: float = Field(default=10.0, gt=0.0, le=100000.0)


class SolverSpec(_Spec):
    """Dorling 迭代参数（有界；论文参数不保证任意输入收敛）。"""

    max_iterations: int = Field(default=400, ge=1, le=MAX_ITERATIONS_HARD)
    repulsion: float = Field(default=1.0, gt=0.0, le=2.0)
    attraction: float = Field(default=0.35, ge=0.0, le=2.0)
    damping: float = Field(default=0.7, gt=0.0, le=1.0)
    #: 单步最大位移（以最大半径为参照的比例），保证数值稳定。
    max_step_ratio: float = Field(default=0.5, gt=0.0, le=1.0)
    #: 收敛判据：最大位移 < tolerance * max_radius。
    tolerance: float = Field(default=1e-3, gt=0.0, le=0.1)


class QualityGate(_Spec):
    """发布资格门（独立 oracle 复核；未达标不得标 publication-ready）。"""

    #: 每个正圆的面积相对误差上限（共同系数带来的浮点误差远小于此）。
    max_area_rel_error: float = Field(default=0.01, gt=0.0, le=1.0)
    #: 任一圆对的最大重叠比例（重叠面积 / 较小圆面积）上限。
    max_pair_overlap_ratio: float = Field(default=0.02, ge=0.0, le=1.0)
    #: 允许超过上式的圆对数量占全部可能对的比例。
    max_overlapping_pair_ratio: float = Field(default=0.01, ge=0.0, le=1.0)
    #: 归一位移上限（位移 / 最大半径）；只作披露，不做"移动小=成功"。
    max_displacement_ratio: float = Field(default=8.0, gt=0.0, le=100.0)


class Budgets(_Spec):
    max_features: int = Field(default=MAX_FEATURES_HARD, ge=2, le=MAX_FEATURES_HARD)
    max_seconds: float = Field(default=30.0, gt=0.1, le=MAX_SECONDS_HARD)
    max_adjacency_edges: int = Field(
        default=MAX_ADJACENCY_EDGES_HARD, ge=1, le=MAX_ADJACENCY_EDGES_HARD)


class AuthoringPlan(_Spec):
    """完整作者计划。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    mode: Literal["dorling_circle"] = "dorling_circle"
    layer_id: str = Field(min_length=1, max_length=200)
    quantity: QuantitySpec
    scope: ScopeSpec = Field(default_factory=ScopeSpec)
    missing_policy: Literal["list_only", "reject"] = "list_only"
    zero_policy: Literal["list_only", "reject"] = "list_only"
    source_crs: str = Field(default="EPSG:4326", min_length=3, max_length=64)
    projection: ProjectionSpec = Field(default_factory=ProjectionSpec)
    area_scale: AreaScaleSpec = Field(default_factory=AreaScaleSpec)
    adjacency: AdjacencySpec = Field(default_factory=AdjacencySpec)
    solver: SolverSpec = Field(default_factory=SolverSpec)
    quality_gate: QualityGate = Field(default_factory=QualityGate)
    budgets: Budgets = Field(default_factory=Budgets)
    title: Optional[str] = Field(default=None, max_length=120)
    #: 最近一次 inspect/plan 返回的源锚；参与 candidate_digest（不参与 plan_digest）。
    source_anchor: Optional[Dict[str, Any]] = None

    # ── 身份 ──────────────────────────────────────────────────────────
    def canonical(self) -> Dict[str, Any]:
        data = self.model_dump(mode="json")
        data.pop("source_anchor", None)
        return data

    def plan_digest(self) -> str:
        payload = json.dumps(
            {"algorithm": ALGORITHM_ID, "version": ALGORITHM_VERSION,
             "plan": self.canonical()},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        return "g47p-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def candidate_digest(self, anchor: Dict[str, Any]) -> str:
        """计划 + 源同代锚 + 算法版本 → 布局身份（旧候选不可冒充新结果）。"""
        payload = json.dumps(
            {"plan": self.plan_digest(),
             "anchor": {k: anchor.get(k) for k in
                        ("ref_id", "content_revision", "content_fingerprint")},
             "algorithm": ALGORITHM_ID, "version": ALGORITHM_VERSION},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        return "g47c-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def effective_max_features(self) -> int:
        """范围内允许的统计单元上限（scope 收窄时按 include 数量取更小值）。"""
        cap = int(self.budgets.max_features)
        if self.scope.include_ids is not None:
            cap = min(cap, len(self.scope.include_ids))
        return max(2, cap)


def _validation_message(exc: ValidationError) -> tuple[str, str]:
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first.get("loc") or ())
    msg = str(first.get("msg") or "invalid plan")
    return loc, msg


def parse_plan(raw: Any) -> AuthoringPlan:
    """把外部 JSON 解析成 typed 计划；错误信息定位到字段。"""
    if not isinstance(raw, dict):
        raise AuthoringError(
            "INVALID_PLAN", "plan 必须是 JSON 对象", field="plan",
            correction_hint="传 schema_version=1 的 dorling_circle 计划。")
    body = dict(raw)
    mode = body.get("mode", "dorling_circle")
    if mode != "dorling_circle":
        raise AuthoringError(
            "UNSUPPORTED_MODE",
            f"本任务只实现 dorling_circle，收到 mode={mode!r}；"
            "Gastner-Newman 扩散/矩形/六边形 cartogram 仍未实现，不会被放行",
            field="mode",
            correction_hint="改 mode=dorling_circle，或改用普通比例圆（原位符号）。")
    try:
        return AuthoringPlan(**body)
    except ValidationError as exc:
        loc, msg = _validation_message(exc)
        code = "INVALID_PLAN"
        # 顺序有意义：**语义**错误（率/密度不是可加总量）优先于字段位置错误，
        # 否则 quantity.unit 的拒绝会被笼统归成 INVALID_QUANTITY_SPEC，
        # 用户就看不到"为什么不接受率"这条可执行解释。
        if "rate" in msg or "可加总量" in msg:
            code = "RATE_OR_MIXED_UNIT"
        elif loc.startswith("quantity."):
            code = "INVALID_QUANTITY_SPEC"
        elif loc.startswith("solver"):
            code = "INVALID_SOLVER_PARAMS"
        elif loc.startswith("quality_gate"):
            code = "INVALID_QUALITY_GATE"
        raise AuthoringError(
            code, f"计划字段 {loc or 'plan'} 非法：{msg}", field=loc or None,
            correction_hint="按合同修正该字段后重试；不要靠重试碰运气。") from exc


def clamp_plan_budget(
    plan: AuthoringPlan, *, features: int, adjacency_edges: int,
) -> None:
    """预检资源上界（运行前就拒绝，而不是跑到 OOM 才失败）。"""
    if features > plan.budgets.max_features:
        raise AuthoringError(
            "BUDGET_EXCEEDED",
            f"统计单元 {features} 超过计划上限 {plan.budgets.max_features}；"
            "超限明确拒绝，不静默截断样本后称完整",
            field="budgets.max_features",
            correction_hint="收窄范围/合并单元，或显式提高上限（仍受硬帽 "
                            f"{MAX_FEATURES_HARD} 约束）。")
    if adjacency_edges > plan.budgets.max_adjacency_edges:
        raise AuthoringError(
            "BUDGET_EXCEEDED",
            f"邻接边 {adjacency_edges} 超过计划上限 {plan.budgets.max_adjacency_edges}",
            field="budgets.max_adjacency_edges",
            correction_hint="提高上限或调整边界容差；不接受降采样后当作全部邻接。",
        )
