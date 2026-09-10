# Workflow V5 Executable Runtime — Baseline (Phase A)

- worktree: `/home/kevin/projects/webgis/webgis-ai-agent-workflow-v5`
- branch: `feat/workflow-v5-executable-runtime`
- base commit: `8a33e3a5` (origin/master, PR #1168 merge + quality manifest regen)
- date: 2026-09-09
- open PRs: 0（近 15 个 PR 全部 MERGED，最新 #1168 workflow-v4、#1172 quality-v2）
- open issues: 0

## 1. 现状事实源地图（全部已验证，file:line 证据）

### 1.1 语义编译层（Workflow V4，本 Epic 的输入）

| 事实 | 证据 |
|---|---|
| `compile_workflow_v4` = 15 阶段 base + 8 个 V4 阶段（resolve_methodology→emit_workflow_package），纯函数零 I/O | `app/services/gis_harness/workflow_v4/compiler_v4.py:94-342`，`WORKFLOW_V4_STAGES` :38 |
| TypedWorkflowGraph：TypedPort（artifact_type/geometry_kind/crs_requirement/unit_requirement/required）+ 节点 kind 词表 5 种 + 端口兼容裁决 `ports_compatible`（**编译期 only**） | `workflow_v4/typed_dag.py:46-64,151-174`；节点上限 64/边 128 :42-43 |
| WorkflowPackage：package_id=recipe_id + semver + canonical compiled form sha256 指纹 + 64KB 预算；**不落库**（emit 后丢弃，只留 fingerprint） | `workflow_v4/package.py:48-71,116-163`；`compiler_v4.py:326-327` 只取 fingerprint |
| 兼容性裁决：compiler major 不同→不兼容；schema minor 更新→SCHEMA_NEWER | `package.py:166-205` |
| `diff_workflow_packages`：7 种 DIFF_KINDS，每条映射 WorkflowChange→recompute 桥接 | `workflow_v4/diff.py:27-35,112-272` |
| `compute_affected_subgraph`：WorkflowChange 种子→正向闭包→RecomputePlan{recompute/reuse/reuse_artifacts/explanations}，O(V+E)，style 零科学重算 | `workflow_v4/recompute.py:93-168` |
| 义务继承：inherit_obligations 幂等、最强 on_violation、科学 kind 优先、provenance 链 | `workflow_v4/obligations.py:114-161` |
| **生产消费只有两处，均为 evidence-only（不执行）**：`plan_orchestrator._compile_v4_evidence`（写 `plan.workflow_v4` 摘要 dict）；`semantic_tools.compile_workflow_semantics`（工具返回摘要） | `app/services/chat/plan_orchestrator.py:643-710`；`app/tools/semantic_tools.py:296-368` |

### 1.2 会话执行态（Harness runtime）

| 事实 | 证据 |
|---|---|
| SessionPlan.gis_chapter 行状态唯一写手 `_mark_progress`；`apply_tool_result` 在工具结果后推进行（pending→complete + bound_ref） | `app/services/session_plan.py:454-491,592-673` |
| `workflow_instance` 块 = **派生投影**（非执行态机）：`derive_workflow_instance` 纯派生自 chapter；证据指纹失配→STALE 沿 reverse 闭包传播；gate_fingerprint 去重门；锁内 CAS 守卫（goal/rows/contract/实例块四重漂移守卫） | `app/services/gis_harness/workflow_instance.py:478-617,950-1003` |
| StageState 词表：pending/ready/active/satisfied/blocked/stale/skipped/failed —— **是投影枚举，无 CAS 转移 API** | `workflow_instance.py:84-106` |
| WorkflowEventKind 8 种事件 → RECOMPUTE_DIMENSIONS 映射（data_arrived/artifact_produced/artifact_stale/algorithm_change/parameter_change/style_mutation/observation/tool_failure） | `workflow_instance.py:57-81` |
| 恢复锚点（Harness V5）：project-level resume anchor，持久化 user_goal+关键 chapter 块+trace 游标+ref 清单 | `app/services/gis_harness/resume_anchor.py:41-80` |
| 生产执行入口：`agent_pi_bridge` 工具结果→`apply_tool_result`（4 处调用） | `app/agent_pi_bridge.py:712-837` |

### 1.3 项目持久工作流（WorkflowEngine，另一执行域）

| 事实 | 证据 |
|---|---|
| `Workflow/WorkflowRevision/WorkflowRun/Artifact` DB 模型；revision 不可变快照 + graph_fingerprint | `app/models/project.py:82-147` |
| WorkflowEngine：run/replay(exact)/resume，输入指纹不变才复用已完成步（INV-REPLAY1/RESUME1）；ToolDispatchService 派发；provenance manifest | `app/services/workflow_engine.py:1-90` |
| **消费的是 recipe WorkflowStepSpec（工具级），不消费 WorkflowPackage/typed ports/方法论语义** | `workflow_engine.py` + `workflow_promotion.py:1-16` |
| API：`/projects/{id}/workflows/...` run/replay/resume/revisions | `app/api/routes/project.py:231+` |

### 1.4 GeoCompute 数据面（DAG 执行引擎）

| 事实 | 证据 |
|---|---|
| `ExecutionNode.semantic_fingerprint()`：category/operation/inputs/dataset_fingerprints/parameters/crs/produces/accepts 参与指纹；estimate/policy/reuse 不参与 | `app/services/geocompute/plan.py:185-205` |
| `GeoExecutionEngine.execute_plan`：同步执行、CancellationToken 取消、ResourceGovernor 准入、run 归属 owner 域隔离（REST 404 读隔离） | `app/services/geocompute/executor.py:245-330` |
| 跨进程复用索引 `geocompute_node_results`（迁移 0029）：(owner_scope,node_fingerprint) 唯一、每 owner LRU 64 条、fail-open、上游指纹一致+ref 可解析才复用 | `app/services/geocompute/reuse_index.py:51-131`；`app/models/db_model.py:277-302` |
| durable 节点穿过 `AnalysisTask` 行（不加第二任务表）；run 级 CancellationToken→`request_cancel_sync` 持久取消；结果经 session ref 交接 | `app/services/geocompute/durable.py:1-80` |
| ops registry：query/filter/aggregate/spatial_join/vector_op/raster_window/interpolation/materialize/artifact_register 等已接线；未接线类别诚实 `OPERATION_UNSUPPORTED` | `app/services/geocompute/ops.py:243-683` |
| `owner_scope_for(caller, session_id)`：user hash 优先，session 回退，anonymous 不与真实用户共享域 | `app/services/geocompute/executor.py:153-175` |

### 1.5 Artifact 身份与生命周期

| 事实 | 证据 |
|---|---|
| 会话 ArtifactRegistry：artifact_id=ref 字符串；ArtifactGraph 纯派生（consumers/lineage/dependents/replacement_chain）；生命周期 valid→superseded→stale→expired；**缓存记录层，不驱动行状态** | `app/services/artifact_registry.py:36-56,128-201` |
| metadata 有界键 24；profile_digest/raster_fingerprints 进 metadata | `artifact_registry.py:43-46,466-530` |
| artifact_revisions（迁移 0026）+ lakehouse DataObject durable identity（PR #1164）为另一持久层 | `app/services/artifact_revisions.py` |

### 1.6 质量门禁与共享文件

- quality manifest 是派生物：语义变化后跑 `scripts/gen_quality_manifest.py`，CI 由 `tests/quality/test_quality_manifest_gate.py` 锁字节一致。
- findings ratchet gate：`tests/quality/test_findings_ratchet_gate.py` 锁零。
- Alembic 单 head：`0033_geocompute_v6_cluster`（0032→0033 已验证）。
- 前端 workflow UI：`frontend/components/sidebar/workflow/`（lineage-list/compare-panel/recovery-actions）为项目工作流面板。

## 2. Gap 分析（Must-have × 现状）

| Must-have | 现状 | Gap 级 |
|---|---|---|
| A. Runtime Instance（状态机/CAS/durable resume） | `workflow_instance` 是投影非执行态机；无实例身份持久化；无 CAS 转移 API | **P0** |
| B. Typed Port Runtime Verification | `ports_compatible` 仅编译期；运行时对实际 artifact 零校验 | **P0** |
| C. Harness Integration | 无任何组件消费 WorkflowPackage 做执行控制 | **P0** |
| D. GeoCompute Integration | 无 workflow node→ExecutionPlan/job 桥接；无 workflow 级幂等键；无取消传播 | **P1** |
| E. Artifact Reuse | 复用仅 geocompute 节点级 checkpoint（owner+fingerprint）；无 workflow 级复用指纹栈（input+algo+param+env+package） | **P1** |
| F. Incremental Recompute | `compute_affected_subgraph` 纯函数无 runtime 消费者；RecomputePlan 不被执行 | **P0** |
| G. Package Registry & Versioning | 包不落库；无 publish/promotion/semver 端到端；无 environment fingerprint | **P1** |
| H. Composite/Subworkflow | typed_dag 有 subworkflow kind；无运行时语义（边界/取消/深度守卫） | **P2** |
| I. User/Renderer Projection | Plan.workflow_v4 是规划期摘要；运行态（当前节点/blocked/stale/reused/为什么重算）无 API | **P1** |

## 3. P0/P1 级问题清单（本 Epic 须修复或明确边界）

1. **P0** V4 包 emit 后即弃（`compiler_v4.py:326-327`）——fingerprint 可重放验证（纯函数同输入同指纹），但包本身无处可取；V5 需要把「编译→注册」做成持久动作。
2. **P0** 会话行状态与 typed DAG 节点**不同名**（行按 capability，节点按 `cap:<capability>`/`data:<role>`）——runtime 需要显式 node↔行映射，不能假设同名。
3. **P1** `diff.py:213` `_edge_set` 对 `from` 不剥端口（`from` 键含端口），`to` 剥——边 diff 的 old/new 语义不对称（记录为已知怪癖；V5 不改 V4 编译期语义，只消费）。
4. **P1** geocompute 复用索引无「内容损坏/被 GC 后」的自愈证据流——`delete_result` 存在但调用方行为需确认（executor 探测失败→重算，需复核）。
5. **P1** `workflow_instance` 的 STALE 传播只到「披露」，没有「谁去重算」的执行闭环。
6. **P2** subworkflow 节点无 depth guard（typed_dag 构建不展开 subworkflow）。
7. **P2** MapSpec style mutation（`mapspec_mutations.py`）与 workflow 域无事件桥（style-only 零科学重算需要该桥接）。

## 4. 复杂度与资源增长

- typed DAG：≤64 节点/≤128 边（typed_dag.py:42-43 硬编码）→ 调度 O(V+E) 有界。
- package compiled form ≤64KB（package.py:37 运行时强制）。
- instance 证据：必须自行设界（节点状态表 ≤ 节点数；转移环形 ≤64；bindings ≤ 节点数）。
- 复用索引：沿用 (owner, fingerprint) 唯一 + 每 owner LRU 纪律。
- geocompute budget：`ResourceBudget` max_nodes ≤256、deadline ≤3600s 服务端红线（plan.py:137-141）。

## 5. 多租户边界

- `owner_scope_for` 哈希域（executor.py:153）是复用键/run 归属的既定纪律——workflow 级复用索引必须同域。
- 项目工作流已有 INV-AUTH1（run/replay/resume 全部重授权 project ownership + tenant scope）。
- session 面 `require_owned_session`（mapspec_mutations.py:7）。
- 复用证据跨 owner 泄漏是本 Epic 的红线测试项。

## 6. 与并发 Epic 的冲突面

| 共享文件 | 冲突方（worktree 已存在） | 缓解 |
|---|---|---|
| `CHANGELOG.md` | 全部 | 只做最小追加 |
| `docs/adr/` | 编号竞争 | 新 ADR 取 0119+（当前最高 0118）|
| `migrations/versions/` | geocompute-v7 / data-fabric-v7 / lakehouse-v7 都可能加迁移 | 链在 0033 后创建 0034；rebase 时若冲突重排 |
| `app/models/db_model.py` | 同上 | 追加新表类（additive，文件尾） |
| `app/api/routes/` 注册 | workbench-v5 等 | 新 router 文件 + app/main.py 或 api/__init__ 一行注册 |
| `frontend/components/sidebar/workflow/` | workbench/collaboration | 新 inspector 组件文件为主，改动既有文件最小化 |
| `app/services/gis_harness/workflow_v4/` | 无（V4 已 merged） | 只读消费 |
| `tests/quality/` manifests | quality 系列 | 语义变化后重新生成派生物 |

## 7. 冻结结论

- V4 编译器契约（15+8 阶段、COMPILER_STAGES 锁测试）零改动；V5 是 additive runtime 消费层。
- 不动 WorkflowEngine（项目工具级运行时）、不动 geocompute scheduler/executor 内核、不动 failure taxonomy——V5 通过适配器消费。
- 执行事实源：**workflow runtime instance（V5 新增，DB）** 持有节点执行态；SessionPlan 行状态、ArtifactRegistry、geocompute run 注册表各自保持既有事实源地位，V5 只引用不复制。
- 证实「测试为什么能证明正确」：V4 测试全部 contract-only（纯函数），执行闭环零测试——因为执行闭环尚不存在。
- 证实「mock-only 路径」：`subworkflow` 节点 kind、composite 义务继承无生产调用方（V4 PR Known limitation #3）。
