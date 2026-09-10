# Workflow V5 Executable Runtime — Architecture (Phase B, R1 修订版)

> 修订记录：纳入 Subagent-A 架构挑战（agent_5af42753）全部 BLOCKER/CRITICAL/MAJOR：
> B-1（V4 recompute 真实形态闭包失效，已本地复现）、C-1（RUNNING 窗口）、C-2（oracle 三方矛盾）、
> C-3（整行 CAS 卡死）、M-1~M-8 及 MINOR 全项。修订处以 [R1-*] 标注。

## 0. 一句话

把 V4 编译产物（WorkflowPackage/typed DAG/RecomputePlan）从 evidence-only 升级为**可执行、可恢复、可增量重算、可复用证明**的运行时，通过适配器复用既有 GeoCompute 数据面与 Harness 会话执行，不建第二事实源。

## 0.1 前置缺陷修复 [R1-B1]

**V4 `compute_affected_subgraph` 在真实包形态上是坏的**（已实测复现：`data_role` 变更→闭包只含种子；`parameter` 变更→空集）。根因：`TypedWorkflowEdge.to_bounded_dict()`（typed_dag.py:110-114）把边端点写成 `"node.port"`，而 recompute.py:110-116 用原始字符串建邻接表。本 Epic **Wave 1 先修此缺陷**：边端点按「最后一段 `.` 后缀若剥离后命中已知节点 id 则归一」的防御性规则归一化（节点 id 词表 `<kind>:<name>` 无 `.`；端口名无 `.`；同名冲突时保持原样诚实降级）。补两类回归测试：真实 `compile_workflow_v4` 产物形态 + 强闭包断言（下游全量/参数子树含 output）。这是 bug fix，不改语义词表，COMPILER_STAGES 契约零改动。

## 1. 层次（目标架构）

```text
Workflow V4 Compiler（语义唯一事实源；仅 §0.1 bug fix）
  ↓ WorkflowPackage（emit_workflow_package 纯函数，指纹可重放验证）
WorkflowPackage Registry（V5 新增，DB 持久，semver 发布；注册时 re-emit 比对指纹）
  ↓ resolve(package_id, version?)
Runtime Instance Store（V5 新增，DB 持久；实例行 + 每节点行两级 CAS）
  ↓ 实例化（节点 PENDING 初始化 + data 角色绑定槽位）
Binding/Qualification Gate（V5：运行时 typed port 校验，真 artifact descriptor）
  ↓ READY 节点（driver 认领）
DAG Execution Driver（V5：拓扑波次调度 + 节点级 CAS + 租约心跳 + 取消/恢复）
  ↓ 适配器
GeoCompute Adapter（node→ExecutionPlan→GeoExecutionEngine；dataset_fingerprints 携带输入指纹）
Harness Adapter（data_input 绑定会话 ref；工具结果→节点证据回写，会话锁外）
  ↓ 输出
Artifact Binding（session ref + ArtifactRegistry 登记；复用索引记录）
  ↓
Evidence（节点 attempt/复用/阻断证据，全部有界）
  ↓
Semantic Change → diff → compute_affected_subgraph（V4 纯函数，§0.1 修复后）
  → 节点级 CAS 批量 STALE → 增量重算（dirty 执行 + clean 复用证明）
```

## 2. 模块布局（新包 `app/services/workflow_runtime/`，零共享文件侵入）

| 模块 | 职责 |
|---|---|
| `contracts.py` | typed contracts：NodeState/InstanceStatus、转移表、BindingVerdict、NodeAttempt、ReuseEvidence、RecomputeDecision、PendingChange（pydantic、有界、canonical 指纹） |
| `machine.py` | 纯函数节点状态机：`validate_transition`、`ready_set(snapshot)`；O(V+E)；非法转移 typed 拒绝 |
| `fingerprints.py` | 复用指纹栈（含 content_revision、geo 栈版本、methodology/算法 registry 指纹） |
| `binding.py` | 运行时 typed port 校验：descriptor × TypedPort → BindingVerdict |
| `store.py` | DB 持久化：实例行（实例级 CAS/租约/终态）+ 节点行（**每节点独立 CAS**）；SQLite busy 与 CAS 冲突分道；恢复扫描 |
| `registry.py` | workflow_packages 表：publish/resolve/compat（复用 V4 check_compatibility/next_version） |
| `reuse.py` | workflow_node_reuse 表：记录/查找/失效/剪枝（每 owner LRU）+ eligibility |
| `adapters_geocompute.py` | workflow node → ExecutionPlan（确定性映射 + 诚实 NODE_NOT_EXECUTABLE）→ execute_plan |
| `driver.py` | 执行驱动：波次调度（有界并发 ≤4）、节点认领（claim token）、取消、重试上界、租约续期、孤儿清扫 |
| `recompute.py` | runtime recompute：changes → V4 plan → 节点级 CAS 批量 STALE → 复用裁决 → 增量执行；quiescence 门 + pending 队列 |
| `service.py` | 门面：instantiate/run/cancel/record_tool_result/apply_changes/explain（API 与 chat 集成唯一入口） |
| `projection.py` | 有界投影 + 「为什么重算/为什么没重算」解释 |

