# G03-GEODATA-READINESS — M0 基线审计（2026-11-07）

## 1. 仓库实时状态（审计时点）

- `origin/master`：`585812ee4e2d75612f2b124e6fe6587af3deca5c`（2026-11-07 17:54 +0800，PR #2010 合并）
- 本方向 worktree：`../webgis-wt-geodata-readiness-fabric-20261107`，branch `zcode/goal-geodata-readiness-fabric-20261107`，从 origin/master 新建
- 开放 PR：仅 8 个 dependabot（#1978-#1986，pip/npm/actions 依赖区间），**无功能性在途 PR 冲突**
- Issues #1999–#2007 开放（导出/版面/截断/单位/summary/OpenAPI 漂移）——均属制图导出与工具摘要域，**不在本方向所有权内，不抢修**；仅 #2006/#2005（工具 summary 丢数值、网络距离/秒混淆）与 GD-04 量纲守卫擦边，若资格链集成被其直接阻断才做最小修复并声明。
- 远端 `feat/g01..h02-vnext`、`zcode/gis-data-onboarding-semantic-vnext`（PR #1868）、`zcode/goal-ogc-cog-data-services-v1`（PR #1878）**全部已合入 master**（merge-base 验证）。
- 下一个可用 ADR 编号：**0286**（最新 0285；有 `tests/test_adr_numbering_contract.py` 契约测试）。

## 2. 既有能力（不重做，只对接）

| 环节 | 现状 | 锚点 |
|---|---|---|
| 语义身份证 | `GISDatasetDescriptor`（ADR-0265）：kind/geometry/bbox/crs/字段语义+置信/temporal/license 槽位/双指纹 | `app/lib/gis/dataset_descriptor.py:285-330` |
| 铸造 | 采样窗 200 要素/16 字段/96KB，四种入口（ingest/mapspec/fabric/用户声明） | `app/services/dataset_semantics/builder.py:366-486` |
| 资格裁决 | 五态 `eligible/transform_required/degraded/blocked/unknown` + 修复操作词表（含实现背书） | `app/services/gis_harness/data_qualification.py:31-73` |
| 多源 adapter | data_fabric 16 类（postgis/ogc_api/wfs/wms/tiles/coverages/arcgis/stac/geoparquet/flatgeobuf/pmtiles/s3/geopackage/local_file/cog/stats_api），声明式 YAML 源注册表 | `app/services/data_fabric/registry.py:141-215` |
| 摄入管线 | IngestPipeline：detect→validate→dedup→profile→quality→store→descriptor→materialize，sha256 指纹，失败回滚 | `app/services/data_ingest/pipeline.py:120-306` |
| 上传 | .geojson/.shp(zip)/.kml/.gpkg/.csv/.tif，50MB/200MB 帽，路径清洗 | `app/api/routes/upload.py:123-203` |
| 字段提案 | 候选+置信+拒绝理由+needs_clarification，阈值 0.5/0.85/0.2 | `app/services/data_preparation/field_proposal.py:17-47` |
| 量纲守卫 | `measurement.py` CANONICAL_UNITS + 资格层单位闸 | `app/lib/gis/measurement.py`；`data_qualification.py:320-369` |
| 多数据预检 | `preflight_multi_dataset` verdict compatible/gaps/unknown + typed gap 码 | `app/services/dataset_semantics/preflight.py:24-462` |
| 角色绑定 | bound/not_found/ambiguous/not_authorized/stale/partial | `app/services/dataset_semantics/role_binding.py:37-45` |
| 生命周期 | ref_lifecycle 唯一失效权威；指纹变化→下游闭包 stale；blob refcount+GC；descriptor 8 版本 | `app/services/ref_lifecycle.py`；`data_lifecycle/service.py:259` |
| 安全 | SSRF（私网/IMDS/IPv6 映射）、zip 炸弹帽、路径穿越、ref id 白名单、三重下载帽 | `app/services/data_fabric/security.py:41-150`；`limits.py:19-118` |
| 大数据 | keyset 分页、流式批（64-1024，governor 压力感知，批边界取消）、幂等 | `app/services/feature_pages.py`；`data_fabric/streaming.py:1-43` |
| CRS | 诚实 CRS（不虚构 4326）、CGCS2000 等价集、axis_order suspect+SWITCH_XY | `data_ingest/pipeline.py:88-100`；`crs_safety.py:42`；`data_preparation/spatial_receipt.py:184-200` |

