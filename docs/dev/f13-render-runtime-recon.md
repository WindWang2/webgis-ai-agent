# F13 — MapSpec → Render Runtime & Data Plane 勘察（recon）

- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24，#1494）。
- 执行时 git fetch --prune 后确认：无更新的 origin/master；无同方向 open PR。
- Open PR 仅 #1489（dependabot docker node bump，只改 `Dockerfile`/`Dockerfile.prod`）——与 F13 零文件交集。
- #1479–#1488 基础波次已全部合入；本 prompt 列出的"未完成面"逐项在最新 master 上复核，结论见五列表。
- 并发方向 worktree（同基线）：f08 / f09 / f11 / f15 —— 本分支保持增量式改动，避开其热区。

## 1. MapSpec 生命周期端到端（生产 → 传输 → apply → 反馈）

| 环节 | 位置 | 要点 |
|---|---|---|
| Schema 权威 | `app/lib/cartography/mapspec_schema.py` | `MapSpecDocument`（:497 version/view/sources/layers/layout/thresholds/scenario_mode/scene）；`MapSpecLayer`（:292 type 词表 9 种 :306-316、`source` 键绑定源 id、`visible` 严格 bool、label/extrusion）；源 union（:172-178，`GeoJSONMapSpecSource.content_revision` :120、`DataFabricMapSpecSource.ref_id/data_fingerprint/profile` :141-157）。**MapSpec 本体无 revision 字段** |
| revision 真相 | `map_state._cartographic_mutation_revision` | 由引擎事务 CAS 维护（`app/services/mapspec/lifecycle_engine.py:1532-1550`）；适配器转发 `mutation_revision`（`app/services/mapspec_store.py:77`） |
| 生产（agent 面） | `tool_dispatch_service.py:1195-1262` → `MapSpecStore.layer_upsert`（mapspec_store.py:288）→ `apply_gis_mutation`（`app/services/gis_world_state/mutation.py:455`） | 信封铸造/幂等/precedence/provenance |
| 传输 | SSE step_result（tool_dispatch_service.py:1252-1268、1430-1479） | `mapspec` + `mutation_revision` + `runtime_observation_seq`（`_DISPLAY_RESULT_METADATA_KEYS` 白名单 :1268/:1544） |
| 前端接收 | `frontend/lib/hooks/use-sse-stream.ts:556-571` | `setMapSpecRevision` + `commitMapSpecDocument`（迟到旧代次拒绝）+ `syncSpecLayersToStore` |
| 会话恢复 | GET map_state（`app/api/routes/chat.py:1470-1500`）→ `frontend/lib/session/map-state-restore.ts:331/:429` | 带 revision 提交 + ref 水合 |
| 用户突变回程 | POST `/chat/sessions/{id}/mapspec/mutations`（`app/api/routes/mapspec_mutations.py:40-126`） | CAS expected_revision、`c:<client_mutation_id>` 幂等、409 superseded、503 锁背压 |
| 前端 apply | `frontend/lib/mapspec/live-spec.ts:156`（composeLiveMapSpec）→ `frontend/lib/mapspec-runtime/runtime.ts:258-312`（reconcileAsync 串行链）→ diff+patch（worker；paint/layout/filter 零 churn 快路径 :458-535） | styleEpoch 防 late render（:113-207） |
| ACK/current | `use-cartographic-observation.ts:118-316` → POST `/cartographic-observation`（chat.py:1874）→ `app/services/gis_harness/render_observation.py` | observation 带服务端盖章 revision；fingerprint 门 + client_generation 门（rejection：`stale_mapspec_fingerprint` / `stale_client_generation`） |

## 2. RenderWorkInput 现状（#1484 遗产 = F13 目标 1 的空位）

- 定义：`app/services/governor/render_budget.py:71-113`（纯输入形状）+ `render_input_from_spec_summary`（:179-214，**宽松键 Mapping 投影，无生产摘要供给方**；docstring 自述"生产接线点为 ADR-0213 out-of-range"）。
- 消费方：仅 `estimation.py:197-204`（`estimate_for_tool(render_input=)` 可选参数）+ 单测。全仓 `RenderWorkInput(` 在 app/ 下零构造点。
- 派发面缺口：`GovernorDispatchAdapter._build_demand`（`app/services/governor/dispatch_adapter.py:305-332`）**不传 render_input**——render 细化通道在派发面恒缺席。#1408 已补 df_cost 通道（:112-165），本方向照同一模式补 render 通道。
- 可用素材：源级 `profile.featureCount`（mapspec_store.py:227-237）、`MapSpecLayer.label`、layout components、`RasterMapSpecSource.imageSize`。

