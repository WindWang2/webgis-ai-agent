# ADR-0214: 生产级视觉观察/批评/修复 Provider —— 观察契约、taxonomy 归一、跨域融合与用户批准修复闭环

- 状态: Accepted
- 日期: 2026-09-26
- 线: zcode/f15-visual-observation-repair-20260926-9e1ad229
- 关联: ADR-0209（verify→critique→repair 闭环）、ADR-0185（VLM critic runtime）、
  ADR-0186（visual self-healing）、ADR-0119 W9（visual seam）、ADR-0060（validity 阶梯天花板：
  visual 结论不入 validity 阶梯）

## 1. 背景

ADR-0209/G4 已把 visual seam 接进 finalizer（`GIS_VISUAL_EVALUATOR` 触发点 +
有界 snapshot + 恒 degradation_only），但截至 `9e1ad229`：

- **无生产 provider**：`module:callable` 注入面在仓库内没有任何实现；
  v2 评审引擎（ADR-0185，需要截图字节）与 seam snapshot（无字节投影）之间无适配器；
- **无观察契约**：snapshot 是无类型 dict，render revision / screenshot ref /
  component boxes / deterministic checks refs 无契约面；
- **无 taxonomy 归一**：v2 五维、healer 四类、语义检查码三套表述并存，
  无单一归一点，视觉 finding 与 deterministic finding 无去重/融合——
  同一实体同类问题会被双重复披露、可能触发重复修复；
- **`visual_repair` 触发无消费者**：healer（ADR-0186）全仓无生产调用方，
  visual error finding 在 plan 面 deferred（executor=user）后无 API 兑现；
- **无跨运行 recurrence 与截图 ref-only 纪律**：同一视觉缺陷跨 finalization
  运行反复出现时无硬停披露；后端无截图存储通道与保留纪律。

约束（沿 ADR-0209 既有裁决，全部维持）：LLM/视觉批评永远不是 correctness
verifier（恒 degradation_only；**披露面** severity 封顶 warning、唯一裁决效应
READY → READY_WITH_WARNINGS；**plan 面**保留 error 级以驱动
defined→requires_user_approval 的既有语义——两面词随 ADR-0209 决策四）；视觉自动修复不得覆盖 user locks/explicit
edits；不把整段截图历史塞入 context/trace。

## 2. 决策

### 决策一：provider-neutral 观察契约（ref-only）

新包 `app/services/gis_harness/visual_observation/contracts.py`：
`VisualObservationInput`（trigger/session/revision/fingerprint + bounded
`cartographic_projection` + observation 摘要 + deterministic findings ≤12 +
`Optional[VisualScreenshotRef]`）与 `VisualObservationResult`（evaluated /
not_evaluated + 机器可读 reason + findings + taxonomy 计数 + sha 摘要回声）。
**ref-only 纪律**：screenshot 只以 `{ref, sha256, size, w, h, revision}` 流动，
字节只在 provider 评估瞬间解析进内存；trace/journal/map_product 只允许
ref+sha 摘要。`from_snapshot`/`to_snapshot` 双向、畸形输入诚实降级。

### 决策二：仓库内首个生产 provider（默认关闭 = 零行为变化）

`visual_observation/provider.py::evaluate(snapshot)` 是
`GIS_VISUAL_EVALUATOR="module:callable"` 的首个仓库内生产实现；
部署通过 env 指向，未配置时 m1 语义逐字节保留。模式
`GIS_VISUAL_PROVIDER_MODE`（默认 `rules_only`）：

- `rules_only`：确定性 rules-half —— 复用 `local_visual_criteria` 的
  像素级可度量事实（墨量/边缘密度/重心/显著色桶 → empty_space /
  legibility / hierarchy / contrast），逐条带测量值证据；组件重叠/越界
  等布局事实是 `derive_component_layout_findings` 的硬证据领地，
  rules-half 绝不重复（W9 边界纪律）；无截图 → not_evaluated 诚实缺席；
- `vlm`：screenshot ref 解析 → 复用 ADR-0185 `build_critic_engine`
  （单次调用、显式超时、fail-closed not_evaluated 矩阵原样）；
- `hybrid`：rules ∪ vlm，同 `(entity, taxonomy)` 融合。

async 引擎在 sync seam 下的桥接用一次性 worker thread + `asyncio.run`
（不污染调用方事件循环），墙钟预算 `GIS_VISUAL_PROVIDER_TIMEOUT_S`
（默认 20s、上限 60s）；超时/失败 → 空 findings（诚实缺席≠合格，无 pass
语义）。全部输出经 seam `_sanitize_finding` 白名单二次消毒——provider
任何越权字段（mutation 意图、非 visual domain）在结构上不可进入披露面。

### 决策三：封闭 taxonomy + 跨域融合（deterministic wins）

`taxonomy.py`：8 类封闭词表 `overlap|crop|legibility|contrast|
label_collision|legend_mismatch|empty_space|hierarchy`；v2 维度/healer
类别 → taxonomy 的单一映射点，映射不出诚实丢弃（计 `unmapped`）；
finding code 沿 `visual_` 命名空间（ADR-0209 防撞名纪律）。`fusion.py`：
`(entity, taxonomy)` 键与确定性 findings 对账——命中 → visual finding
保留披露、`repair_class` 清空并附 `corroborates:<taxonomy 类>` 收据
（不再为同一实体同类问题触发第二修复）；未命中 → 原样通过。确定性
finding 永不被融合删除/降级。

