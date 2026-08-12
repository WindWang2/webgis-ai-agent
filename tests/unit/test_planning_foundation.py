"""Foundation tests for the canonical planning package (app/services/planning).

Covers (design-v3 §"New package app/services/planning/", this slice):
- state machine: recompute_status matrix, terminal statuses, next_pending_steps
- PlanStore: save / load_current / supersede / clear over the in-memory
  session store (conftest sets USE_REDIS=false) + cache-miss restore
- capability validation: unknown tool, unknown family, domain-mismatch warning
- static ref validation: unknown id, forward ref, self-dep, cycle
- resolve_arg_refs: happy path + MissingRefError on bad paths
- classify_error for each dispatch code + message signals
- recovery policy sanity (no blind retry)
- classify_followup for each kind incl. the Chinese examples
"""
import pytest

from app.services.jobs.cancellation import OperationCancelled
from app.services.planning import (
    CONTINUATION_KEYWORDS,
    REF_REUSE_KEYWORDS,
    STYLE_KEYWORDS,
    TERMINAL_STATUSES,
    CanonicalPlan,
    CanonicalStep,
    FailureClass,
    PlanStatus,
    RecoveryAction,
    StepStatus,
    classify_error,
    classify_followup,
    recovery_action_for,
    validate_plan_capabilities,
)
from app.services.planning import FollowUpKind
from app.services.planning.capability import capability_of
from app.services.planning.deps import MissingRefError, resolve_arg_refs, validate_static_refs
from app.services.planning.recovery import RECOVERY_POLICY
from app.services.planning.store import PlanStore
from app.tools.registry import ToolRegistry


# ─── helpers ──────────────────────────────────────────────────────────


def _plan(steps):
    """CanonicalPlan with steps given as (id, StepStatus) tuples."""
    return CanonicalPlan(
        plan_id="p1",
        session_id="sess-f",
        intent="test",
        steps=[
            CanonicalStep(id=sid, n=i + 1, goal="", status=status)
            for i, (sid, status) in enumerate(steps)
        ],
    )


# ─── models: status machine ───────────────────────────────────────────


def test_plan_status_terminal_and_serialization():
    for status in (PlanStatus.completed, PlanStatus.failed, PlanStatus.cancelled, PlanStatus.superseded):
        assert status.is_terminal()
    for status in (PlanStatus.proposed, PlanStatus.validated, PlanStatus.running, PlanStatus.partially_completed):
        assert not status.is_terminal()
    assert TERMINAL_STATUSES == frozenset(
        {PlanStatus.completed, PlanStatus.failed, PlanStatus.cancelled, PlanStatus.superseded}
    )
    # str-enum round-trips through model_dump / model_validate
    p = _plan([("s1", StepStatus.completed)])
    dumped = p.model_dump()
    assert isinstance(dumped, dict)
    assert dumped["steps"][0]["status"] == "completed"
    restored = CanonicalPlan.model_validate(dumped)
    assert restored == p


def test_recompute_status_matrix():
    cases = [
        ([("s1", StepStatus.completed), ("s2", StepStatus.completed)], PlanStatus.completed),
        # failed with none pending/running → failed (beats partially_completed)
        ([("s1", StepStatus.completed), ("s2", StepStatus.failed)], PlanStatus.failed),
        ([("s1", StepStatus.failed), ("s2", StepStatus.completed)], PlanStatus.failed),
        # completed mixed with failed/skipped, none pending/running → partially_completed
        ([("s1", StepStatus.completed), ("s2", StepStatus.skipped)], PlanStatus.partially_completed),
        ([("s1", StepStatus.completed), ("s2", StepStatus.failed), ("s3", StepStatus.skipped)], PlanStatus.failed),
        # any running → running
        ([("s1", StepStatus.running), ("s2", StepStatus.completed)], PlanStatus.running),
        ([("s1", StepStatus.completed), ("s2", StepStatus.running), ("s3", StepStatus.pending)], PlanStatus.running),
        # nothing done / pending present → keep current status
        ([("s1", StepStatus.pending)], PlanStatus.proposed),
        ([("s1", StepStatus.failed), ("s2", StepStatus.pending)], PlanStatus.proposed),
        ([("s1", StepStatus.cancelled)], PlanStatus.proposed),
    ]
    for steps, expected in cases:
        assert _plan(steps).recompute_status() == expected
    # empty steps → keep current status
    p = CanonicalPlan(plan_id="p1", session_id="s", intent="", status=PlanStatus.validated)
    assert p.recompute_status() == PlanStatus.validated


