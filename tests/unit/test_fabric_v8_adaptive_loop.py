"""V8 自适应闭环（ADR-0130 Phase C+D）：治理面数据 → 规划输入。

覆盖：
- ``enrich_request_from_runtime``：source_type 激活 / 事实行数 + 反馈修正
  注入 / 显式提示不覆盖但偏差披露 / NDV 注入 / estimate_basis 披露段；
- 无富集（resolved 缺失）→ 请求逐位不变（V7 行为基线）；
- ``build_enumeration_context`` 消费 stats_hints.caps（探测覆盖优先于静态
  矩阵）；
- EXPLAIN 渲染 estimate_basis（无富集不渲染 —— 输出形状不变）。
"""
import pytest

from app.services.data_fabric.fabric.feedback import (
    ExecutionFeedback,
    SourceObservation,
    get_feedback_store,
    reset_feedback_store,
)
from app.services.data_fabric.fabric.runtime import (
    ResolvedSource,
    reset_fabric_runtime,
)
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    ChainSourceStats,
    FederatedChainRequest,
    enrich_request_from_runtime,
)
from app.services.data_fabric.query.federated.planner import (
    build_enumeration_context,
)


@pytest.fixture(autouse=True)
def _isolated():
    reset_feedback_store()
    reset_fabric_runtime()
    yield
    reset_feedback_store()
    reset_fabric_runtime()


def _req(**kw) -> FederatedChainRequest:
    return FederatedChainRequest(
        sources=[
            ChainSource(source_id="s0", dataset_id="d0"),
            ChainSource(source_id="s1", dataset_id="d1", estimated_rows=50),
        ],
        joins=[ChainJoin(kind="attribute_join", join_field_left="k",
                         join_field_right="k", left_source_id="s0",
                         right_source_id="s1")],
        **kw,
    )


def _resolved(sid="s0", **kw) -> ResolvedSource:
    return ResolvedSource(
        adapter=object(),
        profile_id=f"p_{sid}",
        source_type="postgis",
        scope_key="org:_|owner:_|proj:_",
        revision="rev0123456789ab",
        **kw,
    )


# ── 富集语义 ──────────────────────────────────────────────────────────


def test_enrich_fills_source_type_and_estimate_from_facts():
    req = _req()
    rs = _resolved(facts_row_count=4200, facts_row_count_basis="observed")
    enrich_request_from_runtime(req, {"s0": rs, "s1": _resolved("s1")})

    assert req.sources[0].source_type == "postgis"
    assert req.sources[0].estimated_rows == 4200
    assert req.estimate_basis["s0"]["rows_basis"] == "source_facts:observed"
    # 显式提示不被覆盖。
    assert req.sources[1].estimated_rows == 50
    assert req.estimate_basis["s1"]["rows_basis"] == "request_hint"


def test_enrich_applies_feedback_factor_to_facts_estimate():
    req = _req()
    rs = _resolved(
        facts_row_count=1000, facts_row_count_basis="observed",
        feedback_factor=0.5, feedback_samples=4,
        feedback_basis="feedback_decayed",
    )
    enrich_request_from_runtime(req, {"s0": rs})
    assert req.sources[0].estimated_rows == 500
    assert "×feedback:0.5(samples=4)" in req.estimate_basis["s0"]["rows_basis"]


def test_enrich_discloses_hint_drift_without_override():
    req = _req()
    rs = _resolved(feedback_factor=3.0, feedback_samples=5)
    enrich_request_from_runtime(req, {"s1": rs})
    assert req.sources[1].estimated_rows == 50  # 不覆盖
    drift = req.estimate_basis["s1"]["hint_feedback_drift"]
    assert drift["factor"] == 3.0 and drift["samples"] == 5


def test_enrich_injects_measured_ndv_without_overriding():
    req = _req()
    req.stats_hints = {"s1": ChainSourceStats(column_ndv={"k": 7})}
    rs = _resolved(facts_ndv={"k": 99, "name": 12})
    enrich_request_from_runtime(req, {"s0": rs, "s1": rs})

    assert req.stats_hints["s0"].column_ndv == {"k": 99, "name": 12}
    assert req.stats_hints["s1"].column_ndv == {"k": 7}  # 显式提示保留


def test_enrich_probed_caps_injected_into_hints():
    req = _req()
    overrides = {"aggregate_pushdown": True}
    default = get_capabilities("postgis").model_dump()
    non_default = {k: v for k, v in overrides.items() if default.get(k) != v}
    rs = _resolved(
        caps_basis="probed", caps_overrides=non_default or overrides,
    )
    enrich_request_from_runtime(req, {"s0": rs})
    hint = req.stats_hints.get("s0")
    assert hint is not None and hint.caps is not None


