"""权限模型与信任裁决测试（ADR-0104 Wave 10-11）。"""

from __future__ import annotations

import pytest

from app.extensions_platform.diagnostics import DiagnosticCode, ExtensionPlatformError
from app.extensions_platform.permissions import (
    ALL_PERMISSIONS,
    Permission,
    PermissionGrantSet,
    grants_for,
    parse_grants_config,
    validate_declared_permissions,
)
from app.extensions_platform.trust import TrustLevel, resolve_trust


class TestPermissionModel:
    def test_vocabulary_is_fixed(self):
        assert ALL_PERMISSIONS == frozenset(
            {
                "network", "filesystem_read", "filesystem_write",
                "project_artifact_read", "project_artifact_write",
                "external_process", "database", "model_provider",
                "destructive_action",
            }
        )

    def test_grant_set_require_raises_typed(self):
        grants = PermissionGrantSet(extension_id="acme.x", granted=frozenset({"network"}))
        grants.require(Permission.NETWORK)
        with pytest.raises(ExtensionPlatformError) as exc_info:
            grants.require(Permission.FILESYSTEM_WRITE)
        assert exc_info.value.diagnostic.code is DiagnosticCode.PERMISSION_NOT_GRANTED
        assert exc_info.value.diagnostic.extension_id == "acme.x"

    def test_intersect_only_narrows(self):
        wide = PermissionGrantSet("a", frozenset({"network", "database"}))
        narrow = PermissionGrantSet("a", frozenset({"network"}))
        assert wide.intersect(narrow).granted == frozenset({"network"})
        assert wide.intersect(PermissionGrantSet("a", frozenset())).granted == frozenset()

    def test_parse_grants_config(self):
        grants = parse_grants_config("acme.x:network,database; acme.y: filesystem_read")
        assert grants["acme.x"] == frozenset({"network", "database"})
        assert grants["acme.y"] == frozenset({"filesystem_read"})
        assert grants_for("acme.z", grants).granted == frozenset()

    def test_parse_grants_config_rejects_unknown_permission(self):
        with pytest.raises(ExtensionPlatformError) as exc_info:
            parse_grants_config("acme.x:become_admin")
        assert exc_info.value.diagnostic.code is DiagnosticCode.PERMISSION_DECLARATION_INVALID

    def test_parse_grants_config_rejects_missing_id(self):
        with pytest.raises(ExtensionPlatformError):
            parse_grants_config(":network")

    def test_validate_declared_permissions_flags_unknown_and_dupe(self):
        diagnostics = validate_declared_permissions("acme.x", ["network", "network", "nope"])
        codes = [d.code for d in diagnostics]
        assert DiagnosticCode.PERMISSION_DECLARATION_INVALID in codes
        severities = sorted(d.severity.value for d in diagnostics)
        assert "error" in severities and "warning" in severities


class TestTrustResolution:
    def test_blocklist_beats_allowlist(self):
        trust = resolve_trust(
            "acme.x",
            allowlist=frozenset({"acme.x"}),
            blocklist=frozenset({"acme.x"}),
        )
        assert trust is TrustLevel.BLOCKED

    def test_allowlist_grants_trusted_extension(self):
        assert resolve_trust("acme.x", frozenset({"acme.x"}), frozenset()) is TrustLevel.TRUSTED_EXTENSION

    def test_builtin_ids(self):
        assert resolve_trust(
            "webgis.samples", frozenset(), frozenset(), builtin_ids=frozenset({"webgis.samples"})
        ) is TrustLevel.TRUSTED_BUILTIN

    def test_default_is_local_untrusted(self):
        assert resolve_trust("acme.x", frozenset(), frozenset()) is TrustLevel.LOCAL_UNTRUSTED
