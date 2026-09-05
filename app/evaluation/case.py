"""Benchmark case contract (ADR-0092 B1).

A case is data: the runner interprets it. Nothing here imports planner or
registry internals — the runner owns execution so cases stay declarative and
reviewable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ScriptStep(BaseModel):
    """One deterministic tool dispatch in the execute tier."""

    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    # Values shaped "fixture:<alias>" are resolved to the fixture's session
    # ref before dispatch; other values pass through untouched.
    # When set, the step is EXPECTED to fail and the error message must
    # contain this substring (failure-semantics contract, e.g. INSUFFICIENT_POINTS).
    expect_error_contains: Optional[str] = None
    note: str = ""


class NumericAssertion(BaseModel):
    """Deterministic numeric/aggregate assertion against a named source."""

    source: Literal["step_result", "step_result_bytes", "mapspec", "fixture", "quantity"]
    # for source == "step_result": index into the script's executed steps
    # (0-based; None = last executed step)
    step: Optional[int] = None
    # dot path into the source document, e.g. "chart.data" or "features"
    path: str = ""
    # reduction applied to the resolved value
    agg: Literal["value", "len", "sum", "first", "mean"] = "value"
    op: Literal["==", ">", ">=", "<", "<=", "approx"] = "=="
    value: float
    # for approx: absolute tolerance
    tol: float = 1e-6
    # for source == "quantity": named quantity computed by the runner
    # (e.g. "ndvi_mean" from the lib-level deterministic golden provider)
    quantity: Optional[str] = None
    label: str = ""


class GISBenchmarkCase(BaseModel):
    """One GIS agent semantic-regression scenario (B1 contract)."""

    id: str
    name: str
    group: Literal["poi", "raster", "network", "od", "repair", "semantics", "interpolation",
                 "decision", "negative", "form", "scope", "compound",
                 # Workflow V2（Goal C / C9）：一致性语料分片（按领域分组，
                 # 测试可按片运行）；additive，旧组名不变。
                 "conformance-distribution", "conformance-density",
                 "conformance-statistics", "conformance-point-pattern",
                 "conformance-interpolation", "conformance-terrain",
                 "conformance-hydrology", "conformance-remote-sensing",
                 "conformance-sar", "conformance-temporal",
                 "conformance-change", "conformance-network",
                 "conformance-accessibility", "conformance-equity",
                 "conformance-decision", "conformance-contract",
                 "anti-claim"]
    query: str
    description: str = ""

    # ── plan-tier contract ────────────────────────────────────────────
    expected_task: Optional[str] = None
    # Workflow V2（Goal C / C9）：可接受任务集（语料族中口语展示句式与直接
    # 句式可能诚实落到相邻任务族，如 simple_view； recipe 锁不变）。非空时
    # 按 set 成员判定，覆盖 expected_task 的单值精确判定。
    expected_tasks: List[str] = Field(default_factory=list)
    expected_capabilities: List[str] = Field(default_factory=list)
    optional_capabilities: List[str] = Field(default_factory=list)
    # None = unconstrained; entries are algorithm-id prefixes (e.g. "poi.query")
    allowed_algorithms: Optional[List[str]] = None
    forbidden_algorithms: List[str] = Field(default_factory=list)
    expected_recipe: Optional[str] = None
    # Workflow V2（Goal C / C9）：可接受 recipe 集（口语展示句式诚实落到
    # 相邻产品族时的备选；非空时按 set 成员判定）。
    expected_recipes: List[str] = Field(default_factory=list)
    expected_product_facets: List[str] = Field(
        default_factory=list, description="facet kinds the product contract must require"
    )
    max_tool_calls: Optional[int] = None
    # Semantic GIS（方法论诚实）：期望 plan.methodology_warnings 命中的
    # pattern id（如 equity-无分母 ⇒ ["spatial_equity"]）；空 = 不检查。
    expected_methodology_warnings: List[str] = Field(default_factory=list)
    # 反向契约：这些 pattern 的方法论警告**不得**出现（如纯统计查询不得
    # 带 equity 噪声 —— keyword-gate 的回归锚）。
    forbidden_methodology_warnings: List[str] = Field(default_factory=list)
    # Workflow V2（Goal C / C10）：稳定机器可读警告码断言（workflow 契约/
    # pattern projection 的 warning_codes 列表；比 pattern id 更细粒度，
    # anti-claim 测试族的锁点）。空 = 不检查。
    expected_warning_codes: List[str] = Field(default_factory=list)
    forbidden_warning_codes: List[str] = Field(default_factory=list)

    # ── execute tier ──────────────────────────────────────────────────
    plan_only: bool = False
    fixture_aliases: List[str] = Field(
        default_factory=list, description="named fixture builders to materialize"
    )
    script: List[ScriptStep] = Field(default_factory=list)

    # ── execute-tier assertions ───────────────────────────────────────
    expected_artifact_types: List[str] = Field(
        default_factory=list,
        description="artifact types that must appear in the session artifact registry",
    )
    component_assertions: List[str] = Field(
        default_factory=list,
        description="MapSpec component types required after the script",
    )
    numeric_assertions: List[NumericAssertion] = Field(default_factory=list)
    # e.g. "user-wins": hidden-by-user layers must stay hidden after finalize
    expected_interaction_semantics: List[str] = Field(default_factory=list)

    def model_summary(self) -> str:
        return f"{self.id} [{self.group}] {self.name}"