## 3. 前端状态所有权现状与反弹风险

- 所有权链：服务端 MapSpec（唯一 desired 权威，ADR-0088）→ `frontend/lib/mapspec/session-cursor.ts` committed 镜像 + pending overlay（`getPendingPresentation` :156 导出）→ `composeLiveMapSpec` → `MapSpecRuntime.appliedSpec` → MapLibre。
- 已建防反弹机制：revision 单调守卫 + 迟到文档拒绝（session-cursor.ts:119-154）；乐观 pending per-op 身份（:160-213）；串行链 `enqueueUserMutation`（user-mutation.ts:100-166）；提交收敛序 + 409 回灌（:217-255）；可见性单事务（map-commands/visibility-transaction.ts）；styleEpoch（runtime.ts）。
- **已建状态派生（复用，不造第二套）**：`frontend/lib/layers/layer-status.ts` 封闭词表 `loading|ready|rendering|hidden|stale|failed|expired`——从三事实（HUD 行 / committed revision / render-evidence）只读派生；`use-layer-statuses.ts` 订阅两代次。
- 残余风险：别名层字符串约定（live-spec.ts:54-67）；pending 滞留压制服务端真相；`renderer._rawDataBySource` 隐式数据态（#1409 已补 (source,input,viewport) 三元组身份）；HUD-only 行窗口。

## 4. Data plane 现状

- ref-first 已确立：源带 `ref_id/profile/profile_fingerprint/data_fingerprint`；SSE 剥除 geojson 双拷贝（tool_dispatch_service.py:1224-1240）。
- 拉取：`GET /layers/data/{ref_id}`（`app/api/routes/layer.py`）；MVT `/tiles/{z}/{x}/{y}.mvt` ETag 304 + `Cache-Control`（#1112 同 ref 覆盖语义，`v=<content_revision>` 进 tile URL：`frontend/lib/map-kit/tile-url.ts:6`）。
- 调度器：`frontend/lib/data-plane/scheduler.ts` 并发 3 + 优先级 + 单飞去重（含排队期）+ ETag 条件请求 + 取消 + stale-write 防护。**缓存 key = `sessionId::refId`（:179）——不含 data identity revision**。
- 缓存：`RefDataCache`（cache.ts）字节预算 LRU 256MB；pin 已实现未接线（scheduler.ts:162-170 自述"预留 API，本期未接线"）。
- 大图层：>5000 要素 + mvt_capable → MVT（renderer.ts:76-96；data-plane/plan.ts:29 同值契约）；inline 视口剔除预算 5000 + 确定性抽稀；raw ≥20k worker 化（async-viewport-compute.ts）；增量 updateData diff ≤2000 稳定 id（patch.ts，#1409 唯一 id 契约）；ref 内联上限 20000（ref-source-resolver.ts）。
- `content_revision` 在前端可用：`Layer._descriptor.content_revision`（types/layer.ts:35）、MapSpec 源 `content_revision`；但 **`requestRefFC` 三个调用点均不传**（store/layer-data.ts:139、session/map-state-restore.ts:429、hooks/use-sse-stream.ts:820）。

## 5. Capability / telemetry 现状

- 组件支持矩阵唯一权威：`app/lib/cartography/component_renderers.py:40 _SUPPORT_MATRIX`（只描述已实现；descriptor 交叉对账防漂移）；随契约导出（`app/lib/cartography/design_system.py:100 rendererCapability`）。
- **图层类型级 capability 无声明面**：`MapSpecLayer.type` 9 种词表（schema :306-316）与前端 runtime 支持之间是隐式的，unsupported 只在 apply 时报错。
- 遥测：前端 perf-counters（test-only 自述）、data-plane/observability.ts（本地账本：requests/deduped/cacheHits/etag304/fetchOk/fetchFailed/cancelled/evictions/patchApplied/patchFallbacks + 128 事件环）；唯一后端通道 = RenderObservation POST。**TTFR/patch latency/data bytes/render failure：全仓零命中。**
- finding codes 单一词表：`app/services/gis_harness/completion/contracts.py`（P9 渲染族 :57-71，`RUNTIME_RENDER_CODES` frozenset）。

