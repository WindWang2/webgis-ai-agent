# ADR-0214: MapSpec → Render Runtime & Data Plane 事务语义

- 状态：Accepted
- 日期：2026-09-26
- 关联：ADR-0036（mapspec runtime）、ADR-0054/0057/0058（desired/observed、mutation lifecycle）、ADR-0055/0072（session owns GIS world / user-wins）、ADR-0060（validity ceiling）、ADR-0086（render observation runtime）、ADR-0088（runtime repair）、ADR-0182（resource governor V1 / R13 render budget）、ADR-0183（mutation transactions V1）、ADR-0213（unified cost planning）

## 背景

#1484（ADR-0213）落地了 render 估工模型（`RenderWorkInput` + `render_formula.v1`）与 governor 准入/记账 seam，但留下两个缺口：

1. **无生产投影**：`render_input_from_spec_summary` 只是宽松键摘要投影，全仓没有任何从真实 MapSpec 构造摘要的生产代码；派发面 `_build_demand` 从不传 `render_input`，render 细化通道在准入面上恒缺席。
2. **无结构化 apply ACK**：前端 reconcile 落定后只有自由文本 `reconcile_error` + 有界 error 环；per-layer/组件的应用结果、失败原因、部分应用事实没有 versioned 契约回流服务端，Map Product Finalizer 只能从 layer 在场性反推。

同时 data plane 存在两个已确认的身份/观测缺口：

3. **FC 缓存 key 不含 data identity revision**：`sessionId::refId` 在 #1112 同 ref 覆盖语义下，旧 revision 载荷可继续服务新请求（MVT 通道已带 `v=<content_revision>`，FC 通道没有）。
4. **渲染性能不可观测**：TTFR / patch latency / data bytes / cache hit / render failure 无任何探针与上行通道；`perf-counters.ts` 自述 test-only，data-plane observability 是本地账本无出口。
5. **图层类型 capability 无声明面**：unsupported 只在 apply 失败时隐式暴露（组件族已有 `_SUPPORT_MATRIX` + `rendererCapability` 导出）。

## 决策

### D1 — RenderWorkProjection：versioned 纯投影（单一真相派生）

新增 `app/lib/cartography/render_work_projection.py`：`render_work_projection.v1` = MapSpec 骨架 + 源 profile 计数 → 既有 `RenderWorkInput`（复用 render_budget 形状，不重定义估工模型）。绑定 `mapspec_revision`（会话 `_cartographic_mutation_revision`）与 `mapspec_fingerprint`（`cartographic_fingerprint` 同源）。零数据扫描；缺 profile 时按保守先验估并置 `features_estimated=True`。

### D2 — 派发面 render 通道（生产路径，非辅助函数）

`GovernorDispatchAdapter` 在 subsystem ∈ {RENDER, BROWSER, EXPORT} 时经 `app/services/governor/render_projection.py`（会话级有界缓存，key=(session, fingerprint, revision)）供给 `render_input`；全链 fail-open（任何异常 → None → 回退档位先验）。**绝不改变结果语义**——只细化估工，模式与 #1408 df_cost 通道一致。

### D3 — RenderApplyAck：增维不换通道

`render_apply_ack.v1`（`app/lib/cartography/render_apply_ack.py`）：事务级 status（applied/partial/failed）+ per-layer/per-component status + **封闭 reason code 词表**（missing_after_apply / unsupported_layer_type / source_unresolved / style_diverged / apply_error / user_pending）+ 有界截断披露。前端 builder 是 (desired spec, applied spec, reconcile_error, pending) 的纯投影；**pending 用户操作涉及的图层不进 ACK**（user-wins：中间态不是 agent apply 结果）。载体 = observation POST 增 `apply_ack`/`perf` optional 块；服务端 DTO 归一 fail-closed（非法块整体丢弃按证据缺席降级）、fingerprint 接受门 + client_generation 单调门 + 服务端 revision 盖章承载 stale 语义；ACK 内部 revision ≠ 盖章 revision → 标 `stale: true` 只披露不判定。消费 = 新 finding code `render_apply_failed`（进 `RUNTIME_RENDER_CODES`，transient/可自愈语义，warning 级）。

### D4 — 缓存身份 = data identity + revision

`RefFetchRequest.dataRevision` → key `sessionId::refId@rev`（无 revision 保持旧键，向后兼容）；同 ref 不同 revision 是不同缓存条目、不同网络往返、不同 ETag 协商。三个 `requestRefFC` 调用点传 `content_revision`。style-only patch 不触碰 source → 不产生新 ref 请求（diff 分类既有保证，测试锁定）。可见层 pin（`pinRef`）接线：可见层 `_refId` pin / 隐藏层 unpin，预算逐出永不触碰正在显示的数据。

### D5 — Performance probes 进 trace

`frontend/lib/telemetry/render-probes.ts`：patch latency（desired bump → settle）、TTFR（首 desired → 首 mapIdle，会话一次）、data-plane 计数直读（`getDataPlaneSnapshot`）、cache bytes、render failures。随 observation `perf` 块（纯数字，≤2KB）上行，落库于 `_cartographic_observation`（finalizer 消费的既有 trace 面）。

### D6 — 图层 capability 声明面

`app/lib/cartography/layer_capability.py` 逐类型声明 full/partial/none（truth = 前端 runtime 实况，只描述已实现——与 `_SUPPORT_MATRIX` 同纪律），随 `design_system.build()` 以 `layerCapability` 导出：组合/规划面执行前显式 unsupported；运行时 miss 经 ACK `unsupported_layer_type` 执行后显式。

## 后果

- MapSpec revision ↔ renderer current revision 可靠对账：fingerprint 门（已有）+ 盖章 revision（已有）+ ACK revision 对账与 stale 丢弃（新增，双向）。
- 正向：估工细化进入准入面；渲染失败有机器可读归因；#1112 stale 数据通道关闭；渲染性能首次可观测。
- 代价：observation 载荷增两个有界 optional 块（≤64 层 + ≤2KB perf，数值/ID/枚举）；派发面对 session store 的一次额外读（有界缓存摊薄）。
- 风险与缓解：前端 reason code 词表与服务端漂移 → 服务端封闭词表校验收敛（未知码降级 `apply_error` 并披露），词表单一权威在后端。
- 明确不做：出版链（方向 14）、MVT 重写、第二套 agent loop / 第二 MapSpec 真相、大 GeoJSON 进 agent context。