## 3. 持久化（migration `0034_workflow_v5_runtime`，down_revision=`0033_geocompute_v6_cluster`）

> [R1-M1] 撞号风险：geocompute-v7 分支已出现 `0034_geocompute_v7_dataflow`。本分支按当前 master head 链 0034；
> **rebase 协议（§16）**：若上游 0034 已合入，本迁移重编号为 0035 并改 down_revision——迁移文件名+revision 字符串+down_revision 三处同步，单 head 断言进 CI 测试。

### workflow_packages
- `(package_id, version)` 唯一；列：id、package_id、version、schema_version、compiler_version、methodology_family、recipe_fingerprint、methodology_fingerprint、environment_fingerprint、compiled_form(JSON ≤64KB)、fingerprint、status(draft|published|deprecated)、owner_scope、project_id nullable、created_at、published_at。
- 事实源：registry 管「存在/发布态」；**包内容事实源仍是编译器**——注册时从编译产物 re-emit 比对指纹，不一致拒绝。

### workflow_instances（实例行，轻量）
- 列：instance_id PK、package_id、package_version、package_fingerprint、owner_scope、session_id、project_id、parent_instance_id nullable、parent_node_id nullable（[R1-M7] 子实例反向指针）、status(**running|succeeded|failed|cancelled|superseded** [R1-M6])、revision(int)、run_lease_owner、run_lease_expires_at（租约/心跳）、cancel_requested(bool)、pending_changes(JSON ≤16)、decisions(JSON 环形 ≤16 条 RecomputeDecision)、visited_packages(JSON ≤8, 子工作流环检测 [R1-M7])、error(JSON)、created_at/updated_at/started_at/completed_at。

### workflow_instance_nodes（**每节点一行** [R1-C3]）
- `(instance_id, node_id)` 唯一；列：node_state(PENDING/…)、state_revision(int, 节点级 CAS)、claimed_by(run token)、attempts(int ≤3)、error_code、bound_ref、binding(JSON: BindingVerdict+violations)、reuse(JSON: ReuseEvidence)、transitions(JSON 环形 ≤8)、output_ref、output_fingerprint、updated_at。
- **CAS**：`UPDATE workflow_instance_nodes SET state=..., state_revision=state_revision+1 WHERE instance_id=? AND node_id=? AND state_revision=?`。0 行 = CAS 冲突 → 重读后**重评转移合法性**（完成类转移 RUNNING→SUCCEEDED 由 claim token 持有者重放直至成功或状态已等价——**完成永不放弃** [R1-C3]）；SQLite `database is locked`（OperationalError）与 CAS 冲突分道：前者退避重试，后者重读重评。
- 卡死清扫：driver 持租约（每波次续期）；恢复/运行入口扫描 `state=RUNNING AND lease 过期` 的节点 → READY（孤儿复位，attempts 保留）。

### workflow_node_reuse
- `(owner_scope, reuse_fingerprint)` 唯一；每 owner LRU ≤128；列含 artifact_session_id（复用解析必需）、input_fingerprints、fingerprint_level（content|profile_digest|shape——**shape 级只记录不复用** [R1-M3]）、content_revision、created_at、last_verified_at。

## 4. 节点状态机（typed，节点级 CAS）

词表：`PENDING / READY / RUNNING / SUCCEEDED / FAILED / BLOCKED / SKIPPED / CANCELLED / STALE`

