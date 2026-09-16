# DECISIONS — 初步技术决策（Phase 0 草案，实现前可经评审修订）

## D1 模块落位与命名

| 决策 | 内容 | 依据 |
|---|---|---|
| 服务端新服务层 | `app/services/tile_pipeline.py`（倾向）或 `app/services/layer_lod.py`。mvt.py **只 additive**（保持 encoder + 三原语职责），LOD 金字塔/视口查询/patch 计算放新文件 | #1356 同窗口改 layer.py/raster_tile_service.py；mvt.py 已 1804 行且是共享热文件，新文件 = 零合并冲突 |
| 前端新目录 | `frontend/lib/map-kit/progressive/`：`engine.ts`（编排）、`lod.ts`、`patch.ts`（diff 应用）、`scheduler.ts` + `scheduler.worker.ts`、`memory-budget.ts`、`network-budget.ts`、`degrade.ts`、`types.ts` | map-kit 内新子目录不与 #1356 的 renderer.ts/adapter.ts 深改冲突；同目录测试惯例（*.test.ts） |
| 路由 | `app/api/routes/layer.py` **append**：`GET /layers/data/{ref_id}/view?bbox=&max_features=&lod=&session_id=`（视口要素查询，返回 GeoJSON 或简化 MVT）+ 可选 `GET /layers/data/{ref_id}/tiles/{z}/{x}/{y}/lod{n}.mvt` | 复用 `require_owned_session`（layer.py:76）+ `_layer_data_budget`（layer.py:52）+ `_tile_response` ETag 模式（layer.py:250） |
| 禁改 | mvt.py encoder 核心、mapspec schema、mapspec-compiler、`types.generated.ts`、`scene_*` | PARALLEL_OWNERSHIP.md §3 |

## D2 接口形状（草案）

- **descriptor additive 字段**（`app/schemas/ref_descriptor.py`，全部 Optional，旧 ref 缺省 = 现行为）：
  - `lod_levels?: number`（服务端可用 LOD 级数；0/缺省 = 无金字塔）
  - `payload_bytes?: number`（比 estimate_bytes 启发式（ref_descriptor.py:255-261）更准的实测值，存期算一次）
- **SSE/layer 载荷**：`use-sse-stream.ts` 已透传 `data.ref_descriptor`（use-sse-stream.ts:725, 747）——新字段零改动自动流到 `_descriptor`；progressive engine 从 `_descriptor` 读决策输入，不改 addLayer 形状。
- **progressive engine 接口**（前端）：
  ```
  attachLayer(layer) -> plan（LOD 档位 + 首帧 source 形状）
  onViewportSettled(bounds, zoom) -> 若干（取消旧 token → 新请求/换 LOD → patch 应用）
  onSourceMutated(refId, revision) -> 增量 patch 请求/整包降级
  metrics(): ProgressiveMetrics（首帧/退化/预算水位）
  ```
  生成 token 失效语义镜像 `_viewportRefreshGeneration`（renderer.ts:44）与 mvt epoch（mvt.py:1475）纪律。
- **patch 应用策略**：≤2000 要素沿用现 diff-unchanged 跳过；>2000 要素的小变更走「服务端 patch 端点（since=content_revision）→ 前端按 feature id 合并 → 单次 setData」。**不追求坐标级 partial update**（MapLibre GeoJSON source 无原生 updateData 面向 v5 API 的稳定契约；一次 setData 换取确定性）。若 benchmark 显示 setData 解析仍是瓶颈，二期再评估分桶 source（按 zoom/网格拆 source，增量只 setData 受影响桶）。

## D3 LOD 与预算的单一事实源

- 阈值一律 import `data_tiers`（app/lib/cartography/data_tiers.py:27/33/37 + 前端镜像 frontend/lib/data-tiers.ts）——**禁止新增 5000/20000/50000 同义字面量**（ADR-0163 grep 断言纪律）。渐进加载的"首帧概览要素帽"用 `effective_inline_budget`（data_tiers.py:63）按视口×复杂度算有效值。
- 渲染端既有 `VIEWPORT_RENDER_BUDGET=5000`（renderer.ts:37）保持不动；progressive 引擎把数据帽压在渲染预算之内，而不是两套数字互相打架。
- **与 #1356 的边界（重申）**：本方向 LOD = 数据分级/加载粒度（几何/要素），不读不写 `label.zoomBands`/`thresholds.maxFeatures`（样式承载面，#1356 独占）。

