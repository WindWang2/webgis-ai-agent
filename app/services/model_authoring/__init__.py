"""模型作者域（可视化 GIS 模型构建器 · 参数化工作流 · 可复用专业技能）。

分层（不建第二事实源）：
- ModelDocument（作者草稿，CAS 修订）→ validate → compile → WorkflowPackage
  （既有 `workflow_v4.package` 指纹机制，包 id `umodel_` 前缀）→
  PackageRegistry（既有 draft/publish/deprecated）→
  WorkflowRuntimeService 实例化 adapter（既有 driver/bindings，零重写）。
- 执行态/复用/重算/解释全部读既有 runtime 投影，本域只加模型维度回执。
"""
