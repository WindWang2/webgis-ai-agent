# 02 — Import / Ingest Audit（Agent B）

## 1. 摄取入口清单

| # | 入口 | 格式 | 落点 | 元数据 | ID |
|---|---|---|---|---|---|
| 1 | `POST /upload`（`app/api/routes/upload.py:98-255`） | 白名单 `.geojson,.json,.shp,.zip,.kml,.gpkg,.csv` + `.tif,.tiff`（`data_parser.py:15-16`）；50MB/200MB 限 | `data/uploads/<uuid32>/<name>` → 矢量归一 `original.geojson`、栅格拷贝 `original.tif` + `meta.json`（`data_parser.py:154,220,256,276-289`） | file_type/format/crs/original_crs/geometry_type/feature_count/bbox/attributes（矢量）；band_count/width/height/dtype/transform（栅格） | 目录 uuid4().hex（upload.py:157）；DB `UploadRecord` 自增 id，**filename 存绝对路径**（upload.py:215-226） |
| 2 | `POST /data-fabric/sources`（`data_fabric.py:306-380` → `manager.py:108-184` + `sync_catalog` :187-360） | 11 类源（registry.py:105-169）：postgis/ogc_api/wfs/wms_wmts/arcgis/stac/geoparquet/flatgeobuf/pmtiles/s3/demo | `DataSourceModel`+`CatalogItemModel`（`item_id="cat_{source_id}_{name}"`）；零拷贝虚拟目录 | capabilities/status/描述符→srs/crs/feature_count/fields/fingerprint（manager.py:278-289） | `ds_+uuid12`（manager.py:120） |
| 3 | 物化 `POST /data-fabric/materialize`（`data_fabric.py:904`）+ tool `materialize_dataset`（`data_fabric_tools.py:404-473`）→ `materialization_service.materialize`（:82-190） | 上述全部 | `session_data_manager.store(..., prefix="data-fabric")`（:144） | `MaterializationModel`：query_spec/fingerprints/record_count（manager.py:560-571） | 会话 ref_id |
| 4 | `POST /explorer/start`（`explorer.py:34`）→ Celery discover→fetch→parse→geocode→validate→quality（`explorer/pipeline.py`） | 政务门户 CSV/XLSX/JSON（`gov_data_adapter.py:122-260`，URL 后缀猜格式 :244-254，编码嗅探 :266） | base64 payload ref（fetch_stage.py:49-55） | source_id/size_bytes/format/映射置信度 | 会话 ref_id |
| 5 | Agent 工具：`upload_tools`（id 隐藏路径）、栅格工具 `validate_data_path`（`utils/path.py:3-40`）、`RasterSource` URI 分发（path/`ref:`/`artifact://`/http+/vsi*）、Overpass/AMAP 远端拉取 | | | | |
| 6 | 派生产物：geocompute `MATERIALIZE`/`ARTIFACT_REGISTER`（`ops.py:566-623`）；持久 `Artifact` 表；`data/artifacts/<key>.tif` 内容哈希缓存（唯一内容去重） | | | | |
| 7 | 相邻非数据上传：地图导出 PNG/SVG/PDF（`map.py:151-270`）、admin skill 上传（故意 RCE 面，`config.py:236-289`） | | | | |

## 2. 真实格式支持 vs 名义支持
- **上传真正可用**：GeoJSON/JSON、zipped Shapefile（GDAL 原位读 zip，不解压）、KML、GPKG、CSV（仅点）、GeoTIFF。裸 `.shp` 接受但无 sidecar 必失败（组件检查只对 .zip，data_parser.py:104-105）。
- **仅远端**：PostGIS/OGC API/WFS/WMS-WMTS/ArcGIS/STAC/GeoParquet/FlatGeobuf/PMTiles/S3。
- **缺失**：NetCDF/Zarr/xarray（skills 明确禁用）、Excel（仅政务路径）、GeoParquet/FlatGeobuf 上传、COG 专用处理。
- **依赖**（requirements.txt/pyproject.toml）：geopandas≥1.1.4、shapely≥2.1.2、rasterio≥1.3.10、pyogrio≥0.13（**fiona 未用**）、pandas 2.x、numpy、ijson、httpx/requests/aiohttp、pystac-client、osmium。**缺口**：openpyxl 运行时用但只声明在 requirements-dev（生产崩）；pyarrow/fsspec 被 geoparquet_adapter 用但未声明；无 duckdb。

