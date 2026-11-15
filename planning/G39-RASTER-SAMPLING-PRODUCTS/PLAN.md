# G39 PLAN — 通用数值栅格点集与沿线采样成果

主所有权目录：`app/services/raster_sampling/`、`app/schemas/raster_sampling_schema.py`、`app/api/routes/raster_sampling.py`、`app/tools/raster_sampling_tools.py`、`frontend/lib/api/raster-sampling.ts`、`frontend/components/sidebar/raster-sampling/`、`frontend/messages/{zh-CN,en-US}/rasterSampling.json`、`tests/test_raster_sampling/`。

## 关键决策

1. **不加 DB 表/迁移**：计划/收据按 field_compute 先例用 per-session JSONL 原子文件（`DATA_DIR/.webgis-agent/<session>/raster_sampling/`）；job 走既有 AnalysisTask（kind=raster_sampling 纯加性，String(20) 无 CHECK）。成果走 `publish_data_object` CAS + 会话 ref。
2. **站位身份**：服务端 mint（`sp_` + plan seed + 序列），点集父身份 = 输入 feature id（缺失时披露 generated）；线路 part/chainage 由几何决定，重放稳定。
3. **不可变执行**：执行前对每个源 stat（mtime_ns/size/dev/ino）；执行中读取前后复查 + 打开期间持 stat 锁窗口。跨批（分批读）每批前重验 stat 不变；不一致 → `SOURCE_MUTATED_DURING_READ` typed 失败并丢弃已产内容（不发布混合样本）。不能建立保证的组合（网络源/不可 stat）→ typed 拒绝可重现执行。
4. **双插值合同**：`source_pixel`（floor 反算，与 probe 同口径，边界 tie = floor 即左/上像元）与 `bilinear`（4 支撑 + 权重披露；任一支撑无效 → 严格无效；可选 `bilinear_renormalized` 显式不同方法带 support_ratio）。分类声明波段只允许 source_pixel。
5. **距离轴**：地理 CRS 用 pyproj `Geod` 测地（WGS84/对应椭球）；投影 CRS 平面米制（单位非米 typed 拒绝）；Web Mercator 距离不冒充地面里程（3857 输入按投影平面采样 + 披露，或要求换 CRS —— 平面米制合法但不是测地；披露 mode）。每 part 独立 chainage，part 断口在样本行与曲线上显式。
6. **多源**：≤4 源、每源 ≤8 显式波段、总波段 ≤16、每源在自己的真实网格采样（不重采样对齐）。
7. **预算（初始）**：站位 ≤50,000；source-band 值单元 ≤500,000；活跃读缓存 256MiB；本域临时/结果 512MiB；批 2,000 站位；预览 64 站位；长表行 ≤500k（对齐 G18 帽再核）。分配前 typed 阻断，无 `budget_ok=True` 旁路。
8. **曲线/图表**：结果 API 内联有界抽稀曲线（保端点/极值/缺口断点，方法披露）；完整数据在长表/站位 ref。不新建 mapspec 组件类型。
9. **地图联动**：站位点层 = 会话 ref（GeoJSON FC，一站一要素，波段宽列 + 无效计数）；面板通过现有 layer 通道加载；hover 同步用 station id。

## 模块与 WP 映射

| WP | 文件 | 内容 |
|---|---|---|
| WP01 | planning/*（本文件、BASELINE、DEDUP） | 盘点完成 |
| WP02 | `services/raster_sampling/contracts.py`、`store.py` | 词表/预算/typed 错误/SamplingPlan+SampleRow pydantic/ALGO_VERSION；JSONL 计划存储 + CAS |
| WP03 | `services/raster_sampling/freeze.py` | 材料冻结：点/线 ref 冻结 FC 快照（content hash）、源 stat 快照、G24 handle → materialize、权限重验 |
| WP04 | `services/raster_sampling/stations.py` | 点集站位 + 线路站位（geodesic/planar、part/chainage、重复顶点/零长/跨经线 typed 处理） |
| WP05 | `services/raster_sampling/kernel.py` | source_pixel/bilinear/bilinear_renormalized、每波段有效性、scale/offset 恰一次、outside 不钳位、band17 补读元数据 |
| WP06 | `services/raster_sampling/kernel.py` + contracts | 无效分类学（nodata/masked/non_finite/outside/transform_failed）、单位 unknown 披露、JS 安全整数编码 |
| WP07 | `services/raster_sampling/reader_schedule.py` | source/band/block 分组、reader 生命周期重用、窗口批读、块数/解码字节/cache hit 记账、取消检查、非分块源最低成本预估 |
| WP08 | `services/raster_sampling/publish.py` | 长表 parquet（pyarrow）→ publish_data_object；站位 GeoJSON ref；chart 曲线抽稀；receipt（源 revision/geometry hash/method digest/计数/预算事实/输出摘要）；先内容后登记后回读验证 |
| WP09 | `services/raster_sampling/jobs.py` + `jobs/lifecycle.py`（+1 枚举） | 幂等键 (session,plan_id,params_digest)；收据短路；join 活跃；取消/失败 typed；恢复：job+产物+receipt 因果 |
| WP10 | `schemas/raster_sampling_schema.py`、`api/routes/raster_sampling.py`、`tools/raster_sampling_tools.py`、`main.py` include、tools 注册入口 | describe/plan/preview/execute/status/results/cancel；Pi 工具有界回执 + refs |
| WP11 | `frontend/lib/api/raster-sampling.ts`、`frontend/components/sidebar/raster-sampling/raster-sampling-panel.tsx`、data-sources-tab 挂载、messages | 面板全流程 + 表格分页 + 多单位曲线 + 地图站位联动 |
| WP12 | `tests/test_raster_sampling/` | oracle（常量/线性/棋盘/旋转仿射/非方形/scale/offset/mask 孔/双源/测地）、预算、并发、取消、恢复、API、Pi 真实子进程旅程、G18 回读；前端 vitest |

## 验证旅程（必须真实执行）

- J1 后端单元/契约：stations/kernel/schedule/publish oracle 全绿。
- J2 API 集成（真实 FastAPI TestClient + 临时 DATA_DIR + 合成 COG/GeoTIFF）：S01-S20、S22-S26、S31-S34。
- J3 Pi 真实旅程：真实 Pi 子进程 + 生产 extension + dispatch → raster_sampling_execute → 长表/站位 ref → receipt（S30）。
- J4 G18 回读：长表 DataObject → data_delivery resolve → CSV 下载 → 全行校验（S29）。
- J5 前端 vitest：面板流程、抽稀缺口断线、多单位、hover 同步、恢复（S27/S28 局部）。
- J6 资源：5,000 点同块 → 打开次数=源数、触块数有界（S17）；巨条带拒绝（S18）。

## 风险与兼容

- `pyarrow` 可用性：data_delivery 已依赖（parquet lane）——执行时确认 requirements 锁定版本，不可用则长表降级 CSV+JSON（披露）。
- pyproj `Geod` 在锁定版本可用性：requirements 既有 pyproj（CRS 变换在用）。
- rasterio `block_shapes`/`is_tiled`：S17/S18 记账用。
- 兄弟分支冲突面：`app/main.py` include 行、`data-sources-tab.tsx` 挂载行、messages 注册 —— 仅加性，合流时重生成。
