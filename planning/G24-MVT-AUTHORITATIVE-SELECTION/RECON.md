# G24 RECON — MVT 大图层的权威拾取、选择与编辑连接

调查时间：2026-11-11（UTC）。BASE_SHA = `b8f2a6ecc777d0ae633202034b609a48a8d140d9`（origin/master，含 #2084/#2085/#2086 合并）。
Worktree：`/home/kevin/projects/webgis/webgis-wt-g24-mvt-selection-20261111T151706Z`，分支 `zcode/g24-mvt-authoritative-selection-20261111T151706Z`。

## 0. 远端与 PR 状态

- fetch 后 origin/master = `b8f2a6ecc`；任务快照中开放的 #2084/#2085/#2086 已全部合并 → 不再是本地验证阻断。
- `gh pr list --state open` = 空集（无开放 PR）。
- 近 30 天相关已合并 PR：#2073（能力资格）、#2074（typed field calculation）、#2075（G14 持久化矢量编辑）、#2076（frame product）、#2077（G16 标注）、#2078（G20 栅格）、#2079（G17 导入）、#2080（G18 数据交付）、#2081（Pi 原生发现）、#2083/#2085/#2086（依赖/兼容修复）。
- 相关开放 issues（只登记不接管）：#2004（框选>8截断——SW-02 已在 master 修 matched_count 上行）、#2064（frontend/lib 汇总含框选过滤截断——仅登记边界）、#2061（Pi /config/llm——#2081 已修）。

## 1. 前端现状（已核实 file:line）

### 单击拾取（已有权威回填！）
- `frontend/components/map/map-panel.tsx:1245` click → `map.queryRenderedFeatures(evt.point, {layers: interactiveIdsRef.current})`；interactive ids 来自 applied MapSpec 的全部 `__` 子层（`frontend/lib/map-kit/interactive-ids.ts`），MVT 层子层在内 → **MVT 点击拾取已可达**。
- `frontend/lib/hooks/use-feature-selection.ts:60` `commitSelection()`：写 selectedFeature 快照（MVT 标 `isApproximate`）→ `publishSelection('select')` → 对 MVT 层 `ensureLayerData(targetId,'selection-detail',{featureId})` 权威回填（seq+AbortController 防竞态，失败置 isApproximate）。
- 候选列表：PoiPanel `features.slice(0,5)`（map-panel.tsx:1303）。

### 框选（渲染面事实，无权威面）
- `frontend/lib/selection/brush-select.ts`：`projectBrushHits` ≤ MAX_SELECTED_IDS(50)，`truncated` 时带 bbox 谓词；`matched_count` = **渲染命中数**（已去重）。
- `frontend/lib/selection/selection-store.ts`：SelectionContext 有界契约（ids≤50、props≤8 标量、谓词封闭词表 bbox/in）；**无服务端 handle**。
- `frontend/components/map/map-panel.tsx:1416-1476`：brush onUp → queryRenderedFeatures（label 子层剔除）→ 多数族过滤 → publishSelection('brush')。

### 编辑连接（G24 的核心缺口）
- `frontend/components/map/feature-layer-editor.tsx:44` `targetFeatures(layerId)`：只吃 HUD inline `source.features` 或 `ensureLayerData(layerId,'selection-detail')`（该 reason 需要已选中 featureId 且只返回单要素）→ **MVT 大图层编辑手势的拾取目标恒为空**（delete/edit_vertices/split 首击无效果）。
- `frontend/components/map/feature-edit-toolbar.tsx:103`：编辑入口允许选任何带 `_refId` 的 HUD 行（含 MVT 行）→ 目标可达但拾取断链。
- `frontend/lib/edit/layer-edit-bridge.ts`：preview→approve、回执驱动 refreshDatasetLayer/pruneSelectionIds/undo 注册；`featureIdFromProperties`（_wfid 优先链）。

### 数据面
- `frontend/lib/store/layer-data.ts`：`isMvtLayer`（_tileUrl + mvt_capable + feature_count>5000）；`ensureLayerData` 4 reason；单要素回查带 `id_mode=positional` 回退 + revision guard。
- `FEATURE_ID_KEYS = ['_wfid','id','OBJECTID','fid','osm_id','@id','featureId','feature_id']`（三处同源：layer-data/brush-select/feature_pages.py）。

## 2. 后端现状（已核实）

