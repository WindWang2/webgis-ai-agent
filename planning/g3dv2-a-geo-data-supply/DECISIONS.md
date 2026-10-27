# DECISIONS — g3dv2-a-geo-data-supply

- **D1 摄入 lane 位置**：raster/DEM 注册放进既有 `POST /upload` 的 ingest lane（新增 raster 分支），而非新上传端点。理由：文件传输/dedup/剖析/归属校验已就绪且经审计；新端点会重复 200MB 读取与目录清理纪律。lane 保持 best-effort（失败不阻断上传本体），但**资格为 unavailable 时如实返回而非报错**——那是诚实结果不是失败。
- **D2 单位换算**：非米单位（ft / US survey ft）在摄入期生成**换算副本**（`.dem-m.tif`，float64 累加），receipt 记录 in/out sha256 + 精确因子（ft=0.3048, usft=1200/3937），源文件不动。拒绝服务期隐式乘法（render 路径是共享面，且静默换算不可审计）。无单位声明且 band tag 缺失 → 假定 m + `unit_source="assumed"` 警告（行业惯例 SRTM/ASTER 皆米），资格最多 display_only 需 datum 齐备另议。
- **D3 垂直基准不换算只记录**：无 geoid 模型，绝不在 EGM96/椭球高间偷换。datum 来源优先级：用户表单声明 > GeoTIFF vertical datum geokey（若可读）> None。None → display_only + `UNKNOWN_VERTICAL_DATUM`。analysis_grade 仅当：单波段 + CRS + 北向 + datum 声明 + 单位确定（声明或 tag）+ 无 SYNTHETIC tag + 异常值比例低于阈值。
- **D4 不做 durable 预热 job**：会话级按需渲染（窗口读+LRU+single-flight）已满足完成判据；批量预热属 catalog 级 PMTiles 场景（已有）。PR 列 limitation。
- **D5 TileJSON 形状**：tilejson 3.0.0 兼容核心字段（tiles/bounds/minzoom/maxzoom/attribution/name/description）+ `terrain` 扩展块（encoding/vertical_unit/vertical_datum/qualification/synthetic/nodata/receipt 摘要）。不发明第二套 schema。tiles URL 含 session_id query；匿名会话追加 `&token=`（复用 /sessions/{sid}/raster?token= 先例，layer.py terrain 路由加法式 query token 支持）。
- **D6 quota 执行层范围**：进程内滑动窗（60s 窗口计数，键 tenant+source / session+ref），429 + Retry-After + typed code。env 可配（DATA_FABRIC_TILE_RPM 默认 600/tenant/source；SESSION_TERRAIN_TILE_RPM 默认 1200/session）。多 worker 为每 worker 独立预算（保守方向：总预算=workers×rpm），列为已知限制。出站 adapter 抓取侧的 QuotaDecl 执行在共享 fetch seam 若单点存在则接，否则 PR 列 limitation（证据留 RISKS）。
- **D7 A6 摘要 schema 发布位置**：`app/contracts/terrain_sources.py`（H01 contracts 目录先例）——C 的 get_3d_capabilities 后续可 import，本 track 不改其消费端。
- **D8 复用而非新表**：DEM ref 即 session_data ref（payload 自带 descriptor），不建 DB 表、不动 UploadRecord schema。资格词表沿用 ElevationSourceProfile 的 qualification 枚举，不并表。
- **D9 压缩炸弹/超大图幅**：上传已有 200MB 硬顶 + SEC-F6 cap+1 读；剖析降采样 ≤2048 边；瓦片渲染窗口读有界（#595 纪律）。摄入校验沿用，不新增解压面。超深 zoom（>22）在路由既有 400。
- **D10 旋转/非北向网格**：transform 含旋转（b≠0）或 d≥0 → unavailable `ROTATED_GRID_UNSUPPORTED`（from_bounds 窗口推导假设轴对齐；不猜）。

## 执行期增补（T1–T7 落地时）

- **D11 单位假定的资格影响（D2 细化）**：band tag 缺失且未声明 → 假定米（UNIT_ASSUMED_M）→ 资格封顶 display_only；analysis_grade 要求单位"声明或 tag 确认"。补救路径明确（重新上传并声明 vertical_unit=m），不是永久降级。
- **D12 极端尖峰分级**：包络 (-500, 8850)m 外为 outlier；占比 >0.1% → ELEVATION_OUTLIERS（display_only）；|v|>15000m 任意出现 → ELEVATION_EXTREME_OUTLIERS（display_only）；超 Terrarium 编码域 → ELEVATION_OUT_OF_RANGE（披露）。非有限值占比 >1% → unavailable CORRUPT_ELEVATION。洞（nodata/哨兵）只统计不降级——洞是披露事实不是插值理由。
- **D13 quota 执行层范围裁定**：服务侧三路由（目录 MVT/PMTiles + 会话 terrain）接入真实执行（键=租户+源 / 会话+ref）；出站侧 make_safe_session 增加 rate_limit 参数（typed SourceRateLimitedError），QuotaDecl→各 adapter 逐源接线**列为后续**（需 ConnectionProfile 增字段+全部 adapter 会话构造点改造，侵入面大，不在本 PR 强行半接线成死代码）。
- **D14 response_model 契约**：terrain-sources 声明 response_model=MapResourceAvailabilitySummary（FastAPI 序列化）；terrain-tiles.json 因 ETag 字节稳定序列化 + 304 手工构造 Response，按既有 pmtiles 先例进 EXCLUSIONS（注明理由）。
- **D15 既有红灯不吞**：test_all_json_endpoints_have_response_model 在 origin/master@70dab2b9 即红（pmtiles-tiles/storymap spec 缺 response_model，detached 实测）；scene_quality.py E741 同为 master 既有。本 track 不修（避让矩阵），EVIDENCE 记录对照证据。