def test_next_pending_steps_respects_dependencies():
    p = CanonicalPlan(
        plan_id="p1",
        session_id="s",
        intent="",
        steps=[
            CanonicalStep(id="s1", n=1, goal="", status=StepStatus.completed),
            CanonicalStep(id="s2", n=2, goal="", depends_on=["s1"]),
            CanonicalStep(id="s3", n=3, goal="", depends_on=["s2"]),
            CanonicalStep(id="s4", n=4, goal=""),
        ],
    )
    assert [s.id for s in p.next_pending_steps()] == ["s2", "s4"]
    p.steps[1].status = StepStatus.completed
    assert [s.id for s in p.next_pending_steps()] == ["s3", "s4"]
    # blocked on a failed dependency → never ready
    p.steps[2].depends_on = ["s2"]
    p.steps[1].status = StepStatus.failed
    assert [s.id for s in p.next_pending_steps()] == ["s4"]


def test_step_by_id_and_bump_revision():
    p = _plan([("s1", StepStatus.pending)])
    assert p.step_by_id("s1") is p.steps[0]
    assert p.step_by_id("nope") is None
    assert p.bump_revision() == 2
    assert p.revision == 2


# ─── store: save / load / supersede / clear ───────────────────────────


@pytest.mark.asyncio
async def test_store_save_load_current_roundtrip():
    store = PlanStore()  # default session_data_manager (in-memory under conftest)
    sid = "sess-foundation-1"
    await store.save(CanonicalPlan(plan_id="p-001", session_id=sid, intent="hotspot"))
    loaded = await store.load_current(sid)
    assert loaded is not None
    assert loaded.plan_id == "p-001"
    assert loaded.intent == "hotspot"
    assert loaded.status == PlanStatus.proposed


@pytest.mark.asyncio
async def test_store_save_overwrites_in_place_no_new_ref():
    """Repeated saves must NOT mint a new ref per save (plan_mode trap)."""
    from app.services.session_data import session_data_manager

    store = PlanStore()
    sid = "sess-foundation-2"
    await store.save(CanonicalPlan(plan_id="p-002", session_id=sid, intent="a"))
    refs_before = await session_data_manager.list_refs(sid)
    await store.save(
        CanonicalPlan(plan_id="p-002", session_id=sid, intent="b", status=PlanStatus.running)
    )
    refs_after = await session_data_manager.list_refs(sid)
    assert set(refs_before) == set(refs_after)
    loaded = await store.load_current(sid)
    assert loaded.intent == "b"
    assert loaded.status == PlanStatus.running


@pytest.mark.asyncio
async def test_store_cache_miss_restores_from_session_store():
    """Worker restart: fresh process-local cache, same session store."""
    sid = "sess-foundation-3"
    store = PlanStore()
    await store.save(CanonicalPlan(plan_id="p-003", session_id=sid, intent="restore"))
    store.clear_cache()
    fresh = PlanStore()
    loaded = await fresh.load_current(sid)
    assert loaded is not None
    assert loaded.plan_id == "p-003"
    assert loaded.intent == "restore"


@pytest.mark.asyncio
async def test_store_supersede_moves_old_to_history():
    sid = "sess-foundation-4"
    store = PlanStore()
    await store.save(CanonicalPlan(plan_id="p-old", session_id=sid, intent="first"))
    new = CanonicalPlan(plan_id="p-new", session_id=sid, intent="second")
    superseded = await store.supersede(sid, new)
    assert superseded is not None and superseded.plan_id == "p-old"
    assert (await store.load_current(sid)).plan_id == "p-new"
    history = await store.get_by_id(sid, "p-old")
    assert history is not None
    assert history.status == PlanStatus.superseded
    assert history.intent == "first"
    # the current plan is also addressable by id
    assert (await store.get_by_id(sid, "p-new")).plan_id == "p-new"
    assert (await store.get_by_id(sid, "current")).plan_id == "p-new"


