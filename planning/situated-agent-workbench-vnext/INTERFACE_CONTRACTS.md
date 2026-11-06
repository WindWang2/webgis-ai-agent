# INTERFACE_CONTRACTS — situated-agent-workbench-vnext（R2 契约设计）

> 原则：全部 additive；不建第二真相；词表纪律（有真实前端钩子才收词）；
> 服务端验证（前端申报不是事实本身）；fail-open 不阻断 turn；纯函数 + 单调序。

## GAP → 切片映射

| GAP | 切片 |
|---|---|
| selection/AOI 不成为受控分析输入；queries 零调用 | S1 selection scope 验证投影 |
| 图表类别选择不上报；temporal 无 IR 词表 | S2 IR 词表补齐（有钩子的） |
| 「刚才那组学校」无句柄；选择史不可解析 | S3 selection history 投影 |
| turn→intent→map_op→observed 无一体化链；三类 diff 缺失 | S4 turn chain 投影 |
| 刷新后选择态不恢复（服务端有真相前端不读） | S5 恢复读取 + 工作台状态面 |
| 表现层 undo 对 agent 动作/批量效果不可达 | S6 undo 补齐（复用既有命令模型） |
| 12 组多轮操作无回归保护 | S0 characterization tests（先行） |
| 无多轮交互 journey | S7 journeys j10–j17 |

## S1 — AnalysisScope 验证投影（E1 核心）

**新文件** `app/services/gis_situation/analysis_scope.py`（纯函数 + IO 壳分离）：

```
ClaimedScope（纯派生，零 IO）
  ← 从 IR ledger 投影 + pre-turn selected_feature 快照合并：
  {kind: feature_ids|feature_single|aoi|viewport|none,
   dataset_ref?, layer_id?, feature_ids ≤50, feature_id?,
   aoi_digest?, claimed_at_seq, sources: [ir|pre_turn]}

validate_analysis_scope(session_id, claimed, *, store) -> ValidatedScope（IO 壳，session 锁外只读）
  验证链（每步失败给封闭 reason，不猜默认）：
  1. dataset_ref 可被本 session 的 store 解析（所有权）→ 否则 ref_unresolvable
  2. dataset_ref 的 content_revision 与 MapSpec 层引用一致 → 否则 revision_drifted（stale）
  3. feature_ids 存活（单要素读取面核对，≤50 有界）→ 否则 features_missing(部分/全部)
  4. 宿主层仍在 desired spec（复用 consistency 死选中语义）→ layer_removed
  5. claimed_at_seq 落后当前 mutation_revision 跨代 → superseded_selection（披露不阻塞，由 2/4 收敛）
  → {status: usable|stale|unusable, reason?, dataset_ref(已验证),
     feature_ids(存活), content_revision, layer_id, bbox?(数据集描述符派生)}
```

- AOI 面：canvas affordance 环的有界几何 → `claimed_scope(kind=aoi, geometry_bounded)`，
  分析工具消费 bbox/geojson 参数（temporal_tools.py:184、ogc_service_tools.py:248 先例）。
- **接线**：
  - `situation.analysis` 分区 additive fact `selection_scope`（claimed 纯派生，编译期零 IO，default_factory=unknown 模式）；
  - 新只读工具 `webgis_selection_scope`（新文件 `app/tools/interaction_scope_tools.py`，tier-2，
    statistics 域）返回 ValidatedScope——agent 拿到后显式传参给真实分析工具（不自动注入 TDS）。
- 红线：不修改 `gis_harness/tools.py`（#1972 占用）；`app/tools/__init__.py`/`categories.py`
  additive 一行注册（单独 commit）。

## S2 — IR 词表补齐（仅真实钩子）

| 新 kind | 钩子（已核实存在） | payload 白名单 | authority | 合并 |
|---|---|---|---|---|
| `chart_selection_changed` | chart-panel.tsx:252-271 onClick（publishSelection source:'chart'） | {layer_id, field, categories≤8, action: select|clear} | observation | none（点击语义） |
| `table_selection_changed` | table-panel.tsx:194-207 行点选 | {layer_id, feature_id?, selected_ids≤8, action: select|clear} | observation | none |
| `temporal_window_settled` | temporal-time-controller settle→commitUserTemporal | {mode, field?, extent?≤2, current?, window?≤2} + client_mutation_id 结局 | mapspec（mutation-linked） | replace_pending |

- 表格行=要素：也可复用 feature_selected——但来源语义不同（表格定位 vs 地图点选），
  独立 kind 保留来源身份（投影按 kind 归类来源），不改 feature_selected 语义。
- manifest 双闸再生：`contract_manifest.json` + `frontend/lib/interaction/contract-manifest.ts`
  + 前端 bus emit 接线（chart-panel/table-panel/temporal 三处 additive emit）。
- 不收录：图例分级过滤（map-panel 本地 state 无 settle 钩子，ADR-0227 纪律如实记录）；
  measurement_cleared（仍无钩子）。

## S3 — Selection History 投影（多轮指代证据面）

`app/services/gis_situation/selection_history.py`（纯函数，零 IO）：

```
project_selection_history(ledger_events, *, max_entries=6)
  ← 从环内 feature_selected/deselected/table_selection_changed 序列重建「选择代」：
  [{epoch_seq, kinds, layer_id, dataset_ref?, feature_ids, id_count, truncated,
    superseded: bool（被后续选择替代）, cleared: bool}]
```