```text
PENDING   → READY / BLOCKED / SKIPPED / CANCELLED
READY     → RUNNING / BLOCKED / STALE / CANCELLED        （STALE 为 [R1-C2] 新增）
RUNNING   → SUCCEEDED / FAILED / CANCELLED
FAILED    → READY（attempt+1 ≤3）/ SKIPPED（optional 且超限）
BLOCKED   → READY（阻断解除）/ SKIPPED（optional 且不可解除）
SUCCEEDED → STALE（重算种子/闭包命中）
STALE     → READY（需重算入队）/ SUCCEEDED（复用证明命中，零重算）
SKIPPED   → READY（上游重算成功后的重新资格化 [R1-MINOR-1]）
{PENDING,READY,RUNNING,STALE,FAILED,BLOCKED} → CANCELLED（**仅非终态** [R1-MINOR-2]；SUCCEEDED/FAILED/SKIPPED/CANCELLED 不可）
```

- RUNNING→STALE 不入表：apply_changes 有 quiescence 门（§9），RUNNING 节点不被标 STALE；其陈旧风险由 pending_changes 在完成边界补评（§9.5）[R1-C1]。
- 实例级状态裁决 [R1-MINOR-3]：全部节点 ∈ {SUCCEEDED,SKIPPED} → succeeded；任一非 optional 节点 FAILED 且重试耗尽 → failed；cancel → cancelled；supersede → superseded；存在非 optional BLOCKED（不可解除）→ failed（error=NODE_BLOCKED，诚实终态，不悬挂）。

## 5. 运行时 Typed Port 校验（binding.py）

维度与 reason codes：
1. `artifact_type`：descriptor 类型 ∈ ArtifactTypeRegistry 且与端口要求相等（宽端口 `feature_collection` 接受任意 feature_set 类）；
2. `geometry_kind`：实际几何族 vs 端口（unknown 放行）；
3. `crs_class`：实际 CRS 经 crs_safety 分类 vs 端口要求类；
4. `unit`：声明单位要求时词表比对（缺失 → `UNIT_UNVERIFIED`）；
5. `temporal`：时间语义存在性检查；
6. `required/nullability`：required 输入缺失/空 → violation；
7. `cardinality`：required 输出 0 要素/0 行 → `EMPTY_OUTPUT`；
8. `quality obligation`：义务链 precondition 类复验（复用 data_qualification 既有谓词）。

裁决：violations 空 → PASS；按义务 `on_violation`：block_method → BLOCKED；degrade_with_disclosure → PASS+披露；warn → PASS。PASS/阻断证据都落节点 binding 列。

data_input 节点绑定源：**复用 `workflow_instance._derive_bound_refs` 同一规则**（data_requirements 行 status∈{available,done} 且带 bound_ref；capability_hint→role 映射声明序首个胜出）[R1-M5]——不新造 role→ref 语义。

## 6. 复用指纹栈（fingerprints.py）[R1-M3/M4 硬化]

```
reuse_fingerprint = sha256(canonical({
  v: RUNTIME_SCHEMA_VERSION,
  package_fingerprint, node_id, algorithm_id,
  params: canonical(params),
  inputs: {port: {level, fp, content_revision}},   # 输入内容身份 + 必带 revision
  env_fp,
}))
env_fp = H(RUNTIME_SCHEMA_VERSION, WORKFLOW_COMPILER_VERSION,
           EXECUTION_PLAN_VERSION, geo_stack_digest,     # geopandas/shapely/pyproj importlib.metadata
           package.methodology_fingerprint,              # 方法论注册表指纹
           algorithm_registry_fingerprint)               # 算法注册表指纹（同 id 换实现 → 失效）
```

- 输入内容身份取 **live descriptor**（`get_ref_descriptor`，非 registry 冻结 metadata）[R1-M3]；级别：`content`（sha256，≤1MB 开启时）> `profile_digest`（内嵌 content_revision）> `shape`。
- **eligibility 硬规则**：`fingerprint_level == shape` → 只记录、不复用（宁假 miss，杜绝同 ref 原地覆写的假命中）；`content_revision` 不一致 → miss。
- eligibility（全部满足才复用）：owner_scope 相等且（owner 为 anonymous 时 session_id 同域 [R1-MINOR-5]）；package_fingerprint 相等；逐端口 input fp+revision 一致；artifact_ref 可解析且 ArtifactRecord status=**valid（stale/expired/superseded/failed 均不可复用** [R1-MINOR-4]）；节点 deterministic。
- 判定全程不物化载荷；ref 探测仅存在性检查。复用被拒/条目损坏 → 删条目 + 重算（自愈）。

## 7. GeoCompute 适配（adapters_geocompute.py）