@pytest.mark.asyncio
async def test_store_clear_removes_current_but_save_resurrects():
    sid = "sess-foundation-5"
    store = PlanStore()
    await store.save(CanonicalPlan(plan_id="p-005", session_id=sid, intent="x"))
    await store.clear(sid)
    assert await store.load_current(sid) is None
    await store.save(CanonicalPlan(plan_id="p-006", session_id=sid, intent="y"))
    loaded = await store.load_current(sid)
    assert loaded is not None and loaded.intent == "y"


@pytest.mark.asyncio
async def test_store_miss_does_not_raise():
    store = PlanStore()
    assert await store.load_current("sess-ghost") is None
    assert await store.get_by_id("sess-ghost", "p-missing") is None


# ─── capability validation ────────────────────────────────────────────


@pytest.fixture
def registry():
    r = ToolRegistry()

    @r.tool(name="hotspot_analysis", description="热点分析", domains=["statistics"], tier=2)
    def hotspot_analysis(points: list) -> dict:
        return {"success": True}

    @r.tool(name="osm_query", description="OSM 查询", domains=["osm"], tier=2)
    def osm_query(bbox: list) -> dict:
        return {"success": True}

    @r.tool(name="plain_tool", description="无域工具")
    def plain_tool(name: str) -> dict:
        return {"success": True}

    return r


def test_capability_of_derives_metadata(registry):
    cap = capability_of("hotspot_analysis", registry)
    assert cap is not None
    assert cap.tier == 2
    assert cap.domains == ["statistics"]
    assert cap.execution_policy == "thread"
    assert cap.destructive is False
    assert cap.produces_ref is True  # statistics ∈ PRODUCES_REF_DOMAINS
    assert cap.requires_ref is False
    # no domains → does not produce a ref
    assert capability_of("plain_tool", registry).produces_ref is False
    # unregistered → None
    assert capability_of("does_not_exist", registry) is None


def test_capability_requires_ref_heuristic(registry):
    @registry.tool(name="layer_styler", description="图层样式")
    def layer_styler(layer_ref: str, color: str) -> dict:
        return {"success": True}

    @registry.tool(
        name="ref_desc_tool",
        description="带 ref 描述的工具",
        param_descriptions={"source": "传 ref:geojson-xxx 引用"},
    )
    def ref_desc_tool(source: str) -> dict:
        return {"success": True}

    assert capability_of("layer_styler", registry).requires_ref is True
    assert capability_of("ref_desc_tool", registry).requires_ref is True
    assert capability_of("plain_tool", registry).requires_ref is False


def test_capability_destructive_tier3(registry):
    @registry.tool(name="heavy_tool", description="重型工具", tier=3)
    def heavy_tool(x: int) -> dict:
        return {"success": True}

    assert capability_of("heavy_tool", registry).destructive is True
    assert capability_of("hotspot_analysis", registry).destructive is False


def test_validate_plan_capabilities_issues(registry):
    p = CanonicalPlan(
        plan_id="p1",
        session_id="s",
        intent="",
        steps=[
            CanonicalStep(id="s1", n=1, goal="", tool="ghost_tool"),
            CanonicalStep(id="s2", n=2, goal="", tool_family="raster"),
            CanonicalStep(id="s3", n=3, goal="", tool="osm_query", tool_family="statistics"),
        ],
    )
    issues = validate_plan_capabilities(p, registry)
    assert any("unregistered tool 'ghost_tool'" in i for i in issues)
    assert any("tool_family 'raster' has no registered tool" in i for i in issues)
    assert any("do not cover tool_family 'statistics'" in i for i in issues)


def test_validate_plan_capabilities_clean(registry):
    p = CanonicalPlan(
        plan_id="p1",
        session_id="s",
        intent="",
        steps=[
            CanonicalStep(id="s1", n=1, goal="", tool="osm_query", tool_family="osm"),
            CanonicalStep(id="s2", n=2, goal="", tool_family="core"),
        ],
    )
    assert validate_plan_capabilities(p, registry) == []


# ─── static ref validation ────────────────────────────────────────────


def _steps(*defs):
    """Build steps from (id, depends_on, args) tuples."""
    return [
        CanonicalStep(id=d[0], n=i + 1, goal="", depends_on=d[1], args=d[2])
        for i, d in enumerate(defs)
    ]


