# G31-CROSS-LAYER-TOPOLOGY — INTERFACES

## 后端模块（app/services/topology_workbench/）

| 模块 | 公共面 | 语义 |
|---|---|---|
| `contracts.py` | `RULE_TYPES` / `RULE_ROLE_SPECS` / `SEVERITIES` / `BINDING_KINDS` / `SCOPE_KINDS` / `ERROR_CODES` / 预算常量 / `typed_error()` | 封闭词表单一登记点 |
| `ruleset.py` | `validate_rule(s)` / `ruleset_digest` / `rule_catalog` / `required_roles` / `complete_roles` / `normalize_run_bindings` | 规则 DSL 校验 + 版本 digest |
| `snapshots.py` | `resolve_role_snapshot` / `resolve_session_ref` / `apply_scope` / `bbox_snapshot` / `resolve_selection_handle_scope` / `SnapshotError` | 权威快照（session_ref / project_artifact / bbox AOI；G24 句柄提交时解析） |
| `geometry_core.py` | `choose_computation_crs` / `prepare_source` / `transform_source_in_place` / `geometry_to_geojson` / `RegionUnsupported` | 科学预检 + 度量 CRS 选择（复用 `crs_safety.recommend_metric_crs`） |
| `evaluators.py` | `EVALUATORS` / `RULE_EXPECTED_FAMILY` / `MeasureSpace` / `EvalFinding.key()` | 五类规则纯函数；关系运算在关系 CRS、度量在度量 CRS |
| `runner.py` | `execute_topology_run` / `compute_input_digest` / `RunExecutionError` | 执行体（指纹比对/复用克隆/取消/单事务终态） |
| `service.py` | `create_ruleset` / `update_ruleset` / `get_ruleset` / `list_rulesets` / `submit_run` / `get_run` / `list_runs` / `list_findings` / `get_finding` / `get_finding_witness` / `WorkbenchError` | REST 与工具共用编排面；`anchor_session_id` 会话锚定所有权 |
| `exceptions_svc.py` | `accept_exception` / `revoke_exception` / `list_exceptions` / `recheck_run` | 例外（用户决定）+ 复验（finding-key diff → resolved） |
| `repair.py` | `build_repair_candidates` / `build_g14_preview` / `export_run_findings_geojson` | 候选修复（归属选择类不自动判）→ G14 预览；证据导出（BlobStore+回读核验） |
| `jobs.py` | celery task `topology_workbench.run` | durable job 执行体（registry token + DB 取消探针） |

## 数据模型（migration 0105，app/models/topology_workbench.py）

`TopologyRuleset(id, project_id?, session_id?, name, current_version, rules JSON, digest, created_by)` ·
`TopologyRun(id, ruleset_id→rulesets, ruleset_digest, rules_snapshot, status∈{pending,running,completed,failed,cancelled}, bindings JSON, input_digest, computation_crs, method_version, rule_results JSON, completeness JSON, findings_total/open, witness_key/sha256, job_id, recheck_of_run_id?, …)` ·
`TopologyFinding(id, run_id→runs, rule_id, rule_type, finding_key, severity, status∈{open,accepted_exception,resolved,dismissed}, metric JSON, locators JSON, witness_index, witness_digest, bbox_w/s/e/n, geometry_type, message, resolved_by_run_id?, exception_id?)` UNIQUE(run_id, finding_key) ·
`TopologyException(id, ruleset_id→rulesets, rule_id, finding_key, anchor JSON, reason 1..2000, status∈{active,revoked,stale}, created_by, revoked_at/by)`

## REST（/api/v1/topology-workbench，routes/topology_workbench.py）

`GET /rules/catalog` · `POST /rulesets` · `GET /rulesets` · `GET /rulesets/{id}` · `PUT /rulesets/{id}`（内容变更→新版本） · `POST /runs`（快照解析+durable job） · `GET /runs` · `GET /runs/{id}` · `GET /runs/{id}/findings`（全量计数+状态计数） · `GET /findings/{id}` · `GET /findings/{id}/witness`（BlobStore 读回+stale） · `POST /findings/{id}/exception`（用户决定） · `GET /exceptions` · `DELETE /exceptions/{id}` · `POST /findings/{id}/repair` · `POST /findings/{id}/repair/preview`（G14 plan） · `POST /runs/{id}/recheck` · `POST /runs/{id}/export`

错误信封：FastAPI HTTPException(detail={code,message,…})；全局包装器呈 `{code:VALIDATION_ERROR, data:{code:  typed code}}`。

## @tool（app/tools/topology_workbench_tools.py，_TOOL_MODULES 登记）

`topology_ruleset_configure`（tier2, state_mutation） · `topology_check_run`（tier2） · `topology_check_status`（cacheable_read） · `topology_findings_list`（cacheable_read） · `topology_finding_detail`（cacheable_read） · `topology_repair_preview`（G14 预览；不写数据）。例外接受**不提供**工具面（用户决定，任务书 WP09）。

所有权模型：用户身份（ToolExecutionContext.user_id）或**已验证会话锚定**（turn token 校验后的 session_id；bridge 不带未验证会话回调）。

## 前端

`frontend/lib/api/topology-workbench.ts`（typed client） · `frontend/components/sidebar/project/topology-workbench-panel.tsx`（挂载于 project-assets-section `topology` 页签；相机定位经 `useHudStore.setViewport`） · i18n 命名空间 `topology`（zh/en，key-completeness 钉住）。

## 复用边界（不改他人所有权）

- G14 feature_edit：`service.build_and_store_plan` 只读消费（修复桥）
- G24 selection_handles：`handle_store.get` 提交时解析（句柄不持久化）
- G18 source_resolver 语义模式：project_artifact 读回（`project_artifact_promotion.read_content` + ArtifactRevision）
- durable jobs / BlobStore / feature_pages / crs_safety：只消费
