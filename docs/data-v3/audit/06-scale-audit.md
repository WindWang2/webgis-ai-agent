# 06 — Scale / Performance Audit（Agent F）

## 1. Perf 测试基建与规范
- 标记：`pytest.ini:22-26` 定义 `heavy`、`perf`、`cartography`、`real_services`。perf=确定性性能回归基线；基线需隔离执行——全量跑自跳（#664，`tests/conftest.py:129-154` markexpr token 检查，可见 skip 并给出正确命令 `pytest -m perf --no-cov`）。
- 基线闸：`tests/benchmarks/_baseline_policy.py:25-53` `decide_baseline_action()` → record/compare；缺基线 **fail-closed**，除非 `PERF_UPDATE_BASELINES=1`（刷新）或 `ALLOW_MISSING_PERF_BASELINE=1`（首录）。已提交中位数在 `tests/benchmarks/baselines.json`（{iterations:7, median_ms, floor_ms?}）。
- 风格 A（预算比较）：`tests/benchmarks/test_perf_harness.py:56-60`——中位数 7 次，WARN_FACTOR 1.75、FAIL_FACTOR 4.0；通过条件 `median <= max(floor_ms, baseline*factor)`。负载：raster guard 拒绝、ref 批量、metrics 入队、dispatch 开销、h3 1 万分箱、窗口重分类等。
- 风格 B（确定性契约，**新工作首选**）：`tests/perf/test_runtime_v2_perf_contracts.py`（PC-1..4：调用次数/修订不变量，无墙钟）；`test_data_runtime_v2_perf.py`（确定性计数优先，150k payload 字节计数排除）；`test_perf_large_workspace.py`（50/100 层变更天花板 8s/4s+结构截断断言，provenance 环 ≤64）。
- `tests/perf/` 是查询次数基线/契约而非墙钟：`test_context_assembly_baseline.py:1-20`（7 查询/assemble×60 轮）。全局 pytest 超时 60s/线程（pytest.ini:6-7）。历史性能工作见 CHANGELOG.md:237-501。

## 2. 当前规模天花板
- **上传**：矢量 50MB/栅格 200MB 硬顶（upload.py:112,144-152）。`_load_geojson_features`（upload.py:38-41）ijson 流式解析但 `list(...)` **全量物化进内存**。
- **剖析**：`spatial_meta_profiler.py:169` `json.load(f)`（整文件）+逐要素收集（O(F·K) 内存+全排序；PERF-F4 修了逐字段重扫但单全量走仍在）——**10 万要素级崩溃**。对照：`profile_from_descriptor`（同文件 :115）与 `app/lib/gis/dataset_profile.py`（DatasetProfile，MAX_PROFILE_FIELDS=64，O(1) 零扫描构造）是规模安全路径。
- **矢量加载**：`data_parser.py:110-116` 与 `flatgeobuf_adapter.py:283` `gpd.read_file` 无行上限（后者有界 max_features 读 :462-465,560-566）；app/tools/* 多处 `for f in features` 全循环，`_utils.trim_features` 5000 上限。
- **列表端点**：有分页——layer_service（limit 100）、`list_spatial_catalog`（limit ≤200+offset+order_by）、project_service（limit 50+COUNT）。例外：`list_data_sources`（data_fabric.py:383-413）`.all()` 无分页（几十可、1 万不可）。
- **栅格**：多 GB 安全路径已有——reader.py:30 全读预算 512MiB；`windowed.py` execute_windowed halo/块预算；raster_guard 拒病态 warp（CHANGELOG:308-324 曾 111.6G 像素多分钟 warp）。上传 200MB 顶意味着多 GB 引用须走 fabric/远端而非上传。
- **内存守卫**：会话 50MB LRU；ref_payload_cache 256/128MB；fabric tile cache 2048/256MB；`data_fabric/limits.py` 硬结果界（features floor 1000、bytes floor 16MiB、pages floor 10）。

## 3. 可复用有界/采样/流式件
- `app/lib/json_size.py:26-50` `estimate_json_bytes`——O(node 预算 2 万)结构字节估算，不物化字符串。
- `data_fabric/limits.py:45-62` 256 要素采样字节估算+`enforce_result_bounds`/`enforce_page_bound`。
- `app/utils/geojson.py:77-110` `summarize_feature_properties`（_SCHEMA_SCAN_BUDGET+sample_size）。
- `app/tools/_utils.py:187` `trim_features(max_features=5000)`；semantic_tools `_SAMPLE_FEATURES=200`。
- `geo_raster/windowed.py`（execute_windowed、overview_statistics 降采样 out_shape :185-206）；`raster_grid.py:487-501` 有界块迭代；`rs/stac_client.py:22,149-288` AOI 窗口读。
- `data_fabric/query/statistics.py`——「诚实有界」从描述符出 DatasetStatistics（零 IO、TTL 60s、LRU 1024）——目录统计的正确模式。

## 4. LLM 上下文截断
成熟：`llm_result_formatter.py` `slim_tool_result`（:264-330）是 LLM 前唯一闸；硬预算 `MSG_MAX_CHARS=2500`（:18）+减半 `_serialize_under_budget`（#439 :98-122）；GeoJSON→geojson_summary（计数+5 样本、15 键、80 字符）；`slim_event_result`（:333-371）剥 SSE 体。聊天史：truncate_history_by_budget+HISTORY_TOKEN_BUDGET（context_assembler.py:376、context_builder.py:51-63，_MAX_USER_ACTION_JSON_CHARS=1200）。超 256KB inline args 走 `_ESTIMATE_SIZE_LIMIT` 旁路（tools/registry.py:215-235）。**新控制面工具必须过 slim_tool_result**。

## 5. DB 索引现状
Layer 表复合索引 org/status/category/created（db_model.py:79-82，迁移 f123456789ab）；tasks idx_task_org_status、org_type_status；项目 0012（idx_project_dataset_pid_created、idx_workflow_run_wid_created）；目录（data_fabric.py:40-68）`bbox_json/tags_json/descriptor_json/meta_profile_json` 为普通 JSON 列（PG JSONB per 0011:130-154）；仅 idx_catalog_source_name/geom_feature/availability（0023:55-58）、idx_dataset_stats_fp_collected。**全库无 GIN**——tags/JSON 包含与名称子串搜索在 1 万+行将顺序扫；name 无 trigram。

## 6. Perf smoke 建议
加 `tests/benchmarks/test_data_control_plane_perf.py`（@pytest.mark.perf，风格 B：确定性计数+宽松墙钟顶），baselines.json 经 decide_baseline_action；仅 `pytest -m perf --no-cov` 隔离跑（#664）。目标：(a) 1 万目录行 list+序列化天花板、keyset/offset 正确；(b) 描述符→DatasetProfile 投影恒 O(1)（monkeypatch 计数器断言零 FeatureCollection 读，PC 式）；(c) estimate_json_bytes 对 10 万要素 FC 恒 O(预算)；(d) 15 万字节 payload 永不达 LLM 载荷（字节断言）；(e) lineage/快照 N 条有界（provenance 环式）；(f) 目录统计从描述符出零 IO。DB 工作：tags/descriptor 加 GIN，name 考虑 trigram（1 万行前）。
