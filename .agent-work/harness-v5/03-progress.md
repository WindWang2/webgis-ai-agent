# Harness V5 Progress（waves 已全部实现并本地验证）

Branch: `feat/harness-v5-autonomous-runtime` @ worktree `../harness-v5` · Baseline `445ad30`

| Wave | 决策 | 交付 | 测试 |
|---|---|---|---|
| W1 | D1 durable trace | `trace_store.py` flock/seq/幂等/trim 保护/原子重写 + `gis_trace.py` pin 区 | test_trace_store_v5（8 进程并发零丢行契约） |
| W2 | D2 taxonomy | `failure_taxonomy.py`（11 类 + 适配器 + 预算）+ dispatch seam `harness_failure` | test_failure_taxonomy_v5（含真实 dispatch 集成） |
| W3 | D3 CRS | `longitude.py`（约定/AM/拆分）+ InvalidCRS 收口（KNOWN-GAP #1 xfail 转正） | test_longitude_v5 + scientific_regression 转绿 |
| W4 | D4 profile | `deepen_profile` + longitude_facts 全链（V3→resolver→planner）+ `profile_dataset` 工具接线 | test_progressive_profile_v5 |
| W5 | D5 telemetry | 逐层 render telemetry + chart 数据级核验（前端 registry + 后端 3 新 finding codes） | test_render_telemetry_v5 + vitest |
| W6 | D6 accounting | 子代理专属 TurnEvidence + `llm_usage` + lineage + 父 roll-up | test_subagent_accounting_v5 |
| W7 | D7 retrieval | 66 条人工金标开环语料 + p@1/invalid 指标（实测钉门） | test_retrieval_eval_v5 |
| W8 | D8 resume | anchors 表 + migration 0032 + 服务/路由 + ref_map 重水合 | test_resume_anchor_v5（含重启模拟） |
| W9 | D9 failure corpus | 八类故障语料 + 预算阶梯终止证明 + e2e 恢复场景 | test_recovery_scenario_v5 |
| W10 | docs | ADR-0118 + CHANGELOG | — |

## Review 状态
- Round 1（runtime/persistence/concurrency）：1 CRITICAL + 3 MAJOR + 6 MINOR → **全部修复**（79c17cba）。
- Round 2（perf/security/UX/maintainability）：进行中。

## 本地验证记录
- ruff（app/tests/main/manage —— CI 范围）：0 errors（scripts/gen_science_oracles.py 的 32 个为 master 既有，scripts 不在 CI lint 范围）。
- frontend：eslint 0 / tsc 0 / vitest 全量（2617 tests）——mock-surface 契约测试驱动补齐 `isSourceLoaded`/`querySourceFeatures` mock。
- 后端全量 lane（`-m "not perf and not cartography and not real_services"` --cov-fail-under=75）：见最终报告。

## Known limitations（如实）
- RemediationLedger / 开环 retrieval p@1 0.65 / chart telemetry 依赖前端注册表（旧构建缺席→warning 兜底）——均已在 ADR-0118 披露。
- `normalize_geojson_geometry`/AM 拆分为分析面纯函数入口（MVT 渲染面归 renderer Epic）。
