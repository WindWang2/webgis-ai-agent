# 03 — Artifact Flow Audit（Agent C）

## 1. 逐跳流图

| 跳 | 位置 | 形态 | 身份/版本 | 记录处 |
|---|---|---|---|---|
| source→ingest | `routes/upload.py:215` | 磁盘文件+DB 行 | 仅路径+名，**无内容哈希无版本** | uploads 表 |
| ingest→dataset | `project_service.py:372` | 指针（source_type/source_ref） | `version_fingerprint`（身份指纹非内容，provenance/fingerprint.py:63-81） | ProjectDataset.version_fingerprint |
| dataset→会话 ref | 工具收 `geojson` inline 或 `ref:`（tools/spatial.py:99-102）；`ToolRegistry._resolve_references`（registry.py:1522-1661）deref 零拷贝 | ref→**原始 dict 注入 args** | `ref:geojson-<uuid16>` 随机非内容寻址；store 时描述符；`content_revision` 单调 | 仅会话 store |
| 算法执行 | `buffer_smart` 等（geo_processor/geometry.py:17,142,202）；经 SpatialAnalyzer.buffer（spatial_analyzer.py:71） | **内存原始 FC** | 无。返回 `GeoAnalysisResult`（inline FC+summary+quality 证据，无 id） | 无 |
| 结果→ref 铸造 | `ToolDispatchService.dispatch`（tool_dispatch_service.py:563-580；uncertainty :585-601；heatmap :602-607） | payload→**ref**（inline 剥离 :940-955） | 新随机 ref；`extract_artifact_metadata`→描述符 `content_fingerprint` | ArtifactRegistry `register_tool_artifact` :662——**此接缝无 inputs**（注释 :634-635） |
| workflow（会话计划） | `plan_mode.execute_plan_async`；`${stepId}` 经 `resolve_refs`（plan_mode.py:285-313）——**上游整结果 dict 进下游 args** | 步间原始载荷；确定性别名 slim 持久化（:458-497） | plan ref `ref:plan-*`；别名覆盖不累积 | plan 载荷 `__step_results__` |
| plan-apply 接缝 | `session_plan.apply_tool_result`（session_plan.py:681-779） | ref | capability/tool/**inputs=依赖行 bound_ref**/contract_check | ArtifactRegistry |
| recipe→workflow | `workflow_promotion.build_recipe_steps`（workflow_promotion.py:80-146） | capability 步，无数据 | capability+algorithm_preference | WorkflowRevision（追加式） |
| workflow run | `WorkflowEngine._execute`（workflow_engine.py:320-671）；`_resolve_step_args` :673-693——**原始上游 tool_result dict** | 步间内存原始载荷 | 每步 Artifact+`LineageService.record_lineage`（声明依赖为父母，根走 `_attribute_source_dataset` :295-316，仅标量串匹配）:487-536；run manifest+`run_fingerprint`；`execution_trace` 存**完整 args 含 inline GeoJSON** :468 | DB artifacts/artifact_lineages/workflow_runs |
| run→持久产物 | `project_artifact_promotion.promote_run_artifacts`（:226-340） | ref→payload→**内容寻址 blob**（materialize_blob:157） | sha256 `content_payload_sha256`/`content_location` | artifact.metadata_json（今日唯一内容寻址持久库） |
| geocompute 数据面 | `ExecutionNode`（geocompute/plan.py:115-232：semantic_fingerprint/dataset_fingerprints/LineageLink/upstream_fingerprints）；NodeResultStore（256 条/128MB, executor.py:140-171）；ops 传内存要素表；`MATERIALIZE`→会话 ref；弱 `_output_fingerprint`（executor.py:72-87） | 节点间原始载荷直到 MATERIALIZE | 节点语义指纹；弱输出 fp | 内存 store+ExecutionRun 证据 |
| 结果→图层 | `_author_display_result`（tool_dispatch_service.py:851-1000）：layer `result-<tool_call_id>`，source `{type:geojson, ref_id, profile, profile_fingerprint, data_fingerprint}`，layer.provenance=`{tool_call_id, result_ref}` 仅此 | **ref**（大数据拒 inline，mapspec_source.py:49-56,71-82） | mapspec fingerprint+mutation_revision CAS+磁盘修订 | mapspec.json+revisions/ |
| 制图 | `analysis_cartography_converter`+`spatial_meta_profiler`（:115-161）：需 featureCount/geometryTypes/field schema；描述符 O(1)、全扫回退；recipes+CartoProjectFact 先验 | 描述符派生 profile | profile_fingerprint | layer metadata |
| 导出 | `/api/v1/map/export`（map.py:151）、`/export/pdf`:221、`/export/geojson`:326（**前端回传原始 geojson**）、tool `export_thematic_map`（cartography.py:370） | 文件+inline 载荷 | 仅文件名；owner sidecar（map.py:52-70）；**与会话/图层/产物零关联** | 无持久 |
| 产品版本 | `MapProductVersion`（project.py:345-406）：product_fingerprint/input fps/compute_plan/output fps/artifact_ids/mapspec_snapshot/parent_version_no/lineage_kind/diff_summary | 引用+指纹 | 强 | 每 run 自动记录（workflow_engine.py:639-665） |

