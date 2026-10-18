"""Project Runtime 包（ADR-0224 — Durable Project Cartographic Runtime V1）。

组合既有权威（WorkspaceSnapshot / MapSpec store / artifact registry /
data fabric datasets / capability runtime），提供：

- ``contract`` —— ProjectRuntimeSnapshotV1 manifest 契约 + 恢复状态词表；
- ``store`` —— 项目域 runtime snapshot 存储（原子写 / cap / lease）；
- ``promotion`` —— 会话 ref → durable 指针的显式晋升规划器（quota-aware）；
- ``rebind`` —— MapSpec source 重绑定事务（语义不漂移守卫）；
- ``freeze`` —— 显式冻结编排（consistent cut + 晋升 + manifest 落盘）；
- ``validation`` —— verify-first 校验（schema/owner/artifact/dataset/capability）；
- ``resume`` —— 恢复编排（校验 → 恢复计划 → 新 session 构建 → 回执）。

单一事实源不变式：manifest 只携带 authority ids / fingerprints / durable
pointers / 有界 spec 文档（MapProductVersion.mapspec_snapshot 同款先例），
载荷字节永远只在 Wave-1 BlobStore 一份。
"""
from app.services.project_runtime.contract import (
    DATASET_DESCRIPTOR_LIMIT,
    SOURCE_BINDING_LIMIT,
    RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    ProjectRuntimeSnapshotV1,
    RESUME_STATES,
)
from app.services.project_runtime.store import (
    get_project_runtime_store,
    ProjectRuntimeStore,
)

__all__ = [
    "DATASET_DESCRIPTOR_LIMIT",
    "SOURCE_BINDING_LIMIT",
    "RUNTIME_SNAPSHOT_SCHEMA_VERSION",
    "ProjectRuntimeSnapshotV1",
    "RESUME_STATES",
    "get_project_runtime_store",
    "ProjectRuntimeStore",
]
