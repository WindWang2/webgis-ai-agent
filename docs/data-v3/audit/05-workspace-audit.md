# 05 — Workspace Audit（Agent E）

## 1. 持久化清单

| 表/存储 | 内容 | 重启存活 | 会话 TTL(4h) 存活 |
|---|---|---|---|
| `projects`（project.py:14） | id `proj_*`、org/owner、name、status(active/archived/deleted)、metadata_json | ✓ | ✓ |
| `project_datasets`（:42） | name、source_type+source_ref、schema_profile、crs、quality_status、version_fingerprint、detached_at 墓碑（INV-DEL1） | ✓ | ✓ |
| `workflows`（:79） | 可变 graph_spec、version、created_from_session、current_revision_id（裸指针无 FK） | ✓ | ✓ |
| `workflow_revisions`（:118） | 不可变 graph_spec+graph_fingerprint，unique(workflow_id,revision_no) | ✓ | ✓ |
| `workflow_runs`（:147） | revision、graph_snapshot、input_bindings、input_dataset_fingerprints、outputs JSON、execution_trace、completed_steps、run_manifest、run_fingerprint | 行✓——但 outputs 持 `ref:` 悬挂 | ref 值✗ |
| `artifacts`（:200） | storage_ref（会话 ref 游标）、upload_record_id FK、layer_id FK、content_fingerprint、metadata_json（晋升加 content_status/content_location/content_payload_sha256/content_summary） | 行✓ | storage_ref✗；晋升 content_location✓ |
| `artifact_lineages`（:246） | 父边、producing_tool/capability/algorithm、mapspec_fingerprint、workflow_run_id、parameters、source_dataset_id/fingerprint | ✓ | ✓ |
| `carto_project_facts`（:287） | 项目级制图先验，upsert (project_id,kind,subject) | ✓ | ✓ |
| `map_products`（:345） | 版本账本：product_fingerprint、input fps、compute_plan、output fps、mapspec_snapshot（ADR-0099）、5 维 diff_summary、parent_version_no、lineage_kind(fork/restore/merge/rerun/auto) | ✓ | ✓ |
| `conversations`/`messages`（db_model.py:209,226） | 聊天史含 tool_result JSON。**无 project_id 列** | ✓ | ✓ |
| uploads/reports/layers/analysis_tasks | 上传/报告/图层元数据 | ✓ | ✓ |
| 会话 store（session_data.py:19-618，SESSION_TTL=4h :20） | ref 载荷（LRU 200/50MB）、map_state（图层呈现/base_layer/viewport w/ seq/inline mapspec/_gis_provenance 64 上限/_cartographic_review/observation）、aliases、event log(20)、map-action ACK(200)、descriptors | 内存后端✗；Redis✓ 但 4h TTL | ✗ |
| MapSpec 磁盘（mapspec/store.py:38-41，`DATA_DIR/.webgis-agent/<sid>/`） | mapspec.json+revisions/(20)+fp/rev sidecar；原子写；磁盘复活 get_mapspec :227-281 | ✓ | TTL+1h 被 sweep（:543-602） |
| 检查点（mapspec/checkpoint.py） | checkpoints/<ckpt_id>/{mapspec.json,descriptor.json}+内容寻址 blobs/<sha>.json；manifest；自动 ckpt 20 | ✓（磁盘） | 随会话目录清扫 |
| 晋升内容（project_artifact_promotion.py:62-90） | DATA_DIR/project_artifacts/<shard>/<fp>.json 摘要复核读 | ✓ | ✓ |
| 导出（map.py:56-90；artifact_lifecycle.py:22-67） | DATA_DIR/exports/*+.owner sidecar；**无 DB 表**（SEC-10 注释 map.py:48-51） | 文件✓；owner LRU✗ | 7 天清扫 |

## 2. Project Service（project_service.py）
- CRUD：create :87、get_with_auth :115（IDOR 闸 _caller_may_access :23）、list :292、update :328（status→archive）、attach_dataset :356、detach :397（墓碑）、list_artifacts :456、save_workflow :488+`_publish_revision` :527（指纹幂等+竞态安全）、update_workflow :592、list_revisions :652、get_run :680、list_runs :724。
- **项目打开不自动恢复任何状态**——纯读。唯一真实恢复路径是 map-product 端点（routes/project.py:757 `open` 诚实报告可用恢复模式；:788 `restore` style_only → `MapProductService.restore_style_to_session`（map_product_service.py:358）把 mapspec_snapshot 应用到**活会话**（RestoreStyleIntent）+lineage_kind="restore" 行；完整恢复重跑绑定 run（project.py:823-849）。

## 3. GIS world state / MapSpec / ref 过期
- `gis_world_state/state.py:101-187` build_world_state 是会话 map_state+mapspec 的**只读投影**，无持久化。provenance=map_state `_gis_provenance`（64 上限）。
- MapSpec source 形态（mapspec_source.py:8-83）：geojson inlineData（≤5000 闸）/url/dataPath/**ref+ref_id（会话游标）**；raster **imageRef**+bounds；data_fabric/wms/wmts/pmtiles 目录源。图层仅呈现。
- **ref 过期**：4h 后载荷消失；磁盘 mapspec 活到 TTL+1h 但 sources 指向死 ref——瓦片/要素读空；checkpoint rollback 诚实报 missing_refs（checkpoint.py:433-442,447-459）；晋升记 content_status="session_expired" vs "no_session_context"（promotion :296-305）。自动检查点 presentation_only（refs 不物化 :271-279,351）；显式命名检查点 self_contained。

## 4. 会话 vs 项目边界
`tests/test_async_session_data.py` 仅覆盖后端契约；**无任何会话↔项目交叉链接**。仅会话（TTL 即失）：全部 ref 载荷、运行时图层呈现、viewport/base_layer、provenance 环、review 裁决、事件日志、ACK、计划状态（overwrite 持久）、检查点目录。持久：§1 DB 行。

## 5. 标注
`app/tools/annotation.py` 是「前端命令模式」：后端算距离/面积/marker 几何推 SSE 命令（:1-6）。**无服务端标注存储**——标注只活在前端 zustand HUD store，会话切换即失（use-workspace-session.ts:61,141,295）。

## 6. Workflow run 历史
完全可查：GET /{pid}/runs、/runs/{run_id}、/runs/compare（project.py:317-433）。重执行三路：/runs/{id}/replay（全量 :433）、/resume（部分，input_dataset_fingerprints 陈旧检查 :466-493）、/runs/{id}/rerun?from_step=（增量 ADR-0092 A5 :499-538）+diff 锚定 /map-products/{v}/rerun（:929）。**缺口**：重放把新 ref 写进当前会话；先前 run 输出仅在晋升后可读。

## 7. 导出登记
**不在 DB**。文件+.owner sidecar；ownership 是进程内 LRU `_EXPORT_OWNERS`（map.py:55-58）+sidecar 回退；7 天清扫。代码自注「生产应换 DB 表」（map.py:48-51）。

## 8. 重启即失（内存态）
- session_data_manager 单例（内存后端全失）。
- mapspec_store fp/obj 缓存+_REV_SEQ（仅缓存，安全）。
- `_EXPORT_OWNERS` LRU（sidecar 回退，fail-closed 404）。
- cartography_runtime harness/eval 缓存+删除墓碑。
- session_lock_registry、pi_turn_registry、_turn_resume_registry。
- MVT spatial_index/tile_lru（clear_session 失效）。
- project_context_cache LRU。
磁盘活（mapspec/checkpoints/exports/晋升）但 mtime 清扫 ~5h。

## 9. Alembic 规范
migrations/versions/：旧 hash-id 文件 + 现代序号 `0010_project_workspace_workflow`…**head=`0025_data_fabric_durable_stats`**。规范（0022:19-27）：revision=文件名词干；down_revision=上一词干；头部 docstring 引 ADR+日期；仅加列，`_column_exists` 守卫兼容 create_all。新迁移：`0026_<slug>.py`，down_revision="0025_data_fabric_durable_stats"。

## 10. 前端（简）
工作空间恢复 API 驱动而非 localStorage：use-workspace-session.ts:130-273 拉 sessions/{sid}+map-state→restoreSessionMapLayers（observation-first+ref 回填）+重臂 mapspec CAS。zustand persist 只存设置，**故意不存图层**。chat 发可选 project_id 但无项目工作空间 UI 持久。

## 11. 重载缺口（V3 必须闭合）
1. 无会话↔项目 FK：conversations 无 project_id，项目无法枚举/重载其创作会话。
2. 项目打开零恢复：无端点把项目状态（datasets+artifacts+最新 map product）物化进活会话（除窄的样式恢复）。
3. Artifact.storage_ref 与 WorkflowRun.outputs 持会话游标，TTL 悬挂，除非 /promote-artifacts（project.py:584）；晋升按需非自动。
4. 标注、导出登记、事件日志/ACK 无持久家。
5. 磁盘 mapspec/检查点 ~TTL+1h 无差别清扫——项目「当前地图」静默蒸发。

## 12. 快照/恢复扩展点
- `MapProductVersion.mapspec_snapshot`+restore/fork/merge（map_product_service.py:358-470+）——追加式版本账本，工作空间快照的自然核。
- WorkflowRevision+replay/resume/rerun——分析态恢复已 provenance 完整。
- mapspec/checkpoint.py 内容寻址 blob——**唯一物理再物化 ref 载荷的机制**，推广为「工作空间检查点」的模式。
- 晋升内容库——持久载荷家；把触发扩到 run 完成。

## 13. Workspace V3 家 + 迁移
加 `app/models/workspace.py`（或扩展 project.py）：`WorkspaceSnapshot`（每项目，JSON manifest：dataset/artifact/map-product/annotation refs+指纹，如 map_products 追加式）+`exports` 表（闭合 SEC-10）。迁移 `migrations/versions/0026_workspace_v3.py`，down_revision="0025_data_fabric_durable_stats"，加法式+_column_exists。服务家：`app/services/workspace_service.py`（与 project_service 并排），复用 MapProductService 恢复语义+checkpoint.py blob 物化做「真恢复」。
