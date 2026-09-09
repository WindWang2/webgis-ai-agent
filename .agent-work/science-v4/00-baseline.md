# Science V4 — Baseline Audit（Phase A，只读）

- 分支：`feat/science-v4-spatial-geoai`，基线 = origin/master `445ad30`（PR #1162 merge）。
- 审计方式：2 个只读 subagent（算法实现侧 / 契约质量侧）+ 主 Agent 交叉核实 registry、域包、工具接线、oracle 回放。
- 环境：numpy 2.4.6 / scipy 1.17.1 / shapely 2.1.2 / geopandas 1.1.4 / pyproj 3.7.2 / rasterio 1.4.4；oracle 回放 1084/1084 绿（21.5s）。

## 事实图（关键结论，全部有 文件:行号 证据）

### 契约面
- 181 个 AlgorithmDescriptor 分布于 14 个域包（`app/lib/gis/algorithms/`）。
- **`resource_envelope` 0/181 生产者**；消费方 `backend_selection.py:293` 空转。字段/模型已存在（`algorithm_registry.py:109-156,295`）。
- **`cancellation_profile` 0/181**；词表 `algorithm_registry.py:100-106`。
- **`NumericalTolerance` 0 实例化**（`algorithm_registry.py:159-193`）；各处 `tolerance=` 命中均为自由文本 `numerical_tolerance`。
- `approximation_class` 仅 interpolation 域 13 处。
- `uncertainty_producer_tests` 仅 7 个 descriptor（kriging 2 键、statistics 5、remote_sensing 1）；terrain 0/26、point_pattern 0/12。
- 机器门：PRODUCTION/VALIDATED 必须有 conformance tests（`algorithm_registry.py:687-693`）+ 节点级 AST 校验（`:711-753`）。**没有**「必须声明 envelope/cancellation/tolerance」的 ratchet。

### 算法面
- Kriging（`geo_analysis/kriging.py`，2780 行）：OK/UK(线性)/Indicator/MM1-collocated/Block 已实现；**SK/KED/嵌套 variogram/normal-score/log 变换/全 CoK(LMC)/SGS/时空克里金缺失**。CV（折内重拟合 + z-coverage）与方差面+PI95 已实现。护栏常数齐（MAX_INPUT_POINTS=500k 等）；求解 chunk=1024 有取消 checkpoint。
- Terrain（`geo_analysis/terrain.py`，2478 行）：TPI/TRI/曲率/开放度/SVF/horizon/geomorphons、PF 填洼（heapq，全量非分块）、D8/D∞、flow acc/length、Strahler、watershed、TWI/SPI/LS 已实现；**breaching/HAND/Shreve/Pfafstetter/hypsometry/solar radiation 缺失**；viewshed 为 R3 扇区近似。
- **terrain.py 全文件 0 取消点**（50M 像元护栏 `_guard_cells`，堆循环/扇区循环不可取消）。
- 其他零取消文件：tin_interpolation/trend_surface/spatiotemporal_eha/glcm/spectral/raster_pca/spatial_weights/sar 系。
- spatiotemporal_eha.py 只是 EHA 热点分类；时空克里金无任何实现。

### 错误披露面
- `scientific_errors.py` 14 类（全 ValueError 子类）；**InvalidGeometry 定义但 0 raise**；InvalidCRS 仅 3 处。
- **raw pyproj.CRSError 泄漏**：`geo_processor/core.py:466` `gpd.GeoDataFrame(rows, crs=source_crs)`，source_crs 零校验（core.py:201-221）；CRSError MRO 是 RuntimeError → 逃出 `except ValueError`（tools/registry.py:1547）→ TOOL_ERROR 丢 correction_hint。**即 xfail `tests/quality/test_scientific_regression.py:124`（KNOWN-GAP #1）**。
- **geometry 静默修复**：aggregation.py:378 / core.py:466 / geo_processor/geometry.py:78-236 / geometry_ops.py:120 / density.py:597 / statistics.py:341 全部静默 make_valid 或 buffer(0)，无计数/披露/strict mode。zonal stats（raster_ops.py:53）**无 validity 门**。**即 xfail `test_scientific_regression.py:203`（KNOWN-GAP #2）**。死参数 `auto_repair`（spatial_analyzer.py:58）。
- 裸 rasterio.open：tools/terrain_analysis.py:150/:260（RasterReaderError 包装仅存在于 lib reader）。

### 质量门
- 字节 parity ratchet：quality-manifest.json（test_quality_manifest_gate.py）、ALGORITHM_CATALOG.md / BENCHMARK_MANIFEST.md（test_foundation_v2_infra.py:188-211）、CONTRACT_DRIFT_REPORT、6 张认证表。
- BENCHMARK_MANIFEST 投影 59/181 heavy 算法的 complexity/approximation_class/envelope/变体窗口/取消/容差 → **补声明后必须再生成**。
- 全仓恰好 4 个 xfail（strict=False）：2 个科学（本 Epic ownership，必须收敛）+ 2 个 cartography（不属本 Epic，不动）。
- 测试：862 文件 / ~9257 函数；ci-local 串行无 xdist；coverage ratchet 75。

## Scope 冻结

**本 Epic 解决（ownership 内）**：科学契约 ratchet（envelope/cancellation/tolerance）+ terrain/interpolation 域声明填平；typed errors 补齐 + pyproj 泄漏修复（收敛 KNOWN-GAP #1）；geometry repair 披露 + strict mode（收敛 KNOWN-GAP #2）；SK/KED/normal-score/嵌套 variogram/SGS/CoK-LMC/ST-kriging；breaching/HAND/Shreve+Pfafstetter/hypsometry/solar radiation；分块 Priority-Flood + terrain 取消下沉；backend 变体 parity harness；oracle 扩充 + perf/resource corpus。

**明确不做**：generic scheduler/storage/cartographic rendering；cartography 2 个 xfail；GWR/MGWR/SAR 深化（其他 Epic backlog）；全量 181 算法的声明迁移（只填 science-owned 的 interpolation+terrain + ratchet 冻结其余）；ML 训练设施/GPU 路径。
