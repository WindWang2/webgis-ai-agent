"""GISActionPlan IR —— 统一 GIS 执行语义中间表示（H10 / ADR-0217）。

产品意图（MapRequestIntent / Recipe / MapProductPlan）回答"做什么产品"；
MapPlanIR 回答"地图长什么样"；本 IR 回答"**执行什么**"：把 Agent 的全部
GIS 行为（取数 / 检查 / 派生 / 分析 / 制图表达 / 呈现变更 / 导出 / 观察）
收敛为可编译（→ tool DAG）、可 diff（参数级）、可回滚（compensation）的
执行计划，让 LLM 不再手工拼不透明工具序列。

纪律（与 plan_ir / decision_record 同口径）：

- **refs-only**：数据本体永不入 IR —— inputs/outputs 只携带
  (name, ref, semantic_type, fingerprint)；params 只允许**小而 typed** 的
  调参 token（键数与字节双闸，构造期 fail-closed），大 payload 一律走 ref。
- **确定性**：``plan_fingerprint`` = canonical JSON sha256（复用
  decision_record.canonical_decision_json 口径）；同输入同 id。
- **可序列化**：纯 Pydantic v2 frozen 模型，model_dump 后可直接进
  trace/replay/golden。
- **零运行态**：本模块不知道 session/store/registry 的存在 —— 编译产物
  不持久化 runtime state（权威仍在 MapSpec/SessionPlan/ToolRegistry）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field

#: IR schema 版本（语义变更 = bump + 迁移说明；消费方按版本分派）。
ACTION_IR_VERSION = "1.0.0"

#: 内容寻址 id 前缀（plan 级；action 级由 plan_id + 序号派生）。
PLAN_ID_PREFIX = "gap-"

# ── 有界词表（封闭；新增 = additive）────────────────────────────────────

#: 顶层执行语义通道（与产品意图词表正交 —— 一个 map intent 会投影为
#: 多个 kind 的 action；一个 action 不承载产品语义）。
ActionKind = Literal[
    "data_acquire",         # 取数：ingest/query/materialize/catalog 发现
    "inspect",              # 只读检查：profile/describe/status/validate
    "transform",            # 确定性派生：classification 重算/图表/轻量换算
    "analyze",              # 空间/统计分析 capability 运行
    "cartograph",           # 制图表达构建：legend/symbology/语法制图
    "mutate_presentation",  # MapSpec 呈现变更（层/组件/视图/布局）
    "export",               # 导出：报告/图片/数据交付
    "observe",              # 观察：render 采集/视觉批评/验证
]

#: 副作用类别（编译器据此选 failure strategy 缺省与 compensation 资格）。
SideEffectClass = Literal[
    "pure",           # 无可观察状态变化
    "derived",        # 只产会话内派生 artifact/ref（GC 可回收）
    "session_state",  # 变更会话状态（MapSpec/视图/布局 —— 可补偿）
    "external_io",    # 会话外副作用（文件/外部服务 —— 不可自动补偿）
]

#: 幂等语义（executor 去重与重放决策依据）。
Idempotency = Literal[
    "idempotent",      # 同参重放安全（工具声明 idempotent）
    "duplicate_safe",  # 引擎 CAS + client id 去重（mutation 通道）
    "non_idempotent",  # 重放有额外副作用（export/external_io 等）
]

#: 单 action 失败策略（plan 可整体声明缺省，逐 action 覆写）。
FailureStrategy = Literal["fail_closed", "best_effort", "compensate"]

#: 计划来源（审计与投影方向标注；不改变执行语义）。
PlanOrigin = Literal[
    "product_plan",   # MapProductPlan / MapPlanIR 投影
    "tool_call",      # legacy 工具调用投影（adapter）
    "user_direct",    # 用户直改（workbench）
    "delegation",     # subagent 委派
]

#: 前提条件类别（executor 逐步校验；编译器只做静态一致性）。
PreconditionKind = Literal[
    "data_ref_alive",     # ref 在会话内可解析
    "capability_eligible",  # capability 资格在场（权威仍在 capability bind）
    "layer_present",      # MapSpec 存在目标层
    "layer_absent",       # MapSpec 不存在目标层
    "revision_match",     # MapSpec revision CAS
]

#: 补偿动作类别（reverse 语义；synthesis 在服务层，IR 只声明）。
CompensationKind = Literal[
    "none",                # 无需/不可补偿（pure/external_io）
    "remove_layer",        # upsert 层的逆
    "restore_layer_style", # style patch 的逆（携带 prior 指纹）
    "readd_component",     # remove component 的逆
    "remove_component",    # ensure component 的逆
]

# ── 有界上限（构造期强制；防 payload 走私）───────────────────────────────

MAX_ACTIONS = 64
MAX_INPUTS_PER_ACTION = 8
MAX_OUTPUTS_PER_ACTION = 8
MAX_PRECONDITIONS = 8
MAX_DEPENDS_ON = 8
MAX_REASON_CODES = 12
MAX_EVIDENCE = 8
_STR_MAX = 256
_ID_MAX = 128
#: params 调参 token 预算（小而 typed；超限 = 数据走私，构造期拒绝）。
_PARAMS_KEYS_MAX = 16
_PARAMS_BYTES_MAX = 2048
_COMPENSATION_ARGS_KEYS_MAX = 8


def _canon(value: Any) -> str:
    """canonical JSON（与 decision_record 同口径：排序键 + 浮点 6 位）。

    set 先排序为 list（set 迭代序依赖 PYTHONHASHSEED，跨进程不确定）。
    """
    try:
        from app.lib.runtime.decision_record import canonical_decision_json
        return canonical_decision_json(value)
    except Exception:  # noqa: BLE001 — 循环 import 防线（本地等价实现）
        import json

        def _round(v: Any) -> Any:
            if isinstance(v, float):
                return round(v, 6)
            if isinstance(v, dict):
                return {k: _round(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [_round(x) for x in v]
            if isinstance(v, (set, frozenset)):
                return sorted(_round(x) for x in v)
            return v

        return json.dumps(_round(value), sort_keys=True,
                          ensure_ascii=False, default=str)


def digest_of(payload: Any) -> str:
    return hashlib.sha256(_canon(payload).encode("utf-8")).hexdigest()


# ── 引用面（refs-only；大 payload 的唯一合法载体是指纹）──────────────────


class _Bounded(BaseModel):
    """共用模型约束（frozen = 构造后不可变，保证 IR 可作 dict 键/可 replay）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class IODescriptor(_Bounded):
    """一个输入/输出槽位的引用描述（数据本体永不入 IR）。"""

    name: str = Field(min_length=1, max_length=64)
    ref: str = Field(default="", max_length=_ID_MAX)   # ref:xxx / artifact id
    semantic_type: str = Field(default="", max_length=64)
    fingerprint: str = Field(default="", max_length=80)


