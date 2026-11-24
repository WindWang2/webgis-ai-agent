"""方法实验计划与确定性编译（method-experiment WP2）。

一个 **MethodExperimentPlan** 把「同一空间问题的多个合理科学方法/参数
组合」固定成可序列化、可指纹、可重放的试验清单：

- **确定性展开**：候选按卡声明序 + 参数名字典序笛卡尔展开（每轴 ≤4 值、
  每候选 ≤8 变体、全计划 ≤24 试验）——同输入同计划（字节级可重放）；
- **执行前拒绝**（typed preflight）：未知族/候选/指标、缺输入 ref、
  缺值字段、试验超预算——坏计划在编译期死，绝不带病执行；
- **组合爆炸防护**：预算上限是编译期硬门；调用方只能调低（服务端钳制，
  与 AWF max_features 同纪律）；
- **量纲诚实**：值字段的量纲随数据（value_unit）——跨数据集比较在
  evaluate 层裁决为不可比，计划层只固定「同一 ref + 同一 value_field」。

红线：计划不执行任何计算；候选执行真相 = 候选声明的真实工具
（``CandidateSpec.eval_tool``），经 ToolDispatchService 统一分发。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

import pydantic
from pydantic import BaseModel, Field, field_validator

from app.services.gis_harness.analysis_workflows.contracts import canonical_json
from app.services.gis_harness.method_experiment.cards import (
    EXPERIMENT_FAMILY_IDS,
    CandidateSpec,
    get_card,
)
from app.services.gis_harness.method_experiment.protocol import (
    EvaluationProtocol,
    build_protocol_from_card,
)

#: 计划 schema 版本（进指纹；additive 演进只升此号）。
PLAN_SCHEMA_VERSION = 1

#: 试验数上限（编译期硬门；与 AWF 步骤上限同量级的有界纪律）。
MAX_TRIALS_HARD_CAP = 24
DEFAULT_MAX_TRIALS = 12

#: 单候选参数变体上限（CandidateSpec.expand_grid 内部上限的一致重申）。
MAX_VARIANTS_PER_CANDIDATE = 8

#: 候选数上限（一次实验比较的有界纪律；6 个候选 × 网格已够科学争议）。
MAX_CANDIDATES = 6

# ── typed 拒绝码（MEXP_ 前缀；错误契约与 AWF_* 同族）────────────────────
MEXP_UNKNOWN_FAMILY = "MEXP_UNKNOWN_FAMILY"
MEXP_UNKNOWN_CANDIDATE = "MEXP_UNKNOWN_CANDIDATE"
MEXP_UNKNOWN_METRIC = "MEXP_UNKNOWN_METRIC"
MEXP_NO_INPUT_REF = "MEXP_NO_INPUT_REF"
MEXP_VALUE_FIELD_REQUIRED = "MEXP_VALUE_FIELD_REQUIRED"
MEXP_EXTRA_INPUT_REQUIRED = "MEXP_EXTRA_INPUT_REQUIRED"   # + ":<role>"
MEXP_BUDGET_EXCEEDED = "MEXP_BUDGET_EXCEEDED"
MEXP_INVALID_PROTOCOL = "MEXP_INVALID_PROTOCOL"
MEXP_INVALID_INPUT = "MEXP_INVALID_INPUT"
MEXP_EMPTY_TRIALS = "MEXP_EMPTY_TRIALS"
MEXP_EVAL_TOOL_UNREGISTERED = "MEXP_EVAL_TOOL_UNREGISTERED"


class PlanCompileError(ValueError):
    """计划编译失败（fail-closed；``[code]`` 前缀机器可切分）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{detail} [{code}]")


#: 额外输入角色的键数上限（network/facilities 已覆盖 v1 词表；有界纪律）。
MAX_EXTRA_INPUTS = 4


