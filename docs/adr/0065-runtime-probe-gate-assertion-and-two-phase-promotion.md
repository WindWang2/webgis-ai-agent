# Runtime Probe 是门断言而非评分维度；场景两阶段晋升

Date: 2026-08-20

## Status

Accepted

## Context

#673 落地的 Runtime Probe 基础设施引入了三类探针（`layer-exists` / `feature-count` / `pixel-color`）与目录扫描的 Runtime Scenario（`tests/fixtures/runtime/<name>/` 的 `mapspec.json` + `probes.json`）。#672 的 grilling 留下两个耦合问题未决：

1. **探针的裁决位**：探针失败应该「算分」还是「直接红」？`eval_scores` 的 5 维 80 分制（+ 20 分 heuristic visual proxies，ADR-0060/0061）是**记录型**评分，已明确不是生产门。把探针稀释为其中一维会让「数据没渲出来」可以靠其他维度的高分抵消。
2. **场景进 PR 阻塞的条件**：所有场景若立即可进 PR lane，会把尚未稳定的渲染断言推为合并门槛；反之若永远只在 nightly，则门无威慑力。需要一条**机械的**晋升规则，避免人为「感觉够稳就进」的争议。

CONTEXT.md 的 Runtime Scenario / Runtime Probe 词条已写明探针是硬门、场景默认 nightly、晋升需连续绿与时限，本 ADR 将其固化为决策并补齐被否决的备选。

## Decision

### 1. Runtime Probe 是 GATE 断言（hard fail），不是 eval_score 维度

- 任意探针 `pass == false` ⇒ 该次 Runtime 校验 `valid == false`，Node 侧 `runtime-validate.ts` 以 exit 1 结束，Python 侧 `valid` 为 false。`eval_scores` 公式**保持不变**（仍为记录型，80 分封顶 + 20 分 heuristic，见 `app/services/runtime_validator.py:compute_eval_scores`），探针不作为第 6 维加入，也不在 100 分内加权。
- `valid` 的判定为：`mapLoaded && mapIdle && fatalError == null && pageErrors/consoleErrors 为空 && !canvas.blank && !probe_failed`。探针与 infra 信号并列为否决项，缺一即红。
- `expect: fail` 的负例场景同样受此规则：探针红则 `valid == false`，但 infra 必须保持干净（见下文晋升与测试约束），否则失败归因不清。

### 2. 场景晋升的两阶段机械规则

- **默认**：所有 `tests/fixtures/runtime/<name>/` 场景仅在 nightly lane 执行（`pytest -m heavy`，CI 的 `runtime-validator` nightly job），不阻塞 PR。
- **晋升条件**：单个场景连续 **10 次 nightly 绿** 且**单次运行时长 < 30s**，方可获得 `runtime_pr` 标记。
- **晋升后的变更才生效**：PR lane 的用例选择器与 release-gate DAG 的变更（将该场景纳入 PR 必须绿的集合）**仅在晋升发生后**才修改；晋升前即使场景已存在，PR lane 也不拉取它。
- 时长与连续绿均以 CI nightly 的实测为准，本地单跑仅作预检，不计入晋升计数。

## Alternatives considered

### Probe-as-score-dimension（已否决）

将探针失败折算为 `eval_scores` 的扣分项（如每失败一探针扣 10 分），总分低于阈值才红。**否决原因**：会让「图层根本没渲」被其他维度（spatial_data / browser_runtime 等）的高分掩盖；与 ADR-0060「MapSpecValidity 封顶 SEMANTIC_VALID」、ADR-0061「live Observed Map 才是生产门、headless 仅记录」的分层一致性冲突——探针要补的是「渲没渲对」的硬证据，不是可平均的体验分。

### Immediate PR-blocking（已否决）

新场景一经合入即进 PR 阻塞集合。**否决原因**：探针与 MapLibre 版本、底图可达性、CI 机器性能强相关，早期不稳定即阻塞会频繁误拦合并；与 #672 提出的「先 nightly 观测、稳定度达标再晋升」 grill 结论相悖。两阶段规则把「是否进门」的判断机械化，避免主观拍板。

## Consequences

- **门更硬**：渲染语义错误无法靠评分抵消；`expect: fail` 的负例（`fault-missing-source`、`fault-wrong-color`）必须以 infra 干净 + 探针红的方式通过，否则视为无效负例（见 `tests/unit/test_runtime_validator.py` 的 `expect == "fail"` 分支断言）。
- **晋升可审计**：10 次连续绿 + <30s 的阈值与 `runtime_pr` 标记使 PR 门的扩张有据可查；未晋升场景的回归不影响合并，但 nightly 仍会告警。
- **eval_scores 保持可比**：历史分数不受探针引入影响，仍按 ADR-0060/0061 的 80 分制对比；探针结果在 `report.json:probeResults` 与 `valid` 中单独可查。
- **作者成本**：新增 `pixel-color` 场景必须遵守远色规则（见 `tests/unit/test_runtime_fixture_contract.py::test_pixel_color_pairwise_distinguishable`，总通道距离 > 48），否则契约测试即红；`feature-count` 场景需确保数据源在无网络依赖下稳定产出 0/非 0 的可区分计数。
- **与 CONTEXT 一致**：本 ADR 与 CONTEXT.md 的 Runtime Scenario（目录扫描、默认 nightly、晋升条件）与 Runtime Probe（三种类型、5×5 采样 ±16 容差、hard-fail）词条一致；后续新增场景与探针类型须同步更新 CONTEXT 与本 ADR 的引用。

## Relationship to prior ADRs

- **ADR-0060**（MapSpecValidity 封顶 SEMANTIC_VALID）：探针不在 validity 阶梯上，validity 仍止于语义校验；探针是独立的 headless 门。
- **ADR-0061**（live Observed Map 为生产门、headless 仅记录）：`valid` 的 hard-fail 影响 nightly/release-gate 的记录与门控，不改变 live CartographicQuality 的生产门地位。
- **ADR-0062 / 0063 / 0064**：Cartography Verdict 与 L5 语义不变；探针补充的是 headless 渲染证据，不混入 cartographic quality 分级。