## D4 feature flag / kill-switch 约定

- **环境变量**（仓库惯例 = `WEBGIS_*` / 领域大写 env，如 `WEBGIS_REF_CONTENT_HASH`（ref_descriptor.py）、`SESSION_STORE_MAX_BYTES`（session_data.py:337））：
  - `WEBGIS_EXTREME_SCALE_RUNTIME`（默认 `0`）——总开关；关闭时代码路径零进入（决策函数短路返回 legacy 行为），回滚 = env 置 0。
  - `WEBGIS_EXTREME_SCALE_MAX_CONCURRENCY`（渐进请求并发帽，默认 4）、`WEBGIS_EXTREME_SCALE_MEMORY_BUDGET_MB`（前端 source 内存预算，默认 512）。
- **MapSpec/接口**：零新必填字段。若需要 spec 表达渐进意图，仅加顶层 Optional 字段 + identity upgrader（#1356 v1.4 先例纪律）；一期不做（SSE descriptor + env 已足够）。
- **每次行为变更可单独关闭**：LOD 换档、patch 通道、worker 调度各自 short-circuit 于 env / descriptor 存在性。

## D5 并发与一致性纪律（直接继承，不重造）

- 任何异步构建回写缓存：**先捕获 epoch，再 put_if_current**（mvt.py:1621/1632 模式）；并发同 key 计算走 SingleFlight（mvt.py:1703；有界等待 + 诚实降级）。
- 新派生投影（LOD 层、patch 基线）必须经 `ref_lifecycle` 失效契约（ref_lifecycle.py:26-40）：overwrite/rollback/evict 后绝不允许旧 LOD 服务——直接复用 `invalidate_ref_caches` + observer hook（raster_tile_service.py:279-304 先例）。
- 前端所有异步应用带 generation token（renderer.ts:44 先例）+ 会话切换取消（use-sse-stream.ts abort 先例, :811）。

## D6 退化链顺序（渲染退化）

预算/质量判据触发时按固定阶梯降级（每级产显式证据事件）：
1. 降 LOD 档（换 source URL/数据档）
2. 视口抽稀加密度（thinFeaturesForViewport, lib/utils/geo.ts:170——预算仍取 renderer.ts:37 口径）
3. 隐藏非焦点层（可见性 layout 切换，可逆）
4. 兜底：MVT 通道（服务端裁剪）替代内联
反方向恢复同样逐级；禁止跳级恢复（防抖动）。退化状态经 useHudStore 披露面可观测。

## D7 命名词条（进 UBIQUITOUS_LANGUAGE）

- **Progressive Loading**（渐进加载）：首帧概览 → 视口细节的两阶段装载。
- **LOD Tier**（数据分级档）：同一 ref 的泛化等级（服务端预派生），与 data_tiers 的 inline/scan/export **量纲档**正交。
- **Source Patch**（源补丁）：同一 ref 两个 content_revision 之间的要素级 diff 及其应用。
- **Render Degrade**（渲染退化）：预算超限时的确定性降档链（D6）。
- **Ref Ticket**（提货券）：已有词条（ref_id），本方向不改变其语义。

## D8 明确不做（非目标）

- ❌ 重写 MVT encoder / 替换 MapLibre / 新 WebGL 渲染器
- ❌ 新 Data Fabric / 改 session_data 存储协议
- ❌ 坐标级 partial update（MapLibre 契约风险 > 收益，见 D2）
- ❌ 触碰 #1356 的 scene/样式 LOD 承载面（PARALLEL_OWNERSHIP.md §3）
- ❌ 服务端分布式缓存/跨进程协调（单进程三原语已够，Redis 只作 payload 权威）
