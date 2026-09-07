# 04 — Cache / Materialization Audit（Agent D）

## 1. 缓存清单

| # | 缓存 | 位置 | Key 组成 | 值 | 存储 | 上限/TTL | 失效 |
|---|---|---|---|---|---|---|---|
| 1 | `tool_cache` | `app/lib/tool_cache.py:36-66,152-158` | `tool_cache:v1:sha256(tool::canonical_args)[:16]`；**含 ref 的 args 拒绝**(:39-63) | JSON 工具结果 | Redis | 每工具 TTL 1h–24h | 仅 TTL；错误结果不写(:239-253) |
| 2 | `artifact_cache`（ADR-0048） | `app/lib/artifact_cache.py:70-99,133-187` | sha256{src path+**mtime+size**, op, params, `ARTIFACT_VERSION_NS`} | GeoTIFF | 磁盘 `data/artifacts/` | 5GiB LRU(:190-221) | mtime/size 变→新 key；读时复核(:126-127)；手动 ns bump |
| 3 | `analysis_reuse`（V2 P10） | `app/lib/gis/analysis_reuse.py:51-78,217-295`；接线 `tool_dispatch_service.py:394-461` | `analysis:v1:sha256(tool::canonical_args)`——**ref id 是 key 成分非内容** | 指向先前 artifact ref | ArtifactRegistry 记录 | 24h；`GIS_ANALYSIS_REUSE=0` 关 | status=valid+描述符活+形状 fp（要素数+几何族）；栅格内容 fp |
| 4 | `ref_payload_cache` | `ref_payload_cache.py:27-29,161` | (session, ref) | 解析后 payload 对象 | 进程内存 | 5s/256 条/128MB；epoch 防复活 | ref_lifecycle 钩子 |
| 5 | `SpatialIndexCache` | `mvt.py:1391-1527` | (session, ref) | STRtree | 内存 | 256/256MB LRU+epoch | ref_lifecycle；旧构建抛 RefDataUnavailableError |
| 6 | `TileLRUCache` | `mvt.py:1533-1655` | (session,ref,z,x,y) | gzip MVT | 内存 | 4096/256MB/4MB | 同上+put_if_current |
| 7 | 栅格 tile+stats | `raster_tile_service.py:104-180` | **仅 raster_path 字符串** | PNG/(vmin,vmax) | 内存 | 2048/512；stats 满则 bulk clear | 无（"best-effort"自认 :158） |
| 8 | fabric `SafeTTLCache` | `metadata_cache.py:37-67` | sha256(source_key,dataset_id,**auth scope**) | describe() 元数据 | 内存 | 30s/4096 | 仅 TTL |
| 9 | `DatasetStatisticsRecord` | `models/data_fabric.py:103`；`query/statistics.py:355-465` | `dataset_fingerprint`（描述符+载荷哈希） | stats JSON | SQL | expires_at；prune 每 100 存 | 显式过期 |
| 10 | 会话 stores | `session_data.py:88-117`；Redis 版 | session/ref id | refs/events/state | 内存 50MB LRU / Redis 4h | 如左 | clear_session→ref_lifecycle 广播 |
| 11 | 历史上限驱逐（#522） | `history_service_async.py:417-425,649-706` | owner/user 桶（MAX_SESSIONS=1000） | 会话行 | PG | 数量上限 | 完整 clear_session 协议 |
| 12 | 晋升内容库（ADR-0092） | `project_artifact_promotion.py:62-90,157-186` | content_fingerprint→`data/project_artifacts/<shard>/<fp>.json` | canonical JSON blob | 磁盘分片 | **无上限无驱逐** | 读时摘要复核(:189-223) |
| 13 | 杂项 | crs_safety lru(512)（纯安全）；stac lru(1)；cartography_runtime LRU；project_context_cache；distributed_lock 回退注册 | | | | | |

**V3 指纹层已在、未接线**：`app/lib/data/fingerprints.py`——`canonical_fingerprint`(:69)、`FingerprintSet`(:86-111)、`classify_change`(:125)、`staleness_verdict`(:172)、**`compute_reuse_fingerprint`(:192-223) 正是目标公式**。唯一消费者 `app/lib/data/artifact_contract.py`。栅格内容寻址 fp：`app/schemas/raster_spec.py:149`。