## 6. 五列表

| 维度 | 内容 |
|---|---|
| **Overlap** | #1489 仅 Dockerfile；无同方向 PR。热区（30d commits）：lifecycle_engine.py 37、export-chrome.ts 28、exporter.ts 27、runtime.ts 17、user-mutation.ts 16、gis_world_state/mutation.py 15、renderer.ts 14。`render_budget.py`/`estimation.py` 仅初始提交 = 冷区 |
| **Already Done** | MapActionStatus desired→ACK 词汇（evidence.py:100-150）；CAS+幂等+superseded+user-wins；乐观 pending/串行链/stale 守卫；RenderObservation 反馈回路 + 服务端 revision 盖章；ref-first data plane + 调度/缓存/取消；MVT+视口剔除+增量 updateData；组件支持矩阵 + rendererCapability 导出；governor seam 与估工模型；#1409 视口缓存身份 |
| **Still Missing** | ① MapSpec→RenderWorkInput 真投影 + 派发面 render_input 供给；② versioned RenderWork 协议（投影无 schema_version/revision 绑定）；③ per-layer/组件 apply status + 封闭失败 reason code + 部分应用披露的结构化 ACK；④ FC 缓存 key 不含 data revision（#1112 同 ref 覆盖 → stale 数据风险）；⑤ style-only patch 与数据重拉的显式 invalidation 语义；⑥ 可见层 pin 未接线；⑦ TTFR/patch latency/data bytes/cache hit/render failure 探针及上行；⑧ 图层类型 capability/unsupported 声明面 |
| **Must Not Touch** | ADR-0007/0016/0022/0055/0059/0060/0072 边界；`mapspec_schema.py` extra="allow" round-trip 契约与 canonical 保序拷贝；`_SUPPORT_MATRIX` 只描述已实现纪律；governor fail-open/observe 语义（dispatch_adapter.py:160-188）；MVT 通道不重写（ref-service.ts 自述） |
| **Integration Seams** | `estimate_for_tool(render_input=)`（estimation.py:116）← dispatch_adapter.py:305；observation POST 载荷（render-observation.ts:72-94 + chat_schema.py:240 DTO）；`composeLiveMapSpec`/`getPendingPresentation`（session-cursor.ts:156）；`requestRefFC`（ref-service.ts:128）+ 三调用点；`pinRef`（scheduler.ts:162）；design_system build（app/lib/cartography/design_system.py:100）；finding codes（completion/contracts.py）；data-plane observability（observability.ts getDataPlaneSnapshot） |

## 7. master 已知失败/flake

未发现本方向相关 xfail/skip 或 flake 记录（TEST_INFRA.md / pytest markers：`heavy/perf/cartography/real_services`）。基线红绿以本地 targeted 运行为准，与本分支新增失败严格区分。

## 8. 新模块落点

后端（全部冷区新增）：
1. `app/lib/cartography/render_work_projection.py` — 纯投影 MapSpec → versioned RenderWorkInput。
2. `app/lib/cartography/layer_capability.py` — 图层类型 capability 声明面（只描述已实现）。
3. `app/services/governor/render_projection.py` — session 级投影缓存 + fail-open 供给。
4. `app/lib/cartography/render_apply_ack.py` — versioned apply ACK 校验 + 封闭 reason code 词表 + findings 派生。

前端（全部新增目录/文件）：
5. `frontend/lib/render-protocol/render-apply-ack.ts` — ACK TS 镜像 + 纯投影 builder + stale 判定。
6. `frontend/lib/telemetry/render-probes.ts` — TTFR/patch latency/data plane 计数聚合。

小接线（低风险增量编辑）：
7. `dispatch_adapter.py` render_input 供给（照 #1408 df_cost 模式）。
8. `chat_schema.py` DTO + `chat.py` ingest + `completion/contracts.py` 新 finding code + `render_observation.py` 消费。
9. `scheduler.ts`/`ref-service.ts` dataRevision 缓存身份 + 三调用点 + `setPriority` 对齐。
10. `render-observation.ts` 挂 apply_ack/perf 块 + hook 传 pendingLayerIds。
11. `design_system.py` 导出 layerCapability。
12. pin 接线模块 + 可见层生命周期消费。
