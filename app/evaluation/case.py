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


class ConversationTurn(BaseModel):
    """V2 多轮对话的一轮（opt-in；turns 非空时 runner 逐轮执行）。

    ``query`` 中的 ``{scope}`` / ``{subject}`` 占位符由 runner 用前序轮
    绑定表替换（指代消解的确定性声明：绑定规则在案，评测的是前序轮
    真实解析出的前件，而非硬编码字符串）。
    """

    query: str
    # 轮级期望（与 case 级字段同语义，按轮判定；空 = 不检查）
    expected_tasks: List[str] = Field(default_factory=list)
    expected_task: Optional[str] = None
    expected_recipes: List[str] = Field(default_factory=list)
    expected_recipe: Optional[str] = None
    expected_warning_codes: List[str] = Field(default_factory=list)
    forbidden_warning_codes: List[str] = Field(default_factory=list)
    # 指代/范围契约：期望本轮解析出与某前序轮一致（或新）的 scope。
    # None = 不检查；"carry" = 必须继承前序轮 scope；"new" = 必须换绑。
    expected_scope_binding: Optional[Literal["carry", "new"]] = None
    note: str = ""


class ExpectedEvidence(BaseModel):
    """V2 证据契约声明（评测 EvidenceGrounding 的案例面）。

    ``scenario`` 是确定性证据情境词表（闭合）：runner 的 evidence tier
    按情境构建 ClaimStore 场景，断言生产 verify_claim 裁决：

    - ``supported``：完整正证明四件套 → 期望 SUPPORTED + positive_proof
    - ``unsupported``：无数值证据的 narrative → 期望 UNSUPPORTED
    - ``missing_evidence``：supporting refs 全缺失 → 期望 UNSUPPORTED
    - ``cross_tenant``：跨租户证据 → 期望 UNSUPPORTED（fail-closed）
    - ``stale``：正证明后证据过期 → 期望 STALE
    - ``stale_propagation``：数据集版本更新 → 后代 claim 期望 STALE
    - ``contradicted``：同轴同方法双 highest → 期望 CONTRADICTED
    """

    claim_type: str = "count"  # ClaimType 词表（evidence_claim.contracts）
    subject: str = ""
    scenario: Literal[
        "supported", "unsupported", "missing_evidence", "cross_tenant",
        "stale", "stale_propagation", "contradicted",
    ] = "supported"
    require_positive_proof: bool = False


class ScopeExpectation(BaseModel):
    """V2 scope 绑定契约（wrong-AOI 硬负例的诚实面）。

    known=True：intent.scope 必须解析出非空 name；known=False：解析器
    不得虚构 scope（空 name + unknown level）—— 错误 AOI 不得静默绑定。
    """

    known: Optional[bool] = None
    name: Optional[str] = None
    level: Optional[str] = None


class PolicyExpectation(BaseModel):
    """V2 SkillPolicy 契约（opt-in policy tier 的案例面）。

    ``facts`` 字段直接映射 ``SelectionFacts`` 构造参数（诚实投影：
    缺席 = unknown）；``expected`` 是 SkillPolicyDecision 子集断言。
    ``disable_policy=True`` 时 runner 在受控窗口内设 kill-switch 再
    resolve（用后恢复），断言 mode == "none"。
    """

    facts: Dict[str, Any] = Field(default_factory=dict)
    expected_mode: Optional[str] = None
    expected_trust_tier: Optional[str] = None
    expected_selected_skill: Optional[str] = None
    expected_shadow_candidate: Optional[str] = None
    forbidden_modes: List[str] = Field(default_factory=list)
    # 隔离集（blocked 契约；SkillPolicy(quarantine_ids=...) 注入面）
    quarantine_ids: List[str] = Field(default_factory=list)
    disable_policy: bool = False
    prefer_execute: bool = True
    allow_shadow: bool = True
    # True 时 tier 注入确定性 induced 演示技能作为 shadow resolver
    # （fixtures.demo_induced_skill；影子旁路契约的声明面）。
    shadow_induced: bool = False
    # 决策确定性：双跑 resolve 对齐（bounded dict 相等）
    check_determinism: bool = False


