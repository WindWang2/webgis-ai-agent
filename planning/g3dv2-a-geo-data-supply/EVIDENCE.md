# EVIDENCE — g3dv2-a-geo-data-supply

环境：worktree `/home/kevin/projects/webgis/webgis-wt-g3dv2-a-data`，分支 `zcode/g3dv2-a-geo-data-supply`（自 origin/master@70dab2b9）；解释器 `webgis-ai-agent/.venv`（Python 3.13，rasterio 1.5.0 / pyproj 实测）。测试命令统一 `python -m pytest … -q --no-cov -p no:cacheprovider`。

## 新增测试（本 track）

| 套件 | 用例数 | 结果 |
|---|---|---|
| tests/unit/test_terrain_ingest.py | 27 | ✅ passed |
| tests/unit/test_upload_raster_lane.py | 8 | ✅ passed |
| tests/unit/test_terrain_tilejson.py | 7 | ✅ passed |
| tests/unit/test_terrain_sources_summary.py | 7 | ✅ passed |
| tests/unit/test_tile_rate_limiter.py | 9 | ✅ passed |
| **合计** | **58** | **五套件合跑 58 passed（一次运行）** |

四段链闭环证据：test_terrain_tilejson.py::test_receipt_driven_tilejson_and_tile_chain —— 上传声明 EGM96 → register（analysis_grade receipt）→ GET terrain-tiles.json（bounds=bbox、terrain 扩展块）→ tiles[0] 实取回 `image/png`（HTTP 200）。

## 既有回归（本 track 触碰面）

| 套件 | 结果 |
|---|---|
| tests/data/test_upload_identity_v4.py + tests/unit/test_upload_executor.py（22） | ✅ 22 passed |
| tests/unit/test_synthetic_terrain.py + tests/cartography/test_synthetic_dem_mapspec_e2e.py + tests/unit/test_raster_tile_service.py（30） | ✅ 30 passed |
| tests/unit/test_pmtiles_tile_route.py + tests/unit/test_deep_review_api_data_tiles.py（15） | ✅ 15 passed |
| tests/unit/api_contract/（57 项收集） | 55 passed + 2 failed —— **两失败均为 origin/master 既有**（见下） |
| tests/quality/test_api_compatibility.py | ✅ 12 passed（快照按官方机制再生成后） |
| -m cartography 发布门 | 见下节最终记录 |

## 既有红灯对照（detached origin/master 实测，非本 track 引入）

- `test_all_json_endpoints_have_response_model`：master 即红（`data-fabric/catalog/{item_id}/pmtiles-tiles/{z}/{x}/{y}` 与 `storymap/sessions/{id}/spec` 缺 response_model）。本 track 的两个新端点已各自合规（terrain-sources 声明模型 / tilejson 进 EXCLUSIONS 注明理由），未新增失败。
- `ruff check app/lib/cartography/scene_quality.py` E741：master 即有，未触碰。

## 契约快照

- `tests/quality/snapshots/openapi.json`：API_SNAPSHOT_UPDATE=1 再生成。⚠ 时序更正（独立评审 P1 抓获）：首次再生成先于 terrain-sources 的 response_model 装饰器提交，快照一度陈旧；评审修复轮已重刷并复跑。diff 含少量 master 陈旧漂移行（storymap v1.1 默认值 2 行等，master 快照未跟上 master 自身变更），本 track 净增量 = 3 新路由 + upload `terrain` 字段 + terrain-sources response schema。
- `tests/quality/snapshots/api_contract_fields.json`：API_CONTRACT_FIELDS_UPDATE=1 再生成（+24 行中约 9 行属 master 陈旧的 pmtiles-tiles/storymap spec 条目，本 track 净增量 = 3 新端点 + upload `terrain` 字段），复跑 1 passed。
- 修复轮终态：两快照门 + api_compat 合跑 **13 passed**（含字节一致闸），时点在全部代码修复之后。

## 静态检查

- ruff：本 track 全部新增/触碰文件 0 error（app/contracts、terrain_ingest、tile_rate_limiter、upload/layer/data_fabric 路由、security、upload_schema、5 个新测试文件）。
- mypy：项目棘轮白名单（app/core + 3 个指定文件）不含本 track 文件；本 track 未触碰白名单文件，棘轮不受影响。

## 资源纪律实测

- 摄入剖析：降采样读（≤2048 边），8192 级大图幅走 2048² 用例验证同一代码路径（test_large_grid_profiled_via_decimation）。
- 换算副本：GDAL block 窗口流式（无整幅进内存）；上传 200MB 上界即副本上界。

## 发布门终态（评审修复轮之后）

- `-m cartography`（2451 项收集）：**2448 passed / 7 skipped / 3 xfailed，exit 0**（305.6s，修复前一轮 128.8s 同绿；时长差异来自并行负载）。
- 新测试合跑：**66 passed**；受影响回归合跑：**66 passed**；快照门+api_compat：**13 passed**。
