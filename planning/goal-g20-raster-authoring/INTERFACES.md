# INTERFACES — G20 共享接线与并行契约

## 1. 权威与单一写入

- **MapSpec 仍是唯一权威**。栅格表达不进入任何浏览器 Zustand 私有 store、不建第二
  style store/ID 系统/撤销栈/evidence store。
- 提交路径唯一：`app/services/raster_authoring/service.py::commit_expression()`
  → `apply_gis_mutation(session_id, UpsertSourceIntent | UpsertLayerIntent,
  origin="user"|"agent", expected_revision=…)`。UI HTTP 路由与 Pi `@tool` 都调用
  **同一个** `commit_expression()`，不是各写一套。
- 复用既有 intent（不新增 intent、不新增 Body codec、不改
  `tests/unit/test_mutation_registry_consistency.py` 的硬编码旗标集合、
  不改 `mutation_parity_corpus.golden.json`）：
  - **单笔** `UpsertLayerIntent(layer=…, source_data=<numeric_raster 源条目>)`
    原子写入 source + layer（`raster_style` + `legend_spec`），全新会话由引擎的
    auto-init 骨架补 `version`。`app/services/mapspec_source.py::store_data`
    新增 `numeric_raster` typed passthrough 分支（与 model3d/vector 同款）。
  - 独立评审 P1-3 后由「最多 3 笔」改为「恒 1 笔」：一次 CAS、一个
    checkpoint、一条 provenance、一次 undo 即回到上一表达；失败时
    「nothing was written」为真。
  - 走 `SessionLockRegistry`、CAS `expected_revision` 与 `pre-commit` 校验；
    `origin="user"` 缺 CAS 由 engine 直接拒绝。
- 不写 `session_token` / owner token 进 MapSpec、日志、导出产物。

## 2. 契约版本与 schema 分区

- MapSpec 文档版本保持 `1.9`（`KNOWN_VERSIONS` / `LATEST_VERSION` 不变）。
  理由：本批 G15/G16/G21 各自会在自己的分支上做 additive 变更，抢同一个
  `1.10` 只会制造合并冲突；`mapspec_schema.py` 全模型 `extra="allow"` +
  canonical 保序深拷贝，纯 additive optional 字段无需迁移、旧文档 `parse_mapspec`
  结果逐位不变。本任务用**独立子契约版本**表达演进：
  `RASTER_EXPRESSION_SCHEMA_VERSION = "raster_expression.v1"`
  （写入 `source.schema_version` 与 `layer.raster_style.schema_version`）。
- 新增模型（仅 raster 子模型区，与 frame/label/flow 分区不相交）：
  - `NumericRasterMapSpecSource(type="numeric_raster")`：
    `refId, bounds, crs?, dataRevision, dataFingerprint?, schema_version`。
    只承载**数据身份**，不含样式、不含 token。
  - `MapSpecRasterExpression`：`schema_version, style_id, mode(single|rgb), bands[1..3],
    stretch{min,max,per_channel?,source,method,sample_count}, colormap?, opacity,
    resampling, classification?, data_revision, legend`。
  - `MapSpecLayer.raster_style: Optional[MapSpecRasterExpression]`。
- 同源双层不同样式：样式在 **layer** 上，源在 `sources` 里；compiler/runtime 为
  `numeric_raster` 层派生 `sourceId + "::" + layerId` 的 MapLibre 源（每层独立瓦片 URL
  与缓存身份），共享源 ID 不串色。
- 生成物：新增模型加入 `SCHEMA_EXPORT_MODELS`，运行
  `python -m app.lib.cartography.ts_projection` 重新生成
  `frontend/lib/mapspec-compiler/types.generated.ts`（`tests/cartography/test_ts_projection_contract.py`
  漂移闸必须绿）。

## 3. 数据面（tile / descriptor / probe）

- 所有新端点在**新文件** `app/api/routes/raster_authoring.py`；`layer.py` 保持零修改
  （新路由复用其 `_resolve_raster_tile_path`、`_png_tile_response`、`get_raster_tile`
  的守卫模式与 `app/services/raster_tile_service.py` 的缓存）。
- 瓦片 URL（运行时生成，不落 spec）：
  `/api/v1/raster-authoring/tiles/{ref_id}/{z}/{x}/{y}.png?session_id=<sid>&layer_id=<lid>&style_id=<sid-hash>`
  - `session_id` 由 `MapSpecRuntime.setSessionScope()` 注入模板占位符 `__SESSION_ID__`；
  - 凭证由既有 `frontend/lib/map-kit/tile-auth.ts` 的 `transformRequest` 注入 header
    （第一方 URL 才注入），URL 里没有 owner token；
  - 后端 `require_owned_session` + `session_data_manager.get_ref_data(owner_token=…)` +
    `resolve_scoped_raster_path` 三重 owner 校验；
  - 后端把 `style_id` 与**当前 MapSpec** 中该层的 `raster_style.style_id` 比对，
    不一致 → `409 stale_style`，绝不拿旧 URL 出旧像素。
