# ADR-0224: RenderWork Protocol V1 —— MapSpec → 执行信封 → Apply ACK → Telemetry 的执行闭环

- 状态：Accepted
- 日期：2026-10-18
- 方向：Goal 03（RenderWork Protocol V1）
- 基线：master `ddd10fb4`
- 前置：ADR-0214（F13 render runtime recon 及其 D1/D3/D5/D6 落地）、ADR-0180（settle/K9）、#1617（MapSpec v1.6 RepresentationAtScale，open —— 本 ADR 前向兼容消费）

## 背景与问题

ADR-0214（F13）清偿了 render runtime 的第一批缺口：apply ACK v1
（per-layer/组件 + 封闭 reason code + revision-stale 门）、估工投影
（governor 预算面）、图层类型 capability 矩阵、data revision 缓存键、
可见层 pin、渲染探针 v1。但执行闭环仍缺一层**工作身份绑定**：

1. ACK 只绑 `mapspec_revision` —— 无 work 身份、无数据依赖身份。两份
   ACK 同 revision 时无法区分"同一份工作重放"与"不同数据代次的两次
   apply"；
2. 数据面实际使用的 revision 无上行证据 —— 层挂上了但数据是旧代的
   风险（#1112 同 ref 覆盖的下游）在 ACK 面不可见；
3. style-only patch 与 data reload 的 invalidation 语义只在 diff 层
   隐式成立，无跨语言显式契约；
4. basemap style wipe（#459 styleEpoch）后的 apply 结果无可审计代次；
5. ACK 词表是 Python 权威 + TS 手写镜像 + 服务端降级兜底 —— 无机器
   drift gate；
6. renderer capability 只有 layer type 维度，源类型/feature 面
   （clustering/heatmap/terrain/3D/generalization/export）无声明面。

## 决策

### D1 — RenderWorkEnvelopeV1（执行信封，非第二 MapSpec）

`app/lib/cartography/render_work_envelope.py`：纯函数投影
`MapSpec + 会话事实 → render_work.v1` 信封。信封只含
refs/IDs/revisions/fingerprints/计数/布尔 —— **零载荷**（样式/几何/
GeoJSON 绝不进信封；消费方不得从信封重建地图）。字段：`work_id`
（deterministic：`rw-{revision}-{fp[:12]}`）、`session_id`、
`mapspec_revision/fingerprint`、`observation_target_seq`、
`data_dependencies[]`（source_id + kind + revision + fingerprint + ref）、
`layer_work[]`、`component_work[]`、`invalidation` hints、
`required_capabilities[]`、`diagnostics[]`、`work_fingerprint`
（canonical 语义字段的 sha256；同输入必同指纹 —— 幂等重放可检测）。

与 ADR-0214 D1 估工投影的分工：估工投影回答"这活多大"（governor
admission），执行信封回答"这是哪一代工作、依赖哪些数据身份"（对账）。

### D2 — invalidation hints（显式失效语义）

`previous` 信封在场时计算：`style_only`（数据依赖身份 + 层/组件集合
全不变 → 数据面不应重拉）、`data_changed[]`、`layers_added/removed[]`、
`components_changed[]`。首份/会话恢复 → 全 unknown（`style_only=None`），
绝不虚构"无变化"。

### D3 — ACK v1 additive 演进（不换版本号）

`render_apply_ack.v1` 保持，additive：

- 可选 `work` echo 块（`work_id` + `work_fingerprint` + `style_epoch`）
  —— **仅同代信封才 echo**（信封 revision == ACK revision；旧代信封的
  身份对本代 ACK 是错误归因，绝不冒充）；
- 可选 `sources[]`：per-source 数据面状态（`applied|stale|failed|pending`
  + `data_revision` actually used + `cache`）—— envelope 依赖 revision
  与 applied spec 源 revision 对账，落后 → `stale` +
  `stale_data_revision`，并披露实际在用的 revision；
- entry status 新词 `stale`（词表对齐 #1618 settle disposition `stale`：
  旧代次证据只进诊断）。事务级 stale（ACK revision ≠ 服务端盖章）语义
  不变 —— 条目 findings 一律跳过。

词表纪律：不新增无真实产生点的枚举（`superseded` 事务语义已由既有
stale 旗标表达，不另造码）。

### D4 — stale/generation 守卫三件套

1. **前端**：`isStaleApplyAck`（既有）+ work echo 同代门（D3）+
   `workEchoStatus`（absent/matched/stale）；
