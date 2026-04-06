# B004 空间分析引擎 - 实施记录

## 设计回顾

根据需求：
1. ✅ MVP 砍掉 Celery → 使用 FastAPI `BackgroundTasks` 替代
2. ✅ 搞清楚 Layer 数据存储方式：
   - 元数据 → PostgreSQL 数据库
   - 实际地理数据 → 文件存储 (GeoJSON/Shapefile/TIFF)
   - 读取路径 → `source_url` 指向文件位置
3. ✅ Buffer 分析注意 CRS 投影转换：
   - WGS84 (EPSG:4326) 是地理坐标系，单位是**度**，不能直接用米做 buffer
   - 自动检测地理坐标系 → 投影到对应 UTM zone → 计算 buffer → 投影回去
4. ✅ 测试覆盖率目标 60-70%，不测试过度

## 已完成修改

### 1. 核心空间分析 (app/services/spatial_analyzer.py)
- ✅ `buffer()` 方法重构，增加 `source_crs` 参数
- ✅ 自动 CRS 检测：如果是地理坐标系自动投影到 UTM
- ✅ 自动计算 UTM zone 基于中心点
- ✅ buffer 计算完成后投影回原坐标系
- ✅ 更新入口 `execute_analysis` 传递 `source_crs`

### 2. 任务执行重构 (app/api/routes/tasks.py)
- ✅ 移除 Celery 依赖
- ✅ 改用 FastAPI `BackgroundTasks` 异步执行
- ✅ 进度保存在数据库，不再使用 Redis
- ✅ SSE 进度查询从数据库读取，保留 30 分钟超时保护
- ✅ 支持重试（重用 BackgroundTasks 重新提交）
- ✅ 支持取消（标记为 cancelled，BackgroundTasks 不支持运行中取消）

### 3. 数据读取模块 (app/data/layer_data.py) **新建**
- ✅ 支持多种格式读取：GeoJSON/Shapefile/KML/GPX/GeoParquet
- ✅ 统一返回 GeoJSON 特征列表给分析算子
- ✅ 相对路径相对于 `DATA_DIR`

### 4. 图层服务扩展 (app/services/layer_service.py)
- ✅ 添加任务操作方法：`get_task`, `update_status`, `update_progress`, `update_message`, `update_failed`, `update_completed`, `create_result_layer`, `list_paginated`
- ✅ 修复权限检查使用 `LayerPermission`（替换旧 `UserRole`）
- ✅ 结果自动保存为新图层：GeoJSON 文件写入 + 数据库记录创建

### 5. 项目配置
- ✅ 合并 master 到 develop，获得完整后端模型

## 关键修复点

1. **CRS 投影转换问题**：
   - 之前代码直接在 WGS84 上用米做 buffer，100米 → 100度，这是错误的
   - 现在正确：地理坐标系 → UTM 投影（米单位）→ buffer → 投影回去

2. **移除 Celery 依赖**：
   - 之前代码依赖 Celery + Redis，增加部署复杂度
   - MVP 用 BackgroundTasks 足够，简单部署，不用额外组件

3. **数据读取分层**：
   - 新增独立模块处理各种格式读取，解耦
   - 分析算子只需要处理特征列表，不用关心来源格式

## 待测试

- [ ] 端到端测试：提交 buffer 分析任务
- [ ] 验证 CRS 转换正确性
- [ ] 验证 BackgroundTasks 异步执行正常
- [ ] 验证 SSE 进度推送正常

## 依赖检查

需要确认 geopandas/pyproj/shapely 已经在 requirements.txt：
