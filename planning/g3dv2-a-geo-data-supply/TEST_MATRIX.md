# TEST_MATRIX — g3dv2-a-geo-data-supply

所有新功能覆盖维度：正常输入、缺失资源/权限、非法 CRS/垂直基准、越界/超预算、取消重试（lane best-effort/幂等重注册）、多轮/重载（list_refs 重建摘要）、2D 回归（不动 MapSpec 渲染链）、离线/无公网（纯本地 fixture）、失效证据、日志脱敏（receipt 不含 token/绝对路径对外披露走相对化）。

## 文件 → 用例族

### tests/unit/test_terrain_ingest.py（fixture 家族，A7 核心）
1. 正常有效 DEM（米+声明 EGM96）→ analysis_grade，receipt 字段齐全（bbox/CRS/GSD/nodata/单位来源）
2. 负高程（死海量级 -430m）→ 合法，不误判异常
3. 无 datum 声明 → display_only + UNKNOWN_VERTICAL_DATUM
4. 英尺单位+用户声明 → 换算副本 + 双 sha256 证据 + 换算后值域校验
5. 英尺单位+band tag 声明 → 同上（tag 来源记录）
6. nodata 洞（-9999 散布）→ hole 统计进 receipt，渲染透明不伪造
7. 无 CRS GeoTIFF → unavailable TERRAIN_INGEST_NO_CRS
8. 多波段 → unavailable TERRAIN_REQUIRES_SINGLE_BAND（摄入期拒绝，非渲染期）
9. 非法像素（NaN/Inf 混入）→ 记 inf/nan 计数，>阈值 unavailable
10. 异常尖峰（±15000m 外少量）→ outlier 统计 + display_only
11. 旋转网格 → unavailable ROTATED_GRID_UNSUPPORTED
12. SYNTHETIC_DEM tag → 强制 display_only（即使声明了 datum）
13. 幂等：同会话同内容二次注册 → 复用 ref（不重复 store）
14. 剖析预算：超大尺寸合成图幅只降采样读（耗时/内存有界断言）

### tests/unit/test_terrain_tilejson.py
- bounds=transform_bounds(CRS→4326) 数值断言；minzoom/maxzoom 由 GSD 推导
- 鉴权：跨会话 404；匿名 ?token= 通过；错误 token 404
- tiles URL 可直接 GET 回 PNG（四段链闭环）；terrain 扩展块字段
- 退役 ref（delete_ref）→ 404
- 2D 回归：不涉 terrain 的普通请求零额外 DEM 读取（无 DEM 摄入时摘要不含 terrain ref）

### tests/unit/test_terrain_sources_summary.py
- 静态画像投影：aws-terrarium display_only、synthetic-demo-dem、air_gapped 过滤
- 会话 DEM ref 并入 + qualification 透传 + reasons
- 空（无 DEM）→ 3d terrain unavailable + 解释（对接 A6 契约）

### tests/unit/test_tile_rate_limiter.py
- 窗口内超 rpm → 429 + Retry-After>0 + typed code
- 键隔离（tenant A 不影响 B；source 隔离）；窗口滚动恢复
- env 覆盖配置；禁用（rpm=0 → 不限）

### tests/unit/test_upload_raster_lane.py（四段链）
- multipart 上传小 GeoTIFF + register_ref → session_ref 返回 + qualification
- 无声明 datum → response 警告 + display_only
- lane 失败（如文件损坏）→ 上传本体仍 200 + ref_registration_error 如实
- 既有 vector lane 回归不受影响

### 既有回归
- tests/unit/test_raster_tile_service.py、test_synthetic_terrain.py、tests/cartography/test_synthetic_dem_mapspec_e2e.py 全绿（共享路径零语义变更）
- tests/unit/test_pmtiles_tile_route.py（quota 接线不破坏 304/跨租户拒绝）