def test_validate_static_refs_clean():
    steps = _steps(("s1", [], {}), ("s2", ["s1"], {"bbox": "${s1.data.bbox}"}))
    assert validate_static_refs(steps) == []


def test_validate_static_refs_unknown_id():
    steps = _steps(("s1", ["ghost"], {}), ("s2", [], {"src": "${ghost.path}"}))
    issues = validate_static_refs(steps)
    assert sum("unknown step 'ghost'" in i for i in issues) == 2


def test_validate_static_refs_forward_ref():
    steps = _steps(("s1", ["s2"], {}), ("s2", [], {}))
    assert any("forward-references step 's2'" in i for i in validate_static_refs(steps))


def test_validate_static_refs_self_dep():
    steps = _steps(("s1", ["s1"], {}))
    assert any("depends on itself" in i for i in validate_static_refs(steps))


def test_validate_static_refs_cycle():
    steps = _steps(("s1", ["s2"], {}), ("s2", ["s1"], {}))
    assert any("cycle" in i for i in validate_static_refs(steps))
    # cycle via args placeholders only
    steps2 = _steps(("s1", [], {"a": "${s2}"}), ("s2", [], {"a": "${s1}"}))
    assert any("cycle" in i for i in validate_static_refs(steps2))


def test_validate_static_refs_duplicate_id():
    steps = _steps(("s1", [], {}), ("s1", [], {}))
    assert any("duplicate step id 's1'" in i for i in validate_static_refs(steps))


# ─── reference resolution ─────────────────────────────────────────────


def test_resolve_arg_refs_happy_path():
    results = {"s1": {"data": {"bbox": [1, 2, 3, 4]}}}
    assert resolve_arg_refs("${s1.data.bbox}", results) == [1, 2, 3, 4]
    assert resolve_arg_refs("${s1}", results) == {"data": {"bbox": [1, 2, 3, 4]}}
    assert resolve_arg_refs("查询 ${s1.data.bbox.0} 到 ${s1.data.bbox.1}", results) == "查询 1 到 2"
    args = {"list": ["${s1.data.bbox}", "static"], "nested": {"x": "${s1.data.bbox.0}"}}
    out = resolve_arg_refs(args, results)
    assert out["list"][0] == [1, 2, 3, 4]
    assert out["list"][1] == "static"
    assert out["nested"]["x"] == 1
    # non-placeholder values pass through untouched
    assert resolve_arg_refs(42, results) == 42
    assert resolve_arg_refs("plain", results) == "plain"
    assert resolve_arg_refs({"k": [None, True, 1.5]}, results) == {"k": [None, True, 1.5]}


def test_resolve_arg_refs_missing_ref_raises():
    results = {"s1": {"data": {"bbox": [1, 2, 3, 4]}}}
    with pytest.raises(MissingRefError) as ei:
        resolve_arg_refs("${s1.data.missing}", results)
    assert ei.value.step_id == "s1"
    assert ei.value.path == "data.missing"
    assert ei.value.available_keys == ["s1"]
    # a placeholder naming a step with no result raises too (never silent None)
    with pytest.raises(MissingRefError):
        resolve_arg_refs("${ghost}", results)
    # embedded placeholder with a bad path raises (no silent "")
    with pytest.raises(MissingRefError):
        resolve_arg_refs("prefix-${s1.nope}-suffix", results)
    # list index out of range
    with pytest.raises(MissingRefError):
        resolve_arg_refs("${s1.data.bbox.9}", results)
    # path segment into a non-dict / non-list
    with pytest.raises(MissingRefError):
        resolve_arg_refs("${s1.data.bbox.0.x}", results)


# ─── failure classification ───────────────────────────────────────────


def test_classify_error_dispatch_codes():
    assert classify_error(code="VALIDATION_ERROR") == FailureClass.validation
    assert classify_error(code="NOT_FOUND") == FailureClass.missing_ref
    assert classify_error(code="UNKNOWN_TOOL") == FailureClass.tool_unavailable
    assert classify_error(code="TOOL_ERROR", message="boom") == FailureClass.internal
    assert classify_error(code="TOOL_ERROR", message="连接超时") == FailureClass.transient_network
    assert classify_error(code="TOOL_ERROR", message="network timeout") == FailureClass.transient_network
    assert classify_error(code="cancelled") == FailureClass.cancelled
    assert classify_error(status="cancelled") == FailureClass.cancelled


