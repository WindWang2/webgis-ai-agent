# GIS Semantic Workflow V3 —— 任务本体、Typed Planning 与最终地图确认闭环

> 状态：已实现（feature branch `feat/gis-semantic-workflow-v3`）
> 前置：[Workflow 架构（ADR-0101）](./architecture.md) · [Recipe DSL V2](./recipe-dsl.md)

## 一句话

V3 把系统从「keyword 匹配 Recipe」升级为完整语义管线：

```text
用户需求 → GIS 任务语义（ontology）→ 数据理解（qualification）
        → 科学可行性（obligations）→ 算法候选 → 工具候选
        → 制图候选 → 评分（multi-candidate planning）
        → 可执行 DAG → 运行 → 地图状态检查（final map verification）
        → 确定性修复 → 最终输出（verified / degraded / failed）
```

## 一、GIS Task Ontology（`gis_ontology.py`）

版本化（`ONTOLOGY_VERSION = 3`）、可机器消费的任务本体：46 个
`TaskDescriptor` 覆盖 9 个域（distribution / spatial_statistics /
interpolation / network / terrain_hydrology / remote_sensing / sar /
decision / cartographic）。每个任务声明：

| 维度 | 字段 | 消费方 |
| --- | --- | --- |
| 数据需求 | `required/optional_data_roles`、`geometry_expectations` | data qualification |
| 方法引用 | `common_capabilities`（⊆ CapabilityRegistry） | candidate planning |
| 产出契约 | `output_artifacts`（⊆ ArtifactTypeRegistry） | 完成契约 |
| 制图期望 | `cartographic_expectations`（⊆ MapModelRegistry） | 制图解析 |
| 歧义声明 | `ambiguity_rules`（默认解释 + 备选 + 消歧依据） | 用户披露 |
| 回退策略 | `fallback_strategy`（preferred/degraded/minimal/blocked 四层） | fallback V3 |
| 词表联动 | `family_triggers`（⊆ intent.TaskType） | 保守任务升级 |

红线：

- **「分布」不预设热力**——`distribution.point_distribution` 的歧义规则
  显式声明制图形态由数据资格决定（点图/格网/比例符号/热力均为候选）；
- **planned 诚实**——能力未注册的任务（`sar.coherence`、
  `sar.temporal_analysis`）标注 `semantic_status="planned"`，路由与规划
  不得当作 native；
- 全部引用经 `registry_validation.validate_gis_library` 与单一事实源对账，
  悬空引用 fatal；
- 确定性匹配（`match_intent`）：task family +3 / analysis intent +2 /
  cartography +1 / 关键词 +1 / geometry +0.5；同分时**直接证据（关键词
  命中）优先于间接信号**，再按 task_id 字典序。

### 保守任务升级（escalation）

`escalation_target` 是本体驱动的专业语义入口，红线三条：

1. 源任务仅限通用族（`distribution_overview` / `simple_view`，含口语
   包装规则命中的 simple_view）——专业性规则特异性更高先行命中，不受影响；
2. 证据门槛：query 必须命中本体任务的**专业关键词**（泛表述「分布」
   永不触发）；
3. 目标族必须**无 V1 seed 保护**（不在 `RecipeRegistry.v1_served_tasks`）
   ——「地理加权回归」升级 `spatial_autocorrelation`，而「地表覆盖分布」
   保持 raster seed 产品族零漂移。

升级发生在 `resolve_map_request_intent` 尾部（planner / compiler / 工具 /
评测四条路径共享单一入口），记录 `matched_rules` 与 `assumptions` 可审计。

## 二、Typed Data Qualification（`data_qualification.py`）

对 workflow 的每个数据角色给出机器可读的五态裁决：

```text
eligible（事实满足）→ transform_required（自动可修复变换）→
degraded（可降级但必须披露）→ blocked（结构性不可能）→
unknown（画像无事实；unknown ≠ 不满足）
```

- **科学性检查委托算法层**：`numeric_field_required` /
  `projected_crs_required` 经公开 `evaluate_precondition` 评估，用
  `facts_used` 区分「无事实 PASS」与真 PASS（单一事实源，不重复科学语义）；
- **结构性检查在资格层**：几何类别、字段在场、空值率（>0.5 不可靠）、
  样本量、分母强证据（`population/pop_/人口/household/户数`；弱提示
  `total/count` 永不满足）；
- **RemediationStep 显式修复声明**：`reproject / repair_geometry /
  derive_field / aggregate / normalize / filter_null / filter_nodata /
  resample`（与 SpatialRepairPipeline ops 同源）；`auto_applicable=True`
  仅限有确定性实现的操作——存在不可自动修复项时裁决为 degraded 而非
  transform_required（不虚构可修复性）；