class ExperimentInput(BaseModel):
    """实验输入绑定（ref 为权威；qualifier 只作画像线索）。

    extra_inputs 有界（review C-P2）：键 ≤4、键名 ≤32、ref 值 ≤128——
    该 dict 进计划指纹与持久快照，无界会放大两者。
    """

    ref: str                                    # 主输入（点要素集 ref:xxx）
    value_field: str = ""                       # 值字段（cv 族必需）
    extra_inputs: Dict[str, str] = Field(default_factory=dict)  # role → ref
    crs_hint: str = ""                          # 声明 CRS（未声明=工具级 preflight 裁决）
    unit_hint: str = ""                         # 值单位声明（评价披露用，不换算）

    @field_validator("ref", "value_field")
    @classmethod
    def _bounded_strings(cls, v: str) -> str:
        return v[:128]

    @field_validator("extra_inputs")
    @classmethod
    def _bounded_extra(cls, v: Dict[str, str]) -> Dict[str, str]:
        if len(v) > MAX_EXTRA_INPUTS:
            raise ValueError(
                f"extra_inputs 键数超上限 {MAX_EXTRA_INPUTS}: {len(v)}")
        return {str(k)[:32]: str(val)[:128] for k, val in v.items()}


class CandidateSelection(BaseModel):
    """一个候选的编译产物（展开后）。"""

    candidate_id: str
    method_id: str
    eval_tool: str
    label_zh: str = ""
    variants: Tuple[Dict[str, Any], ...] = ()   # 展开的参数组（声明序）
    sample_floor: int = 0
    metric_ids: Tuple[str, ...] = ()
    limitations: Tuple[str, ...] = ()


class ExperimentBudget(BaseModel):
    """实验资源预算（编译期硬门 + 预览披露）。"""

    max_trials: int = DEFAULT_MAX_TRIALS        # [1, MAX_TRIALS_HARD_CAP]
    max_features: int = 100_000                 # 单次工具输入行数预算
    wall_hint_seconds_per_trial: int = 60       # 预览披露（非硬超时）

    @field_validator("max_trials")
    @classmethod
    def _trials_cap(cls, v: int) -> int:
        return max(1, min(int(v), MAX_TRIALS_HARD_CAP))

    @field_validator("max_features")
    @classmethod
    def _features_cap(cls, v: int) -> int:
        return max(100, min(int(v), 100_000))


class Trial(BaseModel):
    """一个试验 = 候选 × 参数组 × seed（执行与指纹的最小单元）。"""

    trial_id: str                               # "t<零补序号>"
    candidate_id: str
    variant_index: int                          # 候选内参数组序（声明序）
    params: Dict[str, Any] = Field(default_factory=dict)
    seed: int = 0                               # 0 = 确定性（无 RNG 语义）
    eval_tool: str = ""                         # 执行真相（冗余自 CandidateSelection）