### 瓦片生产
- `app/api/routes/layer.py:705` `GET /layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt`（session 鉴权 + ETag 304 + 瓦片缓存 `_encode_tile_cached`）。
- `app/services/mvt.py`：
  - `_chain_stable_id`（:49）：FEATURE_ID_KEYS 链（顶层 id 优先）→ str；无链内 id → `_positional_feature_id`（:69）= FC 自然索引+1 写 MVT field 1（仅 revision 内稳定）。
  - `_encode_value`（:226）：int 出全 64 位 varint；**|v|≥2^63 才降级 string** —— 2^53<|v|<2^63 仍以 int64 发出，JS 端 varint→Number 丢精度（A06 风险点）。
  - 空间索引 `SpatialIndexEntry` + `feature_id_index()`（:1358）懒构建 id→自然索引；`spatial_index_cache` 常驻 source_features。
  - 反子午线拆分 `_split_line/_split_ring/_split_polygon`；world copies 由 MapLibre 多世界渲染产生（同一 tile 内容重复渲染）→ 框选去重靠 id。

### 权威单要素 lookup（P1 大半已在）
- `app/api/routes/layer.py:443` `GET /layers/data/{ref_id}/feature/{feature_id}`：
  - 链内 id：优先 `feature_id_index()`（O(1)）；回退 `_find_feature_by_id` 线性扫。
  - `id_mode=positional`：位置 id 回查，v 不符 → 409。
  - 已有 session 鉴权（require_owned_session）、_layer_data_budget。
  - **注意**：`_find_feature_by_id`（:425）的 key 链缺 `_wfid`（走索引路径时 feature_id_index 以 FEATURE_ID_KEYS 含 _wfid —— 待核实 feature_pages.py 常量）。

### 分页/聚合（P3/P4 地基已在）
- `app/api/routes/layer.py:130` `GET /layers/data/{ref_id}/features`：cursor 分页 + bbox 窗口 + fields 投影 + order + typed where AST + revision guard(409) + total_matching（可证才给）+ id_mode/duplicate_id_count 披露 + reason_codes。
- `GET /layers/data/{ref_id}/aggregate`：分组聚合 + AGG_SCAN_LIMIT 422。

### feature_edit（G14，复用不重写）
- `app/services/feature_edit/identity.py`：WFID 物化（fingerprint+index → wfid）、`resolve_feature_ids`（既有 id 链→WFID）、`index_by_wfid`。
- journal/CAS/undo/split/merge/topology 闸齐全（#2075）。

### 选择/字段计算/标注消费面
- （详见调查员报告 INTERFACES.md：fieldcompute typed selection scope、G16 标注 author state、导出消费者）

## 3. 缺口结论（G24 增量）

| # | 缺口 | 对应工作包 |
|---|------|-----------|
| 1 | 编辑手势对 MVT 层拾取断链（targetFeatures 空） | P2/P4 |
| 2 | 命中后无"精确命中"判定（瓦片几何裁剪/简化，长线段中点、洞、Multi* 处理在编辑路径缺失） | P2 |
| 3 | 框选/条件选择无权威全集：matched_count 是渲染面计数；>50 无服务端 handle；消费者无法翻页"全部" | P3 |
| 4 | 属性表与选择集不贯通（分页属性表面已有端点，无选择 scope/handle 接线） | P4 |
| 5 | split/merge 后 selection 消费 lineage（pruneSelectionIds 已有 retired 清理；created 合并/更新未见） | P5 |
| 6 | 无 Pi 侧 selection-inspect/pick-detail 工具 | P6 |
| 7 | 10万/百万级 + holes/多部件/跨tile fixture 与性能证据 | P7 |
| 8 | >2^53 整数 id 经 MVT int64 丢精度（编码侧与 JS 接收侧） | P0 |
| 9 | 只读/不可溯源 MVT 源的明确 unsupported 披露（readonly 远程瓦片） | P0/P1 |

## 4. 资源与环境

- 16 CPU / 62GB RAM / 542GB 空盘。编译默认 `-j2`；pnpm store 共享只读缓存，worktree node_modules 独立（install 进行中）。
- 后端测试：借主仓 `.venv`（只读层）+ worktree `pythonpath=.`；`pytest tests/unit --no-cov -q`。
- 前端测试：`pnpm test`（vitest）。
- 其他并行方向 worktree（g22/g23/g27 等）在跑 —— 宿主重任务锁纪律：重 build/大 pytest 一次一个。
