# ADR 0293 — 控制点影像配准与可交付地理栅格（G48）

- **状态**: Accepted（执行中）
- **日期**: 2026-11-15
- **任务编号**: G48-GCP-GEOREFERENCING
- **相关域**: `app/services/georeferencing/*`, `app/tools/georeferencing_tools.py`, `app/services/raster_tile_service.py`, `app/services/raster_authoring/*`, `frontend/components/georeferencing/*`

## 1. 背景与问题

WebGIS 用户经常需要将历史纸图、无人机正射截图、勘察平面图或无坐标静态影像（PNG / JPEG / 单页未配准 TIFF）配准到工程坐标参考系并纳入线上出图链路。既有仓库虽然具备 GeoTIFF 栅格瓦片渲染（G20）、COG 写入校验、MapSpec 图层注册以及 G23 持久作业 Runner 与 G36 图像解码器，但缺少端到端的控制点（GCP）影像配准闭环：
1. 无专门的源图接收入口、EXIF 方向诚实校验与不可变原图/归一化像素落盘；
2. 无带坐标归一化、SVD/QR 稳健求解、独立检查点（checkpoint）误差披露与过拟合诊断的 2D 仿射解算器；
3. 现有栅格瓦片与出版窗口在读取 3 通道或 RGBA 影像时忽略 alpha/mask 或对单波段各自拉伸，导致旋转斜边“虚假黑边”与失真；
4. 无面向配准长任务的 G23 取消护栏、重启恢复、MapSpec 图层装配、独立 GDAL 读回校验与受权下载适配。

## 2. 决策

1. **受控能力范围**：v1 明确支持 **PNG / JPEG / 单页未配准 TIFF**（uint8 灰度、uint8 RGB/RGBA、以及显式能力矩阵中的 uint16 单页灰度 TIFF）配合 **2D 一般仿射 6 参数模型**；明确拒绝多页 TIFF/GIF、SVG/PDF、VRT/任意 URL、地理度制/英尺/垂直/复合 CRS 作为“计量 CRS”假冒成功。
2. **复用现有底座、不另起孤岛**：
   - 不新增数据库迁移头与平行表；草稿/冻结配准方案保存在 `DATA_DIR/georeferencing/<session_id>/` 下的 `fcntl.flock` 原子 JSON 文件，原始/归一化像素与导出栅格双落 `BlobStore`（内容寻址完整性校验）及会话 scoped 路径；
   - 通过 `app/services/jobs/lifecycle.py` 增补 `JobKind.georeference = "georeference"` 复用 G23 `submit_async_job` / `run_async_job` 与启动恢复勾子；
   - 通过 `session_data_manager.store(..., prefix=...)` + `apply_gis_mutation(UpsertLayerIntent)` 复用 G20 live 瓦片与 MapSpec CAS 出版，并把“数据生成成功”与“MapSpec 图层绑定 CAS 冲突”明确分层；
   - 仅扩展 `raster_tile_service.py` / `raster_authoring/render.py` / `raster_authoring/publication_window.py` 的 **RGBA / 原始 uint8 直通与内嵌 mask 适配器**，保持所有既有 `RasterExpressionSpec` 与栅格样式向后兼容。
3. **数值与几何纪律（防黑盒陷阱）**：
   - 双线性（bilinear）重投影必须使用 **直通 alpha 预乘权重重采样**（同时重投影 `c*w` 与 `w`，仅在 `dst_w > 1e-6` 处除回），绝不在插值前或插值后抹黑半透明边界；合法纯黑像元 `(0,0,0,255)` 与旋转透明四角语义严密区分；
   - **禁止**把原始 GCP 直接交给 `rasterio.warp.reproject(..., gcps=...)` 让 GDAL 自动重拟合；而是基于启用拟合点解算并冻结 6 参数 `src_transform = Affine(a1, a2, a0, b1, b2, b0)` 直接重投影；
   - 点对 `src -> map` 严格遵守 `px/py ∈ [0, W]×[0, H]` 与 GDAL corner `(0,0)` / pixel center `(0.5, 0.5)` 约定，WGS84 地图拾取使用 `pyproj.Transformer(..., always_xy=True)` 转换并记录原始与目标坐标溯源。
4. **Pi 集成（Deferred Tools Only）**：
   - 注册 6 个 deferred 工具至 `app/tools/__init__.py`（`webgis_georef_inspect`, `webgis_georef_plan`, `webgis_georef_preview`, `webgis_georef_execute`, `webgis_georef_status`, `webgis_georef_report`），**不进入** `NATIVE_TOOL_NAMES`；
   - 严格受 `verify_session_owner`、有界回执（只返回摘要与 artifact_ref）与防编造检查点（不可猜坐标、不可静默删点）约束。

## 3. 后果

- 正面：无需引入新 Alembic 迁移头或平行 job/MapSpec 系统，前后端 UI 与 Pi 共用同一配准规划/预览/作业/交付引擎，消除了 RGBA 瓦片拉伸与旋转黑边缺陷。
- 约束：未来若升级至 TPS/多项式或前端大型 TIFF 原图分块浏览（IIIF/DeepZoom）将在本模块 `DraftPlan.transform_model` 版本化字段上扩展，不影响已有 `affine_2d` 方案。