- 确定性 category 映射：transform operation ∈ {buffer,clip,dissolve,overlay} → VECTOR_OPERATION；{filter}→FILTER；{aggregate}→AGGREGATE；{reproject}→REPROJECT；analysis 节点按算法描述符 output artifact 类型 raster 族 → INTERPOLATION/RASTER_OPERATION；无映射 → `NODE_NOT_EXECUTABLE`（BLOCKED + 披露，绝不假装执行）；output 节点 → MATERIALIZE + ARTIFACT_REGISTER。
- `ExecutionNode.dataset_fingerprints` 携带输入内容指纹 → geocompute 自身 `semantic_fingerprint` 天然跨实例去重 [R1-M8]；`parameters.idempotent=True`（durable 通道要求，graph.py:93）[R1-M8]。
- 工作流级幂等：dispatch 前查本实例 bindings——同 reuse_fingerprint 已 SUCCEEDED → 跳过（防重复注册输出）；幂等键证据记 `wf-node:{reuse_fp[:16]}`（**不含 instance_id** [R1-M8]，实例/节点身份在 evidence 侧）。
- 每工作流节点 = 单节点 ExecutionPlan（budget 继承服务端红线）；`execute_plan` in-process（调用方卸载线程）；取消：driver CancellationToken → engine。
- 重试：RetryPolicy max_attempts≤3、transient-only、有界退避 + jitter。

## 8. Harness 集成（边界保持；挂钩点 [R1-M5] 已核实）

- **plan_orchestrator 确定性合成块末尾**（plan_orchestrator.py:628-635 附近，`plan.workflow_v4` 编译成功后）：flag `GIS_WORKFLOW_RUNTIME`（默认开，fail-open）→ registry.register(draft) + service.instantiate。失败只日志。
- **session_plan.apply_tool_result 锁外**（函数返回前、session 锁已释放 [R1-C3/M5]）：flag 同上 → `service.record_tool_result`（capability→`cap:*` 节点绑定 + CAS；**单次尝试 + 1 次退避重试，绝不长持锁**）。
- **mapspec_mutations 路由 apply 成功后**（fail-open）：`service.record_style_change` → style 维 recompute（科学零触碰）。
- 执行驱动（driver.run）**不由 chat 路径自动触发**（`GIS_WORKFLOW_RUNTIME_AUTORUN` 默认关）；执行经显式 API——chat 集成只做事实记录，不改变会话执行行为。
- 不写 SessionPlan 行、不碰行状态机；实例是执行面事实。

## 9. 增量重算闭环（recompute.py）

1. `apply_changes(instance, changes)`：
   - **quiescence 门** [R1-C1]：存在 RUNNING 节点 → changes 入 pending_changes（≤16，超界拒绝）并返回 `deferred`；仅在无 RUNNING 时应用；
   - 维度映射 [R1-M2]：obligation/义务变化 → `dimension="data", target_kind="output"`（与 diff.py 同通道）；RECOMPUTE_DIMENSIONS 词表零改动；
   - V4 `compute_affected_subgraph`（§0.1 修复后，**消费完整 RecomputePlan 对象，绝不用 bounded dict** [R1-MINOR-6]）；
   - style-only（dimensions=={style}）：output 节点做呈现证据刷新（render-only），科学节点零触碰，不入科学 STALE oracle；
   - CAS 批量：plan.recompute ∩ {SUCCEEDED,READY,STALE} → STALE（补 `READY→STALE` 转移 [R1-C2]），留 change 指纹证据。
2. `run_incremental`：STALE 节点复用裁决 → 命中 STALE→SUCCEEDED（reuse evidence）；未命中 STALE→READY → driver 执行 dirty 子图。plan.reuse ∩ SUCCEEDED = 复用候选（从未执行的 PENDING/BLOCKED 不在复用语义内 [R1-C2]）。
3. 每次应用落一条 RecomputeDecision（changes 指纹、完整 plan 摘要、复用/重算集合、解释）——「为什么重算/为什么没重算」可答。
4. oracle（§14 修订）：STALE 标记集合 == plan.recompute ∩ {apply 时 SUCCEEDED|READY|STALE}；重算执行集合 == plan.recompute − 复用命中 − {BLOCKED,FAILED,PENDING,SKIPPED,RUNNING}；复用集合 == plan.reuse ∩ {SUCCEEDED}；style-only 独立呈现态裁决，不入科学 oracle [R1-C2]。
5. 完成边界补评 [R1-C1]：driver 每波次收尾（无 RUNNING 节点时）drain pending_changes 应用之。