## 2. 陈旧风险
- **tool_cache 按路径参数为键**——原地改写文件则最长 24h 陈旧（spatial.py:152）；键无 CRS/版本/环境；canonicalization `default=str` 对奇异对象不确定（tool_cache.py:64）。
- **analysis_reuse 键=ref id 指针**（:9-19）：content_hash 恒 None；形状守卫漏同形坐标改写；无 CRS 无算法版本；仅 24h 年龄+栅格内容 fp。矢量原地覆盖同形 → 假命中。
- 栅格 tile/stats 按路径键；源改写后陈旧 PNG/stats。
- fabric describe 缓存不含 dataset fingerprint；仅 30s TTL。
- **最安全**：artifact_cache（mtime+size+读复核+版本 ns，但 ns 手动、缺 GDAL/rasterio 库升级感知）；DatasetStatisticsRecord（内容指纹键）；晋升库（摘要复核）。

## 3. Singleflight
- Redis 锁单飞（tool_cache.py:161-308：SET NX+token、120s 锁 TTL、30s 等待预算、取消安全释放、降级直算）。
- 线程 `SingleFlight`（singleflight.py:31-105：per-key events、1024 inflight、30s 超时、过载直算）；fabric describe 在用。
- asyncio `SingleFlightManager`（mvt.py:1661-1744）。
- **缺口**：`SpatialIndexCache.get_or_build` 容忍重复并发构建（mvt.py:1396-1397）；`artifact_cache.publish_artifact` 自身无锁。turn 级去重：tool_dispatch_service.py:361-387。

## 4. 临时文件生命周期
- artifact_cache mkstemp→os.replace、失败 unlink（干净）。
- **泄漏**：栅格算子 `_suffix_output_path`（raster_math.py:105-109，用于 :203,320,517）把输出直接写源旁——cache miss 时 publish 拷贝入缓存后"留在原地由调用方清理"（artifact_cache.py:139-141）——**无人清理**：`*_resampled.tif/_reclassified.tif/_calc.tif` 在 uploads 旁堆积，无 sweeper 覆盖。
- map.py:313 NamedTemporaryFile(delete=False)→7d 清扫；mapspec/faiss/bridge mkstemp+replace（干净）。

## 5. GC/清理设施（可复用）
无 Celery beat；全部周期工作为 `app/main.py` lifespan asyncio 任务：`_periodic_session_cleanup` 每 600s（:195-235）跑 `cleanup_idle_sessions`（session_data.py:606）+`sweep_expired_session_files`（mapspec/store.py:543）+`sweep_aged_artifacts`（`artifact_lifecycle.py:176-301`；exports 7d/reports 14d/孤儿 uploads 7d，env 可调 :65-76）；`_periodic_stale_job_sweep` 每 60s（:238）。驱逐钩子：`purge_session_artifacts`（artifact_lifecycle.py:101）接入历史上限驱逐与会话删除。磁盘布局：`data/{artifacts,exports,reports,uploads,project_artifacts}`——**project_artifacts 无上限，全系统无磁盘配额**。

## 6. 指纹化复用接缝建议
扩展 **`app/lib/data/fingerprints.py` + artifact_contract**（compute_reuse_fingerprint 已是公式、零消费者）：
1. `analysis_reuse.compute_analysis_key` 委托之：ref-id 键 → 内容指纹键（RefDescriptor 内容哈希 + raster_content_fingerprint）+ operation_version（algorithm_registry）+ runtime semver；保留 kill switch。
2. 产出文件的算子接 `artifact_cache`（mtime 代理 → 内容 fp；覆盖 raster_math.py:203,320 今日无缓存的 reclassify/raster_calculator）。
3. 新存储驱逐走 ref_lifecycle 式钩子 + `_periodic_session_cleanup`；给 data/project_artifacts 加上限。
4. 三套 singleflight 保持为并发层。
