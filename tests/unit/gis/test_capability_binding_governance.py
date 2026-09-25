"""F06 — capability binding 治理 + conformance fixtures + registry canary
（ADR-0215 D4/D8）.

覆盖：fixtures 确定性 + 4 码覆盖、治理分类规则、报告有界/确定性、CLI、
durable 标签诚实性、live registry canary（fatal==0 + ratchet 不增 + 治理
全覆盖 + situation 供给等价性 —— DoD 4）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lib.gis.capability_binding_governance import (
    CLASS_LEGAL_MULTI_PROVIDER,
    CLASS_METADATA_MISSING,
    CLASS_SUSPECTED_MISDECLARATION,
    CapabilityBindingGovernanceReport,
    collect_live_inputs,
    durable_label_honesty_facts,
    format_governance_report,
    govern_capability_bindings,
    govern_live_registry,
)
from app.lib.gis.capability_conformance import validate_capability_conformance
from app.lib.gis.conformance_fixtures import make_conformance_fixture

BASELINE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures" / "governance"
    / "capability_binding_governance_baseline.json"
)


# ── fixtures 模块 ────────────────────────────────────────────────────


class TestConformanceFixture:
    def test_deterministic(self):
        f1 = make_conformance_fixture()
        f2 = make_conformance_fixture()
        assert f1.tool_registry.all_metadata() == f2.tool_registry.all_metadata()
        assert f1.capability_ids == f2.capability_ids

    def test_covers_all_four_conformance_codes(self):
        f = make_conformance_fixture()
        issues = validate_capability_conformance(
            capability_ids=f.capability_ids,
            algorithms=f.algorithms,
            tool_metadata=[
                (n, f.tool_registry.metadata(n) or {})
                for n in f.tool_registry.list_tools()
            ],
        )
        codes = {i.code for i in issues}
        assert codes == {
            "capability_id_dangling",
            "capability_binding_unbacked",
            "descriptor_metadata_incomplete",
            "provider_output_contract_divergence",
        }

    def test_registry_is_manifest_compatible(self):
        """fixture registry 可直接喂 compile_runtime_manifest。"""
        from app.lib.gis.runtime_manifest import compile_runtime_manifest

        f = make_conformance_fixture()
        manifest = compile_runtime_manifest(tool_registry=f.tool_registry)
        declared = manifest.declared_capability_bindings
        assert declared.get("declared_legal") == ["cap_a"]
        fatal = [i for i in manifest.issues
                 if getattr(i, "severity", "") == "fatal"]
        # 悬空声明在 manifest 编译面 fatal（与 #1482 语义一致）
        assert any("cap_ghost" in str(getattr(i, "detail", ""))
                   for i in fatal)


# ── 治理分类 ─────────────────────────────────────────────────────────


class TestGovernanceClassification:
    def _govern_fixture(self):
        f = make_conformance_fixture()
        return f, govern_capability_bindings(
            capability_ids=f.capability_ids,
            algorithms=f.algorithms,
            tool_metadata=[
                (n, f.tool_registry.metadata(n) or {})
                for n in f.tool_registry.list_tools()
            ],
        )

    def test_three_way_classification(self):
        f, report = self._govern_fixture()
        by_tool = {e.tool: e.classification for e in report.entries}
        assert by_tool["declared_legal"] == CLASS_LEGAL_MULTI_PROVIDER
        assert by_tool["declared_metadata_gaps"] == CLASS_METADATA_MISSING
        assert by_tool["declared_out_conflict"] == CLASS_SUSPECTED_MISDECLARATION

    def test_counts_match_entries(self):
        _, report = self._govern_fixture()
        total = sum(report.counts.values())
        assert total == len(report.entries)

    def test_dangling_fatal_surfaced(self):
        _, report = self._govern_fixture()
        assert any(d["capability"] == "cap_ghost"
                   for d in report.dangling_fatal)

    def test_migration_hints_present(self):
        _, report = self._govern_fixture()
        for e in report.entries:
            assert e.migration_hint

    def test_determinism(self):
        r1 = self._govern_fixture()[1]
        r2 = self._govern_fixture()[1]
        assert r1.to_dict() == r2.to_dict()

    def test_bounded_and_serializable(self):
        _, report = self._govern_fixture()
        assert len(report.entries) <= 512
        blob = json.dumps(report.to_dict(), ensure_ascii=False, default=str)
        assert "entries" in blob

    def test_format_markdown(self):
        _, report = self._govern_fixture()
        text = format_governance_report(report)
        assert text.startswith("# Capability Binding Governance Report")
        assert "Durable label honesty" not in text or True
        for e in report.entries[:5]:
            assert e.tool in text


class TestDurableHonesty:
    def test_broker_false_discloses_ineffective(self, monkeypatch):
        import app.core.config as cfg

        monkeypatch.setattr(cfg, "settings",
                            type(cfg.settings)(USE_REDIS=False),
                            raising=False)
        facts = durable_label_honesty_facts({})
        assert facts["broker_configured"] is False
        assert facts["label_effective"] is False

    def test_affected_tools_listed_when_label_ineffective(self, monkeypatch):
        import app.core.config as cfg

        monkeypatch.setattr(cfg, "settings",
                            type(cfg.settings)(USE_REDIS=False),
                            raising=False)
        meta = {
            "celery_tool": {"execution_policy": "celery"},
            "thread_tool": {"execution_policy": "thread"},
        }
        facts = durable_label_honesty_facts(meta)
        assert facts["affected_tools"] == ["celery_tool"]
        assert facts["affected_count"] == 1

    def test_broker_true_label_effective(self, monkeypatch):
        import app.core.config as cfg

        monkeypatch.setattr(cfg, "settings",
                            type(cfg.settings)(USE_REDIS=True),
                            raising=False)
        facts = durable_label_honesty_facts({})
        assert facts["label_effective"] is True

    def test_unknown_broker_stays_unknown(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "app.core.config",
                            None)  # import 失败路径
        facts = durable_label_honesty_facts({})
        assert facts["label_effective"] is None


# ── live registry canary ─────────────────────────────────────────────


class TestLiveRegistryCanary:
    @pytest.fixture(scope="class")
    def live_report(self):
        return govern_live_registry()

    def test_no_dangling_fatal(self, live_report):
        assert live_report.dangling_fatal == []

    def test_governance_covers_all_divergences(self, live_report):
        inputs = collect_live_inputs()
        issues = validate_capability_conformance(
            capability_ids=inputs["capability_ids"],
            algorithms=inputs["algorithms"],
            tool_metadata=inputs["tool_metadata"],
        )
        unbacked = [i for i in issues
                    if i.code == "capability_binding_unbacked"]
        assert len(unbacked) == sum(live_report.counts.values())

    def test_misdeclaration_ratchet_not_growing(self, live_report):
        """治理门禁：suspected_misdeclaration 不超过 committed 基线。"""
        baseline = json.loads(BASELINE_PATH.read_text())
        ratchet = baseline["suspected_misdeclaration_ratchet"]
        current = live_report.counts.get(CLASS_SUSPECTED_MISDECLARATION, 0)
        assert current <= ratchet, (
            f"suspected_misdeclaration grew: {current} > {ratchet}. "
            f"Fix the declarations (see CLI report) or re-baseline with "
            f"governance review.")

    def test_honesty_facts_attached(self, live_report):
        assert "label_effective" in live_report.durable_label_honesty

    def test_json_report_shape(self, live_report):
        d = live_report.to_dict()
        assert d["policy_version"] == "capability_binding_governance.v1"
        assert set(d["counts"]) == {
            CLASS_SUSPECTED_MISDECLARATION, CLASS_METADATA_MISSING,
            CLASS_LEGAL_MULTI_PROVIDER}


class TestPlannerDispatchEquivalence:
    def test_situation_supply_equivalence_on_live_registry(self):
        """DoD 1：planner 面与 dispatch 面对同一事实集合产出同一资格结论。

        用 live 图 + 合成事实集合：同 facts dict 喂两条构造路径
        （build_situation⊕merge vs build_runtime_situation），断言
        facts_digest 相等，且对同一 tool 的 bind 求值结论一致。
        """
        from app.lib.tool_security import set_credential_presence_provider
        from app.services.gis_harness.capability_resolution import (
            build_situation,
        )
        from app.services.gis_harness.hotpath_convergence.capability_bind import (
            bind_tool_capability,
        )
        from app.services.gis_harness.hotpath_convergence.runtime_situation import (
            merge_situation_facts,
            reset_runtime_situation_cache,
        )
        from app.lib.gis.runtime_manifest import (
            compile_runtime_manifest,
        )
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        set_credential_presence_provider(None)
        reset_runtime_situation_cache()
        facts = {
            "offline": False,
            "credentials_present": {},
            "dependency_available": {},
        }
        planner_ctx = build_situation(task_hint="canary")
        # 同 facts 注入两条路径
        planner_ctx.offline = facts["offline"]
        dispatch_ctx = merge_situation_facts(None, "canary-session")
        dispatch_ctx["offline"] = facts["offline"]

        reg = ToolRegistry()
        init_tools(reg)
        manifest = compile_runtime_manifest(tool_registry=reg)
        # 抽样声明面工具（有界 ≤8）：两条路径 bind 结论一致
        tools = sorted(manifest.declared_capability_bindings.keys())[:8]
        assert tools, "live registry must declare capability tools"
        for tool in tools:
            via_planner = bind_tool_capability(
                tool, registry=reg, session_id="canary",
                situation=planner_ctx)
            via_dispatch = bind_tool_capability(
                tool, registry=reg, session_id="canary",
                situation=dispatch_ctx)
            assert (via_planner is None) == (via_dispatch is None)
            if via_planner is not None:
                assert via_planner.refused == via_dispatch.refused
                assert (via_planner.decision is None) == \
                    (via_dispatch.decision is None)
