"""Delegation（V7 ADR-0130 D7）回归锁：handoff schema / 台账 / 失败回收。

不变式：
1. role fail-closed：未知角色拒绝（不 spawn，error 披露）；
2. 台账：gis_chapter["delegations"] additive 单键，环形 ≤8；状态词表
   pending/running/completed/failed；
3. 失败回收：首败 + repair 预算有余 → 重试一次；预算尽或再败 → failed
   诚实披露（不换 role 盲目重试，不无限对抗）；
4. 成功回写：result_summary + refs + lineage（父侧 provenance）；
5. QA 驱动点：默认关（env 门）；开启后同成品 revision 幂等；
6. 投影行：台账缺席零漂移，运行中委派有界披露。
"""
from __future__ import annotations

import shutil
import uuid
from typing import Any, Dict, List

import pytest

from app.services.gis_harness.delegation import (
    DELEGATIONS_KEY,
    DelegationRecord,
    DelegationSpec,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    delegate,
    delegation_enabled,
    delegate_cartography_qa,
    format_delegations_line,
)


class _FakeDispatcher:
    """可编程 fake（记录调用；按脚本返回结果）。"""

    def __init__(self, script: List[Any]):
        self.script = list(script)
        self.calls: List[Dict[str, Any]] = []

    async def run(self, *, task, max_rounds=10, role=None, **kw):
        self.calls.append({"task": task, "max_rounds": max_rounds, "role": role})
        outcome = self.script.pop(0) if self.script else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok(summary="QA 复核完成", refs=None):
    from app.services.subagent import SubagentResult

    return SubagentResult(success=True, summary=summary,
                          refs=list(refs or ["ref:chart-1"]),
                          lineage={"depth": 1, "role": "cartography_reviewer"})


def _fail(error="llm timeout"):
    from app.services.subagent import SubagentResult

    return SubagentResult(success=False, summary="", error=error)


def _spec(**kw) -> DelegationSpec:
    base = dict(role="cartography_reviewer", task="复核图例与标注质量",
                plan_step="qa:1", required_outputs=["复核结论"])
    base.update(kw)
    return DelegationSpec(**base)


@pytest.fixture()
async def clean_session():
    sid = f"dlg-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    try:
        from app.lib.data.large_data import BASE_STORAGE_DIR

        d = BASE_STORAGE_DIR / sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


async def _save_plan(sid: str) -> None:
    from app.services.session_plan import SessionPlan, save_session_plan

    await save_session_plan(SessionPlan(
        envelope_id="env-dlg", session_id=sid, user_goal="成都学校分布",
        gis_chapter={
            "plan_id": "planG", "query": "成都学校分布",
            "data_requirements": [
                {"capability": "poi_query", "status": "complete"}],
        },
    ))


# ── role fail-closed ─────────────────────────────────────────────────────


async def test_unknown_role_rejected(clean_session):
    await _save_plan(clean_session)
    rec = await delegate(clean_session, _spec(role="no_such_role"),
                         dispatcher=_FakeDispatcher([_ok()]))
    assert rec.status == STATUS_FAILED
    assert "unknown role" in rec.error


# ── 台账与成功回写 ───────────────────────────────────────────────────────


async def test_success_record_persisted_with_lineage(clean_session):
    await _save_plan(clean_session)
    fake = _FakeDispatcher([_ok(summary="图例正常", refs=["ref:ok-1"])])
    rec = await delegate(clean_session, _spec(), dispatcher=fake)
    assert rec.status == STATUS_COMPLETED
    assert rec.attempts == 1
    assert rec.refs == ["ref:ok-1"]
    assert rec.lineage.get("role") == "cartography_reviewer"
    assert fake.calls[0]["role"] == "cartography_reviewer"
    # 台账持久化（环形 ≤8）
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(clean_session)
    records = (plan.gis_chapter.get(DELEGATIONS_KEY) or {}).get("records") or []
    assert len(records) == 1
    assert records[0]["status"] == STATUS_COMPLETED
    # 台账满 8 条后最老被裁剪
    for i in range(10):
        await delegate(clean_session, _spec(task=f"t{i}"),
                       dispatcher=_FakeDispatcher([_ok(summary=f"s{i}")]))
    plan = await load_session_plan(clean_session)
    records = (plan.gis_chapter.get(DELEGATIONS_KEY) or {}).get("records") or []
    assert len(records) == 8