## 10. 子工作流（H）[R1-M7]

- subworkflow 节点：引用 package_id；实例化惰性展开一层子实例（parent_instance_id/parent_node_id 反向指针）；深度 ≤3（visited_packages 存实例行，环 → BLOCKED `SUBWORKFLOW_DEPTH`）；**每 owner 活跃子实例 ≤32**（超界 BLOCKED 披露）；
- 义务：父子 chain 经 V4 `inherit_obligations`（via_subworkflow provenance）合并；
- 取消：父取消 → 子 cancel；子终态 → 父节点按其状态推进（FAILED→父节点 FAILED，error 附子实例 id）；
- 恢复传播：子实例孤儿复位由其自身租约管理；父 driver 在波次收尾检查子实例终态后再推进父节点。

## 11. API / 前端投影

新路由 `app/api/routes/workflow_runtime.py`（prefix `/workflow-runtime`，get_current_user + owner 域过滤；session 资源 require_owned_session）：

- `POST /packages/register`、`POST /packages/{package_id}/publish`、`GET /packages`、`GET /packages/{package_id}/versions`
- `POST /instances`（package + session）→ 投影
- `POST /instances/{id}/run`（deadline 默认 60s，有界；超时 typed 错误——不挂 HTTP 无界 [R1-NIT]）
- `POST /instances/{id}/cancel`、`POST /instances/{id}/changes`（≤16 条）
- `GET /instances/{id}`（节点状态/blocked/stale/reused/recomputed/义务/方法论/解释）
- `GET /instances/{id}/recompute-plan`（dry-run）

前端 minimal：`frontend/components/sidebar/workflow/runtime-inspector.tsx` + vitest 单测。

## 12. 安全 / 多租户

- 全表带 owner_scope（`owner_scope_for` 同域哈希）；读路径 owner 过滤（他人 404）；anonymous 域内复用加 session 同域约束 [R1-MINOR-5]。
- 输入界：changes ≤16、query ≤400 字符、instance id 格式校验；projection 全 bounded。
- 节点认领：READY→RUNNING CAS 写 claimed_by（run token）；外部 record_tool_result 对 RUNNING 且 claimed_by≠自身 的节点拒绝（双执行者协调 [R1-MINOR-7]）。
- 无秘密入库存（指纹/词表/计数）。

## 13. 资源包络（硬界）

| 项 | 界 |
|---|---|
| 实例节点数 | ≤64（继承 DAG cap） |
| transitions 环 | 节点行 ≤8；实例 decisions ≤16 |
| 每节点 attempts | ≤3 |
| 复用索引 | 每 owner LRU ≤128 |
| changes/次 | ≤16；pending ≤16 |
| 并发 dispatch | ≤4 |
| CAS/重试 | 完成类转移永不放弃；其余 ≤3 次退避重试 |
| 活跃子实例 | 每 owner ≤32 |
| 执行预算 | geocompute ResourceBudget 红线 + run deadline 60s 默认 |

## 14. 测试 oracle 与完成证明

- **差分 oracle（修订 [R1-C2]）**：见 §9.4；实现以修复后的 V4 纯函数 + 真实 compiled_form 形态 fixture 为对标。
- **确定性**：同输入两次实例化+执行 → 同复用指纹、同输出内容指纹、同 evidence 结构（时间戳除外）；恢复后 fp 失配 = 诚实 miss 自愈（显式场景，不默认成立 [R1-MINOR-8]）。
- **完成证明场景**（Epic §18）：synthetic 小数据 → register+instantiate+run（buffer→materialize→output 真执行）→ style change 零科学重算 → 参数 change 精确子树（含 output）重算 → 未受影响节点复用证明 → evidence 全程落库。in-process 本地可复现。

## 15. 明确不做

不改 V4 编译器语义/契约（§0.1 bug fix 除外）；不改 geocompute 调度内核/签名；不改 WorkflowEngine；不重做 UI 工作台；workflow package 不成为第二 artifact registry。

## 16. Rebase 集成协议

- 迁移撞号检查：rebase 后 `ls migrations/versions/ | grep 0034`；若 geocompute-v7 的 0034 已在 master → 本迁移重编号 0035/down_revision=0034，并跑单 head 断言；
- `db_model.py` / API 注册 / plan_orchestrator / session_plan / mapspec_mutations 挂钩做语义级合并；
- changed-scope + shared-contract 测试复跑。
