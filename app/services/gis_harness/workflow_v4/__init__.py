"""Semantic GIS Workflow Compiler V4 —— 编译期语义层（Epic workflow-v4）。

本子包决定「应该做什么、为什么、有哪些候选方法、需要满足哪些科学/数据/
输出义务」；「如何在运行时可靠执行、观察、恢复、验证」仍归 GIS Harness
runtime（workflow_instance / SessionPlan / planner_runtime），不是本包
职责。全部模块确定性：同输入同输出，零 LLM、零 I/O。

红线：

- 不建第二事实源：方法族/方法候选只引用既有 task ontology、
  CapabilityRegistry、AlgorithmRegistry、ArtifactTypeRegistry、
  workflow_schema 词表；科学资格裁决复用数据资格五态与算法层
  scientific preconditions，不重复实现；
- 15 阶段既有编译器契约（COMPILER_STAGES）零改动 —— V4 以 additive
  包装（compiler_v4.compile_workflow_v4）接入；
- 产物全部可序列化、有界、同输入同输出。
"""

from app.services.gis_harness.workflow_v4.methodology import (  # noqa: F401
    METHODOLOGY_FAMILIES,
    METHODOLOGY_SCHEMA_VERSION,
    MethodCandidate,
    MethodQualification,
    MethodQualificationSet,
    MethodologyFamily,
    MethodologyRegistry,
    get_methodology_registry,
    qualify_method_candidates,
    resolve_methodology_family,
)

from app.services.gis_harness.workflow_v4.compiler_v4 import (  # noqa: F401
    WORKFLOW_COMPILER_VERSION,
    WORKFLOW_V4_STAGES,
    WorkflowCompilationV4,
    compile_workflow_v4,
)