- external 获取通道（data_fabric / user_upload）规划期不可证伪 →
  unknown，不假设缺失也不假设存在；
- compiler 阶段 7（`qualify_data`）消费；auto-applicable remediation
  物化为 `source="data_qualification"` 的显式 transform step。

## 三、Recipe 分层组合（`workflow_families.py`）

覆盖靠**组合生成**，不是无上限平铺复制：

| 层 | 数量 | 来源 |
| --- | --- | --- |
| Atomic Recipe | 164 | 既有 RecipeRegistry（行为不变） |
| Workflow Family | 152 | RecipeRegistry 的**确定性投影**（按 domain × workflow_family 聚簇；seed 自成单成员 family） |
| Composite Recipe | 12 | 审定组合：base 产品族 + 条件并入 supporting 层 |
| Scenario Template | 7 | 审定场景：主体词 + 本体任务双信号激活 |

- Family 聚合成员的 capability / 数据角色 / 义务 / 制图 / 关键词；本体
  任务自动链接（成员 intent_tasks ∩ 任务 family_triggers）；成员变化 →
  投影变化（指纹可感知），零第二事实源；
- Composite 的 supporting 层带**数据资格条件**（如密度筛查链只在统计
  前提 eligible 时并入显著性热点层——防「任何分布都上热点」）；组合层
  不携带算法事实（无 capability/obligation 字段），只声明「何时并入谁」；
- Scenario 模板必填 `minimal_disclosure`（minimal 兜底披露）与制图候选
  序（数据资格裁决取位）；`scenario.school_distribution` 即「成都小学
  分布情况」示范：候选含点图/行政聚合/格网/热力，形态由数据定；
- 分层实体合计 **335**，落在 300–500 参考区间；全部引用经
  `validate_gis_library` 收口。

## 四、Multi-Candidate Planning（`plan_candidates.py`）

从「keyword 路由取 top-1」升级为「生成 → 过滤 → 评分 → 选择 → 留痕」：

- **候选池**：语义路由 top-N recipe + 触发的 composite（本体任务/显式
  形态信号）+ 激活 scenario 的场景变体；
- **科学合法过滤**：复用 `check_eligibility` + 义务评估 + V3 资格裁决；
  工具可用性委托 `AlgorithmResolver`；
- **九维确定性评分**（§12 对齐）：semantic_fit / data_fit /
  scientific_validity / cost / latency / determinism / user_intent /
  output_quality / fallback_quality；
- **路由权威红线**：语义路由名次（含 seed 资历）以 0.5 权重并入
  semantic_fit；composite/scenario 的 base 与 intent task 无关且非语义
  top-1 时不得成为选择（`base_not_task_relevant`）——组合层是增强，
  不是路由覆盖（「各区小学数量」仍归 `administrative_choropleth`）；
- **零漂移改写**：仅当语义 top-1 被科学阻断而最优候选可行时改写计划
  承载 recipe；
- **完整候选集留痕**：selected + rejected + 逐候选拒绝理由进入编译产物
  （`compilation.plan_candidates`），供 trace / replay / evaluation。

## 五、Fallback V3（`fallback_v3.py`）

四层语义回退的统一确定性裁决：

| 层 | 语义 | 降级分类（复用 DOWNGRADE_CLASSES） |
| --- | --- | --- |
| preferred | 数据充足、能力 native | equivalent |
| degraded | 数据不完整仍可近似 | approximation / proxy |
| minimal | 仅安全、真实、不误导的描述性输出 | degraded |
| blocked | 不能科学执行，声明缺什么 | not_allowed |

- **planned 诚实红线**：`uses_planned_capability` 或本体任务 planned →
  tier 不得 preferred，强制降档 proxy + 强制披露；
- **无事实诚实缺省**：规划期无 profile → preferred（unknown ≠ 不满足，
  不虚构降级）；
- 事实在手且无任何可执行路径 → minimal + 强制披露（不得静默）；
- 裁决进入编译产物与完成契约（`fallback_tier / fallback_downgrade_class /
  fallback_disclosures`），finalize / verdict 消费同一份证据。

## 六、Final Map Verification（`completion/map_verification.py`）

任何「要求成图」的 workflow 在 finalize 前必须给出最终地图状态裁决。
既有 finalizer（validate → repair → revalidate ≤2 passes + render
observation）之上，V3 补齐三组验证缺口：

