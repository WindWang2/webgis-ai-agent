# Quality Report（自动生成）

> 由 `python scripts/gen_quality_report.py` 聚合各派生工件，
> 请勿手改。本地运行状态（runner）不在本报告内：见
> `.agent-work/quality-v1/runner-report.md`（gitignored）。

## Capability Coverage（QualityManifest）

- tools 294 · algorithms 192 · capabilities 123 · artifact_types 21 · recipes 164
- 描述符富化闸：**PASS**（可执行工具 294）

| finding code | count |
|---|---|
| ALGO_HEAVY_NO_VARIANTS | 30 |
| ALGO_NO_CONFORMANCE | 20 |
| CAPABILITY_NO_CONFORMANCE | 16 |
| TOOL_DESCRIPTOR_INCOMPLETE | 155 |
| TOOL_UNTESTED | 32 |

## Contract Drift

- total 0 · BLOCKER 0 · MAJOR 0 · MINOR 0

## Certifications

| 认证表 | 位置 |
|---|---|
| trace_completeness | `docs/quality/certifications/TRACE_COMPLETENESS.md` |
| cancellation_coverage | `docs/quality/certifications/CANCELLATION_COVERAGE.md` |
| resource_safety | `docs/quality/certifications/RESOURCE_SAFETY.md` |
| determinism | `docs/quality/certifications/DETERMINISM.md` |
| chaos_fault_registry | `docs/quality/certifications/CHAOS_FAULT_REGISTRY.md` |
| security_controls | `docs/quality/certifications/SECURITY_CONTROLS.md` |

## Known Limitations

- 静态测试引用 ≠ 行为覆盖（manifest findings 是复核线索）。
- trace 认证表如实披露 contract-only 阶段（无人填充）。
- 科学/制图回归套件内的 xfail 项即已知缺口（strict=False，
  见各模块 docstring 的 KNOWN-GAP 列表）。
- OpenAPI 快照不覆盖 WS/SSE 消息契约（各自回归闸保护）。
