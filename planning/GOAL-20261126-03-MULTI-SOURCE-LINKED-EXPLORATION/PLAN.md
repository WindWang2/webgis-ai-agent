# PLAN — 03 多源空间联动探索、指标钻取与 Agent 交互分析

基线：origin/master `59eb3f527`，分支 `zcode/goal-multi-source-linked-exploration-20261126`。
证据基础：BASELINE.md + 三个只读探查报告（后端 G46 全链 / 前端面板 / 邻接能力）。

## 设计骨架（WP1–WP3 共同契约）

**多源计划模型（v3，向后兼容 v2）**
- `PLAN_SCHEMA_VERSION = 3`；`ExplorationPlan` 新增 `sources: List[MultiSourceBinding]`
  （每源：`source_id` 稳定短别名、`anchor: SourceAnchorV2`、`role: primary|context`、可选粒度标注）。
  v2 读取兼容：`anchor` 非空且 `sources` 缺失 → 视为单源 v2（primary = anchor）。
- `ChartSpec` / `CategoryCondition` / `RangeCondition` 增可选 `source_id`（缺省 = primary，
  v2 计划零迁移可读）；unknown source_id → typed 拒绝。
- **关系（links）**：引用已批准 G38 JoinPlan（`join_plan_id`）或显式声明空间关系
  （`kind: spatial_within`，左源点/右源面 等，走 data_join 同款 predicate 词表）。
  声明必须给出 cardinality（one_to_one/many_to_one/one_to_many）与 join key 对或空间谓词；
  不声明 cardinality 的 many-to-many 一律拒绝（防行数膨胀）。
- **组合语义**：`compile_combined_where` 扩展为 per-source 编译（按 source_id 分组，
  组内沿用现 AND-跨控件 / OR-同控件规则）；跨源过滤传播方向显式（primary→context 或
  context→primary 需在 link 上声明 `propagation`，默认无传播；禁止循环：links 成 DAG 校验）。
  跨源行选择 = 先按本源 where 过滤，再经 link key 半连接（semi-join）到对端，**不物化笛卡尔积**。
- **钻取（granularity）**：源绑定可声明 `hierarchy`（字段映射：child_field → parent_field，
  来自已有字段关系或用户显式指定；不从名称猜测）。drill-down/up = 在 primary 源上追加
  parent/child 字段条件并同步聚合粒度；无层级映射时 typed 拒绝 + 诚实说明（WP5 场景）。

**指标解释合同（WP2，新模块 `app/services/linked_exploration/metrics.py`）**
- 算子：`count / distinct_count / sum / mean / weighted_mean / rate`（rate = numerator/denominator
  双度量 + 显式分母字段；分母合计为 0/空/非数值 → 该组指标值 `unavailable` + 原因码，
  绝不返回 0）。quantile/median：仅当分组行数 ≤ 有界帽（如 10k/组、总量 ≤ 50k）时精确计算，
  否则 typed `METRIC_UNSUPPORTED_AT_SCALE` + 建议精确路径；不声明近似。
- 语义披露：每个指标结果带 `unit`（field_contracts 声明优先，否则 unknown）、`null_excluded`、
  `non_numeric_count`、`denominator_note`、`granularity`（计数粒度声明）、`precision: exact`。
- 面积加权：仅当源要素带有效几何且用户显式选择 `weight: area` 时启用；几何无效/缺失按
  `invalid_weight_count` 披露并排除，不静默。
- oracle：独立手算 fixture（WP5），不用生产函数自我对比。

**作用域传播与快照（WP3）**
- 保存/重开沿用 `ExplorationView` + plan JSON 权威（无需迁移；`source_ref/source_revision`
  冗余列语义固化为 primary 源，多源清单存 plan JSON）。v2 视图照常打开。
- selection 引用：scope 记录 = per-source `{anchor, where_digest, selection_handle|ids_ref}`；
  "刚才那组选中点" 走 `selection_history.resolve_selection_ref`（previous/epoch_seq），
  不可解析 → `SELECTION_REF_UNRESOLVED` + 最小澄清问题；epoch 过期 → 明确 stale，不假装可用。
- 并发：plan 保存走 view CAS；MapSpec apply 走 mutation_revision 链（P1-1 模式）；
  前端 per-source conditions_digest epoch 拦截迟到响应。

## WP 执行序列（每个 WP 内：复核现状 → 失败用例 → 最小实现 → 接线 → 正负验证 → commit → ledger）