- 缓存身份：`(session, ref, z,x,y, tile_size, style_id, data_revision, stats_method)`。
  `style_id` 是规范样式 JSON + 数据修订的 sha256；`data_revision` 额外进 ETag/LRU 键。
  全局拉伸范围按 `(raster_path, indexes, stretch_resolution_key)` 只解析一次并带 TTL，
  不按瓦片各算范围；手动 stretch 完全确定，自动 stretch 基于有界样本并把
  `method/sample_count/clip` 冻结进 `style_id`，缩放/导出不会偷偷重估。
- 完整样式参数进 `render_raster_tile(..., style=…)`（向后兼容默认参数）：
  bands/stretch/resampling/colormap/opacity。opacity **只在服务端瓦片里应用一次**，
  不再同时用 MapLibre `raster-opacity`；未知色带/反向范围/NaN/非法 band/2 波段 RGB
  在提交边界拒绝，不生成假成功瓦片。
- 渲染失败：抛结构化 `RasterTileRenderError`，路由返回 `502` + 可观测 code，
  **不缓存、不返回透明 PNG 伪装空白地图**（旧 `render_raster_tile` 的透明兜底只在
  「窗口与栅格不相交」这一合法空域保留）。

## 4. 像元检视 / 元数据契约

- `GET /api/v1/raster-authoring/descriptor/{ref_id}`：波段清单（1-based、dtype、
  description、**只称第 N 波段**，`role` 仅当真实元数据可证明时给出）、
  CRS、transform、bounds、nodata、scale/offset/unit（读取 rasterio tags / scales /
  offsets / units，缺失一律 `unknown`，不猜）、有界直方图（桶数 ≤64、
  采样边 ≤2048、披露 `method`/`sample_count`/`approximate`）、`data_revision`。
- `POST /api/v1/raster-authoring/probe`：`{ref_id, crs, points[], bands[], data_revision?}`，
  点数 ≤16、波段 ≤16。返回逐点 `{status: ok|outside|masked|no_crs|invalid_crs|stale,
  row, col, requested_coord, source_coord, values:[{band, raw, scaled, valid, unit, scale,
  offset}], data_revision}`。`raw` 是源像元原值（窗口/像元读取，不从显示 PNG 反推）；
  `scaled` 单独列。缺单位/scale 明确 `unknown`；整数 dtype 不单独证明类别。
- RGB mask 规则（图面与检视共用）：所有请求通道都有效才绘制/报告 `ok`；
  任一通道 mask/nodata/非有限 → 该像元透明并报 `partially_masked` + 通道明细。
- 快速点击 A、B：前端 `probeRequestSeq` 单调递增，只有最后一个响应可写面板；
  Pi 解释只接收当前序列的 probe `probe_id`。

## 5. Pi 工具边界

- 新模块 `app/tools/raster_expression_tools.py`，在 `app/tools/__init__.py`
  `_TOOL_MODULES` 追加一行（共享文件最小接线）。
- 工具：`raster_expression_describe`（只读，有界波段/统计）、
  `raster_expression_apply`（写，effect=state_mutation，`map_mutations`）、
  `raster_expression_probe`（只读源像元）、
  `raster_expression_export`（冻结修订，产出地图 PNG + 表达说明）。
- 参数只接受 (`ref_id`, `layer_id`, 显式 style 字段, `point_crs`, 少量点)；
  拒绝任意本地路径 / 内部 tile URL 拼接。回执只含 revision、style 摘要、图例、
  输出 ref 与有界采样，不塞影像数组/直方图全桶。
- 错误统一为 `{success:false, code, message, correction_hint}`，与 UI 同源；
  UI 与 Pi 的错误码表来自 `app/services/raster_authoring/errors.py`。
- stubLLM 旅程沿用 `tests/integration/test_pi_stub_llm_journeys.py` 的
  `JOURNEY_SCRIPT` 机制；不造新 SDK 协议，不改 G12 的 pin/extension。

## 6. 前端接线清单（局部挂载，不改全局骨架）