async def test_failure_retries_once_then_honest_fail(clean_session):
    await _save_plan(clean_session)
    fake = _FakeDispatcher([_fail("boom-1"), _fail("boom-2")])
    rec = await delegate(clean_session, _spec(), dispatcher=fake)
    assert rec.status == STATUS_FAILED
    assert rec.attempts == 2
    assert len(fake.calls) == 2
    assert "boom-2" in rec.error


async def test_failure_budget_exhausted_no_retry(clean_session, tmp_path, monkeypatch):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    from app.services.gis_harness.durable_context import update_recovery_state

    await update_recovery_state(clean_session, loop="repair", detail="x")
    await update_recovery_state(clean_session, loop="repair", detail="y")
    # repair 计数 2/2 → 余量 0 → 不重试
    await _save_plan(clean_session)
    fake = _FakeDispatcher([_fail("boom")])
    rec = await delegate(clean_session, _spec(), dispatcher=fake)
    assert rec.status == STATUS_FAILED
    assert rec.attempts == 1
    assert len(fake.calls) == 1


async def test_first_failure_with_budget_retries(clean_session, tmp_path, monkeypatch):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    await _save_plan(clean_session)
    fake = _FakeDispatcher([_fail("transient"), _ok(summary="二次成功")])
    rec = await delegate(clean_session, _spec(), dispatcher=fake)
    assert rec.status == STATUS_COMPLETED
    assert rec.attempts == 2
    assert rec.result_summary == "二次成功"


# ── QA 驱动点 ────────────────────────────────────────────────────────────


async def test_qa_driver_gated_and_idempotent(clean_session, monkeypatch):
    from app.services.gis_harness import delegation as delegation_mod

    monkeypatch.setenv("GIS_HARNESS_DELEGATION", "0")
    assert delegation_enabled() is False
    assert await delegate_cartography_qa(
        clean_session, findings_codes=["C1"], product_revision=1) is None

    # 开启门 + monkeypatch delegate（真实 dispatcher 需 ChatEngine —— 门与
    # 幂等逻辑在此验证；委派机制由上面用例覆盖）
    monkeypatch.setenv("GIS_HARNESS_DELEGATION", "1")
    await _save_plan(clean_session)
    calls: List[DelegationSpec] = []

    async def _fake_delegate(sid, spec, **kw):
        calls.append(spec)
        rec = DelegationRecord(
            delegation_id="dg-x", spec=spec, status=STATUS_COMPLETED)
        # 与真实 delegate 一致：台账落账（幂等查重消费同一台账）
        from app.services.session_plan import goal_key, load_session_plan

        plan = await load_session_plan(sid)
        await delegation_mod._persist_records(
            sid, delegation_mod._parse_records(
                plan.gis_chapter.get(DELEGATIONS_KEY)) + [rec],
            validated_goal=goal_key(plan.gis_chapter, plan.user_goal),
            merge_by_id=rec)
        return rec

    monkeypatch.setattr(delegation_mod, "delegate", _fake_delegate)
    rec = await delegate_cartography_qa(
        clean_session, findings_codes=["C_EXPORT_COMPLETENESS"],
        product_revision=1)
    assert rec is not None and rec.status == STATUS_COMPLETED
    assert len(calls) == 1
    assert calls[0].role == "cartography_reviewer"
    # 同成品 revision 再次触发 → 幂等（不重复委派）
    rec2 = await delegate_cartography_qa(
        clean_session, findings_codes=["C_EXPORT_COMPLETENESS"],
        product_revision=1)
    assert rec2 is None
    assert len(calls) == 1


# ── 投影行 ───────────────────────────────────────────────────────────────


def test_projection_line_zero_drift_and_active():
    assert format_delegations_line({}) == ""
    chapter = {
        DELEGATIONS_KEY: {"schema": "delegations.v1", "records": [
            {"delegation_id": "dg-1",
             "spec": {"role": "cartography_reviewer", "task": "t",
                      "plan_step": "qa:1", "max_rounds": 4},
             "status": STATUS_RUNNING, "attempts": 1},
            {"delegation_id": "dg-2",
             "spec": {"role": "gis_inspector", "task": "t2",
                      "plan_step": "", "max_rounds": 4},
             "status": STATUS_COMPLETED, "attempts": 1},
        ]},
    }
    line = format_delegations_line(chapter)
    assert line.startswith("[GIS Delegation] active=1 done=1 failed=0")
    assert "running:cartography_reviewer" in line
    assert len(line) <= 480
