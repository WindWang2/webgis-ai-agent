"""GIS Execution Compiler —— RequirementIR → 执行面的确定性编译层。

G02-GIS-EXECUTION-COMPILER。本包是既有组件（requirement_ir /
analysis_workflows / families / goal_satisfaction / execution_catalog）
之上的**纯函数投影层**，补齐四条审计断链（BASELINE.md §2）：

- C1  RequirementIR → 族候选/表达候选/单位义务（requirement_to_tasks）
- C2  analysis workflow 运行回执 → goal_satisfaction 验收桥（acceptance_bridge）
- C3  族声明 ↔ 注册表一致性 fail-closed 对账（catalog_conformance）
- C4  IR patch → 受影响数据角色/步骤失效集（invalidation）

红线：

- 零 LLM / 零 IO：同输入同输出（逐字节），available_tools / data_facts
  由调用方显式注入；
- 不新增第二 planner / 第二注册中心 / 第二验收器：编译产物**喂给**既有
  ``plan_workflow`` / goal_satisfaction，裁决权威不变；
- 全部模型 bounded + 可序列化 + 带指纹（漂移检测面）。
"""
from app.services.gis_harness.execution_compiler.acceptance_bridge import (
    evaluation_view_with_workflow_rows,
    load_workflow_runs,
    merge_workflow_rows,
    project_workflow_rows,
    workflow_disclosures,
)
from app.services.gis_harness.execution_compiler.catalog_conformance import (
    FamilyConformanceReport,
    audit_family_conformance,
)
from app.services.gis_harness.execution_compiler.contracts import (
    CompileVerdict,
    ExpressionCandidate,
    FamilyCandidate,
    InvalidationScope,
    SignalEvidence,
    TaskCompilation,
    UnitObligation,
)
from app.services.gis_harness.execution_compiler.invalidation import (
    patch_invalidation_scope,
)
from app.services.gis_harness.execution_compiler.requirement_to_tasks import (
    compile_requirement_to_tasks,
)
from app.services.gis_harness.execution_compiler.units import UNIT_DIMENSIONS

__all__ = [
    "CompileVerdict",
    "ExpressionCandidate",
    "FamilyCandidate",
    "FamilyConformanceReport",
    "InvalidationScope",
    "SignalEvidence",
    "TaskCompilation",
    "UnitObligation",
    "UNIT_DIMENSIONS",
    "audit_family_conformance",
    "compile_requirement_to_tasks",
    "evaluation_view_with_workflow_rows",
    "load_workflow_runs",
    "merge_workflow_rows",
    "patch_invalidation_scope",
    "project_workflow_rows",
    "workflow_disclosures",
]
