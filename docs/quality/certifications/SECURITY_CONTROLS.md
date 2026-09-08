# Security Controls Certification（自动生成）

> 由 `python scripts/gen_security_manifest.py` 派生，请勿手改。
> 契约：`app/lib/quality/security_manifest.py`；行为红线：
> `tests/quality/test_security_regression.py`。

## 控制 → 实现 → 回归测试

| control | area | status | 实现锚点 | 回归测试 |
|---|---|---|---|---|
| SEC-03 | tenant isolation（data-fabric sources / catalog） | TESTED | `app/api/routes/data_fabric.py`（锚点 `def _require_tenant_owned`） | `tests/unit/test_data_fabric_security.py::test_authorize_catalog_item_blocks_cross_tenant`<br>`tests/integration/test_cross_tenant_isolation.py::test_cross_tenant_session_detail_404` |
| SEC-04 | SSRF egress gate（pre-flight validate_url + per-hop adapter） | TESTED | `app/services/data_fabric/security.py`（锚点 `def validate_url`） | `tests/unit/test_data_fabric_security.py::test_ssrf_adapter_blocks_metadata_ip_on_send`<br>`tests/test_connection_manager_ssrf.py::TestConnectionManagerHostOnlySSRF::test_rejects_private_loopback_metadata_host_without_url` |
| SEC-07 | stored connection profile shape（allow_private 钉扎） | TESTED | `app/api/routes/data_fabric.py`（锚点 `allow_private=False,`）<br>`app/services/data_fabric/manager.py`（锚点 `stored_profile = conn_profile.model_dump()`） | `tests/quality/test_security_regression.py::test_create_route_forces_allow_private_false_regardless_of_payload`<br>`tests/quality/test_security_regression.py::test_create_data_source_stores_allow_private_false_round_trip`<br>`tests/quality/test_security_regression.py::test_probe_route_reader_defaults_to_false_when_key_missing` |
| SEC-08 | session ownership guard（owner_token / legacy fail-closed） | TESTED | `app/core/auth.py`（锚点 `async def verify_session_owner`） | `tests/test_sec08_session_owner_token.py::test_new_anon_session_404_with_wrong_token`<br>`tests/test_sec08_session_owner_token.py::test_legacy_null_null_session_fail_closed` |
| SEC-RT-01 | path traversal（report download validator + 路由调用点） | TESTED | `app/api/routes/report.py`（锚点 `def _validate_file_path`）<br>`app/api/routes/report.py`（锚点 `if not _validate_file_path(report.file_path, REPORT_DIR):`） | `tests/test_api_path_traversal.py::test_rejects_planted_symlink_escaping_root`<br>`tests/test_api_path_traversal.py::test_download_rejects_symlink_escape_planted_in_report_dir` |
| SEC-RT-02 | query injection net（AST→SQL/CQL2/ArcGIS/FES 编译器 + 标识符卫生） | TESTED | `app/services/data_fabric/query/compilers.py`（锚点 `def quote_ident`）<br>`app/services/data_fabric/adapters/postgis_adapter.py`（锚点 `def _sanitize_identifier`） | `tests/quality/test_security_regression.py::test_predicate_values_stay_bound_parameters`<br>`tests/quality/test_security_regression.py::test_adapter_identifier_rejects_injection`<br>`tests/quality/test_security_regression.py::test_no_fstring_sql_execution_in_app` |
| SEC-RT-03 | error oracle（is_production 门控 traceback/error_detail 出口） | TESTED | `app/core/exception.py`（锚点 `include_details = not settings.is_production()`） | `tests/quality/test_security_regression.py::test_production_response_is_generic_no_traceback`<br>`tests/quality/test_security_regression.py::test_dev_response_pins_designed_detail_behavior`<br>`tests/test_error_sanitization_v2.py::test_chat_exception_hides_internal_details` |
| SEC-RT-04 | sensitive-key redaction parity（2 denylist + trace hints + 1 allowlist） | TESTED | `app/services/jobs/redaction.py`（锚点 `SENSITIVE_KEY_PARTS: tuple[str, ...] = (`）<br>`app/services/data_fabric/security.py`（锚点 `def sanitize_profile_dict`）<br>`app/lib/runtime/trace.py`（锚点 `_SENSITIVE_KEY_HINTS = (`）<br>`app/services/geocompute/tracing.py`（锚点 `allowed = {`） | `tests/quality/test_security_regression.py::test_jobs_redaction_denylist_covers_baseline_behaviorally`<br>`tests/quality/test_security_regression.py::test_geocompute_trace_allowlist_never_admits_baseline_keys`<br>`tests/jobs/test_job_redaction_progress.py::test_sensitive_keys_are_redacted` |
| SEC-KG-01 | artifact ownership（路由直调禁令 + session 作用域签名钉扎） | TESTED | `app/services/artifact_registry.py`（锚点 `async def get_artifact`） | `tests/quality/test_delete_ownership_matrix.py::test_no_route_module_calls_artifact_registry_directly`<br>`tests/quality/test_delete_ownership_matrix.py::test_artifact_registry_public_api_is_session_scoped`<br>`tests/quality/test_security_regression.py::test_artifact_session_scope_isolates_foreign_sessions` |
| SEC-KG-02 | templates / knowledge delete authZ（owner 矩阵回归） | TESTED | `app/api/routes/templates.py`（锚点 `async def delete_template(`）<br>`app/api/routes/knowledge.py`（锚点 `async def delete_document(`） | `tests/quality/test_delete_ownership_matrix.py::test_template_delete_owner_allowed`<br>`tests/quality/test_delete_ownership_matrix.py::test_template_delete_other_user_denied_row_intact`<br>`tests/quality/test_delete_ownership_matrix.py::test_template_delete_admin_may_delete_foreign`<br>`tests/quality/test_delete_ownership_matrix.py::test_template_delete_anonymous_denied`<br>`tests/quality/test_delete_ownership_matrix.py::test_template_builtin_readonly_even_for_admin`<br>`tests/quality/test_delete_ownership_matrix.py::test_template_missing_404`<br>`tests/quality/test_delete_ownership_matrix.py::test_template_null_creator_fail_closed_for_viewer`<br>`tests/quality/test_delete_ownership_matrix.py::test_knowledge_delete_creator_allowed`<br>`tests/quality/test_delete_ownership_matrix.py::test_knowledge_delete_other_user_denied_row_intact`<br>`tests/quality/test_delete_ownership_matrix.py::test_knowledge_delete_same_org_member_denied`<br>`tests/quality/test_delete_ownership_matrix.py::test_knowledge_service_no_identity_fail_closed` |

## 显式缺口（KNOWN-GAP：manifest 门允许『有测试』或『显式缺口』，沉默缺失即红）

（无）

> 审计出处：`.agent-work/quality-v1/06-security-regression-map.md`（file:line 证据 + 15 风险排名）。

- 内容指纹：`23a6ede46b108a7b…`
- 门禁校验：`validate_security_manifest()` → 0 issue(s)