## 3. 断链清单（本方向的真正缺口，按严重性）

1. **【P0】Explorer 产出与语义链断裂**：explorer geocoded rows 直接入 session store，不经 IngestPipeline、不铸 descriptor → role binding 对 explorer 数据只能 not_found；fetch 只走 GovDataAdapter，与 16 类 fabric adapter 零互通（`validate_stage.py:35`、`fetch_stage.py:53`、`pipeline.py:140-145`）。
2. **【P0】工具分发面不消费资格**：`app/services/tool_dispatch_gates.py` grep descriptor/qualification 零命中；五态资格只在 recipe 编译面与 `compile_workflow_semantics` 生效，Agent 走裸分析工具时无资格闸。
3. **【P0】远程 GeoJSON URL 无 adapter**：`geojson` alias 被移除（#767）后未补（`registry.py:18-21`）。
4. **【P1】MapSpec 编译不消费 preflight/qualification**：`mapspec_compile_coordinator.py` 零命中。
5. **【P1】bbox-地名一致性缺失**：preflight 只做两两 bbox 相交，不与任务地名/视口比对；explorer geocode 无 bbox 交叉验证。
6. **【P1】切城市无失效**：只有数据指纹变化才 stale，viewport/城市变化不触发 overlay 重资格判定。
7. **【P1】表格（CSV）无语义身份**：IngestPipeline 拒绝非 FC；CSV+坐标上传无 descriptor 供给。
8. **【P2】license 不流入 ingest 正向路径**：`derive_descriptor_from_v3` 无 license 参数，上传数据 license 恒 ""（`builder.py:366-387`）。
9. **【P2】恶意 GeoJSON 专项 schema 校验未确认**：防线只有大小帽 + FC 形状检查；NaN/Infinity 坐标、超深结构、属性文本注入无专项校验。
10. **【P2】百万要素异步物化/断点续传未确认**：`limits.py:90` 提示存在但实现未证实。
11. **【P3】两套质量分不互通**：explorer 五维分 vs descriptor quality_signals。

## 4. 冲突矩阵与避让决策

| 潜在冲突面 | 决策 |
|---|---|
| dependabot PR #1978-#1986 | 只动 requirements/frontend 包清单，与后端数据服务零交集；不处理 |
| Issues #1999-#2007 | 制图导出/工具摘要域，非本方向所有权；不抢修 |
| #1868（统一数据准备）/ #1878（OGC/COG）已合并 | 作为底座对接；数据准备路由/OGC adapter 行为缺口只在必要处扩展 |
| #1972 typed envelope / #1994 PostGIS pool / #1998 载荷预算 | 不重做；readiness 结果进入其既有结构（ArtifactEnvelope/limits）而非平行结构 |
| 3D 地形/scene3d（g3dv2 系 worktree） | 完全避让 |
| Pi Runtime / Cartography Renderer / 前端主地图组件 | 不动；dispatch gate 只做薄对接 |

## 5. 环境验证

- Python 3.13.17（复用主仓 `.venv` 解释器，worktree 内运行）
- 冒烟：`tests/unit/gis_harness/test_data_qualification.py` → **29 passed / 71.6s**（2026-11-07）
- pytest 配置：asyncio auto、timeout 180s、`-m heavy` 需 geopandas/numpy/rasterio（本机 pip-only 可跑）、`real_services` 需真实 Postgres（默认自跳过）

## 6. 风险登记

| 风险 | 缓解 |
|---|---|
| 资格链已有多套词表（五态/gap 码/绑定态），新增易造平行词表 | GD-03 用**映射文档**把目标验收词表对齐到既有五态，不新增枚举 |
| tool_dispatch_gates 属共享接缝，改动波及全工具面 | 只加"读取语义 store 判 stale/unknown 则阻断"的薄闸 + feature flag |
| explorer→descriptor 桥接可能大改 explorer | 桥接放在 dataset_semantics/builder 新入口 + validate_stage 末端一跳，不动五段链结构 |
| 本地无真实 PostGIS/OGC 服务 | 用 repo 既有 fixture/stub 模式；环境边界在 INGEST_PERFORMANCE.md 明示 |
