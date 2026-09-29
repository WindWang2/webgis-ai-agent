"""离线 Harness 评测实验室（E15）。

统一场景契约（:mod:`spec`）+ deterministic fake/故障注入（:mod:`fakes`）+
settlement 终态检查（:mod:`settlement`）+ adapter 编排（:mod:`adapters`）+
统一指标/报告（:mod:`metrics` / :mod:`report` / :mod:`runner`）。

边界承诺（docs/dev/e15-offline-harness-evaluation-lab-design.md）：

- **编排而不是第二 runner**：所有判错委托既有 evaluator
  （replay oracle / GISBenchmarkRunner / evaluate_cartography_semantics /
  science oracles / 视觉 fixture），本包只做编译、编排、聚合；
- **故障注入零生产钩子**：故障只发生在 lab/fake 边界与场景纯变换层；
- **诚实缺席**：每维裁决 ∈ {pass, fail, not_evaluated}，声明了却没评出
  不算绿，任何 fail 即 spec fail（平均分不掩盖 correctness failure）；
- app 不静态 import tests（science oracles 经 adapter 惰性注册）。
"""
from app.lib.harness.lab.spec import (
    LAB_SCHEMA_VERSION,
    DataBinding,
    Expectation,
    FaultStep,
    LabScenario,
    ProviderOp,
    SpecError,
    TurnScript,
)
from app.lib.harness.lab.journeys import default_spec_paths, load_specs

__all__ = [
    "LAB_SCHEMA_VERSION",
    "DataBinding",
    "Expectation",
    "FaultStep",
    "LabScenario",
    "ProviderOp",
    "SpecError",
    "TurnScript",
    "default_spec_paths",
    "load_specs",
]