def test_classify_error_exceptions():
    assert classify_error(exception=TimeoutError()) == FailureClass.transient_network
    assert classify_error(exception=ConnectionError()) == FailureClass.transient_network
    assert classify_error(exception=KeyError("x")) == FailureClass.missing_ref
    assert classify_error(exception=FileNotFoundError()) == FailureClass.missing_ref
    assert classify_error(exception=ValueError("bad")) == FailureClass.validation
    assert classify_error(exception=OperationCancelled()) == FailureClass.cancelled


def test_classify_error_message_signals():
    assert classify_error(message="permission denied") == FailureClass.auth
    assert classify_error(message="quota exceeded (429)") == FailureClass.resource_limit
    assert classify_error(message="未返回任何空间要素或有效数据") == FailureClass.empty_result
    assert classify_error(message="no data found") == FailureClass.no_data
    # suspicious (successful but empty) results classify as empty_result
    assert classify_error(status="ok", message="no features returned") == FailureClass.empty_result
    assert classify_error(status="ok", code="ok", message="empty result") == FailureClass.empty_result
    assert classify_error(message="something else entirely") == FailureClass.internal
    assert classify_error() == FailureClass.internal


# ─── recovery policy ──────────────────────────────────────────────────


def test_recovery_policy_sanity():
    for fc in FailureClass:
        assert fc in RECOVERY_POLICY
        assert RECOVERY_POLICY[fc], f"{fc} has an empty recovery policy"
        assert len(RECOVERY_POLICY[fc]) == len(set(RECOVERY_POLICY[fc]))
        assert recovery_action_for(fc) == RECOVERY_POLICY[fc][0]
    # no blind retry: retry_transient only for transient_network failures
    for fc, actions in RECOVERY_POLICY.items():
        if fc is not FailureClass.transient_network:
            assert RecoveryAction.retry_transient not in actions
    assert RECOVERY_POLICY[FailureClass.transient_network] == [RecoveryAction.retry_transient]
    assert recovery_action_for(FailureClass.validation) == RecoveryAction.correct_args
    assert recovery_action_for(FailureClass.cancelled) == RecoveryAction.stop


# ─── follow-up classification ─────────────────────────────────────────


_DOMAIN_KEYWORDS = {
    "poi": ["医院", "学校", "POI"],
    "statistics": ["热点", "热力", "聚类"],
}