2. **服务端**：stamped revision 门（既有）+ `stale` entry 产生当前代
   warning finding（`F_RENDER_APPLY_FAILED` 族 —— 当前代数据风险不是
   旧 ACK 噪声）；
3. **styleEpoch**：`MapSpecRuntime.getStyleEpoch()`（#459 token 的
   additive getter）随 ACK echo —— basemap wipe 后旧 appliedSpec 复活的
   风险变成可审计证据（两份 ACK epoch 不同 = 中间发生过 style 重建）。

### D5 — Renderer capability v1（versioned + fingerprint）

`app/lib/cartography/renderer_capability.py`：单一真相引用（layer types
引用 `layer_capability`、组件清单引用 `component_renderers`，不复制判定）
+ 首个源类型/feature 面声明。**红线：不把未来功能声明为 supported** ——
#1617 未合并，`semantic_zoom_representation`/`generalization` =
`supported: false`（信封对 `layer.representation` 透传不重裁决）。
`capability_fingerprint`（canonical manifest sha256）是 Python/TS parity
锚；`design_system.renderCapabilityManifest` 导出（fail-open）。

### D6 — 会话供给（fail-open，照 ADR-0214 D2 纪律）

`app/services/render_work_supply.py`：per-session LRU（身份键
`(fingerprint, revision, seq)`，≤128）+ last-emitted 记忆（invalidation
比较基；逐出/重启 → hints 退化 unknown）。**身份命中直接返回缓存信封**
（重投影绝不把自身当 previous —— 否则 hints 退化为自比较，破坏确定性）。

接线（薄）：

- SSE：`tool_dispatch_service` 两处 authoring 路径 attach
  `result["render_work"]`（`slim_event_result` 只剥大载荷键，信封自动
  透传）；
- 恢复：`GET /sessions/{id}/map-state` 同带 `render_work`；
- 前端：`render-work store`（防御式解析 + 代次门 + 会话隔离）在
  `use-sse-stream` 与 `map-state-restore` 收纳。

### D7 — telemetry per-work receipt

`render_perf_probes.v1` additive：`data_plane.bytesFetched`（scheduler
fulfilled 字节一次估算双消费）+ `work_receipt`（work_id /
work_match（信封指纹 vs 观察指纹）/ recompile_fallbacks）。bounded
封闭键；信封缺席 → 整块缺席（旧后端兼容）。

### D8 — Python↔TS 三重锁 + 场景语料

`tests/cartography/render_work_corpus/`：

- `contract_manifest.json`（generate.py 用真实 consts 生成）≡ Python
  consts ≡ `frontend/lib/render-protocol/contract-manifest.ts` —— 双侧
  drift test（`test_render_work_corpus_v1.py` /
  `corpus.golden.test.ts`）；
- `render_work_golden.json`：12 场景（Goal J 全清单）—— Python 侧重放
  projector 断言 envelope golden；TS 侧用**同一份 JSON** 以真实
  `buildRenderApplyAck` 断言 ACK 语义（含幂等重放确定性 s11≡s01）。
  手改 JSON = 伪造一致性，双侧重放即爆。

## 后果

- Harness 可机器区分：规范里有图层（MapSpec）→ 编译器生成指令
  （compiler）→ 前端收到（信封收纳）→ MapLibre 应用成功（ACK applied）
  → 数据代次正确（sources applied）→ 或数据落后（stale）/不支持
  （skipped+unsupported）/失败（failed+closed reason code）；
- ACK 是执行结果证据，**不是**视觉正确性 oracle —— Observed Map /
  cartographic quality 现有权威（P9 observation、semantic checks、
  quality loop）不变；
- #1617 合并后：`layer.representation` 经 extra="allow" round-trip 自然
  透传；capability 面将 `semantic_zoom_representation` 翻 true 需其一
  行改动 + manifest 再生（fingerprint 变化由 parity gate 守护）。

## 明确不做（本 ADR 边界）

- 不重写 runtime.ts / MapLibre renderer / MVT data-plane；
- 不把每帧 zoom 回写后端（信封是 mutation 代次产物，非帧流）；
- 不重构 settle/finalization（#1618 域）；ACK 摘要可被 settle 消费是
  后续方向；
- 服务端对 `work_fingerprint` 的深度对账（信封按 revision 重放校验）
  留作后续 —— v1 以 stamped revision 门 + 前端同代门为机器可验证对。