class SecurityExpectation(BaseModel):
    """V2 注入 contained 契约（security tier）。

    ``benign_twin`` 是同一 GIS 诉求去掉注入壳的良性查询；升级检测 =
    payload 解析出的算法集不得超出 twin 的算法集（注入不得增加能力面），
    叠加 network 工具复查与任务解析逃逸检查。

    ``expected_contained`` 是该 payload 对当前产线 planner 的**手工审定**
    真值（审定注释随语料行）：True = 注入必须被容纳（runner 全绿）；
    False = 已知劫持（runner 必须以 injection-escalation / containment
    签名**检出**，不得静默通过）。两种真值都锁定检测器行为：
    回归 = 检出签名消失或 contained 行转红。
    """

    benign_twin: str
    expected_contained: bool = True
    forbid_network_tools: bool = True


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
                 "anti-claim",
                 # Wave 2+3（质量场景语料）：按目标文档类别分片（底图 / 科学 /
                 # 制图 / 数据 / Agent）；additive，与 conformance-* 同规。
                 "quality-basemap", "quality-science", "quality-cartography",
                 "quality-data", "quality-agent",
                 # Benchmark Factory V2（hard-negative / 技能策略 / 安全 /
                 # 证据 / 任务 / 制图轴）：同 additive 扩展规约，旧组名不变。
                 "hard-negative", "benchmark-policy", "benchmark-security",
                 "benchmark-evidence", "benchmark-mission", "cartography-axes"]
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

    # ── V3 semantic planning contract（Goal §十二，全部 opt-in）────────
    # intent 的本体 top-1 必须命中该任务 id（GIS task ontology 匹配锁）。
    expected_ontology_task: Optional[str] = None
    # 规划确定性：置 True 时 runner 对同一 query 双跑 plan tier，差异即败。
    check_determinism: bool = False
    # 数据资格 / 回退契约：提供画像时 runner 走 compile_workflow 复评
    # （V3 qualify_data + fallback_v3），断言 per-role 状态与回退层。
    qualification_profile: Optional[Dict[str, Any]] = None
    expected_qualification: Dict[str, str] = Field(default_factory=dict)
    expected_fallback_tier: Optional[str] = None

    # ── 质量场景语料契约（Wave 2+3，全部 opt-in，缺省 = 既有行为）────────
    # 语料族标签（goal-doc 场景类别，如 "data_bad_crs" / "agent_cancel" /
    # "science_hotspot"）；测试按前缀分片。None = 非场景语料案例。
    scenario_kind: Optional[str] = None
    # 工具类别契约（ToolDescriptor.output_semantic_type 词表）：plan tier
    # 断言 resolved 工具的输出语义类别**覆盖**期望集（extra 不罚）。
    expected_tool_classes: List[str] = Field(default_factory=list)
    # 导出格式契约：plan.exports 的子集断言（如 ["png", "csv"]）。
    expected_export_formats: List[str] = Field(default_factory=list)
    # 上下文 schema 预算（字节）：resolved 工具的 registry.schema_size 之和
    # 不得超过该预算（大上下文回归锚）。None = 不设预算。
    max_context_schema_bytes: Optional[int] = None
    # 离线契约：True 时 resolved 工具不得声明 network=True（全离线族锁定）。
    forbid_network_tools: bool = False
    # trace 完整性需求标签：原样记入 plan evidence["trace_requirements"]。
    # W5 的 trace_contract 按 task class 认证，不按本字段断言 —— 本字段是
    # 场景级"意图声明"，供后续工作（按场景的 trace 认证）消费。
    trace_requirements: List[str] = Field(default_factory=list)

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

    # ── Benchmark Factory V2（全部 opt-in，零声明零行为）────────────────
    # 多轮对话：非空时 runner 逐轮执行（共享绑定表；指代占位符替换）。
    # 非空时顶层 query 作为轮 1（其余轮依序追加）。
    turns: List[ConversationTurn] = Field(default_factory=list)
    # scope 绑定契约（wrong-AOI 硬负例；plan tier 断言）。
    expected_scope: Optional[ScopeExpectation] = None
    # 可接受工具名集合（exact-match 备选集；非空时 resolved 工具必须
    # 全部落入该集合 —— 比 allowed_algorithms 前缀集更细的第一类备选面）。
    allowed_tools: Optional[List[str]] = None
    # 自由标签（域/能力/语言多标签；报告按 group 聚合，tags 仅检索用）。
    tags: List[str] = Field(default_factory=list)
    # 证据契约（evidence tier；None = 未声明）。
    expected_evidence: List[ExpectedEvidence] = Field(default_factory=list)
    # SkillPolicy 契约（policy tier；None = 未声明）。
    policy_expectation: Optional[PolicyExpectation] = None
    # 注入 contained 契约（security tier；None = 未声明）。
    security_expectation: Optional[SecurityExpectation] = None

    def model_summary(self) -> str:
        return f"{self.id} [{self.group}] {self.name}"
