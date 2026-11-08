# 项目数据刷新（G10 · Project Data Refresh）

> 让用户更新学校点、行政统计表或栅格数据后，**知道**哪些分析、图层和统计会受影响；
> **先预览**变化，再只重算可证明受影响的部分，保留人工颜色、图层顺序、图例位置与文字，
> 最后受控采纳新成果或回退旧版。Pi 能解释「为什么更新、更新了什么、哪些不能自动合并」。

## 1. 它能做什么

| 能力 | 说明 |
|---|---|
| 检查更新 | 手动触发；汇总项目挂载数据集的**稳定逻辑身份**（`dataset:<id>`，不是文件名）、内容版本（`content_hash` / `version_fingerprint`）、证据级别与 `pinned/refreshable/ephemeral/missing/version_unknown` 状态 |
| 差异判定 | 字段增/删/类型/单位、稳定 feature ID 对齐后的新增/删除/属性/几何变化、CRS、bbox/时间窗、分母、方法版本、栅格块指纹 |
| 影响预览 | 输出 `unchanged/reuse/recompute/rebind/conflict/unverifiable` 裁决、最小可证明重算子图、复用结果、预算、保留的人工编辑与冲突；**只读** |
| 隔离候选 | 在 `data-refresh/` 隔离命名空间生成候选 MapSpec，当前可见地图零触碰 |
| user-wins 合并 | baseline / 当前人工编辑态 / 更新候选态三方合并，按稳定 entity ID 保留颜色、透明度、可见性、层序、组件位置/族锁、文本 |
| 原子采纳 | 既有 session 锁 + `MapSpecStore.save_mapspec` 单事务 CAS（expected revision + proposal fingerprint 双核对） |
| 回退 | 前向恢复（**新 revision**，不倒写）或既有 `resume_runtime_snapshot`（新 session） |
| Pi 工具 | 8 个自然语言语义工具，走既有 `ToolRegistry → ToolDispatchService → 领域服务` 链 |

## 2. 用户旅程（项目面板 → 资产 → 「数据更新」）

1. **检查更新**（只计算）→ 显示来源版本清单、证据级别、变化数、截断状态。
   首次检查只建立基线，明确提示「尚不能判定数据变化」。
2. **预览影响**（只读，不改 MapSpec / 数据 / 基线）→ 显示精确增/删/改数量、
   将重算的最小子图、可复用结果、预计预算、保留的人工调整、冲突列表与
   `proposal_fingerprint`。
3. **生成候选**（只写隔离命名空间）→ 候选完成后显示对比：差异、保留项、冲突、
   是否可采纳。刷新页面后从服务端 proposal/status 恢复进度。
4. **采纳 / 放弃 / 回退**（改变项目当前版本，需显式确认）：
   - 采纳：原子 CAS 提交，revision 单调增，旧快照不变；
   - 放弃：保留旧成果，释放候选 GC pin 与配额；
   - 回退：创建新 revision 或恢复会话，**不倒写 revision、不改不可变快照**。

按钮明确区分「只计算」与「改变项目当前版本」。

## 3. 证据级别与诚实披露

| 证据 | 能声称什么 |
|---|---|
| `content_exact`（`content_hash` + 完整四维指纹 + 稳定 ID + 未抽样/未截断） | 精确逐要素差异计数 |
| `revision_token`（`content_revision` / ETag） | 只能判定「有变化」，不能声称精确内容差异 |
| `timestamp_only` / `none` | 只标 `unknown`，触发保守整项重算 |
| 无稳定 feature ID / 抽样 / 预算不足 / 全局方法依赖 | `sample` 或 `unknown`；**绝不 silently unchanged**，转 `conservative full recompute` |

- 栅格存在 stencil/kernel/全局统计/重采样/网络拓扑/全局分类断点时，扩大依赖域，
  不假设「改一个像元只影响同一块」。
- 几何按已知 CRS 做规范化后比较，**不简化顶点、不设大容差**；CRS 改变时几何值整体失义。
- 大 FeatureCollection / 大表 / 全量日志不进模型上下文：只用有界 ref + 有界摘要，
  摘要带关键数值、证据级别、完整/截断状态与警告。
- 工具执行成功 ≠ 任务执行成功 ≠ 地图已显示 ≠ 版本已采纳 ≠ 文件已导出。
  回执四态分别披露；未做真实浏览器渲染观察时 `map_observation = unverified`，
  **不伪造 renderer ACK**。

## 4. REST API

