"""分级能力认证管线测试（ADR-0199）。

覆盖 Oracle 核心语义：
- happy path：最小 v4 pack 认证 certified=true；报告逐字节确定（跑两遍
  JSON 相同——无时间戳、无原始墙钟读数）；
- 声明但未实现 → 认证失败（activation 对账 fail closed）；
- 探针断言失败 / 确定性重放失败 / latency 类别谎报 → 认证失败；
- 坏 skill 契约 → schema 阶段失败；好契约通过；
- 升级/卸载孤儿检查：rollback 残留 → lifecycle fail（ORPHAN）。
"""

from __future__ import annotations

import json

import pytest

from app.extensions_platform.capability_certification import run_pack_certification
from app.extensions_platform.host import ExtensionState

EXTENSION_ID = "certv4.pack"
PROJECTED_TOOL = "certv4_rect_area"
PROJECTED_ALGORITHM = "certv4.rect_area_algo"

# 与 conftest 工厂配套的最小（合法 / 非法）skill 契约。
VALID_SKILL_CONTRACT = {
    "name": "Area Skill",
    "description": "Test skill for area workflows.",
    "domain": "general",
    "version": "1.0.0",
    "procedure": {"steps": [{"step_id": "s1", "title": "Compute area", "kind": "analyze"}]},
}
INVALID_SKILL_CONTRACT = {
    "name": "Broken Skill",
    "domain": "general",
}  # 缺 procedure（SkillContract 必填）→ schema 阶段 fail