class MethodExperimentPlan(BaseModel):
    """可版本化实验计划（确定性编译产物；fingerprint = 内容 hash）。"""

    schema_version: int = PLAN_SCHEMA_VERSION
    experiment_id: str                          # "mexp-<family>-<fingerprint 前 8>"
    title: str = ""
    family_id: str                              # ⊆ EXPERIMENT_FAMILY_IDS
    input: ExperimentInput
    protocol: EvaluationProtocol
    budget: ExperimentBudget = Field(default_factory=ExperimentBudget)
    candidates: Tuple[CandidateSelection, ...] = ()
    trials: Tuple[Trial, ...] = ()
    preflight_findings: Tuple[str, ...] = ()    # 非阻断披露（阻断项已 raise）
    created_from_question: str = ""             # 溯源（用户问题原文，有界）

    @field_validator("family_id")
    @classmethod
    def _family_vocab(cls, v: str) -> str:
        if v not in EXPERIMENT_FAMILY_IDS:
            raise ValueError(f"family 越界: {v}")
        return v

    def fingerprint(self) -> str:
        """计划内容指纹（试验清单+协议+输入绑定；不含标题等展示字段）。"""
        payload = {
            "schema": self.schema_version,
            "family": self.family_id,
            "input": self.input.model_dump(),
            "protocol": self.protocol.to_dict(),
            "budget": self.budget.model_dump(),
            "candidates": [c.model_dump() for c in self.candidates],
            "trials": [t.model_dump() for t in self.trials],
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def trial_count(self) -> int:
        return len(self.trials)

    def to_bounded_summary(self) -> Dict[str, Any]:
        """有界摘要（LLM/SSE/UI 消费；试验只投影计数与清单，不带大载荷）。"""
        return {
            "experiment_id": self.experiment_id,
            "title": self.title[:80],
            "family_id": self.family_id,
            "protocol": self.protocol.to_dict(),
            "budget": self.budget.model_dump(),
            "candidates": [
                {"candidate_id": c.candidate_id, "label_zh": c.label_zh[:40],
                 "method_id": c.method_id, "eval_tool": c.eval_tool,
                 "variants": len(c.variants),
                 "param_axes": {
                     k: list(v) for k, v in _grid_preview(c).items()}}
                for c in self.candidates],
            "trials": [
                {"trial_id": t.trial_id, "candidate_id": t.candidate_id,
                 "variant_index": t.variant_index, "params": dict(t.params)}
                for t in self.trials],
            "preflight_findings": list(self.preflight_findings)[:8],
            "fingerprint": self.fingerprint()[:16],
        }


def _grid_preview(c: CandidateSelection) -> Dict[str, List[Any]]:
    """每个候选各参数轴的取值预览（≤4 值；UI 展示「代价」用）。"""
    axes: Dict[str, List[Any]] = {}
    for variant in c.variants:
        for k, v in variant.items():
            axes.setdefault(k, [])
            if v not in axes[k]:
                axes[k].append(v)
    return {k: vals[:4] for k, vals in axes.items()}


def compile_experiment_plan(
    *,
    family_id: str,
    input_ref: str,
    value_field: str = "",
    extra_inputs: Optional[Dict[str, str]] = None,
    crs_hint: str = "",
    unit_hint: str = "",
    candidate_ids: Optional[List[str]] = None,
    scheme: str = "",
    folds: int = 5,
    stability_runs: int = 5,
    metric_ids: Optional[List[str]] = None,
    max_trials: int = DEFAULT_MAX_TRIALS,
    max_features: int = 100_000,
    title: str = "",
    question: str = "",
) -> MethodExperimentPlan:
    """确定性编译：方法卡 × 输入绑定 × 候选选择 → 试验清单。

    阻断项 raise PlanCompileError（typed 码）；非阻断事实进
    preflight_findings（执行期权威仍在工具级校验）。
    """
    card = get_card(family_id)
    if card is None:
        raise PlanCompileError(
            MEXP_UNKNOWN_FAMILY,
            f"未知方法族 {family_id}（词表: {EXPERIMENT_FAMILY_IDS}）")
    if not input_ref:
        raise PlanCompileError(
            MEXP_NO_INPUT_REF, "缺主输入 ref——实验必须绑定数据引用")
    if card.requires_value_field and not value_field:
        raise PlanCompileError(
            MEXP_VALUE_FIELD_REQUIRED,
            f"族 {family_id} 的评价需要值字段（如待插值/待检验的数值列）")

    extra = dict(extra_inputs or {})
    for role in card.extra_inputs:
        if not extra.get(role):
            raise PlanCompileError(
                f"{MEXP_EXTRA_INPUT_REQUIRED}:{role}",
                f"族 {family_id} 需要额外输入角色 {role}（如路网/设施 ref）")

    # ── 评价工具对账（review C-P2：注册面可用时悬空 fatal——「悬空 fatal」
    # 承诺的兑现点；注册面不可用（纯编译期）则交给运行期 dispatch 诚实失败）──
    try:
        from app.agent_pi_bridge import try_get_tool_registry
        live_registry = try_get_tool_registry()
    except Exception:  # noqa: BLE001 — 桥不可用不阻断纯编译
        live_registry = None
    if live_registry is not None:
        dangling = [c.candidate_id for c in card.candidates
                    if live_registry.get(c.eval_tool) is None]
        if dangling:
            raise PlanCompileError(
                MEXP_EVAL_TOOL_UNREGISTERED,
                f"候选评价工具在注册面悬空: {dangling[:4]}")

    # ── 候选选择（缺省 = 卡全候选；显式子集须 ⊆ 卡；声明序不变）────────
    chosen: Tuple[CandidateSpec, ...]
    if candidate_ids:
        known = {c.candidate_id for c in card.candidates}
        unknown = [cid for cid in candidate_ids if cid not in known]
        if unknown:
            raise PlanCompileError(
                MEXP_UNKNOWN_CANDIDATE,
                f"未知候选 {unknown}（族 {family_id} 可选: {sorted(known)}）")
        wanted = set(candidate_ids)
        chosen = tuple(c for c in card.candidates if c.candidate_id in wanted)
    else:
        chosen = card.candidates
    if len(chosen) > MAX_CANDIDATES:
        raise PlanCompileError(
            MEXP_BUDGET_EXCEEDED,
            f"候选数 {len(chosen)} 超上限 {MAX_CANDIDATES}——分多次实验")

    # ── 协议（fail-closed 构建）────────────────────────────────────────
    try:
        protocol = build_protocol_from_card(
            family_id, scheme=scheme, folds=folds,
            stability_runs=stability_runs,
            metric_ids=tuple(metric_ids) if metric_ids else ())
    except ValueError as exc:
        raise PlanCompileError(MEXP_INVALID_PROTOCOL, str(exc)) from exc

    # ── 确定性展开（候选声明序 × 参数名字典序轴）────────────────────────
    budget = ExperimentBudget(max_trials=max_trials, max_features=max_features)
    selections: List[CandidateSelection] = []
    trials: List[Trial] = []
    truncated_findings: List[str] = []

    for cand in chosen:
        variants = cand.expand_grid(max_variants=MAX_VARIANTS_PER_CANDIDATE)
        remaining = budget.max_trials - len(trials)
        if len(variants) > remaining:
            variants = variants[:max(0, remaining)]
            truncated_findings.append(
                f"trial_budget_truncated:{cand.candidate_id}"
                f"（预算 {budget.max_trials} 试验上限——提高 max_trials 或减少候选）")
        if not variants:
            continue
        selections.append(CandidateSelection(
            candidate_id=cand.candidate_id,
            method_id=cand.method_id,
            eval_tool=cand.eval_tool,
            label_zh=cand.label_zh[:40],
            variants=tuple(variants),
            sample_floor=cand.sample_floor,
            metric_ids=tuple(cand.metric_ids),
            limitations=tuple(lim[:120] for lim in cand.limitations),
        ))
        for vi, params in enumerate(variants):
            trials.append(Trial(
                trial_id=f"t{len(trials) + 1:02d}",
                candidate_id=cand.candidate_id,
                variant_index=vi,
                params=dict(params),
                seed=42 if protocol.kind == "stability" else 0,
                eval_tool=cand.eval_tool,
            ))

    if not trials:
        raise PlanCompileError(MEXP_EMPTY_TRIALS, "展开后无任何试验（候选/网格为空）")

    try:
        exp_input = ExperimentInput(
            ref=input_ref, value_field=value_field, extra_inputs=extra,
            crs_hint=crs_hint[:24], unit_hint=unit_hint[:24])
    except pydantic.ValidationError as exc:
        # 有界校验失败 → typed 拒绝（review C-R2：裸 ValidationError 逃逸
        # 到 REST 层会变成 500——必须在这里折成 PlanCompileError）。
        raise PlanCompileError(
            MEXP_INVALID_INPUT,
            f"输入绑定校验失败: {exc.errors()[0].get('msg', str(exc))[:160]}"
        ) from exc
    tmp = MethodExperimentPlan(
        experiment_id="pending", title=title[:80],
        family_id=family_id, input=exp_input, protocol=protocol,
        budget=budget, candidates=tuple(selections), trials=tuple(trials),
        preflight_findings=tuple(truncated_findings),
        created_from_question=question[:200])
    fp = tmp.fingerprint()
    experiment_id = f"mexp-{family_id[:12]}-{fp[:8]}"
    return tmp.model_copy(update={"experiment_id": experiment_id})
