"""CapabilityRuntimeSnapshot 合成投影对抗测试(H05)。

覆盖:五态词表聚合(best-wins)/ provider 级裁决矩阵 / 有界与确定性 /
fail-open(解析失败单能力降级)/ 指纹无材料。
"""
from __future__ import annotations

import pytest

from app.services.capability_runtime import snapshot as snap_mod
from app.services.capability_runtime.snapshot import (
    MAX_SNAPSHOT_CAPABILITIES,
    STATUS_AVAILABLE,
    STATUS_CREDENTIAL_MISSING,
    STATUS_DEGRADED,
    STATUS_POLICY_DENIED,
    STATUS_UNAVAILABLE,
    CapabilityRuntimeFact,
    ProviderRuntimeFact,
    _aggregate_status,
    _provider_runtime_status,
    build_capability_runtime_snapshot,
)


class _Qual:
    def __init__(self, status: str) -> None:
        self.status = status


class _Cand:
    def __init__(self, kind: str, pid: str, status: str = "eligible") -> None:
        self.kind = kind
        self.id = pid
        self.qualification = _Qual(status)
        self.score = 0.0


class _FakeRegistry:
    """metadata 面:tool → requires_credentials / required_permission。"""

    def __init__(self, meta: dict) -> None:
        self._meta = meta

    def metadata(self, tool_id: str) -> dict:
        return self._meta.get(tool_id, {})


def _patch_resolution(monkeypatch, table: dict):
    """patch capability_status(函数级 import → patch 模块属性生效)。"""
    import app.services.gis_harness.capability_resolution as cr

    def _fake(cap, situation, *, graph=None, session_id=""):
        status, ranked, rejected = table.get(
            cap, ("unknown", [], []))
        return status, ranked, rejected

    monkeypatch.setattr(cr, "capability_status", _fake)


# ── provider 级裁决 ──────────────────────────────────────────────────────


class TestProviderStatusMapping:
    def _dec(self, allowed, reason="ok"):
        from app.lib.capability_policy import CapabilityPolicyDecision

        return CapabilityPolicyDecision(
            allowed=allowed, reason_code=reason,
            error_code="CREDENTIALS_REQUIRED"
            if reason == "credential_missing" else "")

    def test_matrix(self):
        f = _provider_runtime_status("eligible", "closed", self._dec(True))
        assert f == STATUS_AVAILABLE
        assert _provider_runtime_status(
            "degraded", "closed", self._dec(True)) == STATUS_DEGRADED
        assert _provider_runtime_status(
            "eligible", "open", self._dec(True)) == STATUS_UNAVAILABLE
        assert _provider_runtime_status(
            "eligible", "half_open", self._dec(True)) == STATUS_DEGRADED
        assert _provider_runtime_status(
            "eligible", "closed", self._dec(False, "credential_missing")
        ) == STATUS_CREDENTIAL_MISSING
        assert _provider_runtime_status(
            "eligible", "open", self._dec(False, "credential_missing")
        ) == STATUS_CREDENTIAL_MISSING  # 凭证缺失优先披露(可修复归因)
        assert _provider_runtime_status(
            "eligible", "closed", self._dec(False, "permission_denied")
        ) == STATUS_POLICY_DENIED
        assert _provider_runtime_status(
            "eligible", "closed", self._dec(False, "provider_unhealthy")
        ) == STATUS_UNAVAILABLE


class TestAggregate:
    def test_best_wins(self):
        providers = (
            ProviderRuntimeFact(kind="tool", id="a", runtime_status=STATUS_UNAVAILABLE),
            ProviderRuntimeFact(kind="tool", id="b", runtime_status=STATUS_DEGRADED),
            ProviderRuntimeFact(kind="tool", id="c", runtime_status=STATUS_CREDENTIAL_MISSING),
        )
        assert _aggregate_status(providers) == STATUS_DEGRADED

    def test_empty_is_unavailable(self):
        assert _aggregate_status(()) == STATUS_UNAVAILABLE

    def test_all_five_distinguishable(self):
        seen = {
            _aggregate_status((ProviderRuntimeFact(
                kind="tool", id="x", runtime_status=s),))
            for s in (
                STATUS_AVAILABLE, STATUS_DEGRADED, STATUS_UNAVAILABLE,
                STATUS_CREDENTIAL_MISSING, STATUS_POLICY_DENIED,
            )
        }
        assert len(seen) == 5  # DoD:五态可区分


# ── 合成投影 ─────────────────────────────────────────────────────────────


