"""Harness V6 Wave 14 Resume VNext（verify-not-assume）契约测试。

覆盖恢复后验证阶段的四类行为 + E2E Scenario 9（中断恢复全链路）：

- 存活 ref → 保持 satisfied，不进 recompute（有肯定证据才敢言 live）
- 修订漂移（content_revision）→ STALE + 进 recompute 闭包
- 锚点无证据快照（旧版锚点）→ UNKNOWN + 披露，绝不标 live
- stale 种子经 W5 ``compute_affected_subgraph`` 扩散到下游（upstream_stale）
- 图面依赖（mapspec source → ref）存活/悬空两路披露
- 恢复授权：owner/session 隔离语义不变（attacker 与匿名拒绝）
- Scenario 9：多节点 DAG → 执行 40% → 建锚 → 模拟进程重启中断 →
  resume 恢复 graph/artifact mapping → stale 标记正确 → fake executor
  继续剩余 DAG → 全绿（全确定性，不依赖 LLM 与网络）
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.resume_anchor import (
    build_anchor,
    resume_from_anchor,
    save_anchor,
)
from app.services.gis_harness.resume_verify import (
    VERDICT_LIVE,
    VERDICT_STALE,
    VERDICT_UNKNOWN,
    verify_resumed_refs,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    SessionPlan,
    load_session_plan,
    save_session_plan,
)


class _FakeDb:
    """极简 async DB 桩：内存表语义（owner 查询路径够用）。"""

    def __init__(self):
        self.rows = {}

    def add(self, row):
        if not getattr(row, "id", None):
            row.id = str(uuid.uuid4())
        self.rows[row.id] = row

    async def commit(self):
        return None

    async def refresh(self, row):
        return None

    async def get(self, model, pk):
        return self.rows.get(pk)

    class _Result:
        def __init__(self, row):
            self._row = row

        def scalar_one_or_none(self):
            return self._row

    async def execute(self, stmt):
        from app.models.project import WorkflowResumeAnchor

        sid = None
        try:
            for desc in stmt.whereclause.clauses:
                left = str(desc.left)
                if "session_id" in left:
                    sid = desc.right.value
        except Exception:  # noqa: BLE001
            sid = None
        row = None
        for r in self.rows.values():
            if isinstance(r, WorkflowResumeAnchor) and (
                sid is None or r.session_id == sid
            ):
                row = r
        return self._Result(row)


def _stage(cap, state, bound_ref="", deps=()):
    return {
        "capability": cap,
        "kind": "analysis",
        "state": state,
        "bound_ref": bound_ref,
        "resolved_algorithm": f"alg-{cap}",
    }


def _instance(stages, deps=(), rows_fp="rows-fp-v6"):
    return {
        "instance_id": "inst-v6",
        "plan_id": "plan-v6",
        "state_revision": 1,
        "rows_fingerprint": rows_fp,
        "stages": stages,
        "dependencies": [
            {"consumer": c, "producer": p, "state": "satisfied"}
            for c, p in deps
        ],
    }


def _plan_with(sid, instance, extra_chapter=None):
    chapter = {"query": "确定性验证查询", "workflow_instance": instance}
    if extra_chapter:
        chapter.update(extra_chapter)
    return SessionPlan(
        envelope_id="sp-v6-verify",
        session_id=sid,
        user_goal="验证恢复后不断言存活",
        gis_chapter=chapter,
        progress=[],
    )


def _payload(tag):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"tag": tag},
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]}},
        ],
    }


async def _seed_instance(sid, stages, deps=(), extra_chapter=None):
    """落 plan + 占位 bound_ref（调用方建锚前自行绑定真实 ref）。"""
    await session_data_manager.clear_session(sid)
    await save_session_plan(_plan_with(sid, _instance(stages, deps), extra_chapter))
    return sid


# ── 验证器行为 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_live_refs_keep_satisfied():
    """有肯定证据（载荷在场 + 修订一致）→ live，satisfied 保持，不进 recompute。"""
    sid = f"v6-verify-live-{uuid.uuid4().hex[:6]}"
    await _seed_instance(sid, [_stage("interpolate", "satisfied")])
    ref = await session_data_manager.store(sid, _payload("live"))
    # 绑定真实 ref 后重存 plan（seed 时 ref 尚未存在）
    plan = await load_session_plan(sid)
    plan.gis_chapter["workflow_instance"]["stages"][0]["bound_ref"] = ref
    await save_session_plan(plan)

    db = _FakeDb()
    anchor = await build_anchor(sid)
    assert anchor["ref_evidence"].get(ref, {}).get("content_revision") == 1
    saved = await save_anchor(db, session_id=sid, user_id="u-v6")

    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-v6")
    new_ref = result["ref_map"][ref]
    verdict = result["ref_verdicts"][new_ref]
    assert verdict["verdict"] == VERDICT_LIVE, verdict

    restored = await load_session_plan(result["session_id"])
    stage = restored.gis_chapter["workflow_instance"]["stages"][0]
    assert stage["state"] == "satisfied"
    assert result["stale_nodes"] == []
    assert result["workflow_verify"]["verdict"] == VERDICT_LIVE
    assert restored.gis_chapter["resumed_from"]["verify"]["workflow_verdict"] == "live"

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])


@pytest.mark.asyncio
async def test_verify_revision_drift_marks_stale():
    """来源覆写致 content_revision 漂移 → STALE + 翻标记 + 进 recompute。"""
    sid = f"v6-verify-drift-{uuid.uuid4().hex[:6]}"
    await _seed_instance(sid, [_stage("interpolate", "satisfied")])
    ref = await session_data_manager.store(sid, _payload("v1"))
    plan = await load_session_plan(sid)
    plan.gis_chapter["workflow_instance"]["stages"][0]["bound_ref"] = ref
    await save_session_plan(plan)

    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-v6")
    # 中断前来源被覆写（同 ref 新内容 → 修订递增，真实漂移）
    assert await session_data_manager.overwrite(sid, ref, _payload("v2"))

    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-v6")
    new_ref = result["ref_map"][ref]
    verdict = result["ref_verdicts"][new_ref]
    assert verdict["verdict"] == VERDICT_STALE, verdict
    assert any("漂移" in r for r in verdict["reasons"]), verdict

    restored = await load_session_plan(result["session_id"])
    stage = restored.gis_chapter["workflow_instance"]["stages"][0]
    assert stage["state"] == "stale"
    assert stage["stale_reason"] == "resume_ref_stale"
    assert "interpolate" in result["stale_nodes"]
    assert "interpolate" in result["recompute_plan"].get("recompute", [])

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])


@pytest.mark.asyncio
async def test_verify_no_evidence_marks_unknown_not_live():
    """旧版锚点（无证据快照）→ UNKNOWN + 披露，satisfied 照样翻 stale，绝不标 live。"""
    sid = f"v6-verify-unk-{uuid.uuid4().hex[:6]}"
    await _seed_instance(sid, [_stage("interpolate", "satisfied")])
    ref = await session_data_manager.store(sid, _payload("legacy"))
    plan = await load_session_plan(sid)
    plan.gis_chapter["workflow_instance"]["stages"][0]["bound_ref"] = ref
    await save_session_plan(plan)

    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-v6")
    # 模拟旧版锚点：剥离 V6 证据快照
    saved["anchor"].pop("ref_evidence", None)
    saved["anchor"].pop("workflow_fingerprint", None)

    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-v6")
    new_ref = result["ref_map"][ref]
    verdict = result["ref_verdicts"][new_ref]
    assert verdict["verdict"] == VERDICT_UNKNOWN, verdict
    assert any("快照" in r for r in verdict["reasons"]), verdict

    restored = await load_session_plan(result["session_id"])
    stage = restored.gis_chapter["workflow_instance"]["stages"][0]
    assert stage["state"] == "stale"
    assert stage["stale_reason"] == "resume_ref_unknown"
    assert "interpolate" in result["stale_nodes"]
    assert result["workflow_verify"]["verdict"] == VERDICT_UNKNOWN
    assert result["verify_disclosures"], "无证据路径必须有披露"

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])


@pytest.mark.asyncio
async def test_stale_seed_expands_to_downstream_closure():
    """stale 种子经 W5 闭包污染下游 satisfied，下游翻 upstream_stale，pending 不动。"""
    sid = f"v6-verify-closure-{uuid.uuid4().hex[:6]}"
    await _seed_instance(
        sid,
        [_stage("load_data", "satisfied"),
         _stage("interpolate", "satisfied"),
         _stage("render", "pending")],
        deps=[("interpolate", "load_data"), ("render", "interpolate")],
    )
    ref_a = await session_data_manager.store(sid, _payload("a-v1"))
    ref_b = await session_data_manager.store(sid, _payload("b-v1"))
    plan = await load_session_plan(sid)
    stages = plan.gis_chapter["workflow_instance"]["stages"]
    stages[0]["bound_ref"] = ref_a
    stages[1]["bound_ref"] = ref_b
    await save_session_plan(plan)
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-v6")
    assert await session_data_manager.overwrite(sid, ref_a, _payload("a-v2"))

    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-v6")
    restored = await load_session_plan(result["session_id"])
    by_cap = {s["capability"]: s
              for s in restored.gis_chapter["workflow_instance"]["stages"]}
    assert by_cap["load_data"]["state"] == "stale"
    assert by_cap["load_data"]["stale_reason"] == "resume_ref_stale"
    assert by_cap["interpolate"]["state"] == "stale"
    assert by_cap["interpolate"]["stale_reason"] == "upstream_stale"
    assert by_cap["render"]["state"] == "pending"
    assert set(result["recompute_plan"].get("recompute", [])) >= {
        "load_data", "interpolate"}

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])


@pytest.mark.asyncio
async def test_mapspec_dependency_live_and_dangling():
    """图面依赖：存活 source → live；被删 source → 悬空 unknown 披露。"""
    sid = f"v6-verify-map-{uuid.uuid4().hex[:6]}"
    await _seed_instance(sid, [_stage("compose", "satisfied")])
    ref_live = await session_data_manager.store(sid, _payload("map-live"))
    ref_gone = await session_data_manager.store(sid, _payload("map-gone"))
    map_product = {
        "verdict": "READY",
        "sources": {
            "src-live": {"ref": ref_live},
            "src-gone": {"ref": ref_gone},
        },
        "layers": [
            {"id": "lyr-live", "source": "src-live"},
            {"id": "lyr-gone", "source": "src-gone"},
        ],
    }
    plan = await load_session_plan(sid)
    plan.gis_chapter["workflow_instance"]["stages"][0]["bound_ref"] = ref_live
    plan.gis_chapter["map_product"] = map_product
    await save_session_plan(plan)
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-v6")
    # 中断前一个 source 的 ref 被删除（驱逐/清理模拟）
    assert await session_data_manager.delete_ref(sid, ref_gone)

    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-v6")
    assert ref_gone in result["missing_refs"]
    layers = {l["layer_id"]: l
              for l in result["mapspec_verify"]["layers"]}
    assert layers["lyr-live"]["verdict"] == VERDICT_LIVE, layers
    assert layers["lyr-gone"]["verdict"] == VERDICT_UNKNOWN, layers
    assert result["mapspec_verify"]["verdict"] == VERDICT_UNKNOWN

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])


@pytest.mark.asyncio
async def test_resume_authz_owner_isolation_unchanged():
    """恢复授权语义不变：非 owner 与匿名一律拒绝（走原鉴权路径）。"""
    sid = f"v6-verify-authz-{uuid.uuid4().hex[:6]}"
    await session_data_manager.clear_session(sid)
    await _seed_instance(sid, [_stage("interpolate", "pending")])
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="owner-u")
    with pytest.raises(PermissionError):
        await resume_from_anchor(
            db, anchor_id=saved["anchor_id"], user_id="attacker-u")
    with pytest.raises(PermissionError):
        await resume_from_anchor(
            db, anchor_id=saved["anchor_id"], user_id=None)
    ok = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="owner-u")
    assert ok["session_id"].startswith("resume-")
    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(ok["session_id"])


# ── review Q3：零证据快照一律 unknown ─────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_evidence_snapshot_is_unknown_not_stale():
    """零证据快照（{} / 仅 revision 0）→ unknown，绝不以「无可用字段」
    为由判漂移 stale；仅比对出不一致才 stale。"""
    sid = f"v6-q3-zero-{uuid.uuid4().hex[:6]}"
    await session_data_manager.clear_session(sid)
    ref = await session_data_manager.store(sid, _payload("q3"))
    try:
        for snap in ({}, {"content_revision": 0}):
            verdicts = await verify_resumed_refs(
                sid, {ref: ref}, {ref: dict(snap)})
            v = verdicts[ref]
            assert v["verdict"] == VERDICT_UNKNOWN, (snap, v)
            assert not any("漂移" in r for r in v["reasons"]), (snap, v)
    finally:
        await session_data_manager.clear_session(sid)


# ── E2E Scenario 9：中断恢复全链路 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_scenario9_interrupt_resume_continue_full_green():
    """Scenario 9：五节点 DAG 执行 40% → 建锚 → 进程重启式中断 →
    resume 恢复 mapping → stale 标记正确 → fake executor 跑完剩余 DAG 全绿。

    中断模拟：清空旧 session（plan + ref 载荷全失 = 进程重启/存储丢失），
    唯一幸存的是 DB 里的锚点。恢复后 fake executor（确定性夹具，不碰
    LLM/网络）重算 stale 节点并推进剩余节点。
    """
    caps = ["load_data", "interpolate", "zonal_stats", "compose_map",
            "render_output"]
    deps = [("interpolate", "load_data"), ("zonal_stats", "interpolate"),
            ("compose_map", "zonal_stats"), ("render_output", "compose_map")]
    sid = f"v6-scenario9-{uuid.uuid4().hex[:6]}"
    await _seed_instance(
        sid, [_stage(cap, "pending") for cap in caps], deps=deps,
    )
    refs = {}
    for cap in caps[:2]:  # 执行 40%：前两节点 satisfied 并绑定真实产物
        refs[cap] = await session_data_manager.store(sid, _payload(cap))
    plan = await load_session_plan(sid)
    for s in plan.gis_chapter["workflow_instance"]["stages"]:
        if s["capability"] in refs:
            s["state"] = "satisfied"
            s["bound_ref"] = refs[s["capability"]]
    await save_session_plan(plan)

    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-v6")
    anchor = saved["anchor"]
    assert set(refs.values()) <= set(anchor["ref_ids"])  # 40% 的产物证据在锚
    assert anchor["trace_last_seq"] >= 0

    # 中断：旧 session 全清（重启/TTL 语义）
    await session_data_manager.clear_session(sid)
    assert await load_session_plan(sid) is None

    # 恢复：graph/artifact mapping 还原
    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-v6")
    new_sid = result["session_id"]
    assert result["missing_refs"] and not result["restored_refs"]
    restored = await load_session_plan(new_sid)
    got_caps = [s["capability"]
                for s in restored.gis_chapter["workflow_instance"]["stages"]]
    assert got_caps == caps  # graph mapping 还原（含拓扑序）
    assert restored.gis_chapter["resumed_from"]["source_session_id"] == sid
    assert result["workflow_verify"]["verdict"] == VERDICT_LIVE  # 行指纹一致
    # 已执行节点的证据随中断丢失 → stale 标记正确并进 recompute
    by_cap = {s["capability"]: s
              for s in restored.gis_chapter["workflow_instance"]["stages"]}
    assert by_cap["load_data"]["state"] == "stale"
    assert by_cap["interpolate"]["state"] == "stale"
    assert set(result["stale_nodes"]) >= {"load_data", "interpolate"}
    assert set(result["recompute_plan"].get("recompute", [])) >= {
        "load_data", "interpolate"}
    assert by_cap["zonal_stats"]["state"] == "pending"

    # fake executor：按拓扑序重算 stale + 推进 pending（确定性夹具）
    for cap in caps:
        fresh = await load_session_plan(new_sid)
        stages = fresh.gis_chapter["workflow_instance"]["stages"]
        stage = next(s for s in stages if s["capability"] == cap)
        assert stage["state"] in ("stale", "pending"), (cap, stage["state"])
        new_ref = await session_data_manager.store(new_sid, _payload(f"re-{cap}"))
        stage["state"] = "satisfied"
        stage["bound_ref"] = new_ref
        stage.pop("stale_reason", None)
        await save_session_plan(fresh)

    # 全绿：五节点 satisfied、无 stale、产物全部可读
    final = await load_session_plan(new_sid)
    final_stages = final.gis_chapter["workflow_instance"]["stages"]
    assert [s["capability"] for s in final_stages] == caps
    assert all(s["state"] == "satisfied" for s in final_stages)
    assert not [s for s in final_stages if s["state"] == "stale"]
    for s in final_stages:
        assert await session_data_manager.get(new_sid, s["bound_ref"]) is not None

    await session_data_manager.clear_session(new_sid)
