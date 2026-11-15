# G39 INTERFACES — 新增/触碰的生产接缝

## 新增文件（本域所有权）

| 文件 | 职责 |
|---|---|
| `app/services/raster_sampling/contracts.py` | 词表（站位模式/方法/无效原因/单位状态/距离模式）、预算常量、`RasterSamplingError` typed 错误、`SamplingPlan`/`SourceBinding`/`SampleRow`/`CoverageCounts`/`ExecutionReceipt` 模型、`SAMPLING_ALGORITHM_VERSION="g39.v1"`、`compute_params_digest` |
| `app/services/raster_sampling/stations.py` | 纯几何站位：`build_point_stations`（身份保持/重复披露）、`build_line_stations`（part 独立 chainage/顶点/端点/断口；geodesic=pyproj Geod，planar=米制投影；跨经线/极点/零长 typed 拒绝）、`crs_distance_capability` |
| `app/services/raster_sampling/kernel.py` | 纯采样：`sample_source_pixel`（floor 同 probe 口径）、`sample_bilinear`（4 支撑+权重+严格/renorm 双合同）、`classify_raw`（nodata/masked/non_finite 分型）、`apply_physical_once`（scale/offset 恰一次）、`world_to_grid`（旋转/非方形仿射反算）、`BandFacts.validate_method`（分类拒插值；未声明拒插值）、`supplement_band_facts`（band 17+ 真实头补读）、`raw_exact_if_needed`（>2^53 精确串） |
| `app/services/raster_sampling/reader_schedule.py` | 块调度：`execute_sampling`（每源一次 open；source/block 分组批读；批间 checkpoint 取消 + stat 代次复核 SOURCE_MUTATED_DURING_READ；`estimate_execution_cost` 值单元/条带最低成本闸）、`ReadMetrics`（opens/blocks/window_reads/decoded_bytes 按真实块布局记账） |
| `app/services/raster_sampling/freeze.py` | 输入冻结：`load_fc_ref`（授权 FC + digest）、`materialize_selection_input`（G24 handle→冻结 ref）、`freeze_source_binding`（data_revision+stat 代次）、`verify_binding_generation`（漂移 typed 拒绝）、`pick_line_feature` |
| `app/services/raster_sampling/store.py` | per-session JSONL 计划/收据 + CAS（`save_plan_cas`）+ by_key 幂等键收据 |
| `app/services/raster_sampling/publish.py` | 长表→`materialize_geoparquet`（fabric ref + 台账 + DataObject CAS）、站位层（一站一要素宽列）、`verify_readback`（行数/schema/抽样值/要素数）、`thin_series`（保端点/缺口/极值）、`build_display_curves` |
| `app/services/raster_sampling/jobs.py` | G23 接线：幂等键 `(session,plan,params_digest)`、执行前绑定现行性复核（S31）、收据短路（产物存活才认）、join 活跃 job、`run_async_job` 终态 |
| `app/services/raster_sampling/service.py` | describe/create_plan/revise(CAS)/preview(有界非权威)/execute/status(恢复因果)/results(分页+曲线)/cancel；`_resolve_bindings` 映射 G20 typed 错误 |
| `app/schemas/raster_sampling_schema.py` | 路由请求模型 |
| `app/api/routes/raster_sampling.py` | `/api/v1/raster-sampling/*`（require_owned_session + X-Session-Token） |
| `app/tools/raster_sampling_tools.py` | `webgis_raster_sampling_{plan,preview,execute,status,results}`（args_model 声明 ref_cursor；bounded 回执；execute wait≤60s + status 指引） |
| `frontend/lib/api/raster-sampling.ts` | 面板客户端（apiFetch transport） |
| `frontend/components/sidebar/raster-sampling/raster-sampling-panel.tsx` | 工作台（源/波段/语义/方法→计划→预览→执行→成果表+曲线+站位层+G18 下载；hover 同 sample_id；行点击 setViewport 定位） |
| `frontend/messages/{zh-CN,en-US}/rasterSampling.json` | 120 键（双语同键集） |
| `tests/raster_sampling/*`、`tests/integration/test_pi_g39_raster_sampling_journey.py` | oracle/合同/旅程 |

## 触碰的共享文件（加性最小）

- `app/services/jobs/lifecycle.py`：`JobKind.raster_sampling`（+2 行；DB 列 String(20) 无 CHECK）
- `app/tools/__init__.py`：`_TOOL_MODULES` +1 行
- `app/main.py`：router import + include（+2 行）
- `frontend/components/sidebar/data-sources-tab.tsx`：prepare 子页 +1 挂载
- `frontend/lib/i18n/messages.ts`：rasterSampling namespace 注册（import/类型/zh/en 各 +1）

## 关键行为合同

1. **插值需要显式连续语义**：`bilinear*` 要求波段 `continuous`（用户声明或元数据 classification tag）；categorical/undeclared 一律 `METHOD_INCOMPATIBLE`。dtype 不参与判定（S06/S07）。
2. **执行前绑定复核**：每次 execute 先 resolve 源并比对 `data_revision`+stat 代次（S20）；漂移 → `SOURCE_BINDING_CHANGED` 409。收据短路也过这道闸（S31）。
3. **批间 ABA 防护**：每批读前 stat（mtime_ns/size/ino）复核；漂移 → `SOURCE_MUTATED_DURING_READ`，整次弃件（S34）。
4. **解码记账按真实块布局**：窗口触达块面积×itemsize×bands（条带源 1×1 窗口按整行计，S18）；`MAX_STRIP_DECODE_BYTES` 超限 typed 拒绝并指引物化。
5. **长表 → G18**：`ref:fabric-parquet/<id>`（fabric_parquet 源锚）可直接 CSV/GeoParquet 交付并全行回读（S29）。
6. **成果存活检查**：results 前核 `fabric_parquet_ref_exists`；缺失 → `RESULTS_EXPIRED` 410（收据仍可读为历史，S32）。
7. **job 完成但无收据** → status 标 `recovery_required`（S25），不盲信 job 行。
8. **预览非权威**：revision 校验 + 均匀选站 + `preview: true` 标注；`PREVIEW_STALE` 409。

## 兼容边界

- G20 probe/预算、terrain_profile、raster_expression、elevation-profile 组件零改动（全套邻域测试绿）。
- 不新增 DB 迁移（JobKind 字符串加性；计划/收据走 BASE_STORAGE_DIR 文件存储先例）。
- pyarrow 为可选依赖（与 fabric/delivery 同口径）：缺失时长表 typed 失败（CONTENT_WRITE_FAILED），不静默降级。
