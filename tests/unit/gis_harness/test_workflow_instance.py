"""WorkflowInstance（V4 / ADR-0104 Wave 1）回归锁。

不变式（对应任务书的 Wave 1 验收）：
1. 数据补齐后，相关 blocked state 自动重算（科学契约解除方向回写）；
2. artifact/输入证据 stale 后，只 invalidate 受影响 downstream（reverse
   依赖闭包），无关分支零触碰；
3. style-only 变化（revision 推进）不触发科学阶段状态变化；
4. algorithm/parameter 变化正确标记 STALE 并推进 StateRevision；
5. 状态转移 deterministic —— 同输入同输出（fingerprint/revision 稳定）；
6. transition evidence bounded（≤MAX_TRANSITIONS）、可 trace；
7. 同一输入重复执行 fingerprint 一致。

全部纯函数测试零 IO；服务入口测试用 fakes（无 Redis/DB）。
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.services.gis_harness.workflow_instance import (
    MAX_STAGES,
    MAX_TRANSITIONS,
    WORKFLOW_INSTANCE_KEY,
    WorkflowEventKind,
    derive_workflow_instance,
    format_instance_line,
    gate_fingerprint,
    derive_unblock_contract,
    row_signature,
    rows_fingerprint,
)


# ── 章节构造器（最小 MapProductPlan dict 形状）──────────────────────────

def _chapter(
    *,
    plan_id: str = "plan111",
    recipe_id: str = "",
    contract: Any = None,
    rows: int = 2,
    steps: int = 2,
) -> Dict[str, Any]:
    chapter: Dict[str, Any] = {
        "plan_id": plan_id,
        "recipe_id": recipe_id,
        "query": "学校分布与教育公平",
        "status": "finalized",
        "data_requirements": [
            {
                "capability": f"cap_req_{i}",
                "purpose": "req",
                "status": "pending",
                "bound_ref": "",
                "resolved_algorithm": "",
                "resolved_tool": "",
                "depends_on": [],
                "params": {},
            }
            for i in range(rows)
        ],
        "analysis_steps": [
            {
                "capability": f"cap_step_{i}",
                "purpose": "step",
                "status": "pending",
                "bound_ref": "",
                "resolved_algorithm": "",
                "resolved_tool": "",
                "depends_on": [],
                "optional": False,
            }
            for i in range(steps)
        ],
        "map_layers": [],
        "components": [],
    }
    if contract is not None:
        chapter["workflow_contract"] = contract
    return chapter


def _bind(chapter: Dict[str, Any], capability: str, ref: str, algo: str = "") -> None:
    """把一行翻成完成态 + 绑 ref（模拟 _mark_progress 的行事实）。"""
    for row in chapter.get("data_requirements", []) + chapter.get("analysis_steps", []):
        if row.get("capability") == capability:
            row["status"] = "available" if row in chapter["data_requirements"] else "done"
            row["bound_ref"] = ref
            if algo:
                row["resolved_algorithm"] = algo


def _derive(chapter, stored=None, **kw):
    return derive_workflow_instance(
        chapter,
        instance_id="sess:env:plan",
        mapspec_revision=kw.pop("revision", 0),
        render_seq=kw.pop("render_seq", 0),
        stored=stored,
        **kw,
    )


def _state_map(block) -> Dict[str, str]:
    return {s.capability: s.state for s in block.stages}


# ── 1. 基础派生与确定性 ──────────────────────────────────────────────────

def test_derive_deterministic_same_input_same_fingerprint():
    ch = _chapter()
    a = _derive(ch)
    b = _derive(ch)
    assert a.state_fingerprint == b.state_fingerprint
    assert a.state_revision == b.state_revision == 1
    assert a.gate_fingerprint == b.gate_fingerprint
    # dict 序列化也确定
    assert a.to_bounded_dict() == b.to_bounded_dict()


def test_stored_same_content_keeps_revision():
    ch = _chapter()
    first = _derive(ch)
    stored = first.to_bounded_dict()
    again = _derive(ch, stored=stored)
    assert again.state_revision == first.state_revision
    assert again.state_fingerprint == first.state_fingerprint
    assert again.transitions == []


def test_content_change_increments_revision_and_records_transitions():
    ch = _chapter()
    _bind(ch, "cap_step_0", "ref:geojson-abc")
    first = _derive(ch)
    stored = first.to_bounded_dict()
    # 无依赖的 pending 行在首派生即 ready（plan_graph 语义）——绑定后翻
    # satisfied，转移记录 ready→satisfied。
    assert _state_map(first)["cap_step_1"] == "ready"
    _bind(ch, "cap_step_1", "ref:geojson-def")
    second = _derive(ch, stored=stored)
    assert second.state_revision == first.state_revision + 1
    changed = {t.capability: (t.from_state, t.to_state) for t in second.transitions}
    assert changed.get("cap_step_1") == ("ready", "satisfied")


# ── 2. rows_fingerprint V2：参数/算法可见 ────────────────────────────────

def test_rows_fingerprint_sees_algorithm_and_param_changes():
    ch = _chapter()
    fp0 = rows_fingerprint(ch)
    _bind(ch, "cap_step_0", "ref:geojson-abc", algo="idw")
    fp1 = rows_fingerprint(ch)
    assert fp0 != fp1, "algorithm change must be visible to the gate"
    # 参数-only 变化（V1 签名完全相同的盲区）
    ch2 = _chapter()
    for row in ch2["data_requirements"]:
        if row["capability"] == "cap_req_0":
            row["params"] = {"power": 3}
    assert rows_fingerprint(ch2) != fp0, "parameter-only edit must break the gate"


def test_row_signature_stable_and_sorted():
    ch = _chapter()
    assert rows_fingerprint(ch) == rows_fingerprint(ch)
    sig = row_signature(ch["data_requirements"][0])
    assert sig.startswith("cap_req_0:pending")


# ── 3. 证据 stale 传播：只污染下游 ───────────────────────────────────────

def test_staleness_marks_only_downstream_closure():
    ch = _chapter()
    # 链：step_0 → step_1（显式依赖）
    for row in ch["analysis_steps"]:
        if row["capability"] == "cap_step_1":
            row["depends_on"] = ["cap_step_0"]
    _bind(ch, "cap_step_0", "ref:geojson-a")
    _bind(ch, "cap_step_1", "ref:geojson-b")
    first = _derive(ch)
    stored = first.to_bounded_dict()
    assert _state_map(first)["cap_step_0"] == "satisfied"

    # 上游输入事实漂移（换 ref）→ 上游 STALE + 下游 STALE；
    # 无关分支（cap_step_2 不存在——用 req 行验证零触碰）
    _bind(ch, "cap_step_0", "ref:geojson-a2")
    second = _derive(ch, stored=stored)
    states = _state_map(second)
    assert states["cap_step_0"] == "stale"
    assert states["cap_step_1"] == "stale"
    # 无关行（req 行）不因闭包变 stale
    assert states["cap_req_0"] != "stale"
    assert second.science.divergent is False


def test_staleness_not_computed_on_first_derivation():
    ch = _chapter()
    _bind(ch, "cap_step_0", "ref:a")
    block = _derive(ch)
    assert all(s.state != "stale" for s in block.stages)
    assert all(s.evidence.status == "current" for s in block.stages)


# ── 4. 阻塞态：DAG 阻塞 + 恢复 ──────────────────────────────────────────

def test_dag_blocked_and_recovery():
    ch = _chapter()
    for row in ch["analysis_steps"]:
        if row["capability"] == "cap_step_1":
            row["depends_on"] = ["cap_step_0"]
    # 上游 unavailable → 下游 BLOCKED（BLOCKED_BY_DEPENDENCY）
    for row in ch["analysis_steps"]:
        if row["capability"] == "cap_step_0":
            row["status"] = "unavailable"
    blocked = _derive(ch)
    states = _state_map(blocked)
    assert states["cap_step_0"] == "blocked"
    assert states["cap_step_1"] == "blocked"
    stage = next(s for s in blocked.stages if s.capability == "cap_step_1")
    assert any(r.startswith("BLOCKED_BY_DEPENDENCY") for r in stage.blocked_reasons)

    # 数据到位（恢复路径）：上游可用 → 下游回 pending/ready（自动重评）
    stored = blocked.to_bounded_dict()
    _bind(ch, "cap_step_0", "ref:geojson-fixed")
    recovered = _derive(ch, stored=stored, event="data_arrived")
    assert _state_map(recovered)["cap_step_1"] in ("pending", "ready")
    assert _state_map(recovered)["cap_step_0"] == "satisfied"


# ── 5. 科学契约解除（数据到位；单调、保 method，review Round-1 语义）────

def test_unblock_candidate_honest_none_without_recipe():
    contract = {
        "recipe_id": "r",
        "roles": [
            {"role": "denominator", "status": "unresolved", "required": True,
             "missing_policy": "block"},
        ],
        "obligations": [],
        "method_blockers": [],
        "data_blockers": ["denominator"],
    }
    ch = _chapter(recipe_id="edu_equity", contract=contract)
    # edu_equity recipe 不存在 → 诚实 None（不虚构评估）
    assert derive_unblock_contract(ch) is None


def test_unblock_requires_new_binding_evidence():
    """无新绑定证据（角色早已 bound）⇒ None —— 零重写。"""
    contract = {
        "recipe_id": "poi_distribution_overview",
        "roles": [
            {"role": "subject", "status": "bound", "bound_ref": "ref:old",
             "required": True, "missing_policy": "block"},
        ],
        "obligations": [], "method_blockers": [], "data_blockers": [],
    }
    ch = _chapter(recipe_id="poi_distribution_overview", contract=contract)
    for row in ch["data_requirements"]:
        if row["capability"] == "cap_req_0":
            row["status"] = "available"
            row["bound_ref"] = "ref:new"
    assert derive_unblock_contract(ch) is None


def test_failed_row_never_binds_and_method_blockers_preserved():
    """review Round-1 #1 对账测试：failed 行不产生绑定；method_blockers /
    obligations 逐字保留 —— 重算永不放松方法红线。"""
    contract = {
        "recipe_id": "poi_distribution_overview",
        "roles": [
            {"role": "subject", "status": "unresolved", "required": True,
             "missing_policy": "block"},
        ],
        "obligations": [{"obligation_id": "o1", "status": "blocked"}],
        "method_blockers": ["o1"],
        "data_blockers": ["subject"],
    }
    ch = _chapter(recipe_id="poi_distribution_overview", contract=contract)
    # failed 行 + step 行绑定 —— 都不算数据在场证据
    for row in ch["data_requirements"]:
        if row["capability"] == "cap_req_0":
            row["status"] = "failed"
            row["bound_ref"] = "ref:stale"
    for row in ch["analysis_steps"]:
        if row["capability"] == "cap_step_0":
            row["status"] = "done"
            row["bound_ref"] = "ref:step"
    assert derive_unblock_contract(ch) is None
    # available 行 → 恰好解除该角色；method/obligations 原样
    for row in ch["data_requirements"]:
        if row["capability"] == "cap_req_0":
            row["status"] = "available"
    candidate = derive_unblock_contract(ch)
    if candidate is not None:
        # poi_distribution_overview 的 subject 角色 hint 若命中 cap_req_0
        assert candidate["method_blockers"] == ["o1"]
        assert candidate["obligations"] == [{"obligation_id": "o1", "status": "blocked"}]
        assert "subject" not in candidate["data_blockers"]


def test_science_recheck_neutral_without_contract():
    ch = _chapter()
    block = _derive(ch)
    assert block.science.evaluated is False
    assert block.science.direction == ""


def test_non_subset_candidate_never_unblocks():
    """阻断非严格子集（method 变化）⇒ 不解除（恒等裁决，非数量）。"""
    contract = {
        "roles": [
            {"role": "subject", "status": "unresolved", "required": True,
             "missing_policy": "block"},
        ],
        "obligations": [], "method_blockers": ["o1"],
        "data_blockers": ["subject"],
    }
    ch = _chapter(recipe_id="r", contract=contract)
    fake = dict(contract)
    fake["data_blockers"] = []
    fake["method_blockers"] = []  # method 变了 —— 即使 data 全解除也不算
    science = _derive(ch, recomputed_contract=fake).science
    assert science.direction == "equal"


# ── 6. 事件维度映射（style-only ≠ science）──────────────────────────────

def test_style_event_dimensions_exclude_science():
    dims = WorkflowEventKind.STYLE_MUTATION.dimensions
    assert dims == ("style",)
    assert "data" not in dims and "algorithm" not in dims
    assert WorkflowEventKind.PARAMETER_CHANGE.dimensions == ("parameter",)
    assert WorkflowEventKind.ALGORITHM_CHANGE.dimensions == ("algorithm",)


def test_style_revision_advance_does_not_touch_science_states():
    ch = _chapter()
    _bind(ch, "cap_step_0", "ref:a")
    first = _derive(ch, revision=3)
    stored = first.to_bounded_dict()
    science_before = first.science.to_bounded_dict()
    stage_states_before = _state_map(first)
    # 纯呈现突变：只有 MapSpec revision 推进（行事实零变化）。实例块内容
    # （checked_revision）诚实前进 revision，但科学维与阶段态零触碰——
    # 「style-only 不触发 science recompute」的实例面不变式。
    second = _derive(ch, stored=stored, revision=4, event="style_mutation")
    assert second.checked_revision == 4
    assert second.science.to_bounded_dict() == science_before
    assert _state_map(second) == stage_states_before
    # gate 指纹前进（终验面会重验呈现），转移记录无科学/阶段漂移。
    assert second.gate_fingerprint != first.gate_fingerprint
    assert not any(t.capability == "" for t in second.transitions)


# ── 7. 有界性 ────────────────────────────────────────────────────────────

def test_stages_and_transitions_bounded():
    ch = _chapter(rows=30, steps=30)
    block = _derive(ch)
    assert len(block.stages) <= MAX_STAGES
    # 翻转大量行制造大量转移 → 仍 ≤MAX_TRANSITIONS
    stored = block.to_bounded_dict()
    for row in ch["data_requirements"] + ch["analysis_steps"]:
        row["status"] = "available" if row in ch["data_requirements"] else "done"
        row["bound_ref"] = "ref:x"
    big = _derive(ch, stored=stored)
    assert len(big.transitions) <= MAX_TRANSITIONS
    d = big.to_bounded_dict()
    assert d["schema"] == "workflow_instance.v1"


# ── 8. 门指纹 ────────────────────────────────────────────────────────────

def test_gate_fingerprint_inputs():
    ch = _chapter()
    g0 = gate_fingerprint(rows_fingerprint(ch), 0, 0, None)
    g_same = gate_fingerprint(rows_fingerprint(ch), 0, 0, None)
    assert g0 == g_same
    assert gate_fingerprint(rows_fingerprint(ch), 1, 0, None) != g0
    assert gate_fingerprint(rows_fingerprint(ch), 0, 1, None) != g0
    contract = {"roles": [], "obligations": [], "method_blockers": [], "data_blockers": []}
    assert gate_fingerprint(rows_fingerprint(ch), 0, 0, contract) != g0


# ── 9. 投影行 ────────────────────────────────────────────────────────────

def test_format_instance_line_bounded_and_additive():
    assert format_instance_line(None) == ""
    ch = _chapter()
    assert format_instance_line(ch) == ""  # 无实例块 → 空（旧章节零漂移）
    block = _derive(ch)
    ch[WORKFLOW_INSTANCE_KEY] = block.to_bounded_dict()
    line = format_instance_line(ch)
    assert line.startswith("[GIS Instance] rev=")
    assert len(line) <= 480


# ── 10. 实例 id 形状 ─────────────────────────────────────────────────────

def test_instance_id_and_plan_id():
    ch = _chapter(plan_id="abc123")
    block = _derive(ch)
    assert block.plan_id == "abc123"
    assert block.instance_id == "sess:env:plan"


# ── 11. 服务入口（clean_session = 真 in-process store，无 Redis/DB 依赖）──

@pytest.fixture
async def clean_session():
    import shutil
    import uuid

    sid = f"wfi-svc-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    try:
        from app.lib.data.large_data import BASE_STORAGE_DIR
        d = BASE_STORAGE_DIR / sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001 — 清理失败不影响测试结果
        pass


async def _save_plan(sid: str, chapter: Dict[str, Any]) -> None:
    from app.services.session_plan import SessionPlan, save_session_plan

    await save_session_plan(SessionPlan(
        envelope_id=f"env-{chapter['plan_id']}",
        session_id=sid,
        user_goal=str(chapter.get("query") or "goal"),
        gis_chapter=chapter,
    ))


@pytest.mark.asyncio
async def test_service_persists_block_and_gate_skips(clean_session):
    from app.services.session_plan import load_session_plan
    from app.services.gis_harness.workflow_instance import (
        maybe_update_workflow_instance,
    )

    ch = _chapter()
    await _save_plan(clean_session, ch)
    block = await maybe_update_workflow_instance(clean_session, reason="test")
    assert block is not None
    assert block["schema"] == "workflow_instance.v1"
    fresh = await load_session_plan(clean_session)
    assert fresh.gis_chapter[WORKFLOW_INSTANCE_KEY]["instance_id"] == block["instance_id"]
    # 门：无变化 → 跳过（None）
    again = await maybe_update_workflow_instance(clean_session, reason="test")
    assert again is None


@pytest.mark.asyncio
async def test_service_recomputes_on_row_change_and_persists(clean_session):
    from app.services.session_plan import load_session_plan
    from app.services.gis_harness.workflow_instance import (
        maybe_update_workflow_instance,
    )

    ch = _chapter()
    await _save_plan(clean_session, ch)
    first = await maybe_update_workflow_instance(clean_session, reason="t0")
    assert first is not None
    # 行事实变化（模拟数据到位）→ 门打破 → 新块 revision 前进
    _bind(ch, "cap_step_0", "ref:geojson-x")
    plan = await load_session_plan(clean_session)
    plan.gis_chapter = {**plan.gis_chapter}
    for row in plan.gis_chapter["analysis_steps"]:
        if row["capability"] == "cap_step_0":
            row["status"] = "done"
            row["bound_ref"] = "ref:geojson-x"
    from app.services.session_plan import save_session_plan

    await save_session_plan(plan)
    second = await maybe_update_workflow_instance(clean_session, reason="t1")
    assert second is not None
    assert second["state_revision"] == first["state_revision"] + 1


@pytest.mark.asyncio
async def test_service_unblock_direction_writes_contract(clean_session, monkeypatch):
    """解除方向：单调解除候选（roles + data_blockers）写回章节。

    review Round-1 语义：fake 必须是合法解除形状 —— data_blockers 严格
    子集、method_blockers 逐字保留。
    """
    from app.services.session_plan import load_session_plan
    import app.services.gis_harness.workflow_instance as wfi

    contract = {
        "recipe_id": "r", "roles": [
            {"role": "denominator", "status": "unresolved", "required": True,
             "missing_policy": "block"},
        ],
        "obligations": [{"obligation_id": "o1", "status": "blocked"}],
        "method_blockers": ["obl_a"], "data_blockers": ["denominator"],
    }
    ch = _chapter(recipe_id="edu_equity", contract=contract)
    await _save_plan(clean_session, ch)
    candidate = dict(contract)
    candidate["data_blockers"] = []           # 严格子集
    candidate["method_blockers"] = ["obl_a"]  # method 逐字保留
    candidate["roles"] = [
        {"role": "denominator", "status": "bound", "bound_ref": "ref:new",
         "required": True, "missing_policy": "block"},
    ]
    calls = {"n": 0}

    def _fake_unblock(chapter):
        calls["n"] += 1
        return dict(candidate)

    monkeypatch.setattr(wfi, "derive_unblock_contract", _fake_unblock)
    block = await wfi.maybe_update_workflow_instance(clean_session, reason="t")
    assert block is not None
    assert calls["n"] == 1
    fresh = await load_session_plan(clean_session)
    written = fresh.gis_chapter["workflow_contract"]
    assert written["method_blockers"] == ["obl_a"], "method blockers must survive verbatim"
    assert written["data_blockers"] == []
    assert written["recheck"]["source"] == "data_arrival"
    sci = fresh.gis_chapter[WORKFLOW_INSTANCE_KEY]["science"]
    assert sci["direction"] == "unblocked"


@pytest.mark.asyncio
async def test_service_non_subset_candidate_does_not_write_contract(clean_session, monkeypatch):
    """非子集候选（method 变化 / 无新证据）⇒ 不回写、方向 equal。"""
    import app.services.gis_harness.workflow_instance as wfi
    from app.services.session_plan import load_session_plan

    contract = {
        "recipe_id": "r", "roles": [], "obligations": [],
        "method_blockers": [], "data_blockers": [],
    }
    ch = _chapter(recipe_id="r2", contract=contract)
    await _save_plan(clean_session, ch)
    not_subset = dict(contract)
    not_subset["data_blockers"] = ["denominator"]  # 增阻断 → 非子集

    monkeypatch.setattr(wfi, "derive_unblock_contract", lambda chapter: dict(not_subset))
    block = await wfi.maybe_update_workflow_instance(clean_session, reason="t")
    assert block is not None
    fresh = await load_session_plan(clean_session)
    assert fresh.gis_chapter["workflow_contract"]["data_blockers"] == []
    sci = fresh.gis_chapter[WORKFLOW_INSTANCE_KEY]["science"]
    assert sci["direction"] == "equal"
    assert sci["divergent"] is False


@pytest.mark.asyncio
async def test_projection_line_appears_after_service_run(clean_session):
    from app.services.session_plan import (
        format_session_plan_projection,
        load_session_plan,
    )
    from app.services.gis_harness.workflow_instance import (
        maybe_update_workflow_instance,
    )

    ch = _chapter()
    await _save_plan(clean_session, ch)
    await maybe_update_workflow_instance(clean_session, reason="t")
    fresh = await load_session_plan(clean_session)
    projection = format_session_plan_projection(fresh)
    assert "[GIS Instance] rev=" in projection


@pytest.mark.asyncio
async def test_kill_switch_disables_service(clean_session, monkeypatch):
    from app.services.gis_harness.workflow_instance import (
        maybe_update_workflow_instance,
    )

    ch = _chapter()
    await _save_plan(clean_session, ch)
    monkeypatch.setenv("GIS_WORKFLOW_INSTANCE", "0")
    assert await maybe_update_workflow_instance(clean_session, reason="t") is None


# ── 12. 真实 V2 recipe 对账（review R2 re-review：不得再用无画像 recipe）──

def test_unblock_parity_with_real_v2_recipe():
    """真实 V2 recipe（admin_feature_audit，subject←poi_query，block）：
    available 行产生真实解除候选，method/obligations 逐字保留。"""
    contract = {
        "recipe_id": "admin_feature_audit",
        "roles": [
            {"role": "subject", "status": "unresolved", "required": True,
             "missing_policy": "block", "capability_hint": "poi_query"},
        ],
        "obligations": [{"obligation_id": "obl-1", "kind": "disclosure",
                         "status": "warning"}],
        "method_blockers": ["obl-1"],
        "data_blockers": ["subject"],
    }
    ch = _chapter(recipe_id="admin_feature_audit", contract=contract)
    # 把首行换成 recipe 真实绑定的 capability（subject ← poi_query）
    ch["data_requirements"][0]["capability"] = "poi_query"
    # failed 行：无新绑定证据 → None
    for row in ch["data_requirements"]:
        if row["capability"] == "poi_query":
            row["status"] = "failed"
            row["bound_ref"] = "ref:dead"
    assert derive_unblock_contract(ch) is None

    # available 行 → 真实候选：subject 解除、method/obligations 原样
    for row in ch["data_requirements"]:
        if row["capability"] == "poi_query":
            row["status"] = "available"
            row["bound_ref"] = "ref:live"
    candidate = derive_unblock_contract(ch)
    assert candidate is not None, "real V2 recipe must produce an unblock candidate"
    assert candidate["data_blockers"] == []
    assert candidate["method_blockers"] == ["obl-1"]
    assert candidate["obligations"] == [{"obligation_id": "obl-1",
                                         "kind": "disclosure",
                                         "status": "warning"}]
    roles = {r["role"]: r for r in candidate["roles"]}
    assert roles["subject"]["status"] == "bound"
    assert roles["subject"]["bound_ref"] == "ref:live"
    # 解除候选驱动科学维裁决（端到端：derive → direction=unblocked）
    block = _derive(ch, recomputed_contract=candidate)
    assert block.science.direction == "unblocked"
    assert block.science.method_blockers == ["obl-1"]
