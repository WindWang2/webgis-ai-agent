# 04 — Test Matrix（Phase D 实测）

| 层 | 命令 | 结果 |
|---|---|---|
| 扩展域全量（含 2032 案例 corpus） | pytest tests/unit/extensions_platform + test_pi_extension_hardening | 2373 passed |
| 静态（全仓，与 CI lint 同面） | ruff check app/ tests/ main.py manage.py | All checks passed |
| 契约层（root-level cross-module） | test_tool_meta_contract + test_subagent_context_isolation_436 + test_ci_local_gate_contract + test_ci_perf_coverage_contract | 39 passed |
| OpenAPI snapshot | tests/quality/test_api_compatibility.py | byte-identical ✓ |
| broader unit 回归 | tests/unit（除 extensions_platform，not perf/cartography/real_services） | 6107 passed, 13 skipped |
| 性能（结构性预算） | worker 冷启动 / 调用延迟（1KB×50） | 209ms（预算<300ms）；p50 0.07ms / p95 0.19ms |
| 负路径 | broker deny 矩阵 / SSRF 字面 IP / 篡改 quarantine / 降级告警 / env 消毒 / 崩溃隔离 / 超时 kill / in-flight 拒绝 | 全部 typed ✓ |
