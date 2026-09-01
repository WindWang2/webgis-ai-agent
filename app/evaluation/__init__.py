"""GIS Agent Evaluation Harness (ADR-0092 Phase B).

A semantic regression benchmark for the GIS agent — NOT a pytest replacement
and NOT a performance benchmark (tests/benchmarks owns the latter). Cases are
contract-first and deterministic-first (B4): assertions live on schema
(shape of plan / artifacts / MapSpec / facets), numeric goldens, and tool
traces. No LLM judge anywhere in the verdict path.

Two tiers per case:
- **plan tier** (always run, offline): query → deterministic planner →
  assert task classification, resolved capabilities, algorithm constraints,
  recipe identity, facet contract.
- **execute tier** (when the case ships a script + fixtures): real
  ToolRegistry dispatch of a scripted, bounded tool sequence on fixture
  data, then artifact / component / numeric assertions.
"""
from app.evaluation.case import (
    GISBenchmarkCase,
    NumericAssertion,
    ScriptStep,
)
from app.evaluation.golden_cases import GOLDEN_CASES, get_all_cases
from app.evaluation.runner import CaseResult, GISBenchmarkRunner
from app.evaluation.report import render_markdown

__all__ = [
    "GISBenchmarkCase",
    "NumericAssertion",
    "ScriptStep",
    "GOLDEN_CASES",
    "get_all_cases",
    "GISBenchmarkRunner",
    "CaseResult",
    "render_markdown",
]
