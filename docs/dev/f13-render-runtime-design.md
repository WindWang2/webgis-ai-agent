# F13 设计 — MapSpec → Render Runtime & Data Plane

ADR：`docs/adr/0214-mapspec-render-runtime-data-plane.md`。Recon：`f13-render-runtime-recon.md`。

## 0. 设计原则

1. **单一真相**：MapSpec 仍是唯一 desired state；RenderWorkProjection / RenderApplyAck / perf 块全部是**纯派生投影**（同 layer-status.ts 纪律），零平行状态写入。
2. **不造第二套**：ACK 词表复用 layer-status 事实源；reason code 词表与 finding codes 同址收口；调度/缓存/取消不重写，只补身份与观测。
3. **fail-open 估值 / fail-closed 校验**：governor render 供给失败 → None（准入回退档位先验，绝不阻断派发）；ACK 载荷校验 fail-closed（非法块整体丢弃 + 披露，不污染 observation 存储）。
4. **user-wins**：pending 用户操作涉及的图层**不进 ACK**（其中间态不是 agent apply 的结果）；ACK builder 输入来自 committed spec + applied spec，永不反写。

## 1. RenderWorkProjection（后端，versioned）

`app/lib/cartography/render_work_projection.py`

```
RenderWorkProjection:
  schema_version = "render_work_projection.v1"
  mapspec_revision: int          # 会话 _cartographic_mutation_revision
  mapspec_fingerprint: str       # cartographic_fingerprint 同源
  work_input: RenderWorkInput    # governor render_budget 形状（复用，不重定义）
  features_estimated: bool       # 源缺 profile.featureCount → 每层保守先验
  unsupported_layer_types: tuple # layer_capability 词表外/不支持类型
  layers_total / layers_visible: int
  notes: tuple[str, ...]         # 有界披露（≤8 条）
```

- **零扫描**：只读 spec 骨架 + 源 `profile.featureCount` / `imageSize`；绝不触碰 inlineData/GeoJSON 本体。
- 确定性：同 spec 必同投影（排序迭代；浮点只来自公式系数）。
- 单调性：层/要素/标注/组件递增 → work 递增（与 render_formula.v1 单调性测试同款锁定）。
- 未知要素先验 `_UNKNOWN_FEATURES_PER_LAYER = 2000`（与 data-plane plan.ts 的保守档同量级），必须带 `features_estimated=True` 披露。

## 2. 生产接线（governor render 通道）

`app/services/governor/render_projection.py`：

- `async get_render_work_projection(session_id, *, force=False)`：读 mapspec（`mapspec_store_instance.get_mapspec`）+ revision（map_state `_cartographic_mutation_revision`）+ fingerprint（`cartographic_fingerprint` 同源）→ 投影；**有界会话缓存**（≤128 entries，key=(session_id, fingerprint, revision)）。
- `dispatch_adapter.run()`：`classify_tool` 命中 `RENDER/BROWSER/EXPORT` 时 await 供给 `render_input`（try/except 全 fail-open → None）；`_build_demand(..., render_input=)` 传入 `estimate_for_tool`。模式照 #1408 df_cost 通道（`:112-165`），不改变任何结果语义——只细化估工。

## 3. RenderApplyAck（desired→apply→ACK 事务面）

### 3.1 契约（后端权威校验）

`app/lib/cartography/render_apply_ack.py` — `render_apply_ack.v1`：

```
status: applied | partial | failed        # 事务级
mapspec_revision: int                      # 本 ACK 所对账的 desired revision
layers: [{layer_id, status: applied|failed|skipped|pending,
          reason_code?}]                   # ≤64，稳定序
components: [{component_id, status, reason_code?}]  # ≤32
reason_code 封闭词表:
  missing_after_apply    # desired 有、applied 缺
  unsupported_layer_type # layer_capability 不支持
  source_unresolved      # 源未收敛（ref 解析中/失败）
  style_diverged         # 挂载但样式未收敛（runtime-repair 域）
  apply_error            # reconcile 报错（reconcile_error 非空）
  user_pending           # 用户 pending 在场 —— ACK 主动弃权项
partial_apply: {discarded: int}            # 有界截断披露
```

- `validate_render_apply_ack(payload)` → `(ok, errors, normalized)`：结构非法 = 整块拒绝（fail-closed），**绝不**部分收编自由文本；未知 reason_code → 该条目降级 `apply_error` + error 记录（词表漂移在服务端收敛，不虚构语义）。
- 载荷有界：64 层 / 32 组件 / reason code ≤32 字符 / id ≤64 字符。

### 3.2 前端 builder（纯投影）

`frontend/lib/render-protocol/render-apply-ack.ts`：

- `buildRenderApplyAck({ spec, applied, reconcileError, revision, fingerprint, pendingLayerIds })`：
  - desired 层在 applied 中 → `applied`；缺失 → `missing_after_apply`；类型不在支持集 → `unsupported_layer_type`；**pendingLayerIds 命中 → 直接排除**（user-wins：中间态不是 apply 结果，不计 failed 也不计 applied）。
  - `reconcileError` 非空 → 事务级降级 + `apply_error` 披露。
  - 稳定序（按 desired spec 层序）+ 有界截断 + `partial_apply.discarded`。
- `isStaleApplyAck(ack, currentRevision)`：`ack.mapspec_revision < currentRevision` → stale（前端 + 后端双向丢弃语义）。

### 3.3 通道与 stale 丢弃

