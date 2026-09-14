# ADR-0193: What-If 反事实假设推演分支管理器（Scenario Branching v1）

- 状态: Accepted
- 日期: 2026-09-15
- 关联: ADR-0183（Map Mutation Transactions）、ADR-0072（GISWorldState 读模型）、
  ADR-0099（Map Product fork_version）、ADR-0058（revision CAS）、ADR-0120（MapSpec 契约 schema）、
  ADR-0104/0118（前端 Comparison View / Workbench）
- 分支: `agent/09-counterfactual-whatif-scenario-branching`

## 1. 背景与问题

当前会话世界状态（GISWorldState / MapSpec）是**单线程线性递增**的：每次
mutation 都覆盖前态（auto checkpoint 只保留回滚点，不保留平行世界）。用户提出
反事实问题——「如果在这个路口新建立交桥，早高峰拥堵如何缓解？」「若把该地块
绿地改为商业中心，周边学区与交通负荷如何变化？」——时，系统无法：

1. 从同一现状（Baseline）**安全分叉**出多个干预方案（Proposal A/B）；
2. 在不污染主游戏面（mainline world state）的前提下对方案做任意修改/回滚；
3. 对多方案做**空间几何差分 + 属性指标差分**，产出可对比的增量（Delta）；
4. 自动生成结构化效益对比矩阵与**处方性**决策建言（prescriptive advice）。

既有最近邻 `app/tools/what_if_simulate.py`（V2 Data-Grounded）是**单次**评估工具：
给定 ScenarioSpec 直接产出 SpatialDecisionResult，无世界状态分叉、无多方案持久
分支、无方案间互不干扰保证。本 ADR 不替代它，而是在其上叠加**状态级**分叉层。

## 2. 决策

### D1. 分支隔离模型 = 会话命名空间（branch-scoped session namespace）

`ScenarioBranch` 的存储与事务隔离**不新造机制**，复用 ADR-0183 的全部事务机器
（per-session 分布式锁、mutation revision CAS、幂等去重、auto checkpoint、原子
落盘、失败回滚、user-wins 守卫、provenance）。实现方式：

- 每个分支映射到一个**派生会话 id**：`{parent_sid}__wif_{branch_id}`。
  分隔符选择 `__wif_`（双下划线 + 词元）：Windows 文件名禁止 `:`/`/` 等字符，
  且 `_session_storage_entries` 的合法会话名正则 `[A-Za-z0-9][A-Za-z0-9._-]*`
  天然放行；派生 id 落在 BASE_STORAGE_DIR 之下、经过既有 `resolve()` 越界检查。
- 分叉（fork）= 读取父会话权威 MapSpec（`mapspec_store.get_mapspec`）→ 深拷贝
  materialize 到分支会话（`store.save_mapspec(branch_sid, spec, mutation_revision=1)`）
  → 在分支 map_state 写 `_whatif_fork` 存证（fork_revision、fingerprint、时间）。
  Fork 是快照物化，不是 agent mutation —— 不走 intent 分发，但**必须**留 fork 存证。
- 分支上的后续干预（intervention）一律经 `apply_gis_mutation(branch_sid, intent,
  origin="agent", actor="whatif", ...)` —— 守卫/事务/检查点全量继承。
- 分支修订计数、dedup 表、锁作用域、provenance 环全部随派生会话 id 天然隔离：
  在分支 A 上回滚或修改，**不可能**影响分支 B 或父会话（不同 Redis hash key、
  不同磁盘目录、不同锁）。这是结构性保证而非约定式保证。

### D2. 分支注册表 = 父会话 map_state 键 `_whatif_branches`

