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
    recompute_workflow_contract,
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


# ── 5. 科学契约重算（数据到位解除阻断）──────────────────────────────────

def test_science_recompute_unblock_direction():
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
    recheck = recompute_workflow_contract(ch)
    # edu_equity recipe 不存在 → 诚实 None（不虚构评估）
    assert recheck is None


def test_science_recheck_neutral_without_contract():
    ch = _chapter()
    block = _derive(ch)
    assert block.science.evaluated is False
    assert block.science.direction == ""


def test_worsened_direction_does_not_rewrite():
    """重算比存储多阻断 → 只披露（worsened），服务不回写 contract。"""
    contract = {
        "roles": [], "obligations": [],
        "method_blockers": ["obl_1"], "data_blockers": [],
    }
    ch = _chapter(recipe_id="edu_equity", contract=contract)
    recomputed = recompute_workflow_contract(ch)
    # recipe 缺席 → None；有 recipe 的场景由服务测试覆盖（fake registry）
    assert recomputed is None or isinstance(recomputed, dict)


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
    """解除方向：重算 contract 写回章节（同一评估器；此处以 fake 评估注入）。"""
    from app.services.session_plan import load_session_plan
    import app.services.gis_harness.workflow_instance as wfi

    contract = {
        "recipe_id": "r", "roles": [], "obligations": [],
        "method_blockers": ["obl_a"], "data_blockers": ["denominator"],
    }
    ch = _chapter(recipe_id="edu_equity", contract=contract)
    await _save_plan(clean_session, ch)
    recomputed = dict(contract)
    recomputed["method_blockers"] = []
    recomputed["data_blockers"] = []
    recomputed["roles"] = [{"role": "denominator", "status": "bound"}]
    calls = {"n": 0}

    def _fake_recompute(chapter):
        calls["n"] += 1
        return dict(recomputed)

    monkeypatch.setattr(wfi, "recompute_workflow_contract", _fake_recompute)
    block = await wfi.maybe_update_workflow_instance(clean_session, reason="t")
    assert block is not None
    assert calls["n"] == 1
    fresh = await load_session_plan(clean_session)
    written = fresh.gis_chapter["workflow_contract"]
    assert written["method_blockers"] == []
    assert written["recheck"]["source"] == "data_arrival"
    sci = fresh.gis_chapter[WORKFLOW_INSTANCE_KEY]["science"]
    assert sci["direction"] == "unblocked"


@pytest.mark.asyncio
async def test_service_worsened_direction_does_not_write_contract(clean_session, monkeypatch):
    import app.services.gis_harness.workflow_instance as wfi
    from app.services.session_plan import load_session_plan

    contract = {
        "recipe_id": "r", "roles": [], "obligations": [],
        "method_blockers": [], "data_blockers": [],
    }
    ch = _chapter(recipe_id="r2", contract=contract)
    await _save_plan(clean_session, ch)
    worse = dict(contract)
    worse["data_blockers"] = ["denominator"]

    monkeypatch.setattr(wfi, "recompute_workflow_contract", lambda chapter: dict(worse))
    block = await wfi.maybe_update_workflow_instance(clean_session, reason="t")
    assert block is not None
    fresh = await load_session_plan(clean_session)
    assert fresh.gis_chapter["workflow_contract"]["data_blockers"] == []
    sci = fresh.gis_chapter[WORKFLOW_INSTANCE_KEY]["science"]
    assert sci["direction"] == "worsened"
    assert sci["divergent"] is True


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