### WP1 多源探索模型与语义连接（后端契约 + 组合语义）
文件：`app/services/linked_exploration/contract.py`（+新 `relations.py` 若需隔离）、
`tests/unit/test_ms_contract.py`（新增）。
1. contract v3：sources/source_id/links/hierarchy 模型 + v2 兼容读取 + typed 拒绝面。
2. per-source where 编译 + link DAG 校验 + 半连接求值骨架（纯函数级）。
3. 失败用例先行：循环传播拒绝、隐式 M:N 拒绝、unknown source_id 拒绝、v2 读取不破。

### WP2 指标确定性计算（metrics 模块）
文件：新 `app/services/linked_exploration/metrics.py`、`tests/unit/test_ms_metrics.py`。
1. metric spec 模型（op/value_field/denominator_field/weight/unit）+ 求值器（fsum 语义对齐 buckets）。
2. 分母治理（零/空/非数值 → unavailable + 原因码）、quantile 有界帽、weighted area 需几何显式开启。
3. 每个算子 ≥1 手算 oracle 用例；unavailable 路径用例。

### WP3 有界执行与快照并发（service 编排）
文件：`app/services/linked_exploration/service.py`（per-source freeze/preview/计数/handle）、
新 `relations` 求值编排、`tests/integration/test_ms_service_journey.py`（新增）。
1. 多源 preview：逐源 freeze（同代校验逐源 stale_reason）→ per-source where → link 半连接 →
   per-source match_count/facets/metrics → per-source handle。
2. 保存/重开：v3 plan 存取、逐源锚核对、v2 兼容重放；apply 组件 per-chart sourceRef 已有通道复用。
3. 反例：并发保存 CAS、跨会话 404、迟到 generation 丢弃、链接源 revision 推进 → per-source stale。

### WP4 Agent 工具 + 前端交互
文件：`app/tools/linked_exploration_tools.py`（新工具/扩参）、
`app/api/routes/linked_exploration.py`（薄接线）、
`frontend/lib/linked-exploration/`（contracts/condition-store 扩展）、
`frontend/components/linked-exploration/exploration-panel.tsx`（多源选择器/关系/指标/钻取面包屑/scope chips）、
locales 局部追加、`tests/unit/test_linked_exploration_tool_annotations.py` 扩展、前端 Vitest。
1. Agent 面：`webgis_exploration_inspect/preview` 扩展多源入参；新增
   `webgis_exploration_metric`（指标解释计算）、`webgis_exploration_drill`（层级钻取/返回，
   无层级 → 诚实阻塞）、selection_ref 指代（previous/epoch）支持。
2. REST：preview/metric 请求模型扩展 + **先补 HTTP 路由层测试网**（现零覆盖）。
3. 前端：多源 chips、关系与指标选择器、口径披露卡、双向联动（复用 chart→map 协议）、
   空态/加载/取消/stale。UI 与 Agent 走同一 REST/service。

### WP5 场景测试与性能
- 成都场景 fixture（区县小学点 + 区县人口表）：count 正确、每万人 rate 正确（手算 oracle）、
  关系缺失/年份不一致/重复键拒绝；选区→类别图→表格→地图→Agent 追问闭环
  （REST + 确定性 stub Pi journey）；区县→街道无层级 → 诚实阻塞。
- 兼容：v2 单源视图重开/保存/导出回归（现有 77 测试不退化）。
- 性能：1k/50k/250k 确定性 fixture，记录 p50/p95、峰值内存、响应次数、handle 大小。

### WP6 独立 review 与交付
- A（科学语义+oracle）、B（快照/并发/授权/预算）、C（真实 journey/前端/性能）三只读审查
  串行；P0/P1 必修，P2 逐项处理；修复后复审。
- 全量本域回归 + 前端 typecheck/ESLint/Vitest；openapi 快照对账（新路由注册）。
- PR：`gh pr create --base master`，正文含旅程/架构/测试/限制/回滚。

## 完成判定（Oracle）

1. ≥2 类多源任务闭环真实通过（REST + Agent 工具双入口）。
2. 每个统计结果可解释：来源锚、粒度、分母、精确性、选择范围（披露字段存在且测试断言）。
3. 并发/过期/取消/关系不可信场景 typed 拒绝或诚实 stale，测试覆盖。
4. 既有 G46 测试（77）全数通过；本域 Ruff 干净；前端本域测试绿。
5. 独立 review 无未解决 P0/P1；PR 已创建并附真实证据。
