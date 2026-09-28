"""统一场景契约 ScenarioSpec v1（E15 D1）。

LabScenario 是**声明式数据**：用户目标、初始数据面、scripted provider 收据、
故障计划、期望（evidence/state/export）全部离线可表达。判错不在这里 ——
``compile_replay_scenario()`` / ``compile_benchmark_case()`` 把规格投影到
既有 runner 的输入（ADR-0104 D3：不建第二 runner）。

fail-closed 纪律：未知 kind / 未知 fixture alias / 未知故障类型 /
未知 settlement check 在 ``validate()`` 即报 :class:`SpecError` ——
场景作者笔误不得静默降级为"没跑"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LAB_SCHEMA_VERSION = 1

#: 场景执行模式。replay = 编译到 OfflineReplayer（T1–T4）；
#: settlement = 驱动生产结算/准入 seam 的终态检查；
#: benchmark = 编译到 GISBenchmarkCase（分层评测）。
SCENARIO_KINDS = ("replay", "settlement", "benchmark")

#: 声明式数据面：alias → ``app.evaluation.fixtures.FIXTURE_BUILDERS`` 键。
FIXTURE_ALIASES = (
    "chengdu_schools",
    "chengdu_schools_large",
    "admin_boundaries_chengdu",
    "od_edges",
    "od_edges_50k",
    "pm25_stations",
    "pm25_stations_sparse",
)

#: lab 侧执行面故障（与 ``replay.faults.FAULT_TYPES`` 的场景变换故障互补）。
#: cancel/duplicate_event/policy_deny/resource_reject/disconnect 驱动生产
#: 结算与准入 seam 的终态语义（E15 D3），每类绑定 fail-closed 断言。
LAB_FAULT_TYPES = (
    "cancel",            # 用户取消 → reduced settle（无 map_product）
    "duplicate_event",   # 同收据二次投递 → 幂等门
    "policy_deny",       # bind gate / SkillPolicy 拒绝 → refused 语义
    "resource_reject",   # governor 准入拒绝 → 分类不伪造成功
    "disconnect",        # 客户端断开 → cancelled 优先
)


class SpecError(ValueError):
    """场景规格非法（未知词表 / 结构缺失）。编译期即红，不静默降级。"""


@dataclass
class DataBinding:
    """声明式数据面绑定：spec 内引用名 → fixture builder + 参数。"""

    alias: str
    builder: str
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataBinding":
        return cls(
            alias=str(data.get("alias") or ""),
            builder=str(data.get("builder") or ""),
            params=dict(data.get("params") or {}),
        )


@dataclass
class ProviderOp:
    """scripted provider/tool 收据（与 replay.ScenarioOp 同构，可互转）。"""

    call_id: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    error_msg: str = ""
    error_code: str = ""
    duration_ms: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderOp":
        return cls(
            call_id=str(data.get("call_id") or ""),
            tool=str(data.get("tool") or ""),
            arguments=dict(data.get("arguments") or {}),
            result=dict(data.get("result") or {}),
            is_error=bool(data.get("is_error")),
            error_msg=str(data.get("error_msg") or ""),
            error_code=str(data.get("error_code") or ""),
            duration_ms=float(data.get("duration_ms") or 0.0),
        )


@dataclass
class TurnScript:
    """replay 模式的 turn 脚本（字段与 replay.TurnSpec 对齐，委托解析）。"""

    user_input: str = ""
    ops: List[ProviderOp] = field(default_factory=list)
    mutations: List[Dict[str, Any]] = field(default_factory=list)
    visual_report: Optional[Dict[str, Any]] = None
    cartography: Optional[Dict[str, Any]] = None
    refs: Dict[str, Any] = field(default_factory=dict)
    expect: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TurnScript":
        return cls(
            user_input=str(data.get("user_input") or ""),
            ops=[ProviderOp.from_dict(op) for op in data.get("ops") or []],
            mutations=list(data.get("mutations") or []),
            visual_report=data.get("visual_report"),
            cartography=data.get("cartography"),
            refs=dict(data.get("refs") or {}),
            expect=dict(data.get("expect") or {}),
        )


@dataclass
class FaultStep:
    """一条故障计划：type + 定位（target_turn / target_call）+ 参数。"""

    type: str
    target_turn: int = 0
    target_call: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FaultStep":
        return cls(
            type=str(data.get("type") or ""),
            target_turn=int(data.get("target_turn") or 0),
            target_call=str(data.get("target_call") or ""),
            params=dict(data.get("params") or {}),
        )

    def as_replay_fault(self) -> Dict[str, Any]:
        """投影为 replay.faults 的平面 dict（场景变换故障共用词表）。"""
        return {"type": self.type, "target_turn": self.target_turn}


@dataclass
class Expectation:
    """期望面（evidence / state / export）。空声明 = 该维 not_evaluated。

    ``goal_status`` 期望 replay goal 推导终态；
    ``user_wins=True`` 钉"用户中途操作必须赢"（hidden/recolor 不被
    finalize 覆写）——经 replay expect 树 + benchmark interaction
    semantics 双面落地。
    """

    evidence_min_count: int = 0
    goal_status: str = ""
    mapspec_components: List[str] = field(default_factory=list)
    export_formats: List[str] = field(default_factory=list)
    user_wins: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Expectation":
        return cls(
            evidence_min_count=int(data.get("evidence_min_count") or 0),
            goal_status=str(data.get("goal_status") or ""),
            mapspec_components=[str(c) for c in data.get("mapspec_components") or []],
            export_formats=[str(f) for f in data.get("export_formats") or []],
            user_wins=bool(data.get("user_wins")),
        )

    def declared_dimensions(self) -> List[str]:
        """显式声明的期望维（未声明的维 = not_evaluated，不毒化）。"""
        declared: List[str] = []
        if self.evidence_min_count > 0:
            declared.append("evidence")
        if self.goal_status:
            declared.append("goal")
        if self.mapspec_components:
            declared.append("map_state")
        if self.export_formats:
            declared.append("export")
        if self.user_wins:
            declared.append("user_wins")
        return declared


@dataclass
class LabScenario:
    """ScenarioSpec v1：离线可执行的完整场景声明。"""

    spec_id: str
    title: str
    kind: str
    goal: str = ""
    description: str = ""
    schema_version: int = LAB_SCHEMA_VERSION
    data: List[DataBinding] = field(default_factory=list)
    initial_mapspec: Optional[Dict[str, Any]] = None
    turns: List[TurnScript] = field(default_factory=list)
    provider_ops: List[ProviderOp] = field(default_factory=list)
    fault_plan: List[FaultStep] = field(default_factory=list)
    settlement_checks: List[str] = field(default_factory=list)
    benchmark_case: Optional[Dict[str, Any]] = None
    science_oracle_domains: List[str] = field(default_factory=list)
    cartography_checks: bool = False
    visual_judge: bool = False
    expectations: Expectation = field(default_factory=Expectation)
    tags: List[str] = field(default_factory=list)

    # ── 解析 ──────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LabScenario":
        return cls(
            spec_id=str(data.get("spec_id") or ""),
            title=str(data.get("title") or data.get("spec_id") or ""),
            kind=str(data.get("kind") or ""),
            goal=str(data.get("goal") or ""),
            description=str(data.get("description") or ""),
            schema_version=int(data.get("schema_version") or LAB_SCHEMA_VERSION),
            data=[DataBinding.from_dict(d) for d in data.get("data") or []],
            initial_mapspec=data.get("initial_mapspec"),
            turns=[TurnScript.from_dict(t) for t in data.get("turns") or []],
            provider_ops=[
                ProviderOp.from_dict(op) for op in data.get("provider_ops") or []
            ],
            fault_plan=[FaultStep.from_dict(f) for f in data.get("fault_plan") or []],
            settlement_checks=[str(c) for c in data.get("settlement_checks") or []],
            benchmark_case=data.get("benchmark_case"),
            science_oracle_domains=[
                str(d) for d in data.get("science_oracle_domains") or []
            ],
            cartography_checks=bool(data.get("cartography_checks")),
            visual_judge=bool(data.get("visual_judge")),
            expectations=Expectation.from_dict(data.get("expectations") or {}),
            tags=[str(t) for t in data.get("tags") or []],
        )

    def expectations_compiled(self) -> Expectation:
        return self.expectations

    # ── 校验（fail-closed，编译期红）───────────────────────────────────

    def validate(self) -> None:
        if not self.spec_id:
            raise SpecError("spec_id is required")
        if self.kind not in SCENARIO_KINDS:
            raise SpecError(
                f"{self.spec_id}: unknown kind {self.kind!r} "
                f"(choose from {SCENARIO_KINDS})")
        if self.schema_version != LAB_SCHEMA_VERSION:
            raise SpecError(
                f"{self.spec_id}: unsupported schema_version "
                f"{self.schema_version} (want {LAB_SCHEMA_VERSION})")
        for binding in self.data:
            if not binding.alias:
                raise SpecError(f"{self.spec_id}: data binding missing alias")
            if binding.builder not in FIXTURE_ALIASES:
                raise SpecError(
                    f"{self.spec_id}: unknown fixture builder "
                    f"{binding.builder!r} (choose from {FIXTURE_ALIASES})")
        known_faults = set(LAB_FAULT_TYPES) | set(self._replay_fault_types())
        for fault in self.fault_plan:
            if fault.type not in known_faults:
                raise SpecError(
                    f"{self.spec_id}: unknown fault type {fault.type!r}")
        # 执行面故障只有 settlement 模式有承接 seam —— 声明在其他 kind 上
        # 会被静默忽略，等价假绿，编译期拒绝。
        lab_only = [f.type for f in self.fault_plan
                    if f.type in LAB_FAULT_TYPES]
        if lab_only and self.kind != "settlement":
            raise SpecError(
                f"{self.spec_id}: lab fault types {lab_only} require "
                f"kind='settlement' (kind={self.kind!r} has no cancel seam)")
        if self.kind == "settlement":
            unknown = [c for c in self.settlement_checks
                       if c not in SETTLEMENT_CHECKS]
            if unknown:
                raise SpecError(
                    f"{self.spec_id}: unknown settlement checks {unknown} "
                    f"(choose from {sorted(SETTLEMENT_CHECKS)})")
            if not self.settlement_checks:
                raise SpecError(
                    f"{self.spec_id}: settlement spec declares no checks")
        if self.kind == "benchmark" and not isinstance(
                self.benchmark_case, dict):
            raise SpecError(
                f"{self.spec_id}: benchmark spec requires benchmark_case")
        if self.kind == "replay" and not self.turns:
            raise SpecError(f"{self.spec_id}: replay spec requires turns")
        # 导出期望只有真实执行链（benchmark execute tier）能证明 ——
        # replay 收据是冻结脚本，对它断言导出 = 绿 by construction。
        if self.kind != "benchmark" and self.expectations.export_formats:
            raise SpecError(
                f"{self.spec_id}: export_formats expectation requires "
                f"kind='benchmark' (kind={self.kind!r} replays canned "
                "receipts)")

    @staticmethod
    def _replay_fault_types() -> tuple:
        from app.lib.harness.replay.faults import FAULT_TYPES

        return FAULT_TYPES

    # ── 编译投影（委托既有 runner 输入，不复制评测逻辑）─────────────────

    def compile_replay_scenario(self):
        """投影为 ``replay.Scenario``（replay kind 的唯一执行入口）。"""
        from app.lib.harness.replay.replayer import Scenario, ScenarioOp, TurnSpec

        bindings = {b.alias: b for b in self.data}

        def _resolve_fixture(value: Any) -> Any:
            """``{"fixture": alias}`` 哨兵 → 声明式数据面 materialize。"""
            if isinstance(value, dict) and set(value) == {"fixture"}:
                alias = str(value["fixture"])
                binding = bindings.get(alias)
                if binding is None:
                    raise SpecError(
                        f"{self.spec_id}: fixture sentinel references "
                        f"undeclared data alias {alias!r}")
                from app.evaluation.fixtures import FIXTURE_BUILDERS

                return FIXTURE_BUILDERS[binding.builder](**binding.params)
            return value

        turns = [
            TurnSpec(
                user_input=t.user_input,
                ops=[
                    ScenarioOp(
                        call_id=op.call_id, tool=op.tool,
                        arguments=_resolve_fixture(dict(op.arguments)),
                        result=_resolve_fixture(dict(op.result)),
                        is_error=op.is_error, error_msg=op.error_msg,
                        duration_ms=op.duration_ms, error_code=op.error_code,
                    )
                    for op in t.ops
                ],
                mutations=[dict(m) for m in t.mutations],
                visual_report=t.visual_report,
                cartography=t.cartography,
                refs={k: _resolve_fixture(v) for k, v in t.refs.items()},
                expect=dict(t.expect),
            )
            for t in self.turns
        ]
        scenario = Scenario(
            scenario_id=self.spec_id,
            category=f"lab/{self.kind}",
            description=self.description or self.title,
            turns=turns,
            faults=[f.as_replay_fault() for f in self.fault_plan
                    if f.type not in LAB_FAULT_TYPES],
            tags=list(self.tags),
        )
        # visual_judge 声明下沉到各 turn cartography fixture（replay 的
        # env 缝按 turn 声明生效）。
        if self.visual_judge:
            for turn in scenario.turns:
                fixture = turn.cartography if isinstance(turn.cartography, dict) else None
                if fixture is not None:
                    fixture["visual_judge"] = True
        return scenario

    def compile_benchmark_case(self):
        """投影为 ``GISBenchmarkCase``（benchmark kind 的唯一执行入口）。"""
        from app.evaluation.case import GISBenchmarkCase

        if not isinstance(self.benchmark_case, dict):
            raise SpecError(f"{self.spec_id}: benchmark_case missing")
        return GISBenchmarkCase(**self.benchmark_case)

    def replay_faults(self) -> List[FaultStep]:
        """场景变换族故障（可由 ``replay.faults.apply_faults`` 编译）。"""
        return [f for f in self.fault_plan if f.type not in LAB_FAULT_TYPES]

    def lab_faults(self) -> List[FaultStep]:
        """执行面故障（需要 lab fake / settlement seam 承接）。"""
        return [f for f in self.fault_plan if f.type in LAB_FAULT_TYPES]


#: settlement 模式的检查词表（E15 D4；每项绑定生产 seam 与诚实终态断言）。
SETTLEMENT_CHECKS = (
    "cancel_reduced_settle",      # cancel → 无 map_product + 证据仍持久
    "settle_idempotent",          # 二次结算不重复投影（幂等门）
    "settle_no_fake_success",     # finalize 缺席 → 不得伪造完成态
    "duplicate_dispatch_dedup",   # 同收据二次投递 → dedup 可观测
    "policy_deny_refused",        # bind gate deny → 拒绝可观测、无假成功
    "resource_reject_classified", # governor 拒绝 → 分类 + 预算尊重
    "disconnect_cancelled",       # 断开 → cancelled 终态优先
)
