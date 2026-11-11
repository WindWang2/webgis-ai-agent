# G24 INTERFACES — 身份映射表与新增契约

## 1. P0 身份审计表：dataset/version → canonical id → tile → rendered → edit id

| 层 | 载体 | 语义（本 PR 统一后） |
|---|---|---|
| source dataset/version | session ref (`ref:geojson-*`) + `content_revision`（覆写即前进；descriptor 披露） | FC 数组 revision 内不可变；自然序 = 稳定序 |
| canonical feature id | `properties._wfid`（物化后，`wf1-<sha256[:16]>`）→ 顶层 `feature.id` → `properties.{id,OBJECTID,fid,osm_id,@id,featureId,feature_id}` | 三侧同链同序（后端 `_chain_stable_id` / `resolve_feature_stable_id` / `_find_feature_by_id` / `_chain_id_of` ≡ 前端 `featureIdFromProperties` / `BRUSH_ID_KEYS`）；物化前身份（feature_edit）= 顶层 id 优先 + wfid 显式分支 |
| tile id | ① 有链内 id：properties 携带（链值字符串/int<2^53）+ **无** field-1 id；② 无链内 id：field-1 = FC 自然索引+1（`id_mode=positional`，仅 revision 内稳定，回查必须带 `v`） | >2^53-1 整数属性以字符串下行（JS Number 精度） |
| rendered feature | MapLibre 子层 `${layerId}__{fill,outline,line,point,…}`，`source-layer="data"`；AM 分裂件/跨瓦片/世界副本同 id 多次命中 | 客户端按 id 去重（brush `projectBrushHits` / `candidateIdsFromHits`） |
| edit id | 物化后一律 WFID（`resolve_feature_ids`）；重复自然键请求 → `ambiguous_target` typed 拒绝（A07） | split/merge 血缘：journal `lineage` + 回执 `created_wfids/retired_wfids` |
| 无 ID 源 | 位置 id 仅 revision 内可读；编辑走首次物化（create/materialize 分配 wfid）；远程只读瓦片（无 ref）无权威面 → 不进编辑通道 | 不发明假稳定 id |

## 2. 新增服务端契约

### selection handles（`app/api/routes/selection_handles.py`，prefix `/api/v1`）
- `POST /layers/data/{ref_id}/selection?session_id&v` body `{kind:'bbox',bbox}|{kind:'where',where}|{kind:'ids',ids≤200}` → `{handle,count,count_exact,revision,scope,missing_ids?,ttl_seconds}`（409 revision_conflict / 400 selection_bad_scope）
- `GET  /layers/data/{ref_id}/selection/{handle}?session_id` → meta（404 not_found 不泄露；410 selection_stale 附 handle/current revision）
- `GET  /layers/data/{ref_id}/selection/{handle}/features?limit≤1000&cursor&fields&order` → FeatureCollection + pagination + selection{count,count_exact} + reason_codes（bbox_coarse / am_split_natural_order / ids_resolved / order_unsupported_*）
- `POST /layers/data/{ref_id}/selection/{handle}/materialize` → `{ref_id,feature_count,coarse_bbox,revision}`（422 selection_over_budget > 10k，绝不静默截断）
- `DELETE /layers/data/{ref_id}/selection/{handle}` → 幂等释放

**安全语义**：每请求 `require_owned_session` + `get_ref_data_shared` 重新鉴权 ref；
handle 只承载寻址不承载权限；TTL 600s 空闲 / 每会话 64 个 LRU；revision 前进 → 410 + 释放。
bbox = 包络粗滤相交（保守超集，reason `bbox_coarse`）；跨反子午线 bbox（w>e）拆两箱（A14）。

### 服务层（`app/services/selection_handles.py`）
`create_selection / selection_features_page / materialize_selection / handle_store`；
ids 解析 first-wins（与回查一致），`missing_ids` 明示；计数 > 250k → `count=None, count_exact=False`。

### feature_edit 增量
- FINDING_CODES += `ambiguous_target`（重复自然键，detail.ids/counts）
- `identity.find_ambiguous_ids(fc, requested)`

### Pi 工具（`app/tools/selection_tools.py`，tier 2，cacheable_read）
- `webgis_pick_detail(ref_id, feature_id)` → found/count=1/completeness=exact/revision/properties≤24/geometry_summary（类型/顶点数/bbox，**无坐标**）
- `webgis_selection_inspect(ref_id, handle, page_size≤200, cursor?)` → count/count_exact/completeness/revision/scope/ids_page/has_more（410→typed stale；跨会话→selection_not_found）

## 3. 新增前端契约

- `lib/edit/geometry-hit.ts`：`preciseFeatureHit(feature,lngLat,project,point,tol)` → `{hit,distPx}`（面=外环减洞、洞内不被邻近救回；线=逐段；Multi*=部件最优）
- `lib/edit/mvt-pick.ts`：`candidateIdsFromHits`（去重≤5）/ `fetchAuthoritativeFeature`（链内→positional v-guard 重试）/ `pickAuthoritativeFeature`（候选→回查→精确命中，最近者胜）
- `lib/api/selection-handles.ts`：`createSelectionHandle / fetchSelectionFeatures / materializeSelectionHandle / releaseSelectionHandle`；`SelectionHandleStale`
- `lib/selection/selection-store.ts`：`SelectionContext.{handle,handle_count,handle_count_exact}`（可选、有界）；`registerSelectionHandleReleaser`（清空/替换/会话切换释放）
- `lib/edit/layer-edit-bridge.ts`：`applyLineageToSelection(retired, created, refId)`（A19）
- `lib/map-components/selection-table-data.ts`：`useSelectionHandleTable({layer,handle})`（分页/410 stale/计数如实）
- map-panel：截断框选 → 句柄（epoch+publishedRevision 迟到守卫）；table-panel：`table-panel-selection-view-toggle` 选中集视图
- feature-layer-editor：`pickEditTarget`（inline 本地 / MVT 权威链）

## 4. 复用（不重写）的既有契约

`GET /layers/data/{ref}/feature/{fid}`（index O(1)）；`GET .../features`（cursor/order/where/bbox/total_matching/id_mode）；G14 feature_edits REST（preview/decision/undo/lineage）；`refreshDatasetLayer`（MVT v= 缓存键重建）；`query_map_features`（渲染面，保持不动）。