| 检查 | 语义 | 级别 |
| --- | --- | --- |
| 图层顺序 | 结果层不得压在上下文（reference）层之下 | warning |
| 结果越界 | result bbox 与观察视口相交性（仅双方事实在场时判定） | warning |
| 陈旧覆盖层 | 仅死 ref / superseded ref 的非计划图层告警（用户有效图层零误伤；basemap 无 descriptor 不误判） | warning |

四态裁决（`final_map_status`）：

- `verified`：complete + 观察验证通过 + 无 V3 发现；
- `verified_with_degradation`：成图但带披露（stale/unknown 观察、警告）；
- `failed`：结果层缺失 / 渲染缺口 / 基础终验 failed；
- `unknown`：无计划图层（无可验证面）。

**强制终验门（final_gate）**：turn 收尾（`agent_settled`）传入
`final_gate=True`——已存裁决非 READY（needs_repair / blocked）时绕过
幂等门强制 diagnose → repair → re-observe → re-verify；READY 会话保持
幂等跳过（happy path 零开销）。未解决会话不得靠幂等门滑过 turn 边界。

裁决与发现进入 `MapCompletionResult`（序列化 / chapter 持久化块）与
runtime trace（`final_map` 维度）。

## 七、Conformance Corpus V3 与 Evaluation Metrics

- 语料 **3240 → 20088** 结构化案例：59 个审定语义族 × 双语 × 12 scope ×
  9 句式（确定性生成，id 稳定可复现）；新增 12 族含本体升级族（路网
  中心性 / TWI / 空间回归）与 comparison / OD / SAR / 暴露等语义维度；
- 资源红线：全量运行（约 20s）挂 `perf` 标记显式执行（#664 约定，
  unfiltered 自跳过）；默认车道为确定性分层抽样（543 案例）+ 领域切片；
- 新增 evaluation metrics（opt-in 契约，`GISBenchmarkCase` 声明即断言）：
  `ontology_top1_correct` / `recipe_selection_correct` /
  `no_false_professional_analysis` / `unnecessary_tool_count` /
  `qualification_states_correct` / `fallback_tier_correct` /
  `planning_deterministic`。

## 八、Compiler 管线（V3 后 15 阶段）

```text
 1 normalize_intent           9  evaluate_obligations
 2 map_task_ontology (V3)    10  resolve_algorithms
 3 resolve_task_family       11  compute_transformations（含 remediation 物化）
 4 resolve_scope             12  resolve_cartography
 5 resolve_recipe_candidates 13  produce_map_product_plan
 6 resolve_data_roles        14  produce_completion_contract（含 fallback_tier）
 7 qualify_data (V3)
 8 plan_candidates (V3)      （7a plan production 供 8-13 消费）
```

编译产物新增有界字段：`ontology_matches` / `data_qualifications` /
`plan_candidates` / `fallback_resolution`。全部纯函数、零 LLM / 零 I/O、
同输入同输出、序列化 < 64KB。

## 九、并发边界（本分支不做什么）

- 不实现缺失的 GIS 算法（capability 词表之外的需求保持 planned）；
- 不实现 Cartography renderer（仅通过 MapModelRegistry / 组件契约消费）；
- 不重写 Pi model runtime 与 GeoCompute（仅通过 descriptor / contract
  使用）。

## 十、已知限制与后续工作

1. **RasterProfile 无生产方**：栅格画像契约（dims/resolution/bands/
   nodata）已声明（`dataset_profile.py`）但无生产调用点——栅格类数据
   资格检查按 unknown 诚实缺省；
2. **GWR / 异质性等 recipe 缺口**：空间回归查询升级到正确任务族，但族内
   尚无 GWR 专属 recipe（corpus 锁定当前诚实行为，已知限制）；
3. **composite supporting 层未进入执行计划**：候选集与组合证据已进入
   编译产物，supporting 层的工具编排是 planner 的后续工作；
4. **stale overlay 只告警不自动清理**：误删用户内容的风险大于残留成本，
   自动隐藏需要用户表达优先的守卫设计；
5. **units / 空间重叠检查**：画像无单位事实，units 类资格检查尚未生效
   （corpus 的 wrong-units 维度在 lib 层锁定）。

## 深读

- [GIS Task Ontology 源码](../../app/services/gis_harness/gis_ontology.py)
- [Data Qualification 源码](../../app/services/gis_harness/data_qualification.py)
- [Workflow Families 源码](../../app/services/gis_harness/workflow_families.py)
- [Plan Candidates 源码](../../app/services/gis_harness/plan_candidates.py)
- [Fallback V3 源码](../../app/services/gis_harness/fallback_v3.py)
- [Map Verification 源码](../../app/services/gis_harness/completion/map_verification.py)
- [Workflow Catalog（自动生成）](./workflow-catalog.md)
