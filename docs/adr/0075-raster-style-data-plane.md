# ADR-0075: Raster Data Plane 基础契约 —— 工件描述子与样式≠重算

日期: 2026-08-27
状态: accepted

## 背景

栅格当前是两条互不相通的渲染路径（审计 Reviewer E）：

- **瓦片流**（raster_tile_service）：单波段恒灰度，`cmap_name` 是死参数
  （只进缓存键不进渲染体）；band 组合写死前 3 波段；
- **烘焙 PNG**（raster_cartography_converter）：colormap 烘进像素——换色 =
  重跑转换 = 新 PNG + 新 imageRef。

即**样式改动 = 重新计算**，且无结构化工件元数据（descriptor 只有
`raster_capable` 布尔启发式）。目标（任务书 §17）：`修改显示样式 ≠ 重新运行
遥感计算`，并继续遵守 Zero Big Data in Context。

## 决策

`app/schemas/raster_spec.py` 定义两个契约对象：

1. **`RasterArtifactDescriptor`** —— 注册期描述子：band schema（每波段
   dtype/min/max/description）、CRS、bounds、nodata、overview 有无。
   `inspect_raster_artifact` 一次计算（降采样 ≤2048 长边，调用方 to_thread），
   消费方零 IO 读取；有界元数据可进 LLM 上下文。
2. **`RasterStyleSpec`** —— 样式契约（MapSpec paint 侧 `paint.raster_style`）：
   bands（1-based 组合，≤3）、stretch 覆盖、colormap、opacity、resampling。
   `cache_key()` 提供数据平面 (数据, 样式) 二元组缓存键的样式侧。

瓦片服务接线：`cmap_name` 真实生效（单波段经 matplotlib colormap LUT 着色，
未知名回退灰度）；`bands` 参数读取指定波段并以各自的全局 stretch 归一；
cmap/bands 进瓦片缓存键。XYZ 路由暴露 `cmap` / `bands` 查询参数（带校验）。

## 后果

- **样式改动 ≠ 重算**在瓦片路径成立：换 colormap/bands 产生新缓存条目，
  同一 raster artifact 不被重新计算。
- 分析叠加路径（烘焙 PNG）暂不迁移——统一两条路径需要 imageRef overlay
  走 XYZ 源，属下一阶段（含 COG/overview 构建、产物 COG 化）。
- colormap LUT 进程内缓存（64 名上限）；matplotlib ≥3.9 的 colormaps 注册表
  API（cm.get_cmap 已移除）。