| 共享文件 | 本任务改动 | 兼容约定 |
|---|---|---|
| `app/main.py` | +1 import、+1 `include_router` | append 到末尾分组；与 `#2022`/G18 冲突时按最新主干校正 |
| `app/tools/__init__.py` | `_TOOL_MODULES` +1 行 | 不改注册循环 |
| `app/lib/cartography/mapspec_schema.py` | 新增 2 模型 + 1 字段 + 2 导出项 | 不动 KNOWN_VERSIONS / frame / label / flow |
| `frontend/lib/mapspec-compiler/types.generated.ts` | 生成器重生成 | 不手改 |
| `frontend/lib/mapspec-compiler/compiler.ts` | `KNOWN_SOURCE_TYPES` +1；numeric_raster 源分支；raster 层 paint 分支 | 旧 `type:"raster"` image 路径逐字保留 |
| `frontend/lib/mapspec-runtime/runtime.ts` | `setSessionScope` 记 sid；`applySource` numeric_raster 分支 | 同源双层派生 source id；样式变化走 replaceExisting |
| `frontend/components/hud/layer-style-panel.tsx` | 挂载 `<RasterAuthoringPanel/>` | 旧禁用控件保持禁用；不改全局布局 |
| `frontend/messages/{zh-CN,en-US}/hud.json` | 新增 `rasterAuthoring.*` keys | 两个语言 key 集合一致（i18n 闸） |
| `frontend/lib/map-kit/exporter.ts` / `mapspec-to-svg.ts` | 冻结修订 + 栅格嵌入/离线披露 | 不重写出版链；`vector_svg_fallback_raster` 语义不变 |

新增文件全部在 `app/services/raster_authoring/`、`app/api/routes/raster_authoring.py`、
`app/tools/raster_expression_tools.py`、`frontend/lib/raster-authoring/`、
`frontend/components/hud/raster-authoring-panel.tsx`、`tests/unit/test_raster_authoring_*.py`、
`tests/cartography/test_raster_authoring_*.py` 等本任务命名空间。

## 7. 导出 / 同语义快照

- `POST /api/v1/raster-authoring/export` 输入 `{expected_revision, format}`；
  服务端在**一次**读取中冻结 MapSpec（revision + style_id + data_revision），
  用冻结样式渲染有界位图（≤2048 边）并绘制图例 + 表达说明，输出真实 PNG 文件；
  返回受权下载引用与 `snapshot` 证据。导出期间样式变化不改已冻结快照。
- SVG/PDF：`mapspec-to-svg.ts` / `mapspec_to_svg.py` 的 raster 分支接
  `raster_style` + 快照 data-URI（离线可读，不依赖 session URL）；无法忠实表达的
  格式显式 `degraded`/`unsupported` + 原因，不静默丢层。
- 位图嵌入与源像元分辨率限制在产物 sidecar 中如实声明；DPI 提升不等于增加细节。

## 7b. 评审后的补充接线（轮 5）

- `app/services/mapspec_source.py::store_data`：`numeric_raster` typed
  passthrough（additive 分支，旧 geojson/raster/vector/model3d/data_fabric
  行为不变；`tests/unit/test_mapspec_source.py` 24 passed）。
- `app/services/raster_authoring/delivery.py`：导出下载**恒**签发 HMAC 能力
  URL（登录用户 `window.open` 不带 Bearer，裸路径必然 401）；沿用既有
  `download_map_export` 的属主 + 签名双重校验。
- `app/services/raster_authoring/source.py`：`_SOURCE_CACHE` 按
  `(session, ref, path, mtime_ns, size)` 5s TTL；`_inspect_bounded` ≤16 波段
  / ≤1024 边；`_read_band_meta` ≤64 波段；统一 `_open`（`rasterio_env` +
  `open_raster`）。`bands_truncated` 通过 descriptor `disclosures` 暴露。
- `frontend/components/layout/context-panel.tsx`：在 layers 分支追加 1 行
  `<RasterAuthoringLauncher />`，供无 HUD 图层时首次挂载。**这是与开放
  `#2032` 的唯一文本冲突段**（局部导入 + 局部挂载，不改布局骨架/主题）；
  合并顺序以上游为准，必要时保留双方两行。
- 面板：默认 `autoStretch=true`（不把近似统计写成精确 manual）；RGB 手动
  模式全局端点由逐通道派生，隐藏全局死滑块；probe 默认请求全部波段（≤16）
  并支持多选；卸载 abort 在途窗口读。
- Pi `webgis_raster_expression_apply` 回执显式 `reconcile_via:"mapspec_event"`
  + `mapspec_revision`；不把完整 MapSpec 塞进 LLM 上下文，live reconcile
  由既有 agent MapSpec 事件通道承担。

## 8. 生命周期 / 预算

- 复用 `ref_lifecycle` 的 `invalidate_raster_ref` additive hook（不写自动刷新引擎）。
- 领域上限：窗口边 ≤2048、请求波段 ≤16、点数 ≤16、直方图桶 ≤64、
  tile z ≤20、并发渲染由既有 singleflight + `tile_lru_cache` 与
  `_layer_data_budget` 约束；超限返回 `truncated` 事实而非无限排队。
- 数据修订变化后旧 style snapshot 仍可读其来源，但新的 probe/tile 校验当前绑定，
  旧统计不搭配新像元；失效后完成的旧请求经 `put_if_current` 不回灌缓存。
- 全机重型阶段按任务书共用 `webgis-heavy-stage.lock`；本任务 Python 测试默认
  `OMP_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`、`MKL_NUM_THREADS=1`，
  vitest 单 worker，不并行起全量构建。