`ScenarioRegistry`（branch_id → `ScenarioBranchMeta`）存父会话
`map_state["_whatif_branches"]`，随 session_data 后端（Redis/内存）持久化。
元数据字段：`branch_id`、`title`、`hypothesis`（反事实问句）、`fork_revision`、
`forked_at`、`baseline_fingerprint`（fork 时父 spec canonical SHA256）、
`branch_session_id`（派生 id，由纯函数派生，存档仅为披露）、`status`
（active/archived/discarded）、`intervention_count`、`last_revision`、
`last_intervention_summary`。注册表写入持父会话锁
（`session_lock_registry.lock(parent_sid)`）—— 与 fork/删除的 check-then-act 原子。

约束（v1 有界）：每会话并发活跃分支 ≤ 5（`MAX_ACTIVE_BRANCHES`）；branch_id
词表 `[A-Za-z0-9_-]{1,32}`；同 baseline fingerprint 的分支可并存（对比正是目的）。

### D3. 空间 Diff 算法标准

`spatial_diff_engine.py` 产出两级差分，均为**确定性纯函数**（同输入同输出）：

1. **几何差分**（GeoDiff，逐 layer_id，配对 baseline vs branch 同名图层）：
   - 要素级配对键：feature `id` 属性优先，缺失时用 WKT 规范化几何 + 类型
     组合键。产出 `added / removed / modified / unchanged` 四类；
   - 面积/长度/计数增量（shapely 精确计算，投影校正由调用方数据负责，
     引擎内一律按 EPSG:4326 → Web Mercator 面积系数或声明 CRS 处理并披露）；
   - 缓冲区覆盖差分：对服务设施类要素（point）以 `service_radius_m` 生成
     baseline/branch 覆盖多边形，差集即「新增覆盖/失去覆盖」区域；
   - 输出显式对比图层（diff overlay FeatureCollection）：每个变更要素携带
     `diff_kind`（added/removed/modified）与 `impact_sign`
     （`positive`/`negative`/`neutral`）→ 前端按绿色/红色/灰色渲染
     （红=负面恶化，绿=正面改善）。
2. **属性指标差分**（MetricDelta，复用 `spatial_decision.models.MetricDeltaV2`
   契约，含 GIS-03 语义）：`baseline/simulated/delta_abs/delta_pct` 可空；
   **无真实基线证据的指标必须保持 None + evidence_gap_note，禁止编造默认值**。
   v1 内置确定性代理模型（`model="proxy:v1"`，常数与公式全部内联披露）：
   - `service_coverage_population`：人口栅格/面图层 ∩ 设施服务缓冲 → 覆盖人口；
   - `facility_count` / `green_area_m2` / `road_length_m`：直接几何聚合；
   - `road_capacity_index`：新增道路长度 × 车道容量系数（proxy），
     阻抗差异以路程长度变化近似披露（不做黑箱交通分配）。
   几何差分与指标差分共享同一配对结果（不重复配对）。

### D4. 处方性建言 = 确定性核 + 可选 LLM 叙述层

`prescriptive_advisor.py` 双层结构（复用 `gis_harness/intent_semantic.py` 范式）：

- **确定性核**（永远执行）：多方案指标矩阵 → 目标方向加权（maximize/minimize）
  → Pareto 非支配集 → ROI 敏感度（Δ指标% / Δ投入代理，投入以新增要素几何量
  为代理）→ 实施优先级排序（影响量级 × 置信 / 实施成本代理）。
  缺基线指标按 GIS-03 剔除出排序、不参与支配判定。
- **LLM 叙述层**（可选，绝不阻断）：`llm_available()` 守卫 → 失败或不可用时
  `mode="deterministic"` + `degraded_reason`（`llm_unavailable` / `llm_output_invalid`
  / `llm_error`）→ 因果链、建议措辞由确定性模板生成。LLM 路径经
  `chat/llm_client.call_llm` + `resolve_llm_config(ModelRole.SPATIAL)`，
  prompt 内嵌 JSON schema + fence 剥离 + pydantic 校验，校验失败降级，绝不抛出。
