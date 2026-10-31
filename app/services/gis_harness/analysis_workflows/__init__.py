"""Analysis Workflows —— 多图层 GIS 分析流程组装薄编排层（goal-02）。

与既有真相的分工（红线，见 planning/analytical-workflow-composition/INTERFACE_CONTRACTS.md）：

- **PlanGraph / SessionPlan**：会话进度事实，本层不写它；
- **Recipe / WorkflowProfile（workflow_schema）**：声明层真相——本层的
  family 模板只**引用**既有 recipe/capability/工具名与 DATA_ROLES /
  RECOMPUTE_DIMENSIONS 词表，不复制语义；
- **plan_mode（propose_plan/execute_plan）**：LLM 自由 authored 通用多步
  计划通道，保持不动——本层是「GIS 契约通道」：步骤来自 family 模板、
  带数据角色合同/科学前置/单位契约/内容指纹，二者互补；
- **workflow_runtime V5**：durable 面向的实例运行时，保持显式 API 不动；
- **执行真相**：步骤执行一律 ``registry.dispatch``（ToolDispatchService
  同一缝——guardrail/复用/ref 铸造全继承；先例 run_3d_analysis）。

模块（全部纯函数或以注入依赖为参数，模块级零单例）：

- ``contracts``   pydantic 契约 + 校验 + 内容指纹
- ``families``    5 族步骤模板（悬空工具/前置引用编译期 fatal）
- ``orchestrator`` 场景 → 契约（角色绑定 + 科学前置裁决）
- ``executor``    真实 dispatch 执行（有界/可取消/幂等）
- ``recompute``   步骤内容指纹 + 选择性失效传播
- ``evidence``    StepReceipt + G02 三段对账投影
- ``state``       会话内持久状态块（恢复/单写者）
"""