def test_enrich_default_caps_basis_not_injected():
    req = _req()
    rs = _resolved(caps_basis="default", caps_overrides={"aggregate_pushdown": True})
    enrich_request_from_runtime(req, {"s0": rs})
    # default basis = 未验证能力 → 不注入（绝不把默认矩阵当探测结果）。
    hints = req.stats_hints or {}
    hint = hints.get("s0")
    assert hint is None or hint.caps is None


def test_no_enrichment_keeps_request_bit_identical():
    req = _req(order_strategy="cost")
    enrich_request_from_runtime(req, {})
    assert req.stats_hints is None
    assert req.estimate_basis is None
    assert req.sources[0].source_type is None
    assert req.sources[0].estimated_rows is None


# ── planner 消费 ──────────────────────────────────────────────────────


def test_planner_prefers_probed_caps_over_static_matrix():
    req = _req()
    req.sources[0].source_type = "postgis"
    static_caps = get_capabilities("postgis")
    req.stats_hints = {
        "s0": ChainSourceStats(
            caps=static_caps.model_copy(update={"aggregate_pushdown": True})
        )
    }
    ctx = build_enumeration_context(req)
    s0 = next(s for s in ctx.sources if s.source_id == "s0")
    assert s0.caps is not None and s0.caps.aggregate_pushdown is True


def test_planner_estimate_basis_passthrough():
    req = _req()
    req.estimate_basis = {"s0": {"rows_basis": "source_facts:observed"}}
    ctx = build_enumeration_context(req)
    assert ctx.estimate_basis == {"s0": {"rows_basis": "source_facts:observed"}}


# ── EXPLAIN 披露 ──────────────────────────────────────────────────────


def test_explain_renders_estimate_basis_when_present():
    from app.services.data_fabric.query.federated.explain import explain_v6_lines
    from app.services.data_fabric.query.federated.enumerator import (
        EnumerationContext,
        JoinEdge,
        SourceFacts,
    )

    ctx = EnumerationContext(
        sources=[
            SourceFacts(source_id="s0", dataset_id="d0"),
            SourceFacts(source_id="s1", dataset_id="d1"),
        ],
        joins=[JoinEdge(left_source_id="s0", right_source_id="s1",
                        kind="attribute_join")],
        estimate_basis={
            "s0": {"rows_basis": "source_facts:observed×feedback:0.5(samples=4)",
                    "caps_basis": "probed"},
        },
    )
    from app.services.data_fabric.query.federated.enumerator import (
        enumerate_federation,
    )

    plan = enumerate_federation(ctx)
    lines = explain_v6_lines(plan, ctx)
    text = "\n".join(lines)
    assert "estimate_basis:" in text
    assert "source_facts:observed" in text and "caps=probed" in text


def test_explain_without_basis_renders_no_extra_section():
    from app.services.data_fabric.query.federated.enumerator import (
        EnumerationContext,
        JoinEdge,
        SourceFacts,
        enumerate_federation,
    )
    from app.services.data_fabric.query.federated.explain import explain_v6_lines

    ctx = EnumerationContext(
        sources=[
            SourceFacts(source_id="s0", dataset_id="d0"),
            SourceFacts(source_id="s1", dataset_id="d1"),
        ],
        joins=[JoinEdge(left_source_id="s0", right_source_id="s1",
                        kind="attribute_join")],
    )
    lines = explain_v6_lines(enumerate_federation(ctx), ctx)
    assert all("estimate_basis" not in ln for ln in lines)


# ── 反馈存储端到端（进程内）───────────────────────────────────────────


def test_feedback_store_corrections_flow_into_enrichment():
    store = get_feedback_store()
    for _ in range(4):
        store.record(
            ExecutionFeedback(
                plan_hash="h", scope_key="org:_|owner:o|proj:_",
                outcome="ok",
                per_source=[SourceObservation(
                    source_id="p_s0", dataset_fingerprint="fp1",
                    estimated_rows=100, actual_rows=50, unfiltered=False,
                )],
            )
        )
    corr = store.correction("org:_|owner:o|proj:_", "fp1")
    assert corr["basis"] == "feedback_decayed"
    assert abs(corr["factor"] - 0.5) < 0.01

    req = _req()
    rs = _resolved(facts_row_count=1000, facts_row_count_basis="observed",
                   feedback_factor=corr["factor"], feedback_samples=corr["samples"],
                   feedback_basis=corr["basis"])
    enrich_request_from_runtime(req, {"s0": rs})
    assert req.sources[0].estimated_rows == 500
