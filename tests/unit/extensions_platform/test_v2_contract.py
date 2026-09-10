"""Extension Platform V2 契约测试（ADR-0105 Wave 1）。

覆盖：V2 manifest 字段（execution / model_providers / 依赖版本约束）、
api_version 1.1.0 特性门控、version_constraints 纯函数、settings 桥的
V2 配置解析（fail closed）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.extensions_platform.api_version import (
    CORE_API_VERSION,
    check_extension_api_compatibility,
)
from app.extensions_platform.manifest import GisExtensionManifest
from app.extensions_platform.version_constraints import (
    satisfied,
    validate_constraint_syntax,
)


def _base_manifest(**overrides):
    data = {
        "id": "acme.ml_tools",
        "name": "ml_tools",
        "namespace": "acme",
        "version": "1.0.0",
        "api_version": "1.1.0",
        "entry_point": "main",
        "description": "ACME v2 tools",
        "tools": [{"name": "bbox_area", "description": "Compute bbox area"}],
    }
    data.update(overrides)
    return data


class TestApiVersion11:
    def test_host_is_1_1(self):
        assert CORE_API_VERSION == "1.2.0"  # ADR-0119：V3 additive bump

    def test_1_x_extensions_stay_compatible(self):
        for api in ("1.0.0", "1.0", "1.1.0", "1.1"):
            assert check_extension_api_compatibility(api).compatible, api

    @pytest.mark.parametrize("api", ["2.0.0", "1.3.0", "0.9.0"])  # 1.2.0 已随 V3 兼容
    def test_incompatible_apis_stay_incompatible(self, api):
        assert not check_extension_api_compatibility(api).compatible


class TestExecutionDeclaration:
    def test_default_execution_is_in_process(self):
        manifest = GisExtensionManifest.model_validate(_base_manifest())
        assert manifest.execution is None
        assert manifest.is_worker_mode is False

    def test_worker_mode_parses_with_budgets(self):
        manifest = GisExtensionManifest.model_validate(
            _base_manifest(
                execution={
                    "mode": "worker",
                    "startup_timeout_s": 5,
                    "call_timeout_s": 12.5,
                    "max_memory_mb": 256,
                    "max_cpu_seconds": 10,
                    "max_output_bytes": 4096,
                }
            )
        )
        assert manifest.is_worker_mode is True
        assert manifest.execution.max_memory_mb == 256

    def test_worker_mode_requires_api_floor(self):
        with pytest.raises(ValidationError, match="api_version >= 1.1.0"):
            GisExtensionManifest.model_validate(
                _base_manifest(api_version="1.0.0", execution={"mode": "worker"})
            )

    def test_worker_mode_rejects_class_instance_sections(self):
        with pytest.raises(ValidationError, match="algorithms"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    execution={"mode": "worker"},
                    algorithms=[{"id": "kde", "description": "x"}],
                )
            )

    def test_worker_mode_rejects_data_providers(self):
        with pytest.raises(ValidationError, match="data_providers"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    execution={"mode": "worker"},
                    data_providers=[{"source_type": "tiles", "description": "x"}],
                )
            )

    def test_worker_mode_rejects_external_process_permission(self):
        with pytest.raises(ValidationError, match="external_process"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    execution={"mode": "worker"},
                    permissions=["external_process"],
                )
            )

    @pytest.mark.parametrize(
        "field, value",
        [
            ("mode", "sandbox"),
            ("startup_timeout_s", 0.1),
            ("startup_timeout_s", 999),
            ("call_timeout_s", 0.01),
            ("max_memory_mb", 8),
            ("max_memory_mb", 99999),
            ("max_cpu_seconds", 0),
            ("max_output_bytes", 512),
        ],
    )
    def test_budget_bounds_fail_closed(self, field, value):
        with pytest.raises(ValidationError):
            GisExtensionManifest.model_validate(
                _base_manifest(execution={"mode": "worker", field: value})
            )

    def test_in_process_mode_is_explicitly_allowed(self):
        manifest = GisExtensionManifest.model_validate(
            _base_manifest(execution={"mode": "in_process"})
        )
        assert manifest.is_worker_mode is False


class TestModelProviderDeclaration:
    def test_model_provider_type_supported_at_1_1(self):
        manifest = GisExtensionManifest.model_validate(
            _base_manifest(
                extension_types=["tools", "model_provider"],
                model_providers=[
                    {
                        "id": "landcover",
                        "description": "Land cover segmentation",
                        "capabilities": ["streaming", "cancellation"],
                    }
                ],
            )
        )
        assert "model_provider" in manifest.declared_type_set()

    def test_model_provider_requires_api_floor(self):
        with pytest.raises(ValidationError, match="api_version >= 1.1.0"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    api_version="1.0.0",
                    model_providers=[{"id": "landcover", "description": "x"}],
                )
            )

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValidationError, match="unknown capabilities"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    model_providers=[
                        {"id": "landcover", "description": "x", "capabilities": ["teleport"]}
                    ]
                )
            )

    def test_duplicate_provider_ids_rejected(self):
        with pytest.raises(ValidationError, match="model_providers"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    model_providers=[
                        {"id": "landcover", "description": "x"},
                        {"id": "landcover", "description": "y"},
                    ]
                )
            )

    def test_credentials_ref_shape(self):
        with pytest.raises(ValidationError, match="credentials_ref"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    model_providers=[
                        {"id": "landcover", "description": "x", "credentials_ref": "BAD REF"}
                    ]
                )
            )

    def test_reserved_future_types_still_rejected(self):
        with pytest.raises(ValidationError, match="not supported"):
            GisExtensionManifest.model_validate(
                _base_manifest(extension_types=["marketplace"])
            )


class TestDependencyVersionConstraints:
    def test_valid_constraint_parses(self):
        manifest = GisExtensionManifest.model_validate(
            _base_manifest(
                dependencies=[{"id": "acme.base", "version": ">=1.2,<2.0"}],
            )
        )
        assert manifest.dependencies[0].version == ">=1.2,<2.0"

    @pytest.mark.parametrize(
        "constraint",
        ["", "1.2", "=1.2", ">= 1 two", ">=1.2,<2.0,>=3.0,>=4.0,>=5.0,>=6.0,>=7.0,>=8.0,>=9.0"],
    )
    def test_invalid_constraints_rejected(self, constraint):
        with pytest.raises(ValidationError):
            GisExtensionManifest.model_validate(
                _base_manifest(dependencies=[{"id": "acme.base", "version": constraint}])
            )

    def test_constraint_requires_api_floor(self):
        with pytest.raises(ValidationError, match="api_version >= 1.1.0"):
            GisExtensionManifest.model_validate(
                _base_manifest(
                    api_version="1.0.0",
                    dependencies=[{"id": "acme.base", "version": ">=1.0"}],
                )
            )


class TestVersionConstraintsPureFunctions:
    @pytest.mark.parametrize(
        "version, constraint, expected",
        [
            ("1.2.0", ">=1.2,<2.0", True),
            ("2.0.0", ">=1.2,<2.0", False),
            ("1.1.9", ">=1.2", False),
            ("1.2", "==1.2", True),
            ("1.3", "==1.2", False),
            ("1.3", "!=1.2", True),
            ("1.1", "<=1.2", True),
            ("1.3", "<=1.2", False),
            ("2.0", ">1.2", True),
            ("1.2", ">1.2", False),
        ],
    )
    def test_satisfied_matrix(self, version, constraint, expected):
        assert satisfied(version, constraint) is expected

    def test_invalid_constraint_is_fail_closed_false(self):
        assert satisfied("1.2.0", "not-a-constraint") is False

    def test_syntax_messages_are_readable(self):
        assert "must start with" in (validate_constraint_syntax("1.2") or "")
        assert "semver" in (validate_constraint_syntax(">=banana") or "")
        assert validate_constraint_syntax(">=1.0, <2.0") is None


class TestSettingsBridgeV2:
    def _policy_from_monkeypatched_settings(self, monkeypatch, **overrides):
        from app.extensions_platform.settings_bridge import host_policy_from_settings

        defaults = {
            "EXTENSIONS_DIRS": "",
            "EXTENSIONS_ALLOW": "",
            "EXTENSIONS_BLOCK": "",
            "EXTENSIONS_BUILTIN_IDS": "",
            "EXTENSION_PERMISSION_GRANTS": "",
            "EXTENSION_FEATURE_FLAGS": "{}",
            "EXTENSION_SETTINGS_JSON": "{}",
            "EXTENSIONS_ACTIVATE_UNTRUSTED": False,
            "EXTENSION_SECRETS_JSON": "{}",
            "EXTENSION_NETWORK_ALLOW": "",
            "EXTENSION_TRUSTED_PUBLISHERS": "",
            "EXTENSIONS_TRUST_SIGNED": False,
            "EXTENSIONS_ALLOW_UNSIGNED_DEV": False,
            "EXTENSIONS_MAX_WORKER_CRASHES": 2,
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            monkeypatch.setattr("app.core.config.settings." + key, value, raising=False)
        return host_policy_from_settings()

    def test_secrets_parsed(self, monkeypatch):
        policy = self._policy_from_monkeypatched_settings(
            monkeypatch,
            EXTENSION_SECRETS_JSON='{"acme.ml_tools": {"api_key": "s3cret"}}',
        )
        assert policy.secrets == {"acme.ml_tools": {"api_key": "s3cret"}}

    def test_secrets_reject_non_string_values(self, monkeypatch):
        from app.extensions_platform.diagnostics import ExtensionPlatformError

        with pytest.raises(ExtensionPlatformError, match="must be a string"):
            self._policy_from_monkeypatched_settings(
                monkeypatch, EXTENSION_SECRETS_JSON='{"acme.ml_tools": {"api_key": 42}}'
            )

    def test_network_allow_parsed(self, monkeypatch):
        policy = self._policy_from_monkeypatched_settings(
            monkeypatch,
            EXTENSION_NETWORK_ALLOW="acme.ml_tools:api.example.com,tiles.example.org;other.x:*",
        )
        assert policy.network_allow["acme.ml_tools"] == frozenset(
            {"api.example.com", "tiles.example.org"}
        )
        assert policy.network_allow["other.x"] == frozenset({"*"})

    def test_network_allow_rejects_path_shaped_hosts(self, monkeypatch):
        from app.extensions_platform.diagnostics import ExtensionPlatformError

        with pytest.raises(ExtensionPlatformError, match="plain host"):
            self._policy_from_monkeypatched_settings(
                monkeypatch, EXTENSION_NETWORK_ALLOW="acme.ml_tools:http://evil.example"
            )

    def test_publishers_parsed(self, monkeypatch, tmp_path):
        keyfile = tmp_path / "acme.key"
        keyfile.write_bytes(b"k")
        policy = self._policy_from_monkeypatched_settings(
            monkeypatch, EXTENSION_TRUSTED_PUBLISHERS=f"acme:{keyfile}"
        )
        assert policy.trusted_publishers["acme"] == keyfile

    def test_publishers_reject_bad_shape(self, monkeypatch):
        from app.extensions_platform.diagnostics import ExtensionPlatformError

        with pytest.raises(ExtensionPlatformError, match="key_id:path"):
            self._policy_from_monkeypatched_settings(
                monkeypatch, EXTENSION_TRUSTED_PUBLISHERS="acme-no-path"
            )

    @pytest.mark.parametrize("value", [0, 11, "many"])
    def test_max_worker_crashes_bounds(self, monkeypatch, value):
        from app.extensions_platform.diagnostics import ExtensionPlatformError

        with pytest.raises(ExtensionPlatformError, match="EXTENSIONS_MAX_WORKER_CRASHES"):
            self._policy_from_monkeypatched_settings(
                monkeypatch, EXTENSIONS_MAX_WORKER_CRASHES=value
            )
