# Ownership — goal-gis-topology-authoring-20261125

## 本任务可写路径（主）
- `app/services/topology_workbench/` — 新增共享拓扑索引、共边编辑、线网约束模块（G31 域内扩展）
- `app/services/feature_edit/` — impact plan/共边 op/线网 op 的编辑链扩展（G14 域内扩展，additive）
- `app/schemas/feature_edit_schema.py`、`app/schemas/topology_workbench_schema.py` — additive schema
- `app/api/routes/feature_edits.py`、`app/api/routes/topology_workbench.py` — 新端点（既有 router prefix）
- `app/tools/feature_edit_tools.py`、`app/tools/topology_workbench_tools.py` — 新工具（免改 tools/__init__）
- `tests/unit/feature_edit/`、`tests/topology_workbench/`、`tests/integration/` — 本域测试
- `frontend/lib/edit/`（如有）、`frontend/lib/types/feature-edit.ts`、`frontend/lib/api/feature-edit.ts`、`lib/api/topology-workbench.ts` — 前端接线
- `frontend/components/map/`、`components/chat/` — additive 组件
- `frontend/messages/{zh-CN,en-US}/topologyEditing.json` — 独立 i18n 命名空间（免冲突）
- `docs/adr/` — 新 ADR（编号顺延现有最大）
- `planning/goal-gis-topology-authoring-20261125/` — 本任务账本

## 只读依赖（仅经既有 API）
- `field_contracts`（G44）、`coordinate_operations`（G45）、`data_quality`、`data_lifecycle`、MapSpec models、project artifact、selection_handles、session_data_manager、ref_lifecycle、ToolRegistry、Pi bridge（G12）、capability registry（G13）
- `app/services/feature_proposals/` — 若接线提案卡仅走既有服务 API

## 共享接线（最小化）
- `app/main.py`：仅当新增 route 文件时加一行 include（检查 4 个开放 PR 均加 include 行——合并顺序冲突需在 PR 中说明）
- `app/tools/__init__.py`：目标为零改动（工具放既有域文件）
- `app/models/__init__.py`：目标为零改动（如需新模型文件才加）
- `frontend/messages/*/map.json`、`chat.json`：目标为零改动（独立命名空间文件）

## 不可触碰
- Pi vendor/bridge（`vendor/`、`app/agent_pi_bridge.py`）
- 方法实验/数据融合/多尺度/情境 harness 的域目录（开放 PR 所有）
- 制图引擎、workflow builder、跨工程包
- 原始 checkout `webgis-ai-agent`（只读；共享 `.venv` 可复用运行测试）
- 其他任务的 worktree/分支/worktree 数据

## Subagent 分工
- A（后端领域）：feature_edit + topology_workbench 服务扩展
- B（接线）：tools/routes/schema/前端 API 层
- C（测试/冷审）：WP6 测试 + R1/R2 独立审查（不参与实现）