- 报告契约：`comparison_report.py` 生成**结构化对比矩阵**（JSON +
  中文 markdown 双面；markdown 管道表格：指标 × 方案，缺基线渲染「—」，
  对齐 `spatial_decision/report_integration.py` 先例）+ 处方性建言章节
  （推荐方案、因果链、ROI 敏感度、实施优先级）。

### D5. 前端协议映射 `scenario_mode`

MapSpec 契约**版本化 additive 演进**（对齐 ADR-0120 W2/W3 先例）：

- `MapSpecDocument.scenario_mode: Optional[Literal["split_view","swipe_compare"]]`
  （顶层、可选、缺省 None = 非推演视图）；
- `KNOWN_VERSIONS += "1.3"`、`LATEST_VERSION = "1.3"`、identity upgrader
  `("1.2","1.3")`（纯 additive，存量 spec 语义不变；spec `version` 字段仍不改写）；
- TS 镜像经 `python -m app.lib.cartography.ts_projection` 再生成；
- 写路径：新 intent `SetScenarioModeIntent(scenario_mode)`（None=退出推演模式），
  走 `apply_mutation` 全事务（COW 只拷 touched 分支；非法值 → is_error 拒绝）；
- 读路径：前端纯函数 `scenarioModeToComparisonKind`：
  `split_view → 'side-by-side'`、`swipe_compare → 'swipe'`，接入既有
  `workbenchSlice.enterComparison/exitComparison`（ComparisonView 组件已具备
  swipe/side-by-side 双形态，零新渲染管线）。

## 3. 备选方案与否决理由

| 备选 | 否决理由 |
|---|---|
| 单会话内多套 MapSpec 键（`mapspec@branchA`） | 绕开 store/engine 全部事务面：锁、CAS、checkpoint、dedup、provenance、user-wins 都要重写一遍；且 `save_mapspec` 的落盘布局按 session 目录组织，旁路键会成为第二个事实源 |
| Postgres 版本树（复用 MapProduct lineage DAG） | MapProduct 是发布态（正式版本），fork 语义声明过「无 branch 列、无移动指针」（ADR-0099）；把未定型的推演分支灌入发布谱系会污染 lineage 审计 |
| 内存对象树（branch = spec deepcopy 链） | 会话重启/TTL 过期即失忆；Redis 内存随分支数无界；与「磁盘优先、cache 不持有磁盘没有的 state」契约冲突 |
| 仅前端双地图对比（不动后端） | 无法回答指标差分问题；分支状态不持久，刷新即丢；多轮推演不可累积 |

## 4. 后果

**正向**：事务机器零重写（复用率最高的隔离方案）；结构性隔离保证（测试可证）；
MetricDeltaV2/GIS-03 语义与 V2 决策面无缝对接；前端复用既有 ComparisonView。

**代价/风险与缓解**：
- 派生会话 id 会出现在会话清理面之外（idle sweep 按目录 mtime 兜底回收；
  `delete_branch` 显式 `clear_session(branch_sid)` 联动磁盘清除 → 内存释放
  在 review 中核验）；
- 分支数量有界（MAX_ACTIVE_BRANCHES=5）防 Redis/磁盘放大；
- `scenario_mode` 1.3 需同步 pinning 测试与 TS 镜像（本 ADR 交付内完成）。

## 5. 与相邻模块的分工（防重复施工）

- `app/tools/what_if_simulate.py` / `spatial_decision`：单次评估与决策引擎保持
  不变；whatif 分支层调用其**模型与契约**（MetricDeltaV2），不重写评估工具。
- `gis_situation/diff.py`：情境事实差分（ SituationDelta）不动；空间几何差分
  在 `spatial_diff_engine.py`，两者词汇不混用。
- 前端 ComparisonView：渲染形态不变；scenario_mode 只是新的**入口协议**。
- `map_product_service.fork_version`：发布谱系 fork 不动；本 ADR 的分支是
  会话态推演分支，两套语义不互通、互不调用。