class TestHappyPath:
    def test_minimal_pack_certifies(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is True, json.dumps(report["checks"], ensure_ascii=False)
        stages = {c["stage"] for c in report["checks"]}
        assert {"supply_chain", "schema", "implementation", "runtime_probe"} <= stages
        assert report["capabilities"]["tools"] == [PROJECTED_TOOL]
        assert report["capabilities"]["algorithms"] == [PROJECTED_ALGORITHM]

    def test_report_is_byte_deterministic(self, v4_env):
        """关键验证：同一 pack 连续两次认证产出逐字节相同报告。"""
        v4_env.build()
        host, _ = v4_env.make_host()
        first = run_pack_certification(host, EXTENSION_ID)
        host.discover()  # 停用后重发现，走完全相同的 COMPATIBLE 路径
        second = run_pack_certification(host, EXTENSION_ID)
        assert first["certified"] is True
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_active_record_certifies_without_disturbing_state(self, v4_env):
        """已 ACTIVE 的记录：认证补证据但不停用（不扰动运维状态）。"""
        v4_env.build()
        host, _ = v4_env.make_host()
        host.activate(EXTENSION_ID)
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is True
        record = host.get_record(EXTENSION_ID)
        assert record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
        host.deactivate(EXTENSION_ID)

    def test_unknown_extension_single_failure(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, "certv4.does_not_exist")
        assert report["certified"] is False
        assert report["checks"][0]["stage"] == "discovery"


class TestDeclaredButUnimplemented:
    def test_ghost_tool_cannot_certify(self, v4_env):
        v4_env.build(ghost_tool=True)
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is False
        impl_fails = [
            c for c in report["checks"]
            if c["stage"] == "implementation" and c["status"] == "fail"
        ]
        assert impl_fails, "ghost declaration must produce implementation failure"


class TestProbes:
    def test_expectation_mismatch_fails(self, v4_env):
        v4_env.build(probe_expect_value=7.0)
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is False
        probe_fails = [
            c for c in report["checks"]
            if c["stage"] == "runtime_probe" and c["status"] == "fail"
        ]
        assert any("expected" in c["detail"] for c in probe_fails)

    def test_nondeterministic_replay_fails(self, v4_env):
        v4_env.build(nondeterministic=True)
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is False
        probe_fails = [
            c for c in report["checks"]
            if c["stage"] == "runtime_probe" and c["status"] == "fail"
        ]
        assert any("replay mismatch" in c["detail"] for c in probe_fails)

    def test_latency_class_lie_fails(self, v4_env, monkeypatch):
        from app.extensions_platform import capability_certification as cc

        monkeypatch.setattr(cc, "_LATENCY_TOLERANCE_FACTOR", 0.001)
        v4_env.build(latency_class="fast")
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is False
        probe_fails = [
            c for c in report["checks"]
            if c["stage"] == "runtime_probe" and c["status"] == "fail"
        ]
        assert any("budget" in c["detail"] for c in probe_fails)

    def test_no_probe_section_warns_but_certifies(self, v4_env):
        v4_env.build(certification_section=False)
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is True
        warns = [
            c for c in report["checks"]
            if c["stage"] == "runtime_probe" and c["status"] == "warn"
        ]
        assert warns


class TestSkills:
    def test_invalid_skill_contract_fails_schema(self, v4_env):
        v4_env.build(skill_contract=dict(INVALID_SKILL_CONTRACT))
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is False
        schema_fails = [
            c for c in report["checks"]
            if c["stage"] == "schema" and c["status"] == "fail"
        ]
        assert any("skill" in c["capability"] for c in schema_fails)

    def test_valid_skill_contract_certifies(self, v4_env):
        v4_env.build(skill_contract=dict(VALID_SKILL_CONTRACT))
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is True, json.dumps(report["checks"], ensure_ascii=False)
        assert report["capabilities"]["skills"] == ["certv4.area_skill"]


class TestLifecycleOrphans:
    def test_rollback_residue_fails_orphan_check(self, v4_env, monkeypatch):
        """卸载残留（升级/卸载 Oracle）：undo 失效 → 孤儿投影检查抓到。"""
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.tools.registry import ToolRegistry as _TR

        v4_env.build()
        host, registry = v4_env.make_host()
        # 让 deactivate 的工具 undo 永远失败（模拟残留）。
        monkeypatch.setattr(_TR, "unregister", lambda self, name: False)
        report = run_pack_certification(host, EXTENSION_ID)
        monkeypatch.undo()
        assert report["certified"] is False
        lifecycle_fails = [
            c for c in report["checks"]
            if c["stage"] == "lifecycle" and c["status"] == "fail"
        ]
        assert lifecycle_fails
        assert any(
            "orphan" in c["detail"] or "rolled back" in c["detail"] for c in lifecycle_fails
        )
        # 现场清理：投影仍在（undo 被劫持），手工摘除。
        registry.unregister(PROJECTED_TOOL)
        get_algorithm_registry().unregister(PROJECTED_ALGORITHM)


class TestReviewRound3Fixes:
    """Round-3 对抗 review 修复的回归钉（P2 簇）。"""

    def test_report_deterministic_even_with_latency_class(self, v4_env, monkeypatch):
        """P2-2：latency 细节不得内插实测耗时——声明 latency_class 的 pack
        连续两次认证报告仍逐字节相同。"""
        from app.extensions_platform import capability_certification as cc

        monkeypatch.setattr(cc, "_LATENCY_TOLERANCE_FACTOR", 1000.0)  # 0.05s 必过
        v4_env.build(latency_class="fast")
        host, _ = v4_env.make_host()
        first = run_pack_certification(host, EXTENSION_ID)
        host.discover()
        second = run_pack_certification(host, EXTENSION_ID)
        assert first["certified"] is True
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_result_size_violation_fails(self, v4_env):
        """P2-1：result_size_policy 判决必须真实执行——inline_small 工具
        返回 40KB 结果 → 认证失败。"""
        v4_env.build(result_size_policy="inline_small", oversized_result=True)
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is False
        probe_fails = [
            c for c in report["checks"]
            if c["stage"] == "runtime_probe" and c["status"] == "fail"
        ]
        assert any("result size exceeds" in c["detail"] for c in probe_fails)

    def test_result_size_within_budget_passes(self, v4_env):
        v4_env.build(result_size_policy="inline_small")
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID)
        assert report["certified"] is True
        size_checks = [
            c for c in report["checks"]
            if c["stage"] == "runtime_probe" and "result within" in c["detail"]
        ]
        assert size_checks and all(c["status"] == "pass" for c in size_checks)

    def test_non_dict_result_with_expect_key_fails(self):
        """P2-4a：expect_key 声明了但结果非 JSON object → 如实失败，
        禁止 None==None 的空洞性通过。"""
        from types import SimpleNamespace

        from app.extensions_platform.capability_certification import (
            _run_probe_by_registered_name,
        )
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register("certv4_list_tool", "returns a list", lambda **kw: [1, 2, 3])
        host = SimpleNamespace(_tool_registry=registry)
        probe = SimpleNamespace(
            args={}, replay=False, expect_key="area", expect_value=6.0, tolerance=1e-9,
        )
        checks = _run_probe_by_registered_name(
            host, "certv4_list_tool", probe, "runtime_probe", "tool:x",
        )
        assert any(
            c["status"] == "fail" and "not a JSON object" in c["detail"]
            for c in checks
        )

    def test_async_tool_probe_runs_without_loop_and_refuses_inside_loop(self):
        """P2-4b：async 工具在无运行循环时经 asyncio.run 真实执行；循环内
        拒绝（不伪造执行）。"""
        import asyncio

        from app.extensions_platform.capability_certification import _probe_callable

        async def _tool(**kwargs):
            return {"area": kwargs["w"] * kwargs["h"]}

        result, err = _probe_callable(_tool, {"w": 3, "h": 2})
        assert err == "" and result == {"area": 6}

        async def _inside_loop():
            result2, err2 = _probe_callable(_tool, {"w": 3, "h": 2})
            assert result2 is None
            assert "running" in err2

        asyncio.run(_inside_loop())

    def test_defensive_deactivate_on_stage_exception(self, v4_env, monkeypatch):
        """P2-6：阶段异常不得把认证中的激活态泄漏到 gate-ON 进程。"""
        from app.extensions_platform import capability_certification as cc
        from app.extensions_platform.host import ExtensionState

        v4_env.build()
        host, _ = v4_env.make_host()

        def _boom(*args, **kwargs):
            raise RuntimeError("stage explosion")

        monkeypatch.setattr(cc, "_stage_runtime_probe", _boom)
        with pytest.raises(RuntimeError):
            run_pack_certification(host, EXTENSION_ID)
        monkeypatch.undo()
        record = host.get_record(EXTENSION_ID)
        assert record.state not in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
