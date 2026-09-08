"""GisExtensionManifest 契约测试（ADR-0104 Wave 1）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.extensions_platform.api_version import (
    CORE_API_VERSION,
    MANIFEST_SCHEMA_VERSION,
    check_core_version_window,
    check_extension_api_compatibility,
    parse_version,
)
from app.extensions_platform.manifest import GisExtensionManifest


def _base_manifest(**overrides):
    data = {
        "id": "acme.bbox_tools",
        "name": "bbox_tools",
        "namespace": "acme",
        "version": "1.0.0",
        "entry_point": "main",
        "description": "ACME bbox tools",
        "tools": [{"name": "bbox_area", "description": "Compute bbox area"}],
    }
    data.update(overrides)
    return data


class TestManifestHappyPath:
    def test_minimal_manifest_parses(self):
        manifest = GisExtensionManifest.model_validate(_base_manifest())
        assert manifest.id == "acme.bbox_tools"
        assert manifest.namespace == "acme"
        assert manifest.trust == "local_untrusted"
        assert manifest.declared_type_set() == frozenset({"tools"})

    def test_namespacing_helpers(self):
        manifest = GisExtensionManifest.model_validate(_base_manifest())
        assert manifest.namespaced_tool_name("hello") == "acme_hello"
        assert manifest.namespaced_algorithm_id("kde") == "acme.kde"
        assert manifest.namespaced_source_type("tiles") == "acme_tiles"


class TestManifestFailClosed:
    @pytest.mark.parametrize(
        "field, value",
        [
            ("id", "acme.bbox-tools"),
            ("id", "bbox_tools"),
            ("id", "other.bbox_tools"),
            ("namespace", "Core"),
            ("namespace", "core"),
            ("namespace", "1abc"),
            ("namespace", "a"),
            ("version", "one"),
            ("api_version", "1"),
            ("trust", "core"),
            ("trust", "sandboxed"),
        ],
    )
    def test_invalid_fields_rejected(self, field, value):
        with pytest.raises(ValidationError):
            GisExtensionManifest.model_validate(_base_manifest(**{field: value}))

    def test_unknown_field_rejected(self):
        data = _base_manifest(sneaky_extra=True)
        with pytest.raises(ValidationError):
            GisExtensionManifest.model_validate(data)

    def test_future_schema_version_rejected(self):
        with pytest.raises(ValidationError, match="schema_version"):
            GisExtensionManifest.model_validate(_base_manifest(schema_version=MANIFEST_SCHEMA_VERSION + 1))

    def test_model_provider_type_rejected_in_v1(self):
        with pytest.raises(ValidationError, match="model_provider"):
            GisExtensionManifest.model_validate(_base_manifest(extension_types=["model_provider"]))

    def test_unknown_extension_type_rejected(self):
        with pytest.raises(ValidationError):
            GisExtensionManifest.model_validate(_base_manifest(extension_types=["wallet"]))

    def test_tier3_tool_declaration_rejected(self):
        with pytest.raises(ValidationError):
            GisExtensionManifest.model_validate(
                _base_manifest(tools=[{"name": "nuke", "description": "x", "tier": 3}])
            )

    def test_duplicate_tool_names_rejected(self):
        tools = [
            {"name": "bbox_area", "description": "a"},
            {"name": "bbox_area", "description": "b"},
        ]
        with pytest.raises(ValidationError, match="duplicate"):
            GisExtensionManifest.model_validate(_base_manifest(tools=tools))

    def test_reserved_namespace_list_enforced(self):
        for ns in ("webgis", "pi", "builtin", "vendor"):
            with pytest.raises(ValidationError):
                GisExtensionManifest.model_validate(_base_manifest(id=f"{ns}.x", name="x", namespace=ns))


class TestCompatibility:
    def test_parse_version_variants(self):
        assert parse_version("1.2") == (1, 2, 0)
        assert parse_version("1.2.3") == (1, 2, 3)
        assert parse_version("1.2.3-rc1+build") == (1, 2, 3)
        assert parse_version("v1.2") is None
        assert parse_version("1") is None
        assert parse_version("") is None

    def test_api_compatibility_matrix(self):
        host_major = int(CORE_API_VERSION.split(".")[0])
        host_minor = int(CORE_API_VERSION.split(".")[1])
        assert check_extension_api_compatibility(CORE_API_VERSION).compatible
        assert check_extension_api_compatibility(f"{host_major}.0.0").compatible
        assert not check_extension_api_compatibility(f"{host_major + 1}.0.0").compatible
        assert not check_extension_api_compatibility(f"{host_major - 1}.{host_minor}.0").compatible
        assert not check_extension_api_compatibility(f"{host_major}.{host_minor + 1}.0").compatible
        assert not check_extension_api_compatibility("banana").compatible

    def test_core_version_window(self):
        assert check_core_version_window("0.1.0", None).compatible
        assert check_core_version_window("0.0.9", "0.2.0").compatible
        assert not check_core_version_window("99.0.0", None).compatible
        assert not check_core_version_window("0.1.0", "0.1.3").compatible  # exclusive upper
        assert not check_core_version_window("bad", None).compatible

    def test_window_consistency_checked_in_manifest(self):
        with pytest.raises(ValidationError, match="maximum_core_version"):
            GisExtensionManifest.model_validate(
                _base_manifest(minimum_core_version="1.0.0", maximum_core_version="1.0.0")
            )


class TestDeclarationBounds:
    def test_too_many_items_rejected(self):
        tools = [{"name": f"tool_{i}", "description": "x"} for i in range(200)]
        with pytest.raises(ValidationError, match="limit"):
            GisExtensionManifest.model_validate(_base_manifest(tools=tools))

    def test_oversized_description_rejected(self):
        with pytest.raises(ValidationError):
            GisExtensionManifest.model_validate(_base_manifest(description="x" * 3000))
