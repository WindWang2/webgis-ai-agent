# TEST EVIDENCE — 03 多源空间联动探索

## R0 基线（2026-11-27，worktree @ 59eb3f527）

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest tests/unit/test_g46_contract.py tests/unit/test_g46_filter_semantics.py tests/unit/test_g46_buckets.py tests/unit/test_linked_exploration_tool_annotations.py tests/integration/test_g46_service_journey.py -q` | **77 passed** (106.65s) |
| `.venv/bin/python -m pytest tests/integration/test_pi_g46_linked_exploration_journey.py -q -rs` | 1 skipped — `journey is opt-in (PI_JOURNEY_E2E=1) — explicit skip, never silent-green` (82.75s) |
| `.venv/bin/python -m ruff check app/services/linked_exploration/ app/tools/linked_exploration_tools.py app/api/routes/linked_exploration.py tests/unit/test_g46_*` | All checks passed |

## 前端基线（待跑，WP4 前补）

- [ ] `frontend` 本域 Vitest（condition-store/contracts/exploration-panel 现有测试）
- [ ] tsc / eslint 本域

## 探查证据矩阵摘要（全文见会话记录；要点存档）

### 后端 G46（app/services/linked_exploration/）
- 契约：`PLAN_SCHEMA_VERSION=2`；`ExplorationPlan(anchor: SourceAnchorV2 单数, charts≤8, conditions≤16, extra="forbid")`；
  `ChartSpec(kind∈category|range, aggregation∈count|sum|mean|min|max, layer_id 仅披露)`；
  条件编译 `compile_combined_where`（跨控件 AND、同控件 OR、facet 自排除 exclude_chart_id）；
  MapLibre typed 投影 `where_to_maplibre`（contains/startswith → FILTER_NOT_COMPILEABLE 拒绝）。
- 服务：`inspect_source / preview_conditions(count_matching + facet/full 两套 + where-scope handle) /
  save_view(重冻结核锚,plan≤32KB) / list/get/update(CAS STALE_VIEW_REVISION)/delete / open_view(revalidate,
  源推进→stale SOURCE_ADVANCED 不无声换数据) / apply_view_to_mapspec(patch_component 链式 CAS+失败回滚)`。
- 冻结：`freeze_source_typed` → bivariate `freeze_source`（session_lock + rev0/rev1 ABA 拒绝 + 250k 硬帽）。
- 授权：REST `require_owned_session`；Pi trusted_session_id 由 registry 注入；源级 `get_ref_data_shared`。
- 求值：`feature_pages.evaluate_where`（eq/ne/in/between/contains/startswith/gte/gt/lte/lt + and/or/not；
  typed identity bool≠数字、int==float、str 严格；null 语义显式）。
- 分桶：`buckets.py`（typed 分组、other≤64 披露、invalid_key_count、denominator_note、STATS_SCAN_LIMIT=250k）。
- digest：`plan_digest / conditions_digest`（前端 epoch 拦截）。

### 单源硬编码点（多源必碰）
S1 contract.py:395 anchor 单数；S2 SourceAnchorV2 三元组；S3 Chart/Condition 无 source 字段；
S4 compile/digest 单 where；S5 service 单 ref_id；S6 chart_statistics 单 features；
S7 export 单 anchor 找层；S8 ExplorationView 冗余单源列；S9 evaluate_where 单要素；
S10 selection handle 单 (session,ref,revision) 绑定（per-source 多 handle 天然可行）。

### 邻接能力（只读复用）
- `selection_handles.py`：bbox|where|ids、revision 绑定 410 stale、TTL600、MAX_SCOPE_IDS=200、
  `materialize_selection`（物化新 ref）、count ≤250k 精确。
- `gis_situation/selection_history.py`：6 代 SelectionEpoch、`resolve_selection_ref("previous"|epoch_seq)`、
  不可解析 → None（最小澄清）；ids ≤50 有界；`ids_truncated` 权威披露。
- `data_join_authoring/`：JoinPlan(mode attribute|spatial_point_in_polygon、cardinality one_to_one|
  many_to_one|one_to_many、keys≤8、aggregates≤16、预算+deadline、preview=权威诊断 gate、CAS stale_plan_revision)；
  **与 linked_exploration 互不引用 —— 本线以「关系引用 JoinPlan」建立接缝**。
- 聚合算子现状：count/sum/mean/min/max（feature_aggregate/buckets）；+avg/stddev/distinct_count
  （data_fabric AggregateDriver）；**缺 weighted mean / rate(ratio) / quantile(median) —— WP2 新增于本域**。
- `field_contracts/`：unit 声明性元数据（不换算）；FIELD_TYPE_NAMES 六类；role 推断在 data_fabric（启发式）。
- 行政区层级：仅省→地级静态表（`spatial_guardrails/data/admin_divisions.py`）+ verify_code parent_chain；
  **无县级表、无通用层级服务 —— 钻取层级必须来自用户显式映射或已有字段关系，否则诚实阻塞**。
- MapSpec：`patch_component(expected_revision) → mutation_revision` 唯一写入口；无独立 set-filter intent。

### 前端（frontend/）
- 面板：`exploration-panel.tsx`（757 行）右抽屉；本地 state 机 idle/loading/ready/error(+generation)；
  竞态五重拦截（seq+epoch+generation+Abort+250ms 去抖）；结果表优先 selection_handle 分页。
- condition-store 单源单例（sourceRef/layerId 单值）→ 多源需 per-source 分组扩展；
  `compileCombinedWhere`（contracts.ts:95）为后端同源镜像。
- 四条过滤通道汇于 `map-panel.tsx:347 selectionFilters`（按 layerId 键 → adapter AND 进 sublayer filter，
  V4 filter 快路径）—— 多源多表达式天然支持。
- 高亮=transient selection（不写 MapSpec）；作者条件=探索条件（面板关闭不清）；apply=服务端组件写入。
- preview 回执 `selection_handle` + `materializeSelectionHandle` 可物化新 ref（钻取产物路径）。
- Agent 呈现缺口：`webgis_exploration_*` 不在 tool-call-card TOOL_NAMES/结果卡白名单（裸 JSON 行）；
  `useLinkedExplorationStore.openWith(ref,layerId)` preset 已有但面板未消费 —— WP4 接线点。
- i18n：`messages/{zh-CN,en-US}/linkedExploration.json` 56 key 对齐；namespace 注册于 lib/i18n/messages.ts。
- a11y：Mount 有 role=dialog 但无 Escape/焦点陷阱（useDialogFocus 可复用）；共享 EmptyState/LoadingState/
  InlineNotice 可替换裸 p。
- 测试：Vitest jsdom；组件测试模式 = mock transport.apiFetch + `__G46_LAYERS__` + 真实 store；
  **无任何 G46 e2e journey**（Playwright 27 个 journey 先例可循）。

### 测试缺口清单（评审输入）
1. HTTP 路由层零直接测试（G46 遗留）—— WP4 先补网再扩多源模型。
2. delete_view 无测试；export 工具 handler 无行为测试；list include_archived/limit、
   _bounded_preview >8KB 降级、open revalidate=False、inspect >20k 抽样、STATS_OVER_LIMIT 未测。
3. e2e journey 缺位。

## 新增测试记录（执行期追加）

（WP 执行时在此登记每批测试命令与结果）