## 3. CRS 检测
- 矢量上传：`gdf.crs`；GeoJSON 无 CRS **静默按 EPSG:4326**（RFC 7946，data_parser.py:136-137）；其余格式无 CRS → `ParseError` 400（:138-142）；统一重投影到 4326，`original_crs` 仅在 ≠4326 时保留（:127-135）。
- CSV：**硬编码 EPSG:4326**，无用户声明入口（:200-204,228）。
- 栅格上传：`str(src.crs) else "未知"`——**缺 CRS 接受并持久化为 unknown**（:245）；路由 DB 默认静默 `"EPSG:4326"`（upload.py:220）。
- 下游守卫：zonal stats 拒无 CRS 栅格（raster_ops.py:143-145）；`classify_crs`/`recommend_metric_crs`（`app/lib/gis/crs_safety.py:46-129`）；`normalize_crs_ref`（geocompute/normalization.py:26-43）；`_declared_crs`（spatial_meta_profiler.py:9-42）。

## 4. 重复导入
- **上传路径无去重**：无内容哈希；同名文件两次 → 两个 uuid 目录两行。
- fabric 目录 sync **幂等**（item_id + 描述符指纹 diff，manager.py:263-297）；派生栅格经 artifact-cache 内容哈希去重；同端点重注册产生新 `ds_*`；每次物化新 ref（无结果指纹去重）。

## 5. 失败处理/回滚
- 上传有强单趟清理纪律（#546）：目录先建，所有失败分支 rmtree（upload.py:159-243）；ParseError→400、OSError/RuntimeError→500、超限→413（先缓冲检查 :139-153）。
- fabric：未知类型先拒（#767）；但 probe/sync 失败仍提交源行（status="unreachable"+空目录，仅 warning，manager.py:145-152,178-182）——可恢复孤儿。物化失败类型化（success=False, ref_id=None），审计提交失败有**补偿性 ref 删除**（:578-600）。
- explorer fetch：按源隔离、部分成功带 `fetch_errors` 继续（fetch_stage.py:66-119）。

## 6. 安全发现
- **路径穿越（上传）**：basename+前导点拒绝（upload.py:120-123）、resolve-父目录包含（:168-172）、GeoJSON 服务包含检查（:369-372）、`validate_data_path` realpath+symlink 防御（utils/path.py:26-38，但宽松放行 `./tmp`）。`tests/test_api_path_traversal.py` 仅单元测试 helper。
- **压缩包**：zip 不解压（GDAL 原位读）；穿越名拒绝、解压上限（zip 炸弹）、.shp+.dbf 必查（data_parser.py:33-60）。良好。
- **SSRF**：`validate_url` 拒私网/回环/metadata（DNS 解析，security.py:95-190）；重定向安全 Adapter（:316-339）；REST 强制 allow_private=False；政务 fetch 每跳校验。**缺口**：`RemoteRasterSource` 打开任意 http(s)/**vsi* URI 无 SSRF 闸（geo_raster/source.py:67-83,172-179）——潜伏（auto() 未接公共工具）。
- **本地路径暴露**：fabric 文件适配器受 `resolve_safe_local_path`+`DATA_FABRIC_LOCAL_FILE_ROOTS` 约束（默认 ./data，敏感目录恒拒，1GiB 上限）（security.py:399-445, config.py:190-195）；upload_tools 用 int id 隐藏服务器路径；`raster_ops.py:140` 的 `RasterSource.from_path` 自身不校验（依赖调用方）。

## 7. Ingest Pipeline V3 插入点建议
1. **主**：以新 `app/services/ingest/` 管线替换 `POST /upload` 的临时逻辑（Detect→Validate→RegisterSource→Infer→CRS→Profile→Quality→Artifact→Materialize；回滚=既有 rmtree 纪律）；保持 `UploadResponse` 契约；`data_parser` 常量/`ParseError` 作 detect/validate 词汇。
2. **统一 fabric**：把 `create_data_source`+`sync_catalog` 包成 Register/Infer 阶段，建在 `materialization_service`（已是声明的 REST+agent 单管线，ADR-0094）。
3. **复用件**：剖析=`spatial_meta_profiler.profile_geojson_source`+`DatasetProfile`；质量=`SpatialQualityEngine.audit_dataset`（spatial_quality_service.py:88-98）；注册=`artifact_registry.register_artifact`；去重=sha256 指纹扩展到上传（UploadRecord 加 content_hash 列，同哈希幂等重导入）。
4. **顺带修**：RemoteRasterSource SSRF 闸；requirements.txt 声明 openpyxl/pyarrow/fsspec。