def test_classify_followup_style_change():
    assert classify_followup(
        "把颜色换成蓝色",
        has_active_plan=True,
        active_domains=[],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.style_change
    # style tweak over an already-active domain is still style_change
    assert classify_followup(
        "热力图换成蓝色",
        has_active_plan=True,
        active_domains=["statistics"],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.style_change
    assert any(kw in STYLE_KEYWORDS for kw in ("颜色", "蓝色", "样式", "style"))


def test_classify_followup_new_goal():
    assert classify_followup(
        "再算一下医院",
        has_active_plan=True,
        active_domains=[],
        session_has_refs=True,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.new_goal


def test_classify_followup_ref_reuse():
    assert classify_followup(
        "刚才那个结果做热点",
        has_active_plan=True,
        active_domains=[],
        session_has_refs=True,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.ref_reuse
    # ref-reuse signals without session refs fall through (no refs to reuse)
    assert classify_followup(
        "刚才那个结果",
        has_active_plan=False,
        active_domains=[],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.unclear
    assert any(kw in REF_REUSE_KEYWORDS for kw in ("刚才", "那个", "该结果"))


def test_classify_followup_continuation():
    assert classify_followup(
        "继续",
        has_active_plan=True,
        active_domains=[],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.continuation
    # continuation without an active plan → unclear
    assert classify_followup(
        "继续",
        has_active_plan=False,
        active_domains=[],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.unclear
    assert "继续" in CONTINUATION_KEYWORDS


def test_classify_followup_unclear():
    assert classify_followup(
        "今天天气不错",
        has_active_plan=False,
        active_domains=[],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.unclear


# ─── P1-B：cache TTL 重新读取 + save revision guard ──────────────


@pytest.mark.asyncio
async def test_store_cache_ttl_expiry_rereads_from_store():
    """P1-B(1)：进程缓存条目超过 L1_TTL_SECONDS 后视为过期，load_current 重新
    从 session store 读取——模拟另一 worker 写入后，本进程在 ~2s 内感知新值，
    而不是一直服务陈旧副本。"""
    from app.services.session_data import session_data_manager as _sdm
    from app.services.planning.store import L1_TTL_SECONDS

    store = PlanStore()
    sid = "sess-foundation-ttl"
    await store.save(CanonicalPlan(plan_id="p-ttl-a", session_id=sid, intent="local"))
    # 模拟另一个 worker 直接在 session store 里写入了更新计划
    ref_id = await _sdm.resolve_alias(sid, "plan-current")
    await _sdm.overwrite(sid, ref_id, CanonicalPlan(
        plan_id="p-ttl-b", session_id=sid, intent="remote",
        revision=5,
    ).model_dump())
    # 把进程缓存条目时间戳拨回 TTL 之前 → 必须重新读取 store（拿到 remote）
    entry = store._cache._data[sid]
    store._cache._data[sid] = (entry[0], entry[1] - L1_TTL_SECONDS - 1.0)
    loaded = await store.load_current(sid)
    assert loaded is not None
    assert loaded.plan_id == "p-ttl-b"
    assert loaded.intent == "remote"


@pytest.mark.asyncio
async def test_store_save_revision_guard_refuses_stale_clobber():
    """P1-B(2)：持久化的 current plan 是**不同 plan_id** 且 revision 更新/相等时，
    save 拒绝覆盖（日志 + 重新读取）；同 plan_id 仍 last-writer-wins。"""
    import io
    import logging as _logging

    store = PlanStore()
    sid = "sess-foundation-revguard"
    # 先把"更新计划"立为 current（模拟另一 worker 已推进）
    await store.save(CanonicalPlan(
        plan_id="p-new", session_id=sid, intent="newer", revision=3,
    ), _promote=True)
    store.clear_cache()  # 让 load_current 走 store（避免缓存掩盖）
    # 陈旧 worker 想用旧计划覆盖
    stale = CanonicalPlan(plan_id="p-stale", session_id=sid, intent="stale", revision=2)
    buf = io.StringIO()
    h = _logging.StreamHandler(buf)
    _logging.getLogger("app.services.planning.store").addHandler(h)
    try:
        await store.save(stale)
    finally:
        _logging.getLogger("app.services.planning.store").removeHandler(h)
    assert "refused to clobber" in buf.getvalue()
    # current 仍是更新计划（没被覆盖）
    current = await store.load_current(sid)
    assert current.plan_id == "p-new"
    assert current.intent == "newer"

    # 同 plan_id 仍 last-writer-wins（revision 更低也允许，同一计划的进化）
    await store.save(CanonicalPlan(plan_id="p-new", session_id=sid, intent="newer-v2", revision=1))
    current2 = await store.load_current(sid)
    assert current2.intent == "newer-v2"
    assert current2.plan_id == "p-new"


# ─── P2-3（adversarial P2-6）：style + query 共存时不判 style_change ──


def test_classify_followup_style_plus_query_is_not_style_change():
    """消息同时含样式词与查询/动作词（查一下蓝色区域的医院分布）→ 不得判为
    style_change（否则会跳过规划/重分析）。"""
    from app.services.planning.followup import QUERY_ACTION_KEYWORDS

    kind = classify_followup(
        "查一下蓝色区域的医院分布",
        has_active_plan=True,
        active_domains=["statistics"],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    )
    assert kind != FollowUpKind.style_change
    # 有内容词 + 查询信号 → 落到 new_goal / unclear，绝不 style_change
    kind2 = classify_followup(
        "找一下红色区域里的学校",
        has_active_plan=True,
        active_domains=["statistics"],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    )
    assert kind2 != FollowUpKind.style_change
    # 纯样式追问（无查询信号）仍是 style_change
    assert classify_followup(
        "换成蓝色",
        has_active_plan=True,
        active_domains=["statistics"],
        session_has_refs=False,
        domain_keywords=_DOMAIN_KEYWORDS,
    ) == FollowUpKind.style_change
    assert any(kw in QUERY_ACTION_KEYWORDS for kw in ("查", "找", "search", "分析"))
