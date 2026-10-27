# feat(g3dv2-a): DEM 数据供给生产化 —— 用户 GeoTIFF 摄入校验/资格记录 → Terrarium XYZ + TileJSON → quota 执行层 → 可用性发现

> Track: g3dv2-a-geo-data-supply（G3D-V2-A）。基线 master@70dab2b9（PR 期间无前移，无需 rebase）。

## 问题证据（为什么需要这个 PR）

#1771 建立了 typed 底图/高程画像、OpenFreeMap、PMTiles→XYZ、合成 DEM 与地形治理，但**用户自备真实 DEM 进入数据面的链路不存在**：

- 上传的 GeoTIFF 被摄入车道显式跳过（`app/api/routes/upload.py` 原 `session ingest lane accepts vector data only; raster skipped`）——200MB 级合法 DEM 永远无法成为 terrain-tiles 路由可服务的 session raster ref（该路由只认 ref payload 的 `file_path`）；
- TileJSON 端点在 `app/api` 全域 grep 零命中（#1771 BASELINE.md:39 自认）；
- `requests_per_minute` 只有规划期成本 hint 消费者（`planning/cost_model.py`），瓦片路由链无任何限流（#1771 D11 显式留白，DECISIONS.md:15/RISKS.md:7,18 与代码现状一致）；
- Agent 发现面（`get_3d_capabilities`）只能看到已写入 MapSpec 的源，无目录级 typed 可用性查询；
- 高程单位/垂直基准在摄入期零治理：未知 datum 的 DEM 可被静默当可用。

## 与已合并三维 PR 的不重复说明

| PR | 不重复点 |
|---|---|
| #1771 | 复用其 ElevationSourceProfile 词表/不变式、terrain_encoding、PMTiles 路由、synthetic fixture；本 PR 只补其留白（TileJSON、quota 执行、真实 DEM ingest），静态画像目录与前端生成镜像**零变更**（basemap-profiles.generated.ts 未触碰） |
| #1834 | scene3d 消费面只读，本 PR 不改 `frontend/lib/map-kit/scene3d/**` |
| #1773 | 量测垂直基准治理是 C 领地；本 PR 的资格记录与其词表对齐但不改 `measurements.py` |
| #1829 | `get_3d_capabilities` 所在 `scene_orchestration_tools.py` 零修改；摘要 schema 发布在 `app/contracts/` 供其后续对接 |
| #1830 | 制图产品无重叠 |

## 已知 issues 避让矩阵

| Issue | 处置 |
|---|---|
| #1805 CGCS2000 classify_crs | **不修**（在修归属）。本 PR 摄入校验不依赖 classify_crs，直接 rasterio `transform_bounds` 实测；仅以「CRS 声明存在但不可解析 → unavailable」作兼容验证 |
| #1797 terrain 水文 DoS | 不碰 measurements；本 PR 摄入剖析降采样 ≤2048 边 + 窗口读纪律 |
| #1809–#1811 MapSpec filter 族 | 不涉及 |
| #1821/#1824/#1826/#1827 审查汇总 | 只读参考 |
| #1784–#1786/#1789/#1790/#1792 SSRF 族 | 只记录现状（security.py seam），本 PR 新端点自带鉴权先于缓存 + validate_data_path |
| master 既有红灯 | `test_all_json_endpoints_have_response_model`（pmtiles-tiles/storymap spec 缺 response_model）与 `scene_quality.py` E741 在 origin/master@70dab2b9 即红（detached 实测）；本 PR 未新增失败（新端点各自合规） |

## 架构与数据契约

```
POST /upload (GeoTIFF + register_ref + vertical_datum/vertical_unit 声明)
  └─ raster ingest lane → terrain_ingest.validate_dem()
       结构门(单波段/CRS/北向上) → 单位解析(声明>tag>假定,冲突=披露)
       → 垂直基准(声明>compound CRS>不可用) → 质量统计(洞/非有限/尖峰)
       → 资格 receipt(analysis_grade|display_only|unavailable, 17 码 reason 词表)
       └─ 合格/仅显示 → session ref(payload 自带 receipt, sha256 幂等)
GET /layers/data/{ref}/terrain-tiles/{z}/{x}/{y}.png   （既有路由 + ?token= 匿名）
GET /layers/data/{ref}/terrain-tiles.json               （TileJSON 3.0.0 + terrain 扩展块）
GET /layers/terrain-sources                             （typed 可用性摘要, schema 在 app/contracts）
三瓦片路由 quota 执行（鉴权后缓存前, 429+Retry-After）
```

诚实红线：未知 datum/单位假定米/synthetic 标记/单位冲突/尖峰污染 → display_only；无 CRS/多波段/旋转网格/单位不可换算/损坏 → unavailable（不注册）；非米单位生成换算副本（block 流式 + 双 sha256 证据）；洞/非有限/反子午线/极区只披露不伪造。**摄入期一锤定音，渲染/发现不再变义。**

## 设计取舍（要点，全量见 planning/g3dv2-a-geo-data-supply/DECISIONS.md）

- 摄入进既有上传 lane（文件传输/dedup/剖析/归属校验已审计）而非新端点；lane best-effort，unavailable 是诚实结果不是失败。
- 无 geoid 模型：垂直基准只记录不换算；analysis_grade 要求 datum+单位双确认。
- quota 进程内滑动窗（单进程部署实测为默认拓扑；多 worker=N×rpm 保守方向，诚实披露）。出站限流 seam（make_safe_session send 层，redirect 逐跳计数）已就绪，QuotaDecl→adapter 逐源接线列为后续（不半接死代码）。
- 资格词表双轨：receipt 严格 3 值，投影面含 unknown（与上游画像 4 值词表对齐）。