class Precondition(_Bounded):
    """一条可机检前提（executor 执行前逐步校验；fail-closed）。"""

    kind: PreconditionKind
    target: str = Field(default="", max_length=_ID_MAX)
    expected: str = Field(default="", max_length=_STR_MAX)


class Compensation(_Bounded):
    """补偿动作声明（逆语义；执行参数由 executor 依现场合成）。"""

    kind: CompensationKind = "none"
    target: str = Field(default="", max_length=_ID_MAX)
    args: Dict[str, Any] = Field(default_factory=dict)
    #: 被补偿 action 的 prior 状态指纹（style 恢复判 stale 用）。
    prior_fingerprint: str = Field(default="", max_length=80)

    def model_post_init(self, __context: Any) -> None:
        if len(self.args) > _COMPENSATION_ARGS_KEYS_MAX:
            raise ValueError(
                f"Compensation.args 键数 {len(self.args)} 超上限 "
                f"{_COMPENSATION_ARGS_KEYS_MAX}")


# ── 动作面 ───────────────────────────────────────────────────────────────


class GISAction(_Bounded):
    """单条执行动作：typed 输入/输出/前提/副作用/资源/幂等/补偿。

    ``tool`` 为空 = 尚未解析（capability 寻址）；``capability`` 为空 =
    直接工具寻址（legacy 投影形态）。两者全空在编译期拒绝 —— 动作必须
    可寻址到执行面。
    """

    action_id: str = Field(min_length=4, max_length=_ID_MAX)
    kind: ActionKind
    capability: str = Field(default="", max_length=96)
    tool: str = Field(default="", max_length=96)
    title: str = Field(default="", max_length=_STR_MAX)

    inputs: List[IODescriptor] = Field(default_factory=list, max_length=MAX_INPUTS_PER_ACTION)
    outputs: List[IODescriptor] = Field(default_factory=list, max_length=MAX_OUTPUTS_PER_ACTION)
    preconditions: List[Precondition] = Field(default_factory=list, max_length=MAX_PRECONDITIONS)

    params: Dict[str, Any] = Field(default_factory=dict)

    side_effect: SideEffectClass = "pure"
    idempotency: Idempotency = "idempotent"
    failure: FailureStrategy = "fail_closed"
    #: 资源档位（latency/memory/scale/cpu/io；与 ExecutionCatalog 投影同词表）。
    resource_class: Dict[str, str] = Field(default_factory=dict)

    depends_on: List[str] = Field(default_factory=list, max_length=MAX_DEPENDS_ON)
    compensation: Compensation = Field(default_factory=Compensation)

    reason_codes: List[str] = Field(default_factory=list, max_length=MAX_REASON_CODES)
    evidence_refs: List[str] = Field(default_factory=list, max_length=MAX_EVIDENCE)

    def model_post_init(self, __context: Any) -> None:
        if len(self.params) > _PARAMS_KEYS_MAX:
            raise ValueError(
                f"GISAction.params 键数 {len(self.params)} 超上限 "
                f"{_PARAMS_KEYS_MAX}（IR refs-only：数据不入 IR）")
        if self.params_bytes() > _PARAMS_BYTES_MAX:
            raise ValueError(
                f"GISAction.params 字节 {self.params_bytes()} 超预算 "
                f"{_PARAMS_BYTES_MAX}（IR refs-only：数据不入 IR）")
        if len(self.resource_class) > 8:
            raise ValueError("GISAction.resource_class 维度超上限 8")
        if not self.capability and not self.tool:
            raise ValueError(
                f"GISAction[{self.action_id}] capability 与 tool 全空："
                "动作必须可寻址到执行面")

    def params_bytes(self) -> int:
        return len(_canon(self.params).encode("utf-8"))

    def action_fingerprint(self) -> str:
        """action 级指纹（diff 判等的最小单元）。"""
        return digest_of(self.model_dump())[:40]


