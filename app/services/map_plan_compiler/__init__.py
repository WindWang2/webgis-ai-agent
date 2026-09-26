"""Map Plan Compiler —— MapPlanIR → 有序最小 MapSpec mutations（F12 / ADR-0214）。

包结构（单一真相纪律见 ADR-0214 D8）：

- ``projector``  : 权威输出（MapProductPlan / GrammarDecision / lock 快照）→ MapPlanIR
                   投影 + typed amendment 多轮演进；不推断语义。
- ``obligations``: 编译前六闸（required components / renderer / data refs /
                   scale-CRS / user lock 冲突 / export 支持），fail-closed。
- ``compiler``   : 纯函数 diff 编译器：IR + 当前 MapSpec → 有序最小 mutation
                   序列（确定性、可 replay）。
- ``receipt``    : compile receipt（内容寻址、可 stale、bounded ring 回链）。
- ``apply``      : 经 MapSpecLifecycleEngine.apply_mutation 逐步 CAS 提交。
- ``finalization``: 期望显示终态 vs 实际 MapSpec/ACK 的确定性对账。
- ``service``    : MapPlanCompilerService 门面。
"""
from app.lib.cartography.plan_ir import (
    PLAN_IR_VERSION,
    MapPlanIR,
    compute_ir_id,
)
from app.services.map_plan_compiler.projector import (
    PlanAmendment,
    amend_plan_ir,
    project_plan_ir,
)
from app.services.map_plan_compiler.obligations import (
    ObligationFinding,
    ObligationReport,
    check_obligations,
)
from app.services.map_plan_compiler.compiler import (
    PlanCompilation,
    PlanMutation,
    compile_plan,
)
from app.services.map_plan_compiler.receipt import (
    CompileReceipt,
    receipt_is_stale,
)
from app.services.map_plan_compiler.finalization import (
    FinalizationCheck,
    check_final_display,
)

__all__ = [
    "PLAN_IR_VERSION",
    "MapPlanIR",
    "compute_ir_id",
    "PlanAmendment",
    "amend_plan_ir",
    "project_plan_ir",
    "ObligationFinding",
    "ObligationReport",
    "check_obligations",
    "PlanCompilation",
    "PlanMutation",
    "compile_plan",
    "CompileReceipt",
    "receipt_is_stale",
    "FinalizationCheck",
    "check_final_display",
]