class TestBuildSnapshot:
    def test_no_providers_is_unavailable_with_hint(self, monkeypatch):
        _patch_resolution(monkeypatch, {"cap_lonely": ("unknown", [], [])})
        snap = build_capability_runtime_snapshot(["cap_lonely"])
        fact = snap.capabilities[0]
        assert fact.runtime_status == STATUS_UNAVAILABLE
        assert "no eligible provider" in fact.hints[0]

    def test_credential_missing_discloses_fix(self, monkeypatch):
        _patch_resolution(monkeypatch, {
            "cap_mail": ("eligible", [_Cand("tool", "send_mail")], []),
        })
        registry = _FakeRegistry({"send_mail": {"requires_credentials": ["smtp"]}})
        snap = build_capability_runtime_snapshot(
            ["cap_mail"], registry=registry)
        fact = snap.capabilities[0]
        assert fact.runtime_status == STATUS_CREDENTIAL_MISSING
        assert fact.providers[0].credential_missing == ("smtp",)
        assert "smtp" in fact.hints[0]

    def test_credential_present_is_available(self, monkeypatch):
        _patch_resolution(monkeypatch, {
            "cap_mail": ("eligible", [_Cand("tool", "send_mail")], []),
        })
        registry = _FakeRegistry({"send_mail": {"requires_credentials": ["smtp"]}})
        monkeypatch.setenv("GIS_TOOL_CREDENTIALS", "smtp")
        snap = build_capability_runtime_snapshot(
            ["cap_mail"], registry=registry)
        assert snap.capabilities[0].runtime_status == STATUS_AVAILABLE

    def test_open_breaker_marks_unavailable_and_hint(self, monkeypatch):
        from app.services.capability_runtime.health import (
            ProviderFailureClass,
            get_provider_health_registry,
        )

        reg = get_provider_health_registry()
        for _ in range(3):
            reg.record_failure("tool:flaky_geo", ProviderFailureClass.TIMEOUT)
        _patch_resolution(monkeypatch, {
            "cap_geo": ("eligible", [
                _Cand("tool", "healthy_geo"),
                _Cand("tool", "flaky_geo"),
            ], []),
        })
        snap = build_capability_runtime_snapshot(["cap_geo"])
        fact = snap.capabilities[0]
        by_id = {p.id: p for p in fact.providers}
        assert by_id["flaky_geo"].health_state == "open"
        assert by_id["flaky_geo"].runtime_status == STATUS_UNAVAILABLE
        assert by_id["healthy_geo"].runtime_status == STATUS_AVAILABLE
        assert fact.runtime_status == STATUS_AVAILABLE  # best-wins
        assert fact.best_provider == "tool:healthy_geo"
        assert any("circuit open" in h for h in fact.hints)

    def test_permission_denied_is_policy_denied(self, monkeypatch):
        _patch_resolution(monkeypatch, {
            "cap_admin": ("eligible", [_Cand("tool", "drop_all")], []),
        })
        registry = _FakeRegistry({"drop_all": {"required_permission": "admin"}})
        snap = build_capability_runtime_snapshot(
            ["cap_admin"], registry=registry)
        fact = snap.capabilities[0]
        assert fact.runtime_status == STATUS_POLICY_DENIED
        assert fact.providers[0].policy_reason_code == "permission_denied"

    def test_resolution_failure_fails_open_per_capability(self, monkeypatch):
        import app.services.gis_harness.capability_resolution as cr

        def _boom(cap, situation, *, graph=None, session_id=""):
            raise RuntimeError("graph gone")

        monkeypatch.setattr(cr, "capability_status", _boom)
        snap = build_capability_runtime_snapshot(["cap_broken"])
        fact = snap.capabilities[0]
        assert fact.runtime_status == STATUS_UNAVAILABLE
        assert "resolution_unavailable" in fact.why

    def test_bounded_and_deterministic(self, monkeypatch):
        table = {
            f"cap_{i:02d}": ("eligible", [_Cand("tool", f"t{i}")], [])
            for i in range(40)
        }
        _patch_resolution(monkeypatch, table)
        ids = [f"cap_{i:02d}" for i in range(40)]
        s1 = build_capability_runtime_snapshot(ids)
        s2 = build_capability_runtime_snapshot(ids)
        assert len(s1.capabilities) == MAX_SNAPSHOT_CAPABILITIES
        assert s1.to_dict() == s2.to_dict()
        assert [c.capability_id for c in s1.capabilities] == sorted(
            c.capability_id for c in s1.capabilities)

    def test_fingerprint_no_material(self, monkeypatch):
        """快照只含 presence 指纹;GIS_TOOL_CREDENTIALS 的值面(形似 secret)
        本就被 presence 桥拒收 —— 双保险断言投影无值面。"""
        _patch_resolution(monkeypatch, {})
        monkeypatch.setenv("GIS_TOOL_CREDENTIALS", "smtp:apikey")
        snap = build_capability_runtime_snapshot([])
        payload = str(snap.to_dict())
        assert "apikey" not in payload
        assert len(snap.credential_presence_fingerprint) == 16
