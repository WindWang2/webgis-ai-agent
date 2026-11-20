# G31-CROSS-LAYER-TOPOLOGY — DEDUP_MATRIX

Base：`ca7b3ec273f2b43336852951e2292a416b2238e6`（origin/master）。执行时逐项追到生产调用链。
状态词表：present（已存在，只做兼容回归）/ partial（部分存在，补增量）/ missing（本任务新增）/ owned-by-other（他任务所有权，不占用）/ blocked（外部阻塞）。

> 勘误说明：研究子代理曾报告 G14/G24/G18 模块"不在 master"——那是它读了旧主工作区（HEAD 816705218，#2073–#2093 合并前）。本 worktree 基于 origin/master ca7b3ec27，下列判断全部以本 worktree 实读为准（feature_edit/topology.py、selection_handles.py、data_delivery/source_resolver.py 均在）。

| # | 任务要求 | 状态 | 生产调用链证据（base SHA） | 本任务动作 |
|---|---|---|---|---|
| 1 | 单数据集重叠/间隙/悬挂端点检查（预算+截断披露） | present | `spatial_quality_service.py:545-645`（TOPOLOGY_OVERLAP/GAP/DANGLING_ENDPOINT，MAX_* 预算 :147-151） | 兼容回归；不重复实现（本任务做的是**跨数据集可声明规则 + finding 工作台**，不同层） |
| 2 | DQ 规则 DSL + 报告持久化 + durable job | present | `data_quality/rules.py:27-118`、`engine.py:271+`、`jobs.py`、`routes/data_quality.py` | 复用模式（封闭词表/digest/有界投影/幂等），不扩展该 DSL 承载跨源语义（RuleEvalContext 单 features 集，rule_functions.py:67-75） |
| 3 | topology_adjacency（单源 Polygon 重叠比例摘要） | present | `rule_functions.py:719-773`（有界 200 面封顶 skipped） | 保留不动；G31 规则是 finding 级、多源、带 witness，不复用该函数 |
| 4 | 多数据集源角色绑定规则（AOI/排除区/跨层/连接） | missing | — | 新增 `topology_workbench` 领域模块（封闭规则词表） |
| 5 | 覆盖缺口 = AOI − union − exclusions 语义 | missing | — | WP05 新增（含 witness 连通分量、面积阈值） |
| 6 | 跨层包含/覆盖（建筑⊆地块，边界政策） | missing | — | WP05 新增 |
| 7 | 跨层禁止重叠 | missing | — | WP05 新增 |
| 8 | 线端点连接规则（设施点连接、合法出口） | missing | SpatialQualityEngine DANGLING_ENDPOINT 是单层悬挂检测（无连接对象/容差语义，spatial_quality_service.py:608-645） | WP06 新增（连接对象、容差、出口例外） |
| 9 | 持久运行 + 逐问题 finding 实体 | missing | QualityReport 只有逐规则行（models/data_quality.py:109） | WP07 新增伴随实体（topology_runs/findings/exceptions） |
| 10 | 稳定 finding key（witness 分量级、快照内定位） | missing | — | WP07 新增 |
| 11 | witness 几何证据存储 | missing（基础设施 present） | `durable_blob_store.py` get_filesystem_blob_store（内容寻址 JSON） | WP07 用 BlobStore 存 witness，行内只存 key+sha+bbox |
| 12 | 权威快照（session-ref + 项目产物全量读取） | present | `data_delivery/source_resolver.py:408-640`（session_ref/project_artifact + 鉴权 + is_complete）；`feature_pages.py` 分页 | WP03 直接复用 resolve_source 模式与鉴权 |
| 13 | 选择句柄消费（提交时解析、>50 全集、coarse 披露） | present | `selection_handles.py`（G24 已入 master；scope 封闭词表、TTL、粗超集披露） | WP03 提交时解析 scope 并持久化可重放范围，不持久化 handle 本身 |
| 14 | G14 编辑预览/提交/CAS/journal/undo | present | `feature_edit/service.py:33-155`、`executor.py`、`topology.py:34-85` | WP10 只读桥接调用；不改 executor/identity/journal |
| 15 | 修复 new-ref + lineage | present | `data_quality/repair_execution.py:101-197`、`repair_transaction.py:253+`（verify 复验先例） | WP10/WP11 复用模式；G31 的修复走 G14（ref-backed）或既有 new-ref |
| 16 | 计算度量 CRS 推荐 | present | `lib/gis/crs_safety.py:164`（recommend_metric_crs → UTM/UPS） | WP04 复用 + 反经线/极区/大范围 typed 阻断与单位语义 |
| 17 | durable jobs 基础 | present | `jobs/submit.py:43-213`、cancellation registry、progress | WP07 新增自有 task type（topology_workbench.run），注册进 task_queue include |
| 18 | REST API（规则集/运行/问题/例外/修复预览/复验） | missing | — | WP08/WP11 新增 routes + main.py 挂载 + api-docs 再生成 |
| 19 | 普通 @tool 通道 | missing | 工具注册模式 `tools/feature_edit_tools.py`、`_TOOL_MODULES`（tools/__init__.py:11） | WP11 新增 `topology_workbench_tools.py` 并登记 |
| 20 | 前端问题工作台（筛选/分页/定位/例外/修复） | missing | quality-panel.tsx 是样例快速审计（preview100、sampleOnly、禁部分修复 :171-173）——保留不动 | WP08 新增独立工作台面板 + i18n 命名空间 |
| 21 | 例外（accepted exception、理由、stale 失效） | missing | — | WP09 新增（用户决定，REST/UI only；agent 工具不得代决定） |
| 22 | 复验（同规则重跑 + resolved 证据判定） | partial | repair_transaction._verify 用通用 DQ 规则复检（:434），非拓扑规则集驱动、非 finding 级 | WP11 新增 finding-key 驱动的复验 |
| 23 | Pi 标准工具旅程（真实子进程 + dispatch + 本域服务） | missing | G12 已有 Pi 原生集成基建（ADR-0288）；G28 完成证据链 | WP12 验证一条真实 Pi 子进程 + ToolDispatch + 本域服务旅程 |
| 24 | 旧快速审计回归 | present | `frontend/test/project/quality-panel.test.tsx`、`tests/data/test_quality*.py` | WP12 兼容回归 |
| 25 | feature_edit 局部拓扑回归 | present | `tests/feature_edit/`（G14 全套）、提交期拓扑闸 | WP12 兼容回归（不改动） |

## 与并行任务的边界

- G29（Pi 交互）/G30（技能作业）：不触碰其 RPC 内核与技能执行面；本任务工具经现有 ToolRegistry/dispatch 消费 Pi。
- G32（矢量变更提案）：不导入其模块；G31 修复只走**已合并**的 G14 服务。二者不互为前置。
- G33/G34/G35：无共享面（legend/布局/时间媒体均不涉及）。
- 执行时若另一 PR 已实现同类面（open PR = 0，已核对），收敛增量；不 cherry-pick 他人分支。

## 历史任务占用核对

- G09 参数化批量分析、G21 OD 流图：未检索到 PR（open=0、closed 列表已核对至 #2008），与本任务无重叠；不重新占用其目标。
- R1 审计问题（质量 O(n²)、GC、权限）：仅作触及路径门禁，不纳入本任务验收。
