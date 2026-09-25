"""F06 — credential/permission presence bridge 契约测试（ADR-0215 D2）.

覆盖：presence provider 契约、env 解析、注入点、CM 接线（registry 闸生
效）、**no-secret 不变量**、有界性、fail-open。
"""
from __future__ import annotations

import json

import pytest

from app.lib.tool_security import (
    CREDENTIALS_ENV,
    MAX_CREDENTIAL_ENTRIES,
    CredentialPresence,
    EnvCredentialPresenceProvider,
    credentials_present_map,
    presence_fingerprint,
    resolve_credential_presence,
    resolve_granted_permissions,
    set_credential_presence_provider,
)
from app.services.gis_harness.hotpath_convergence.security_supply import (
    SECURITY_SUPPLY_ENV,
    bind_session_credentials,
    security_supply_enabled,
)


@pytest.fixture(autouse=True)
def _clean_provider(monkeypatch):
    monkeypatch.delenv(CREDENTIALS_ENV, raising=False)
    monkeypatch.delenv("GIS_TOOL_PERMISSIONS", raising=False)
    set_credential_presence_provider(None)
    yield
    set_credential_presence_provider(None)


class TestEnvProvider:
    def test_parses_id_kind_expiry(self, monkeypatch):
        monkeypatch.setenv(
            CREDENTIALS_ENV, "smtp:secret:2026-12-31, upstream_tile")
        out = resolve_credential_presence()
        assert set(out) == {"smtp", "upstream_tile"}
        assert out["smtp"].kind == "secret"
        assert out["smtp"].expires_at == "2026-12-31"
        assert out["upstream_tile"].kind == ""

    def test_dedup_and_budget(self):
        raw = ",".join(f"cred{i}" for i in range(MAX_CREDENTIAL_ENTRIES + 20))
        provider = EnvCredentialPresenceProvider(raw)
        assert len(provider.available_credentials()) == MAX_CREDENTIAL_ENTRIES

    def test_empty_env_no_presence(self):
        assert resolve_credential_presence() == {}

    def test_to_dict_never_carries_secret_material(self, monkeypatch):
        monkeypatch.setenv(
            CREDENTIALS_ENV,
            "smtp:secret:2026-12-31:tenant-a,sk-live-abc123,api:password=hunter2")
        out = resolve_credential_presence()
        blob = json.dumps([p.to_dict() for p in out.values()])
        # id 本身是声明面引用；值面不含任何形似 secret 的字段。
        assert "secret_value" not in blob
        assert set(out["smtp"].to_dict()) == {
            "credential_id", "kind", "expires_at", "owner_scope", "source"}


class TestInjectionPoint:
    def test_custom_provider_used(self):
        class Fake:
            def available_credentials(self):
                return {"k8s_sa": CredentialPresence("k8s_sa", kind="token")}

        set_credential_presence_provider(Fake())
        out = resolve_credential_presence()
        assert set(out) == {"k8s_sa"}

    def test_provider_exception_fail_open(self):
        class Boom:
            def available_credentials(self):
                raise RuntimeError("vault down")

        set_credential_presence_provider(Boom())
        assert resolve_credential_presence() == {}

    def test_non_presence_values_dropped(self):
        class Junk:
            def available_credentials(self):
                return {"ok": CredentialPresence("ok"), "bad": "not-a-presence",
                        "": CredentialPresence("")}

        set_credential_presence_provider(Junk())
        assert set(resolve_credential_presence()) == {"ok"}


class TestPermissions:
    def test_env_permissions_presence(self, monkeypatch):
        monkeypatch.setenv("GIS_TOOL_PERMISSIONS", "admin:publish, export:all")
        granted = resolve_granted_permissions()
        assert granted == ("admin:publish", "export:all")


class TestHelpers:
    def test_present_map_sorted_bounded(self):
        presences = {f"c{i}": CredentialPresence(f"c{i}") for i in range(20)}
        mapping = credentials_present_map(presences)
        assert mapping == {k: True for k in sorted(presences)[:8]}
        assert len(mapping) == 8

    def test_fingerprint_stable_and_secret_free(self, monkeypatch):
        monkeypatch.setenv(CREDENTIALS_ENV, "a:secret, b")
        p1 = presence_fingerprint(resolve_credential_presence())
        p2 = presence_fingerprint(resolve_credential_presence())
        assert p1 == p2 and len(p1) == 16


class TestBindSessionCredentials:
    @staticmethod
    def _register_tool(reg, meta, func=None):
        reg._tools["cred_tool"] = func or (lambda: {"success": True})
        reg._metadata["cred_tool"] = dict(meta)

    def test_gate_blocks_without_supply(self, monkeypatch):
        import asyncio

        from app.tools.registry import ToolRegistry

        monkeypatch.delenv(CREDENTIALS_ENV, raising=False)
        reg = ToolRegistry()
        self._register_tool(reg, {"name": "cred_tool", "tier": 1,
                                  "requires_credentials": ["smtp"]})

        async def run():
            return await reg.dispatch("cred_tool", {}, session_id="s")

        result = asyncio.run(run())
        assert result.get("code") == "CREDENTIALS_REQUIRED"

    def test_gate_passes_with_presence_grant(self, monkeypatch):
        import asyncio

        from app.tools.registry import ToolRegistry, present_credentials

        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        reg = ToolRegistry()
        self._register_tool(reg, {"name": "cred_tool", "tier": 1,
                                  "requires_credentials": ["smtp"]})

        async def run():
            with bind_session_credentials("s1"):
                assert "smtp" in present_credentials()
                return await reg.dispatch("cred_tool", {}, session_id="s")

        result = asyncio.run(run())
        assert result.get("success") is True

    def test_contextvar_reset_after_scope(self, monkeypatch):
        from app.tools.registry import present_credentials

        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        with bind_session_credentials("s"):
            assert "smtp" in present_credentials()
        assert "smtp" not in present_credentials()

    def test_body_exception_propagates(self, monkeypatch):
        """授予域绝不吞异常（contextlib throw 纪律）。"""
        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        with pytest.raises(ValueError, match="dispatch body"):
            with bind_session_credentials("s"):
                raise ValueError("dispatch body")

    def test_kill_switch(self, monkeypatch):
        from app.tools.registry import present_credentials

        monkeypatch.setenv(CREDENTIALS_ENV, "smtp")
        monkeypatch.setenv(SECURITY_SUPPLY_ENV, "0")
        assert security_supply_enabled() is False
        with bind_session_credentials("s"):
            assert "smtp" not in present_credentials()

    def test_view_projection_bounded(self, monkeypatch):
        monkeypatch.setenv(
            CREDENTIALS_ENV,
            ",".join(f"c{i}" for i in range(MAX_CREDENTIAL_ENTRIES + 5)))
        with bind_session_credentials("s") as view:
            # provider 构造预算 = MAX_CREDENTIAL_ENTRIES：presence 面有界。
            assert len(view.credential_ids) == MAX_CREDENTIAL_ENTRIES
            assert view.to_bounded_view()["count"] == MAX_CREDENTIAL_ENTRIES


class TestPermissionNotAutoGranted:
    def test_env_permission_not_injected_into_contextvar(self, monkeypatch):
        """权限保持 user-wins：env 声明只做 presence 披露，不自动授予。"""
        from app.tools.registry import granted_permissions

        monkeypatch.setenv("GIS_TOOL_PERMISSIONS", "admin:publish")
        with bind_session_credentials("s"):
            assert "admin:publish" not in granted_permissions()