- 载体：**不新建 endpoint**——observation POST 载荷增 `apply_ack` / `perf` 两个 optional 块（增维不换通道，与 P9/V5/V6 同纪律）。
- 服务端（`chat.py` ingest + `chat_schema.py` DTO）：DTO 归一（非法块 = None，按证据缺席降级）；fingerprint 接受门通过后随 observation 落库（`_cartographic_observation.apply_ack`）；stale 语义由既有门承载（fingerprint 门 + client_generation 单调门 + 服务端 revision 盖章）。ACK 内部 `mapspec_revision` ≠ 盖章 revision → 块标记 `stale: true` 保留披露但**不进 findings 判定**（stale ACK 不覆盖新状态）。
- 消费：`render_observation.derive_apply_ack_findings()` → 每个失败/跳过层一条 warning（新 finding code `render_apply_failed`，进 `RUNTIME_RENDER_CODES`；transient semantics 与 P9 族一致——可自愈，不推翻 status）。截断 `MAX_RENDER_FINDINGS` 内。

## 4. 图层 capability 声明面

`app/lib/cartography/layer_capability.py`：

- `LAYER_TYPE_SUPPORT`：`MapSpecLayer.type` 9 种词表逐项声明 `full | partial | none`（truth = 前端 mapspec-compiler/runtime 实况；`background/hillshade` 为 AC-06 补齐的 full；其余 full；`fill-extrusion` 需要 source zoom 支持标 partial——对齐 renderer 实况）。
- `unsupported_layer_types(mapspec)`：有界去重披露。
- `design_system.build()` 增 `layerCapability` 导出（与 rendererCapability 同链）——组合/规划面在**执行前**显式 unsupported；运行时 miss 在 ACK 以 `unsupported_layer_type` 显式（执行后）。

## 5. Data plane：身份 / invalidation / pin

1. **缓存身份升级**：`RefFetchRequest.dataRevision?: number|string`；key = `sessionId::refId`（无 revision，向后兼容）或 `sessionId::refId@rev`。`setPriority` 对齐。ETag/304/去重语义不变（去重按完整 key：同 ref 不同 revision 是不同网络往返——正确，#1112 同 ref 覆盖语义下旧 revision 数据不得服务新请求）。
2. **三调用点接线**：`use-sse-stream.ts` / `map-state-restore.ts` / `store/layer-data.ts` 传 `descriptor?.content_revision ?? source.content_revision`。
3. **invalidation 显式化**：style-only patch（paint/layout）不触碰 source → 不产生新 ref 请求（既有 diff 分类保证，测试锁定）；`content_revision` 变化 → 新缓存键 + 新 ETag → 旧键自然 LRU 逐出（测试锁定不复用）。
4. **pin 接线**：`frontend/lib/data-plane/visibility-pin.ts` `applyVisibilityPins(layers)`：可见层 `_refId` pin、隐藏层 unpin；在 Layer Manager 订阅点消费（可见性变化即同步，幂等）。预算逐出从此永不触碰正在显示的数据。

## 6. Performance probes

`frontend/lib/telemetry/render-probes.ts`（纯增量模块）：

- `patchLatencyMs`：desired 变化（`subscribeMapSpecLive` 代次 bump）→ observation settle 完成。
- `ttfrMs`：会话首个 desired 变化 → 首次 mapIdle settle（会话级一次）。
- `dataPlane`：`getDataPlaneSnapshot()` 计数直读（requests/cacheHits/etag304/fetchOk/fetchFailed/cancelled/deduped/evictions）。
- `cacheBytes`：调度器缓存 `totalBytes`（有界读取）。
- `renderFailures`：本代 ACK failed 层数。
- 上行：observation POST `perf` 块（≤2KB，纯数字/布尔）→ 服务端随 observation 落库（trace 通道 = finalizer 消费的既有 `_cartographic_observation`），DTO 有界校验。绝不上传 GeoJSON/特征数据。

## 7. 测试矩阵

| 层 | 文件 | 锁定 |
|---|---|---|
| py | tests/cartography/test_render_work_projection.py | 确定性/单调性/revision 绑定/features_estimated 披露/unsupported/空图 |
| py | tests/cartography/test_layer_capability.py | 词表全覆盖（9 种）/design_system 导出链 |
| py | tests/governor/test_render_projection.py + test_dispatch_render_input.py | 会话缓存/fail-open/adapter 仅 render 族供给/estimate 细化生效 |
| py | tests/cartography/test_render_apply_ack.py | 校验 fail-closed/封闭词表/有界/stale 标记/findings 派生/DTO 归一 |
| ts | render-protocol/render-apply-ack.test.ts | 投影各态/pending 排除（user-wins）/stale/截断披露 |
| ts | telemetry/render-probes.test.ts | fake 时序：patch latency/ttfr once/计数快照 |
| ts | data-plane/scheduler.test.ts 增补 | dataRevision 键隔离/同 ref 异 revision 不串数据/setPriority 对齐 |
| ts | data-plane/visibility-pin.test.ts | pin/unpin 幂等/可见层豁免逐出 |

## 8. Out of Scope（边界重申）

- PDF/SVG 出版链（方向 14）。
- MVT 瓦片通道重写；新分页 endpoint（>5000 已走 MVT，bounded transfer 由既有 MVT+调度器承载，本方向只补观测与身份）。
- 前端本地状态改为第二 MapSpec（禁止）；visibility-transaction / user-mutation 重构（已稳定，只读消费）。
- governor 系数校准（R17）；新增 ADR-0213 之外的预算语义。
