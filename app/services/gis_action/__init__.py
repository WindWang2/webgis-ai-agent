"""GISAction —— 统一 GIS Action IR 的服务层（H10 / ADR-0217）。

包结构权威说明：

- ``app/lib/gis/action_ir.py``：纯 IR 层（versioned / content-addressed /
  refs-only / bounded；禁 import app.services）——ActionKind/SideEffect/
  Idempotency/FailureStrategy 词表与 GISAction/GISActionPlan 根文档；
- ``compiler``：IR → 确定性有序 tool DAG（contract validation + 拓扑 +
  admission hint；纯函数）；
- ``diff``：计划语义 diff（新增/删除/参数/副作用/工具/顺序）；
- ``legacy_adapter``：legacy 工具调用 → Action 投影 + 直接/计划路由
  收敛指标；
- ``derive``：classification 参数级派生重算（投影期物化；compiler 保持
  纯函数）；
- ``executor``：dry-run / 前提校验 / 失败策略 / 补偿回滚 / step receipts；
- ``service``：门面 + dispatch 投影闸（GIS_ACTION_IR_BIND，fail-open）。

边界（禁止破坏）：

- 产品意图（MapRequestIntent/Recipe/MapProductPlan）与 MapPlanIR 不在本
  包的语义内 —— 它们是**上游投影源**；本包是执行语义唯一层；
- 不重造 ToolRegistry；governor 是资源准入唯一权威（本包只产 hint）；
- compiler 不做 IO、不持久化 runtime state。
"""
from app.lib.gis.action_ir import (
    ACTION_IR_VERSION,
    ActionKind,
    Compensation,
    FailureStrategy,
    GISAction,
    GISActionPlan,
    Idempotency,
    IODescriptor,
    PlanOrigin,
    Precondition,
    PreconditionKind,
    SideEffectClass,
    compute_plan_id,
    describe_plan,
    digest_of,
)
from app.services.gis_action.compiler import (
    ActionFinding,
    ActionPlanCompilation,
    CatalogToolResolver,
    ResolvedAction,
    ResourceSummary,
    ToolResolution,
    ToolResolver,
    compile_actions,
)
from app.services.gis_action.diff import (
    ActionPlanDiff,
    ParamChange,
    diff_plans,
    plans_equal,
)
from app.services.gis_action.executor import (
    ActionPlanExecutor,
    PlanRunReceipt,
    StepReceipt,
)
from app.services.gis_action.legacy_adapter import (
    project_tool_call_to_action,
    project_tool_call_to_plan,
    record_usage,
    reset_usage_counters,
    usage_snapshot,
)
from app.services.gis_action.service import (
    ActionIRBind,
    GISActionService,
    RegistryToolResolver,
    action_ir_bind_enabled,
    action_ir_strict,
    bind_action_ir,
    gis_action_service,
)

__all__ = [
    # IR 层
    "ACTION_IR_VERSION",
    "ActionKind", "SideEffectClass", "Idempotency", "FailureStrategy",
    "PlanOrigin", "PreconditionKind",
    "IODescriptor", "Precondition", "Compensation",
    "GISAction", "GISActionPlan",
    "compute_plan_id", "digest_of", "describe_plan",
    # compiler
    "ToolResolver", "ToolResolution", "CatalogToolResolver",
    "ResolvedAction", "ActionFinding", "ResourceSummary",
    "ActionPlanCompilation", "compile_actions",
    # diff
    "ActionPlanDiff", "ParamChange", "diff_plans", "plans_equal",
    # executor
    "ActionPlanExecutor", "PlanRunReceipt", "StepReceipt",
    # adapter
    "project_tool_call_to_action", "project_tool_call_to_plan",
    "record_usage", "usage_snapshot", "reset_usage_counters",
    # service
    "GISActionService", "gis_action_service", "RegistryToolResolver",
    "ActionIRBind", "bind_action_ir",
    "action_ir_bind_enabled", "action_ir_strict",
]