所有端点在 `/api/v1/projects/{project_id}/data-refresh` 下，均需项目鉴权
（跨 owner 一律 404 不泄露存在性），写面另需会话所有权。

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/check` | 手动检查更新（首次建立基线） |
| GET | `/sources?session_id=` | 来源版本清单 |
| POST | `/preview` | 只读影响预览 → proposal |
| GET | `/proposals` | 有界提案清单（恢复入口） |
| GET | `/proposals/{id}` | 提案详情 |
| GET | `/proposals/{id}/status` | 候选进度（刷新后恢复） |
| GET | `/proposals/{id}/explain` | 解释某图为何过期 |
| POST | `/proposals/{id}/execute` | 执行隔离候选 / `cancel=true` 取消 |
| GET | `/proposals/{id}/compare` | 候选与当前对比 |
| POST | `/proposals/{id}/adopt` | 原子 CAS 采纳 |
| POST | `/proposals/{id}/discard` | 放弃候选（释放 GC pin） |
| POST | `/proposals/{id}/rollback` | 回退 |

请求体严格校验（`extra=forbid`、数组长度、DAG 节点/边上限、有限数）；
客户端传来的 `owner_id` / `approved` / `verified` 一律拒绝，**owner 由服务端
从 session 所有权 + 项目鉴权解析**。`adopt` 的 `expected_revision` 与
`proposal_fingerprint` 只作提示，服务端重新读取权威值双核对。

## 5. Pi 工具（自然语言语义）

| 工具 | 语义 |
|---|---|
| `webgis_check_project_source_updates` | 检查项目来源变化 |
| `webgis_explain_stale_map_product` | 解释某图为何过期 |
| `webgis_preview_project_data_refresh` | 预览更新 |
| `webgis_execute_project_data_refresh` | 执行授权候选 |
| `webgis_adopt_project_data_refresh` | 采纳候选 |
| `webgis_discard_project_data_refresh` | 放弃候选 |
| `webgis_rollback_project_data_refresh` | 查询/回退版本 |
| `webgis_project_refresh_status` | 查询候选与进度 |

例如 Pi 可以如实回答：「改了 12 个要素，只重算两项统计，图例位置保留」——
这些数字来自真实 `compute_source_diff` 的 `exact_counts` 与
`compute_affected_subgraph` 的子图，不是编造。

## 6. 复用的既有基础（不重新实现）

- `app/lib/data/fingerprints.py`：`classify_change` / `staleness_verdict` /
  `compute_reuse_fingerprint` / `canonical_fingerprint`
- `app/services/artifact_registry.py`、`data_lifecycle/service.py::impact_analysis`：依赖图与只读裁决
- `app/services/gis_harness/workflow_v4/recompute.py::compute_affected_subgraph`：受影响子图
- `app/services/workflow_runtime/*`：重算计划 / attempt / 取消 / 锁（**不新建第二执行器**）
- `app/contracts/workbench_locks.py`：组件族锁读取
- `app/services/distributed_lock.py::session_lock_registry` + `MapSpecStore.save_mapspec`：原子 CAS
- `app/services/project_runtime/*`：runtime snapshot、resume、目录与原子写原语
- `ToolRegistry` / `ToolDispatchService` / `PiToolRequest → dispatch_tool`

## 7. 边界与限制

- **不自动**刷新数据：默认手动「检查更新」；本轮没有后台定时器，也不自动发起
  互联网抓取或拉取付费数据。远程再取数必须走受控 data-fabric adapter 与
  SSRF/DNS/预算规则。
- **不自动改写报告 / 公开发布**：G11 只消费本功能给出的「哪些结果过期、更新到哪个版本」事实。
- **不修改** G09 的参数表单 / 批次调度，也不改动 Data Fabric 下载摄入、资格/CRS 校验、
  MapSpec mutation、workflow 重算服务的既有签名与错误语义。
- 候选产物是「数据版本重绑定 + 人工编辑保留」的 MapSpec，**不伪造 GIS 分析数值**；
  科学重算节点由既有 Workflow Runtime 依据本计划执行。
- 硬数据语义错误（单位/CRS 不一致）不会被 user-wins 变成合法科学结论：
  `adoptable=False` 并显式冲突。

## 8. 回滚方式

1. 若已采纳：在项目面板点「回退」或调 `POST .../rollback`。
   优先以采纳前 MapSpec 作**新 revision** 前向恢复；若提案绑定了 runtime snapshot，
   则走既有 `resume_runtime_snapshot` 创建新 session。
   **不倒写 revision、不删用户后续数据、不改不可变快照**。回退后地图需重新观察。
2. 若只想撤销分支：删除本功能分支/worktree 即可；功能为 additive 模块 +
   两处注册（`app/main.py`、`app/tools/__init__.py`）+ 一个资产 tab，无破坏性迁移。
3. 数据面：候选 doc/manifest 位于 `DATA_DIR/workspaces/<project>/data-refresh/`；
   「放弃候选」会释放 pin，GC 仍由既有 artifact lifecycle 引用计数负责。

## 9. 未验证项

- 真实浏览器 E2E（`frontend/e2e/journeys/j13-project-data-refresh.journey.ts`，
  `E2E_MODE=real`，需要 `E2E_USER/E2E_PASS` + `E2E_G10_PROJECT_ID/SESSION_ID`
  与运行中的真实后端）：本机未跑通，**标未验证**，不把组件测试提升为浏览器证据。
- 线上 LLM provider / 付费模型可用性：用 stub LLM + 真实工具 dispatch 验证调用链，
  模型理解能力另列未验证。
- 完整「真实 Pi RPC + stub LLM」端到端车道（`PI_JOURNEY_E2E=1`，需 vendor/pi 构建）：
  本轮用 `PiToolRequest → dispatch_tool` 真实入口 + 真实 `ToolDispatchService` 验证，
  完整 RPC 子进程车道未跑。
- 线上 CI/CD：本地验证为交付依据，未轮询 Actions、未宣称线上 CI 通过。
