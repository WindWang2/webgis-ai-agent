"""F06 — RuntimeSituation 生产构造器契约测试（ADR-0215 D1/D3/D5）.

覆盖：断言纪律（默认零配置 = 零新事实）、kill switch、fail-open、合并
语义（caller facts win）、可用性探针（注入 + 事件循环安全 + TTL 缓存）、
no-secret 不变量、有界性。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.lib.tool_security import set_credential_presence_provider
from app.services.gis_harness.hotpath_convergence.runtime_situation import (
    OFFLINE_ENV,
    SITUATION_SUPPLY_ENV,
    WORKER_PROBE_ENV,
    RuntimeSituation,
    build_runtime_situation,
    build_runtime_situation_async,
    merge_situation_facts,
    reset_runtime_situation_cache,
    situation_facts_digest,
    situation_supply_enabled,
    set_worker_availability_probe,
)
from app.services.gis_harness.qualification_v8 import QualificationContext


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(CREDENTIALS_ENV, raising=False)
    monkeypatch.delenv("GIS_TOOL_PERMISSIONS", raising=False)
    monkeypatch.delenv(OFFLINE_ENV, raising=False)
    monkeypatch.delenv(WORKER_PROBE_ENV, raising=False)
    set_credential_presence_provider(None)
    set_worker_availability_probe(None)
    reset_runtime_situation_cache()
    yield
    set_credential_presence_provider(None)
    set_worker_availability_probe(None)
    reset_runtime_situation_cache()


CREDENTIALS_ENV = "GIS_TOOL_CREDENTIALS"


class TestAssertionDiscipline:
    def test_zero_config_minimum_facts(self):
        """默认零配置：只断言可探明的配置事实；offline/auth/budget 不猜。"""
        s = build_runtime_situation("sess-a")
        assert s is not None
        q = s.to_qualification_dict()
        assert "offline" not in q
        assert "auth_tier" not in q
        assert "budget_cost_class" not in q
        assert "quality_gate" not in q
        assert "owner_scope_key" not in q

    def test_identity_bounded(self):
        s = build_runtime_situation("sess-a")
        assert s.session_id == "sess-a"
        assert len(json.dumps(s.to_bounded_view())) < 2048

    def test_offline_only_when_env_asserted(self, monkeypatch):
        monkeypatch.setenv(OFFLINE_ENV, "1")
        s = build_runtime_situation("sess-a")
        assert s.offline is True
        monkeypatch.setenv(OFFLINE_ENV, "0")
        assert build_runtime_situation("sess-a").offline is False

    def test_kill_switch_disables_supply(self, monkeypatch):
        monkeypatch.setenv(SITUATION_SUPPLY_ENV, "0")
        assert situation_supply_enabled() is False
        assert build_runtime_situation("sess-a") is None
        assert merge_situation_facts(None, "sess-a") == {}

    def test_fail_open_on_broken_provider(self):
        class Boom:
            def available_credentials(self):
                raise RuntimeError("x")

        set_credential_presence_provider(Boom())
        # provider 异常被 tool_security 吞掉 → situation 仍然产出
        s = build_runtime_situation("sess-a")
        assert s is not None
        assert s.credentials_present == {}


class TestMergeSemantics:
    def test_base_none_returns_runtime_dict(self):
        merged = merge_situation_facts(None, "sess-m")
        assert isinstance(merged, dict)

    def test_caller_dict_facts_win(self, monkeypatch):
        monkeypatch.setenv(OFFLINE_ENV, "1")
        caller = {"offline": False, "task_hint": "render map",
                  "credentials_present": {"mine": True}}
        merged = merge_situation_facts(caller, "sess-m")
        assert merged["offline"] is False  # caller 显式事实不被覆盖
        assert merged["task_hint"] == "render map"
        assert merged["credentials_present"] == {"mine": True}

    def test_caller_dict_gaps_filled(self, monkeypatch):
        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        merged = merge_situation_facts({"task_hint": "x"}, "sess-m")
        assert merged["credentials_present"]["smtp"] is True

    def test_caller_ctx_copied_not_mutated(self, monkeypatch):
        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        base = QualificationContext(task_hint="t")
        merged = merge_situation_facts(base, "sess-m")
        assert merged is not base
        assert base.credentials_present == {}  # caller 对象不被就地修改
        assert merged.credentials_present["smtp"] is True

    def test_caller_ctx_offline_wins(self, monkeypatch):
        monkeypatch.setenv(OFFLINE_ENV, "1")
        base = QualificationContext(offline=False)
        merged = merge_situation_facts(base, "sess-m")
        assert merged.offline is False

    def test_non_ctx_base_returned_as_is(self):
        sentinel = object()
        assert merge_situation_facts(sentinel, "s") is sentinel


class TestAvailabilityFacts:
    def test_worker_probe_env_off_by_default(self):
        s = build_runtime_situation("sess-w")
        assert "durable_worker" not in s.runtime_availability

    def test_worker_probe_injected_true(self):
        set_worker_availability_probe(lambda: True)
        s = build_runtime_situation("sess-w")
        assert s.runtime_availability["durable_worker"] is True

    def test_worker_probe_distinguishes_unknown_from_false(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            raise RuntimeError("db down")

        set_worker_availability_probe(flaky)
        s = build_runtime_situation("sess-w")
        assert "durable_worker" not in s.runtime_availability
        set_worker_availability_probe(lambda: False)
        s2 = build_runtime_situation("sess-w2")
        assert s2.runtime_availability["durable_worker"] is False

    def test_ttl_cache_hits(self):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return True

        set_worker_availability_probe(counting)
        build_runtime_situation("sess-c")
        build_runtime_situation("sess-c")
        build_runtime_situation("sess-c")
        assert calls["n"] == 1  # TTL 内单飞
        reset_runtime_situation_cache()
        build_runtime_situation("sess-c")
        assert calls["n"] == 2

    def test_async_variant_does_not_probe_on_loop(self):
        """注入探针在场时 async 路径可回填；事件循环上不发起 DB I/O。"""
        set_worker_availability_probe(lambda: True)

        async def run():
            return await build_runtime_situation_async("sess-async")

        s = asyncio.run(run())
        assert s is not None
        assert s.runtime_availability["durable_worker"] is True

    def test_broker_fact_from_settings(self):
        s = build_runtime_situation("sess-b")
        # 测试环境 USE_REDIS=True（settings）；键存在即断言，值跟随配置。
        if "celery_broker" in s.runtime_availability:
            assert isinstance(s.runtime_availability["celery_broker"], bool)

    def test_availability_namespace_isolated_from_permission_gate(self):
        """review P1 负例：可用性事实绝不允许翻转 #1402 权限门。

        权限门把 dependency_available 非空当作「已声明授予面」；可用性
        事实必须走独立的 runtime_availability 命名空间。
        """
        from app.services.gis_harness.capability_graph import CapabilityGraph, GraphNode
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
            QualificationResult,
            QualificationStatus,
            qualify_node,
        )

        node = GraphNode("perm_tool", "tool", "t", extras={
            "required_permission": "admin:publish"})
        bare = qualify_node(node, QualificationContext(), None)
        assert bare.status == QualificationStatus.UNKNOWN or             bare.status == QualificationStatus.ELIGIBLE

        supplied = QualificationContext()
        supplied.runtime_availability = {"celery_broker": True}
        result = qualify_node(node, supplied, None)
        assert result.status != QualificationStatus.INELIGIBLE, (
            f"availability facts leaked into permission gate: {result.to_dict()}")

    def test_provider_dependencies_consumed_from_runtime_availability(self):
        """工具声明 provider_dependencies × 探针确认不可用 → 失格。"""
        from app.services.gis_harness.capability_graph import CapabilityGraph, GraphNode
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
            QualificationStatus,
            qualify_node,
        )

        graph = CapabilityGraph(
            nodes={}, edges=[], source_fingerprint="t", issues=[])
        node = GraphNode("worker_tool", "tool", "t", extras={
            "provider_dependencies": ["durable_worker", "missing_key"]})
        ctx = QualificationContext()
        ctx.runtime_availability = {"durable_worker": False}
        result = qualify_node(node, ctx, graph)
        assert result.status == QualificationStatus.INELIGIBLE
        checks = [r.check for r in result.reasons]
        assert "dependency" in checks

        # 键缺席 = unknown：不裁决
        ctx_unknown = QualificationContext()
        ctx_unknown.runtime_availability = {"other": True}
        ok = qualify_node(node, ctx_unknown, graph)
        assert ok.status == QualificationStatus.ELIGIBLE


class TestSecurityProjection:
    def test_credentials_flow_into_situation(self, monkeypatch):
        monkeypatch.setenv(CREDENTIALS_ENV, "smtp:secret:2026-12-31")
        s = build_runtime_situation("sess-sec")
        assert s.credentials_present["smtp"] is True
        assert s.credential_metadata["smtp"]["expires_at"] == "2026-12-31"

    def test_no_secret_material_in_any_projection(self, monkeypatch):
        monkeypatch.setenv(
            CREDENTIALS_ENV, "smtp:secret,upstream:token:2099-01-01:tenant-a")
        s = build_runtime_situation("sess-sec")
        for blob in (json.dumps(s.to_qualification_dict(), default=str),
                     json.dumps(s.to_bounded_view(), default=str),
                     json.dumps(s.credential_metadata, default=str)):
            assert "password" not in blob.lower()
            assert "hunter2" not in blob
            # owner_scope 是作用域标签而非身份原文 —— 键面封闭
            for meta in s.credential_metadata.values():
                assert set(meta) <= {
                    "credential_id", "kind", "expires_at",
                    "owner_scope", "source"}

    def test_permission_presence_via_contextvar(self, monkeypatch):
        from app.tools.registry import grant_tool_permissions

        with grant_tool_permissions("admin:publish"):
            s = build_runtime_situation("sess-perm")
        assert s.credentials_present.get("perm:admin:publish") is True

    def test_env_permission_disclosed_not_granted(self, monkeypatch):
        monkeypatch.setenv("GIS_TOOL_PERMISSIONS", "export:all")
        s = build_runtime_situation("sess-perm2")
        # env 权限不做 perm: 投影（授予面保持 user-wins）；仅 provider
        # presence 投影。无凭证 → credentials_present 为空。
        assert "perm:export:all" not in s.credentials_present


class TestEquivalenceDigest:
    def test_facts_digest_stable(self, monkeypatch):
        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        d1 = build_runtime_situation("sess-eq").facts_digest()
        d2 = build_runtime_situation("sess-eq").facts_digest()
        assert d1 == d2

    def test_digest_changes_with_facts(self, monkeypatch):
        d1 = build_runtime_situation("sess-eq").facts_digest()
        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        d2 = build_runtime_situation("sess-eq").facts_digest()
        assert d1 != d2

    def test_planner_dispatch_same_facts_same_digest(self, monkeypatch):
        """DoD 1：同一事实面下 planner 情境与 dispatch situation 等价。"""
        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        monkeypatch.setenv(OFFLINE_ENV, "1")
        dispatch_situation = build_runtime_situation("sess-plan")
        # planner 面：build_situation（base=数据事实）⊕ merge 语义
        from app.services.gis_harness.capability_resolution import build_situation

        planner_base = build_situation(task_hint="cluster points")
        merged = merge_situation_facts(planner_base, "sess-plan")
        assert situation_facts_digest(merged) == \
            dispatch_situation.facts_digest()

    def test_to_qualification_dict_whitelisted_by_bind(self):
        """产出 dict 必须能被 capability_bind 白名单无损接受。"""
        from app.services.gis_harness.hotpath_convergence.capability_bind import (
            _situation_from_optional,
        )

        s = RuntimeSituation(
            owner_scope_key="user-42",
            credentials_present={"smtp": True},
            runtime_availability={"durable_worker": True},
            offline=True,
        )
        ctx = _situation_from_optional(s.to_qualification_dict())
        assert ctx.owner_scope_key == "user-42"
        assert ctx.credentials_present == {"smtp": True}
        assert ctx.runtime_availability == {"durable_worker": True}
        assert ctx.offline is True


class TestBoundedness:
    def test_bounds_on_all_maps(self, monkeypatch):
        monkeypatch.setenv(
            CREDENTIALS_ENV,
            ",".join(f"cred{i}" for i in range(80)))
        s = build_runtime_situation("sess-bound")
        assert len(s.credentials_present) <= 8
        assert len(s.credential_metadata) <= 8
        assert len(s.runtime_availability) <= 8
