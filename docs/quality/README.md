# Quality Platform（ADR-0104）

跨子系统的质量基础设施：生成式清单、契约漂移闸、场景语料、混沌与认证。
本目录内除 `QUALITY_MANIFEST.md` / `quality-manifest.json`（生成物）外的
文档为手写架构文档。

## 组成

| 部分 | 位置 | 形态 |
|---|---|---|
| Quality Manifest | `app/lib/quality/` + `scripts/gen_quality_manifest.py` | registry 派生投影，字节一致性闸 |
| 描述符富化闸（ADR-0103） | `scripts/check_tool_descriptor_coverage.py`（薄壳） | 基线棘轮，回退即红 |
| Contract Drift Gates | `tests/quality/test_contract_drift.py` | 漂移报告工件 + BLOCKER 红 |
| Scenario DSL 与语料 | `app/evaluation/` 扩展 + `tests/quality/scenarios/` | 生成器 → 冻结 JSON → replay |
| Chaos / Fault Injection | `tests/fixtures/chaos.py` + `tests/quality/test_chaos_*.py`（注册表文档：`certifications/CHAOS_FAULT_REGISTRY.md`，生成器 `scripts/gen_chaos_registry.py`） | test-only，fault ID 注册表 + 确定性 schedule + journal |
| 认证表 | `docs/quality/certifications/` | 生成物（cancellation / resource / determinism / …） |
| 质量报告 | `docs/quality/QUALITY_REPORT.md` | 生成物（Wave 20） |
| 本地 runner | `scripts/quality` | 分 lane 一键质量（有界并发） |

## 原则

1. **派生优先**：registry 与测试套件是唯一事实源；本平台一切产物可
   再生，配 drift test。
2. **线索不是判决**：静态发现（未引用、缺声明）是复核线索；行为正确性
   由各 lane 的真测试保证。
3. **生产零开关**：故障注入只存在于测试进程（fixture 层），生产代码
   不含 quality 分支。
4. **资源有界**：runner 并发有上限；语料 replay 不调用外部 LLM。