### 决策四：user-approved visual repair = 两步提案/应用，走既有事务入口

新路由 `POST /chat/sessions/{sid}/visual-repairs/plan|apply`：

- **plan**（零突变）：`map_product.visual_findings` → `repair_bridge`
  翻译为 healer `VisualCritiqueItem`（taxonomy → HEALABLE 四类确定性
  映射，映射不出 → skipped `unmapped_category`）→ `VisualHealStrategyPlanner`
  预览闭包 ⊆ `HEAL_OP_CODES`（label layout / contrast palette / layer
  order / opacity——四类呈现面微变异，绝不触 sources/数据绑定/分类断点）
  → 提案存 `map_state["_visual_repair_proposals"]`（≤4 FIFO）；
- **apply**：必须显式 `approved=true`（否则 400 `approval_required`）；
  `expected_revision` CAS（客户端漂移 → 409 `revision_conflict`；plan→apply
  间状态前进 → 409 `proposal_stale`）；`engine.apply_visual_heal_patch(
  origin="user", mutation_id="vrepair:<proposal_id>")` ——CAS/checkpoint/
  revision 单调/幂等回放全部复用 ADR-0186 事务。**user-wins 语义**（沿
  lifecycle guard 既有裁决）：user origin 是用户自有锁的唯一 override，
  但批准必须知情 —— plan 预览对触达锁定图层的 op 如实标注
  `touches_locked`；agent/system origin 的自动修复路径仍被 guard 一律
  拒绝（锁对自动路径绝对有效）；收敛耗尽 → 200 `{applied:false,
  hard_stop:true}` 诚实硬停（非 5xx）；
- **复验**：apply 成功后后台调 `maybe_finalize_map_product(reason="visual_repair")`
  ——seam 触发白名单既有词首次获得生产消费方；heal 已推进 mutation
  revision，去重门自然打开，不新建循环（沿 ADR-0209 决策四）。

不把 `ApplyVisualHealPatchIntent` 加进 `intent_codec` 14 意图 union——
避免 lifecycle 五注册面热改；专用路由直达同一事务入口，语义同源。

### 决策五：跨运行 recurrence 硬停（披露面）

`recurrence.py`：账本 `map_state["_visual_observation_state"]`（findings
≤16 FIFO、hard_stopped ≤8），键 = `UnifiedFinding.recurrence_fingerprint`
（既有铸造点，不建第二哈希）。同一指纹出现 ≥
`MAX_VISUAL_RECURRENCE_RUNS(=3)` 次 finalization 运行 → 移入
hard_stopped：披露面降 info + `visual_loop.hard_stopped` 收据、**不再进入
plan-face**（不再反复向用户索要同一修复）。`MapCompletionResult.visual_loop`
additive 键（缺席不序列化，镜像 `loop_stop` 纪律）；账本任何异常 → 空披露
绝不阻断终验。与 W11 账本（per-epoch 修复面）、healer 收敛账本
（per-fingerprint 修复面，≤2 次迭代）正交：本账本是观察侧披露面。

### 决策六：截图 blob 与保留纪律

`store.py`：PNG 魔数 + ≤4 MiB 校验 → sha256 →
`get_filesystem_blob_store().put_blob("vshot-<sha>")`（内容寻址去重）；
会话索引 `map_state["_visual_screenshots"]`（≤8 FIFO，淘汰即 prune blob，
fail-open）。journal/trace/map_product 只允许 ref+sha 摘要——无字节、
无 base64、无路径明文。

## 3. 后向兼容

- 全部新模块/新键/新路由；`pipeline.py` 改动仅 additive（snapshot 增补
  三个键、评估后一次记账调用）；`MapCompletionResult.visual_loop` 全默认值、
  `to_dict` 缺键不写——旧读者零漂移；
- 词表冻结：UNIFIED_DOMAINS / FINDING_CLASSES / STATUS_* / VERDICT_* /
  HEALABLE_CATEGORIES / HEAL_OP_CODES 均不改；
- 无 schema 迁移；回滚 = 不配置 `GIS_VISUAL_EVALUATOR`（provider 特性
  整体缺席）或 revert（无持久化形状依赖；`_visual_*` map_state 键被旧
  读者忽略）。

## 4. 验收

- 契约/taxonomy/融合：`tests/unit/gis_harness/test_visual_observation_contracts.py`；
- provider 接线/三模式/fail-closed/反翻转：`tests/unit/gis_harness/test_visual_observation_provider.py`；
- recurrence 硬停：`tests/unit/gis_harness/test_visual_recurrence.py`；
- plan/apply 端点（批准门/CAS/锁/收敛硬停/幂等/复验触发）：
  `tests/unit/gis_harness/test_visual_repair_endpoint.py`；
- 视觉回归 corpus（golden_images 10 缺陷 + 正常负例 + 字节不落 trace）：
  `tests/unit/gis_harness/test_visual_regression_corpus.py`；
- 既有回归：gis_harness 域 + seam/loop 接线测试全绿。
