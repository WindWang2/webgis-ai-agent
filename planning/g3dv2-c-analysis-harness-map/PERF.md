# PERF — g3dv2-c-analysis-harness-map

## 本 track 无新增性能基线

未触碰 tests/benchmarks/ 的 perf lane 文件与阈值（sse/serialization 等），
perf-budget-gate 不受本 PR 影响（无 .py 基线文件变更——`git diff
origin/master --stat -- tests/benchmarks/` 为空）。

## 计算复杂度声明（代码级，非跑分）

- `raster_mask_polygons`：O(N log N)（rasterio.features.shapes 内部扫描
  + 面积排序 N log N）；峰值内存 O(掩膜) + O(多边形环)；有界输出由
  max_polygons/max_total_vertices 钳制；
- `cut_fill_volume`：O(N) 单遍（掩膜/nodata/求和）；无分配放大（dz
  在压缩布尔索引上）；
- viewshed 工具 include_geometry：几何提取与内核串行（同请求线程池），
  61×61 合成 DEM 实测 ~0.2s（测试时长间接观测）；未做 3 轮 p50/p95
  基准（limitation——无生产负载模型支撑定阈值，遵任务书「非确定时不设
  固定通过阈值」）。

## 资源护栏

- 科学层：`_guard_cells` 像元包络复用（与既有量测同护栏）；
  `checkpoint()` 协作取消点（多边形化前/预算循环后）；
- 工具层：`_read_terrain_window` RasterResourceGuard 有界读（既有）；
- dispatch 缝：profile_chart ≤512 点（100KB chart artifact 口径内）；
- 绑定：会话状态 FIFO ≤8 条（投影有界）。

## 验证环境

16C/62G 本地；pytest 串行（-n 不可用——xdist 未装）；前端 vitest 并行
（1 处 perf 抖动见 EVIDENCE.md）。