## 变化清单

- 新增：`app/contracts/terrain_sources.py`（跨方 schema 发布点）、`app/services/terrain_ingest.py`、`app/services/data_fabric/tile_rate_limiter.py`、5 个测试文件（66 例）、`docs/data-plane/terrain-supply-offline.md`、planning 账本 14 篇
- 修改：`upload.py`（raster lane + 2 表单声明 + terrain 响应字段）、`layer.py`（TileJSON 端点 + 摘要端点 + terrain 路由 ?token= + 限流）、`data_fabric.py`（两瓦片路由限流）、`security.py`（make_safe_session rate_limit）、`upload_schema.py`（additive terrain 字段）
- 快照：openapi.json / api_contract_fields.json 按官方机制再生成（净增量=3 新路由+terrain 字段；diff 中少量行属 master 陈旧漂移，已如实归因）

## 测试与验证（全部实跑，命令可复现）

| 门 | 命令 | 结果 |
|---|---|---|
| 新测试 | `python -m pytest tests/unit/test_terrain_ingest.py tests/unit/test_upload_raster_lane.py tests/unit/test_terrain_tilejson.py tests/unit/test_terrain_sources_summary.py tests/unit/test_tile_rate_limiter.py -q --no-cov` | **66 passed** |
| 四段链闭环 | test_terrain_tilejson.py::test_receipt_driven_tilejson_and_tile_chain | 上传→register→TileJSON→tiles URL 实取回 PNG ✅ |
| 受影响回归 | synthetic_terrain / mapspec_e2e / raster_tile_service / pmtiles_tile_route / deep_review_api_data_tiles / upload_identity_v4 | **66 passed** |
| 发布门 | `python -m pytest -m cartography -q --no-cov` | **2448 passed / 7 skipped / 3 xfailed**（评审修复轮复跑同绿） |
| 契约快照 | tests/quality/test_api_compatibility.py + tests/unit/api_contract/test_field_contract.py | **13 passed**（字节一致闸） |
| ruff | 本 PR 全部新增/触碰文件 | 0 error（app/ 全仓余 1 处 master 既有 E741） |
| mypy | 棘轮白名单（app/core 等）未触碰 | 不受影响 |

环境：webgis-ai-agent/.venv（Python 3.13，rasterio 1.5.0）；worktree `../webgis-wt-g3dv2-a-data`。

## 独立 review 结论

两轮独立评审（均未参与实现，源码+调用链+本机复现，非复述作者结论）：
- Round1：1×P1 + 6×P2 + P3 若干 → **全部 P1/P2 与可廉价 P3 已修**（commit 57367324e），安全面/科学判定面无 P0；已核查无问题清单 15 项带行号。
- Round2（新评审者）：a–g 逐项实跑确认修复、修复本身无新 P1/P2、测试抽核真实 → **可合**。
- 全记录：`planning/g3dv2-a-geo-data-supply/INDEPENDENT_REVIEW.md`

## 诚实局限

1. durable 预热 job（DEM→瓦片批量物化）未做——会话级按需渲染（窗口读+LRU+single-flight）已满足判据，catalog 级场景已有 PMTiles 路径（DECISIONS D4）。
2. 出站 QuotaDecl→adapter 逐源接线为后续（seam 已就绪并有测试；不半接死代码）。
3. 限流器多 worker 各自窗口（总预算=N×rpm）；当前部署拓扑实测单进程（Dockerfile 无 --workers）。
4. WMTS 服务端瓦片派生维持 #1771 范围外声明（无合法源证据）。
5. 前端 UI 消费（数据目录 terrain 页签）留给 B 方向经已发布 schema 接入；本 PR 前端零改动。
6. master 既有红灯（response_model 覆盖/E741）未修（避让，证据在案）。
7. 换算副本磁盘放大仅限非米单位 DEM（≤上传上界；随 upload_dir 删除）。

## 回滚方法

纯加法变更：revert 本 PR 单 commit 序列即可；无 DB migration（未动表）；session ref payload 为新 kind 前缀，旧数据无感；快照再生成随 revert 恢复（快照门会红提示需回刷——属预期保护）。功能开关：不传 `register_ref` 时上传行为与旧版逐字节一致（有测试锁定）。

## 安全 / 许可 / 准确性

- 鉴权先于缓存/计算（三新端点 + terrain 路由，评审行号级确认）；匿名 `?token=` 与 SEC-08 既有先例逐字同构（头优先）；跨会话统一 404 不泄漏存在性；429 不泄漏键本体；日志无 token。
- 路径穿越：tile/tilejson 双入口 `validate_data_path`；换算副本与源同目录（DATA_DIR 内，随 upload_dir 清理）。
- 准确性：资格 receipt 是科学输入的唯一真值（C 方向消费）；未知垂直基准绝不偷换 EGM96/椭球高；单位换算留精确因子+双 sha256 证据；synthetic 标记永久跟随。
- 许可：本 PR 不引入新外部数据源；署名链路沿用 #1771 治理（TileJSON attribution 如实标注用户数据/合成标记）。
