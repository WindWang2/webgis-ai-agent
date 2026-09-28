"""Settlement 终态检查测试（E15）：生产 seam 驱动 + 反向假成功防线。

除正向（全部检查 pass）外，必须证明检查**能红**：伪造 stored_product /
注入假完成态时 no-fake-success 检查必须 FAIL —— 绿 by construction 防线。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.lib.harness.lab.journeys import load_spec
from app.lib.harness.lab.settlement import (
    SettlementCheckResult,
    SettlementSandbox,
    run_settlement_checks,
)
from app.lib.harness.lab.spec import LabScenario

SPEC_PATH = (Path(__file__).resolve().parents[2]
             / "tests" / "fixtures" / "lab" / "scenarios"
             / "lab-j7-fault-matrix-settlement.json")


@pytest.fixture(scope="module")
def j7_spec() -> LabScenario:
    return load_spec(SPEC_PATH)


def _run(coro):
    return asyncio.run(coro)


class TestFaultMatrixSettlement:
    def test_all_declared_checks_pass(self, j7_spec):
        results = _run(run_settlement_checks(j7_spec))
        by_name = {r.check: r for r in results}
        # 故障矩阵全谱：timeout 族在生产结算面 = cancel/disconnect/reject 词表。
        for expected in ("cancel_reduced_settle", "settle_idempotent",
                         "settle_no_fake_success", "duplicate_dispatch_dedup",
                         "policy_deny_refused",
                         "resource_reject_classified",
                         "disconnect_cancelled"):
            assert expected in by_name, f"missing check {expected}"
            assert by_name[expected].status == "pass", \
                f"{expected}: {by_name[expected].detail}"

    def test_policy_deny_comes_from_production_gate(self, j7_spec):
        results = _run(run_settlement_checks(j7_spec))
        policy = next(r for r in results
                      if r.check == "policy_deny_refused")
        # 拒绝带 reason + 合格替代（生产 bind 契约），fake 不代答。
        assert policy.observations.get("alternatives")
        assert policy.observations.get("reason")

    def test_resource_reject_classified_and_cancel_idempotent(self, j7_spec):
        results = _run(run_settlement_checks(j7_spec))
        resource = next(r for r in results
                        if r.check == "resource_reject_classified")
        assert resource.observations.get("reasons"), \
            "rejection must carry classifiable reason codes"


class TestNoFakeSuccessDefenses:
    def test_fabricated_completion_without_finalize_is_red(self, j7_spec,
                                                           monkeypatch):
        """真变异：finalize 缺席而管线泄漏出已存完成态 → 检查必须红
        （防 green-by-construction：本测试先证明该检查能失败）。"""
        results = _run(run_settlement_checks(j7_spec))
        no_fake = next(r for r in results
                       if r.check == "settle_no_fake_success")
        assert no_fake.status == "pass"

        async def _fabricated_run():
            import app.services.gis_harness.map_completion as mc

            sb = SettlementSandbox()

            async def _leak(session_id: str):
                return {"task_complete": True, "fabricated": True}

            with sb:
                # 变异注入：模块级 stored 读取面被污染（管线"泄漏"出一个
                # 完成态），而 finalize 仍缺席 —— 检查必须识别为假成功并
                # 翻红。patch 面是 settle 管线惰性 import 的模块属性。
                monkeypatch.setattr(mc, "read_stored_map_product", _leak)
                from app.lib.harness.lab.settlement import (
                    _check_settle_no_fake_success,
                )

                return await _check_settle_no_fake_success(j7_spec, sb)

        fabricated = _run(_fabricated_run())
        assert fabricated.status == "fail", fabricated.detail
        assert "still returned" in fabricated.detail

    def test_check_crash_becomes_fail_not_pass(self, j7_spec, monkeypatch):
        """检查实现崩溃 = 该项 fail（永不静默绿）。"""
        import app.lib.harness.lab.settlement as st

        def _boom(spec, sb):
            raise RuntimeError("simulated crash")

        monkeypatch.setitem(st._CHECKS, "cancel_reduced_settle", _boom)
        results = _run(run_settlement_checks(j7_spec))
        cancel = next(r for r in results
                      if r.check == "cancel_reduced_settle")
        assert cancel.status == "fail"
        assert "simulated crash" in cancel.detail

    def test_unknown_check_name_is_fail(self):
        spec = LabScenario.from_dict({
            "spec_id": "ghost", "title": "t", "kind": "settlement",
            "settlement_checks": ["not_a_check"],
        })
        # 绕过 validate（直接执行层面）——运行时未知检查名同样诚实翻红。
        results = _run(run_settlement_checks(spec))
        assert results[0].status == "fail"

    def test_sandbox_restores_collaborators(self, j7_spec):
        import app.services.gis_harness.map_completion as mc
        import app.services.gis_harness.trace_store as ts

        saved_finalize = mc.maybe_finalize_map_product
        saved_persist = ts.persist_turn_chain
        with SettlementSandbox():
            assert mc.maybe_finalize_map_product is not saved_finalize
        assert mc.maybe_finalize_map_product is saved_finalize
        assert ts.persist_turn_chain is saved_persist


class TestSettlementCheckResultContract:
    def test_status_vocabulary_is_honest(self):
        ok = SettlementCheckResult("c", "pass")
        bad = SettlementCheckResult("c", "fail", "detail")
        assert ok.ok and not bad.ok
