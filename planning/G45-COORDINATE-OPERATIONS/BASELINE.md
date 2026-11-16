# G45-COORDINATE-OPERATIONS — BASELINE

## 环境与基线事实（2026-11-16 启动时复核）

- 仓库：WindWang2/webgis-ai-agent，defaultBranch=master。
- 启动 base SHA：`655f769ca17f17c5797da13b4c299b8fa600e370`（含 G40 #2124 合并，
  比任务书研究快照 34f4e3670 新——G40 已合并，其资产域按既有接口消费，不重新认领）。
- 分支/worktree：`zcode/g45-coordinate-operations-20261115T154805Z` @
  `/home/kevin/projects/webgis/webgis-worktrees/g45-coordinate-operations-20261115T154805Z`。
- Open PR：仅 #2125（G47 Dorling，他人所有权）。Open issue：#2123（旧 master
  1276692737 的 CI 基线债——非本任务目标；本任务只保证自己新增/触及的门禁绿）。
- #2117–#2122、#2124 review submissions 与 inline comments 均为空；#2120 有 1 条
  独立复审 conversation comment（P1×5 已修）。与任务书快照一致。
- 迁移图：单 head `g40_cartographic_symbol_assets`（down=bcf4e5b51f43）。
  本任务 `down_revision = g40_cartographic_symbol_assets`，已在
  `.bare/webgis-id-reservation.lock` 登记（migration g45_coordinate_operations、
  journey e2e/j25-g45-coordinate-operations、pytest
  tests/integration/test_pi_g45_coordinate_operations_journey.py、前缀 g45_、
  JobKind=coord_operations、DataObject kind=coordinate_operation）。
- vendor Pi pin：earendil-works/pi @ v1.5.2（b4afdddf），未动。

## 本机能力矩阵（实测，G45 venv = 本 worktree .venv）

- pyproj 3.8.0 / PROJ 9.8.1（requirements.lock:344 固定，实测一致）。
- `pyproj.network.is_network_enabled() == False`（离线；产品运行时不自动联网补格网）。
- PROJ data dir 无 `grids/` 目录 → 0 个受信格网可用；所有格网依赖操作列为
  unavailable（含原因与官方 URL），不做静默 fallback（C08 基线事实）。
- `TransformerGroup`（pyproj.transformer）：available transformers +
  unavailable CoordinateOperations（含 accuracy/area_of_use/grids 元数据）。
- `Transformer.to_json()`（PROJJSON）→ `Transformer.from_pipeline(json)` 可**精确**
  重建同一操作（实测 4326→32650 bit-identical）→ 操作锁定的实现基础。
- EPSG:4326 官方轴序 = (latitude north, longitude east)；EPSG:2263 单位
  US survey foot（factor 0.30480060960121924）；EPSG:7912 = Geographic 3D（动态
  datum 域）；EPSG:9518 = Compound CRS → 均需 typed 拒绝或明确边界。
- pyarrow 26.0.1 + pyogrio 0.13.0（GPKG driver）+ shapely 2.2.0 可用。
- pyogrio GPKG writer + G18 PyogrioWriter：无 crs.target_crs 时不建 transformer
  → "保持 CRS 导出、无再次选操作" 可达成（C23）。

## 关键既有能力（dedup 基线，详见 DEDUP_MATRIX.md）

- `app/tools/coord_transform.py` reproject_coordinates 工具、
  `app/utils/coord_transform.py` transform_geojson（EPSG legs 用
  Transformer.from_crs always_xy）——保留，加回归保护，不重写。
- G18 data_delivery：SourceSnapshot 四类源锚 / 导出 CRS 语义 / loss 闸。
- G23 data_operations runner：submit_async_job / run_async_job /
  AsyncJobHandle（fencing/看门狗/进度节流）。
- G38 outputs 发布链（staging→回读 oracle→原子更名→DataObject CAS→
  register_artifact→收据）；G39 materialize_geoparquet（fabric-parquet ref）。

## 本任务增量（任务书 G45 专属）

坐标操作的**作者-审阅-冻结-执行-成果证据**闭环：CRS/轴/单位/维度合同 →
TransformerGroup 候选（含 unavailable 原因）→ 服务器签发 candidate_id +
冻结操作定义 + 环境证据 → 有界预览（独立 oracle 支撑）→ durable 全量执行
（all-or-nothing + 预算闸 + 严格发布门）→ 带 target CRS 的原生 GeoParquet 成果
（G18 可保持 CRS 交付）+ 独立 WGS84 显示副本 → UI/Pi 同一服务合同。

## 环境资源纪律

- 本 worktree 独立 .venv（uv, requirements.lock 全量，实测 pyproj/pyarrow 可用）。
- pytest 单进程；重阶段取 `$(git rev-parse --git-common-dir)/webgis-heavy-stage.lock`。
- 前端 pnpm/vitest 1-2 workers；Playwright 1 worker。