# ── 根文档 ───────────────────────────────────────────────────────────────


class GISActionPlan(_Bounded):
    """GISActionPlan 根文档（versioned / content-addressed / refs-only）。

    失败策略**只在 action 级**（``GISAction.failure``）——计划级缺省旋钮
    在唯一投影源恒为 fail_closed、无真实消费者，按 YAGNI 不设第二真相。
    """

    plan_version: str = ACTION_IR_VERSION
    plan_id: str = Field(min_length=1, max_length=_ID_MAX)
    revision: int = Field(default=1, ge=1)
    supersedes: str = Field(default="", max_length=_ID_MAX)
    origin: PlanOrigin = "tool_call"

    actions: List[GISAction] = Field(default_factory=list, max_length=MAX_ACTIONS)

    reason_codes: List[str] = Field(default_factory=list, max_length=MAX_REASON_CODES)
    #: 上游产品/制图计划指纹转录（MapPlanIR / MapProductPlan）。
    upstream_fingerprint: str = Field(default="", max_length=80)

    def plan_fingerprint(self) -> str:
        return "gap-sha256:" + digest_of(self.model_dump())[:40]


def compute_plan_id(payload: Any) -> str:
    """内容寻址 plan_id（不含 plan_id 本身的 canonical sha256 前缀）。"""
    body = {k: v for k, v in dict(payload).items() if k != "plan_id"} \
        if isinstance(payload, dict) else payload
    return PLAN_ID_PREFIX + digest_of(body)[:12]


def describe_plan(plan: GISActionPlan, *, max_lines: int = 24) -> str:
    """有界单行/多行文本投影（LLM 视野 / 日志；不是执行契约）。

    行数预算硬保证 ≤ ``max_lines``（溢出时保留一行省略提示）。
    """
    lines = [
        f"[GISActionPlan {plan.plan_id}] rev={plan.revision} "
        f"origin={plan.origin} actions={len(plan.actions)} "
        f"fp={plan.plan_fingerprint()[:24]}"
    ]
    overflow = len(plan.actions) > max_lines - 2
    shown = max_lines - 2 if overflow else max_lines - 1
    for i, action in enumerate(plan.actions[:max(shown, 0)]):
        target = action.tool or action.capability or "?"
        deps = f" deps={len(action.depends_on)}" if action.depends_on else ""
        lines.append(
            f"  {i + 1:02d}. {action.kind}:{target} "
            f"se={action.side_effect} fail={action.failure}{deps}")
    if overflow:
        lines.append(f"  …(+{len(plan.actions) - shown} actions)")
    return "\n".join(lines)


__all__ = [
    "ACTION_IR_VERSION",
    "PLAN_ID_PREFIX",
    "ActionKind", "SideEffectClass", "Idempotency", "FailureStrategy",
    "PlanOrigin", "PreconditionKind", "CompensationKind",
    "IODescriptor", "Precondition", "Compensation",
    "GISAction", "GISActionPlan",
    "compute_plan_id", "digest_of", "describe_plan",
]
