# ADR 0104 — Quality & Reliability Platform V1

日期：2026-09-08
状态：Proposed（随 feat/quality-reliability-platform-v1 分支交付）
前置：ADR 0103（描述符覆盖率闸）、ADR 0101（工具模型运行时 V2 / workflow
recipe conformance）、ADR 0099（科学元数据）、ADR 0096（GeoCompute chaos）

## 背景

四条主线（Harness、Science、Cartography、Data/Compute）扩大后，系统风险
从"能力不够"转向集成性风险：registry 漂移、静默降级、科学回归、取消失
效、flaky、无界资源、trace 缺口、安全边界回退。Phase 0 审计
（`.agent-work/quality-v1/01…09`）确认：

- 仓库已是 **derivation-first**：registry 是唯一事实源，catalog/oracle/
  manifest 均为投影，每个投影配字节一致性 drift test；
- 已有 20 个质量闸，但发现能力碎片化：`test_descriptor_coverage_gate`
  引用的闸脚本从快照中丢失（master 全量收集损坏）、29 个工具零测试引用、
  4 套互不相通的内存 trace 模型、故障注入靠逐 test 手搓 monkeypatch。

## 决策

### D1 — QualityManifest 是第 21 个投影，不是第二事实源

`app/lib/quality/manifest.py` 从既有 registry（Tool/Algorithm/Capability/
ArtifactType/Recipe）+ 测试引用索引（`app/lib/quality/discovery.py`，只读
扫描 tests/ 与 frontend 测试源）派生 QualityManifest：

- 行内容：目录投影 + 每行的静态测试引用 + 派生 findings；
- findings 是**线索不是缺陷判定**（静态引用 ≠ 行为覆盖）；
- 生成：`scripts/gen_quality_manifest.py`（`--check`），产物
  `docs/quality/QUALITY_MANIFEST.md` + `quality-manifest.json`；
- 红线：`tests/quality/test_quality_manifest_gate.py` 字节一致性 + 编译
  确定性 + findings 词表。

被否决的替代：手写大 YAML 质量矩阵（必然漂移、成为第二事实源）；
给各子系统加"质量注解"装饰器（侵入 5 个冻结 registry，违反 seam 冻结）。

### D2 — ADR-0103 闸收敛为 manifest 编译器子集

丢失的 `scripts/check_tool_descriptor_coverage.py` 以薄壳重建，实现移入
manifest 编译器；历史 import 面（`GATE_THRESHOLDS` / `collect()` /
`gate()`）不变，`tests/unit/test_descriptor_coverage_gate.py` 原样恢复。
阈值语义改为**基线棘轮**：钉在当前实测覆盖率（83/83/95/95/60），只许
前进；上调需后续 ADR。选择棘轮而非绝对阈值的原因：gate 的职责是阻止
回退，不是制造一次性大重构。

### D3 — 质量平台其余部分同样遵循"派生 + 闸"形态

- 场景层：ScenarioSpec DSL 扩展既有 `GISBenchmarkCase`（不建第二 runner），
  语料由生成器产出、JSON 冻结、replay + drift test 保护；
- 漂移层：contract drift validators 输出报告工件，BLOCKER 级漂移即红；
- 混沌层：`tests/fixtures/chaos.py` test-only 故障注入（fault ID、显式
  确定性 schedule），生产路径零改动——"默认关闭"由结构保证而非开关；
- 观测层：收敛到既有 geocompute OTel-shaped emit + sink 协议，不建第二
  tracing backend，不绑定 SaaS；
- 认证层：cancellation / resource safety / determinism / scientific /
  cartographic / security / API compatibility 全部以**可再生的认证表 +
  回归测试**交付，认证表本身是生成物。

### D4 — 落地形态（本分支交付物清单）

| Wave | 交付物 | 形态 |
|---|---|---|
| W1 | `app/lib/quality/manifest.py` + `scripts/gen_quality_manifest.py` + 闸测试 | 派生投影 + 字节闸 |
| W2/3 | `GISBenchmarkCase` 扩展（scenario_kind / 工具类 / 预算 / trace 字段）+ `app/evaluation/quality_corpus.py` | additive DSL + 确定性语料 |
| W4 | `app/lib/quality/drift.py` + `CONTRACT_DRIFT_REPORT` | 6 检查器 + 零 BLOCKER 闸 |
| W5 | `app/lib/quality/trace_contract.py` + `TRACE_COMPLETENESS.md` | 任务类契约 + 诚实缺口 |
| W6 | `app/lib/observability/` + `proj=` 关联字段 | vendor-neutral 事件面 |
| W7/8 | `tests/fixtures/chaos.py` + 4 chaos 套件 + distributed_lock 修复 | test-only 结构性关断 |
| W9/10 | point_pattern 取消检查点 + `certification.py` 两张表 | 行为 + 派生认证 |
| W11/12 | 墙钟文件归入 perf 车道 + 结构基线 + `DETERMINISM.md` | 棘轮 + 双跑红线 |
| W13/14 | 科学/制图跨系统回归套件 | 输入对抗 + 语义认证 |
| W15 | `security_manifest.py` + 安全回归套件 + 路径穿越测试重写 | control→test 清单闸 |
| W16 | `api_compat.py` + OpenAPI 快照 | breaking/additive 分类 |
| W17/18 | 顺序依赖修复 + `gis_samples.py` 合成样本库 | flaky 收敛 + 夹具架构 |
| W19/20 | `scripts/quality` runner + `QUALITY_REPORT` | 本地车道 + 聚合报告 |

跨波集成由 `tests/quality/test_system_scenarios.py` 横向锁定（全链
plan→tools→MapSpec→verify→trace 认证）。

## 后果

- 正面：发现能力收敛为一份可再生清单；master 收集损坏即刻修复；后续
  每条主线的能力变化自动进入质量视野。
- 代价：manifest 编译在测试进程内约 1–4s（810 文件扫描，有界）；生成物
  入库带来例行再生成提交（与既有 catalog 同成本）。
- 已知限制：静态引用索引不能证明行为覆盖；frontend 测试引用只按标识符
  匹配；`tags` 等富化字段当前基线偏低（60–83%），由棘轮逐步抬升。
