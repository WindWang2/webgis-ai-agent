# Quality Platform（ADR-0104）

跨子系统的质量基础设施：生成式清单、契约漂移闸、场景语料、混沌与认证。
`QUALITY_MANIFEST.md` / `quality-manifest.json` / `CONTRACT_DRIFT_REPORT.*` /
`QUALITY_REPORT.*` 为生成物；`certifications/` 下的表同样由脚本再生。

## 组成（全部已落地）

| 部分 | 入口 | 闸 |
|---|---|---|
| Quality Manifest（W1） | `scripts/gen_quality_manifest.py` | 字节一致 + 指纹稳定 + findings 词表 |
| 描述符富化闸（ADR-0103/W1） | `scripts/check_tool_descriptor_coverage.py` | 基线棘轮，回退即红 |
| Scenario DSL 与语料（W2/3） | `app/evaluation/case.py`（新字段）+ `app/evaluation/quality_corpus.py` | 语料规模/确定性/全量 replay |
| Contract Drift（W4） | `scripts/gen_drift_report.py` | BLOCKER/MAJOR 清零 |
| Trace 完整性（W5） | `scripts/gen_trace_certification.py` | 行为认证 + 表字节一致 |
| 结构化观测（W6） | `app/lib/observability/` | 词表锁 + 脱敏 + 有界 digest |
| Chaos / 故障注入（W7/8） | `tests/fixtures/chaos.py` | fault 注册表 + 生产零引用扫描 |
| Cancellation / Resource（W9/10） | `scripts/gen_resource_certification.py` | 行为认证 + 表字节一致 |
| 结构性能闸（W11） | `tests/quality/test_structural_perf_gates.py` | fail-closed 基线（STRUCTURAL_UPDATE_BASELINES=1 刷新） |
| Determinism（W12） | `scripts/gen_determinism_certification.py` | 双跑红线 + 声明自洽 |
| 科学/制图回归（W13/14） | `tests/quality/test_scientific_regression.py`、`test_cartographic_regression.py` | 输入对抗 + 语义认证（xfail=已知缺口） |
| 安全回归（W15） | `scripts/gen_security_manifest.py` + `tests/quality/test_security_regression.py` | control→test 清单闸（无孤儿行） |
| API 兼容（W16） | `tests/quality/test_api_compatibility.py` | 快照 + breaking 分类（API_SNAPSHOT_UPDATE=1 刷新） |
| Flaky/夹具（W17/18） | `tests/fixtures/gis_samples.py` | 确定性/小/可检出坏样本 |
| 本地 runner（W19） | `scripts/quality`（`quick/backend/frontend/science/cartography/data/security/quality/perf/full`） | JSON+MD 报告 → `.agent-work/quality-v1/`（gitignored） |
| 质量报告（W20） | `scripts/gen_quality_report.py` | 聚合投影，字节一致 |

跨波集成：`tests/quality/test_system_scenarios.py`（全链
plan→tools→MapSpec→verify→trace 认证）。

## 原则

1. **派生优先**：registry 与测试套件是唯一事实源；本平台一切产物可再生，
   配 drift test。
2. **线索不是判决**：静态发现（未引用、缺声明）是复核线索；行为正确性
   由各 lane 的真测试保证。
3. **生产零开关**：故障注入只存在于测试进程（fixture 层），生产代码
   不含 quality 分支（chaos 注册表有结构扫描锁）。
4. **资源有界**：runner 车道串行、pytest timeout 兜底；语料 replay
   离线零 LLM。
5. **诚实披露**：认证表区分 runtime-populated / contract-only、TESTED /
   KNOWN-GAP，不为缺口伪造 passed。