- 消费：S1 的 ClaimedScope 可指定 `history_index`（-1=上一个选择集）→
  `webgis_selection_scope` 工具参数 `selection_ref: "current"|"previous"|<epoch_seq>`；
  「刚才那组学校」= previous，有据可解；不可用时 typed reason 不随机挑。
- 「上一张图」句柄：MapSpec generation 无命名实体——用 `mapspec.fingerprint` 前 8 位 +
  receipt 环披露，不建第二实体（诚实限制记录于 ADR）。

## S4 — Turn Chain 投影（E3 可重建链 + 三类 diff）

`app/services/gis_situation/turn_chain.py`（只读投影，IO 壳）：

```
project_turn_chain(session_id, *, journal_reader, store, mapspec_store, max_turns=8)
  ← turn_journal（map_mutated/tool_*/turn_ended）⋈ map_operation receipts ⋈ IR
  mutation-linked 事件 ⋈ _gis_provenance ⋈ _cartographic_observation.sequence：
  turns: [{turn_id, user_intent_digest, revision_span:[from,to],
           changes: [{actor: user|agent|system,
                      class: user_explicit | agent_proposed_pending | executed_observed,
                      operation_id?, effects[], outcome, observed_seq?}]}]
```

- 三类分类纯规则：origin=user mutation → user_explicit；receipt status=preview/pending →
  agent_proposed_pending；receipt applied + observation seq ≥ mutation revision → executed_observed。
- user-wins：同层冲突时 user_explicit 优先披露；迟到旧观察（observed_seq < 当前
  mutation_revision）不进 executed_observed（保持 pending/披露 stale）。
- 端点：`GET /chat/sessions/{sid}/turn-chain`（新 route 文件 `app/api/routes/situation_views.py`，
  require_owned_session；同文件并出 `GET .../situation` JSON 面——见 S5）。

## S5 — 恢复读取 + 工作台状态面（E3 恢复 / E4）

- 后端：`GET /chat/sessions/{sid}/situation`（同一新 route 文件）：
  {situation: GISSituation.to_dict(), inconsistencies[], selection_history[],
   turn_chain(摘要 ≤4 turns), scope: ClaimedScope}——全部只读派生，复用编译器，
  不建新存储。
- 前端 `frontend/components/workbench/situation-status-bar.tsx`（新组件 + hook
  `use-situation-projection.ts`）：
  - 显示：当前分析范围（scope.status：usable/stale/none + 计数）、选中对象（ids 数 + 来源
    map/chart/table）、结果来源（最近 receipt/verdict 指纹短码）、操作执行中（pending_mutations）、
    等待地图验证（observed_behind_desired）、失败可重试（attempted_not_applied + last_recovery
    处置）；
  - 刷新/重连：boot 时 GET situation → 状态面即时恢复（服务端真相，不依赖 localStorage）；
    地图高亮恢复：scope usable 时重放 selection filter（复用 selection-store 发布）；
  - a11y：role="status" + aria-live polite（变更播报摘要）；键盘可达（focus ring + 快捷键
    聚焦）；V4 token；zh/en i18n（no-raw-cjk 门）。

## S6 — Undo 补齐（复用，不造新原语）

- 核实点（实现期验证）：agent 起源的 map_operation commit 是否已录前端 undo 栈
  （undo.ts actor:'agent'）——若未录，在 receipt-bus 消费处 additive 录制
  presentationCommand/invert 命令（反向重放既有 CAS 通道，符合 ADR-0278「回退=再提交」）。
- 后端：map_operations 回执 additive `invertible: bool`（presentation 组可逆、
  remove_layer 不可逆——数据层撤销走 feature_edit 既有 undo）；不新增 undo 端点。
- GIS 算法 job / feature edit 撤销遵循各自既有策略（feature_edit /undo、recovery 重试指令）。

## S7 — Journeys j10–j17（E5）

| id | 旅程 | 档 |
|---|---|---|
| j10 | 点选学校→追问统计→换参数再分析（多轮指代 previous selection） | mock @smoke |
| j11 | 框选区域→「这一区域有多少学校」→AOI scope 真实约束 | mock @smoke |
| j12 | 图表类别选择→地图联动→IR chart_selection_changed 上报→agent 引用 | mock @smoke |
| j13 | 图层显隐→agent 建议恢复→user-wins 锁定（undo 栈） | mock @smoke |
| j14 | 刷新→服务端投影恢复状态面→旧选择 stale 披露 | mock |
| j15 | 后到旧 ACK/迟到观察不覆盖新状态（generation 门） | mock |
| j16 | 分析中途取消→恢复指令→重试成功 | mock（real 视环境） |
| j17 | 权限拒绝/空结果→诚实 not_evaluated/needs_user 披露 | mock |

## 词表与生成物纪律

- 每个 IR 新 kind：contracts.py + manifest.json + contract-manifest.ts + bus emit + 双侧
  漂移闸测试，同一 commit。
- 新端点：openapi snapshot 再生（API_SNAPSHOT_UPDATE=1）、endpoint-scope-matrix.csv、
  api-docs.md、check_generated_staleness --update。
- ADR：`scripts/allocate_adr.py` 分配；watermark 推进 `docs/integration/ownership.json`。
- feature flag：`GIS_SELECTION_SCOPE=1`（默认开，0=工具返回 disabled + situation fact
  unknown）；turn-chain/situation 端点无 flag（只读派生，无行为变化）。