## 2. 血缘丢失点
1. **dispatch 接缝注册无父母**（tool_dispatch_service.py:662 从不传 inputs）——主聊天流（LLM 链式 buffer→overlay）血缘为零；只有 plan-apply 与 WorkflowEngine 填。
2. `GeoAnalysisResult`/`*_smart`（geometry.py:112-132 等）——新 FC 无 id 无父母。
3. `_attribute_source_dataset`（workflow_engine.py:295-316）——仅标量串匹配。
4. 图层 provenance 最小（{tool_call_id, result_ref}），无 artifact_id/inputs/参数。
5. 导出零关联；`export_geojson` 收原始 body。
6. UploadRecord 无内容摘要。
7. `_resolve_step_args` 仅声明依赖产生边。

## 3. 最大原始载荷元凶
- `_resolve_references`（registry.py:1603-1621）整 FC 注入 args（Redis 读重解析；get() deepcopy）。
- `plan_mode.resolve_refs` `${step}` 整 dict 替换 + `step_results` 全量持有。
- WorkflowEngine `step_outputs`/`execution_trace` args（:468）落 JSON 列。
- 每工具往返：GDF→to_json→dict→store→再序列化给 LLM（tool_dispatch_service.py:794-815）。
- heatmap Celery hop pickle 要素表（spatial.py:352-361）。
- `/export/geojson` body 重传（map.py:326）。
- geocompute 每节点拷贝要素表；NodeResultStore 持载荷 128MB。

## 4. 版本缺失
- 会话 ref 随机 id；覆盖仅 bump content_revision；两次同输入跑 → 两个不同 id。
- `Artifact.content_fingerprint`=tool+feature_count+bbox 描述符哈希，易碰撞（provenance/fingerprint.py:145-180）。
- `ProjectDataset.version_fingerprint` 不随后端可变源变化。
- geocompute `_output_fingerprint` 弱；mapspec 图层有指纹但无数据版本；`run.outputs` 截断串；uploads/exports 无版本。

## 5. 中间产物无清理堆积
- 会话 ref LRU 200/50MB；ArtifactRegistry ≤128 条（老 ref 失追，仅会话 TTL/GC 死）。
- `WorkflowRun.execution_trace` 完整 args 持久无剪枝。
- 导出文件+sidecar 无 GC。
- mapspec 修订有保留数+会话清理（OK）；plan 别名确定性覆盖（OK）。

## 6. 既有血缘机制（应扩展）
DB Artifact/ArtifactLineage+LineageService（环检测）；ArtifactRegistry/ArtifactGraph（inputs/replaces/dependents）；RunManifestBuilder/run_fingerprint/ToolExecutionContext；MapProductVersion；analysis-reuse keys；per-ref content_revision；geocompute LineageLink/ARTIFACT_REGISTER；晋升内容库。

## 7. 最小扰动接缝建议
1. **dispatch 接缝 inputs**：`_resolve_references` 已有 `cursor_sink`/`cursor_track_keys` 钩子（registry.py:1648-1658）捕获喂入调用的 refs——接进 dispatch 传 `inputs=[...]` 给 register_tool_artifact（:662）。最大血缘洞近零爆炸半径闭合。
2. 工具入参惰性 ref 化（按 arg/尺寸阈值保 ref 不透明，需要时 get_shared）。
3. store 时内容寻址（session_data.store 算 sha256）。
4. workflow 步间传 ref 而非原始 dict；trace args 只留 keys+refs。
5. 图层 authoring 增 artifact_id+input refs+algorithm。
6. 导出收 ref + exports 记录行关联产物/图层/产品版本。
7. geocompute 节点间 REF 型载荷。
