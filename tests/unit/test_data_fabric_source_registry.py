"""ads-v1 source registry tests (DS1, ADR-0171): load, validate, hot-reload, bridge."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.data_fabric.source_registry import (
    AuthDecl,
    SourceDefinition,
    SourceRegistryError,
    SourceRegistryService,
    source_registry_service,
)

REPO = Path(__file__).resolve().parents[2]


# ── Real repo declarations ───────────────────────────────────────────────────


def test_repo_sources_load_and_count():
    service = SourceRegistryService().load()
    sources = service.list_sources()
    # DS1 验收：≥12 源条目（3 政务 + 3 本地 + ≥6 公开）
    assert len(sources) >= 12
    gov = service.list_sources(protocol="gov_portal")
    assert {s.source_id for s in gov} >= {"beijing_gov", "shanghai_gov", "guangdong_gov"}
    local = [s for s in sources if s.source_id.startswith("local_")]
    assert len(local) >= 3


def test_gov_adapter_reads_registry(monkeypatch):
    """A2 迁移：GovDataAdapter 的平台清单来自源注册表，不再依赖硬编码。"""
    from app.adapters.gov.gov_data_adapter import GovDataAdapter

    platforms = GovDataAdapter._platforms()
    # 真实注册表里三个政务平台的 ID 与 deprecated 常量键不同——来自注册表
    assert "beijing_gov" in platforms and "beijing" not in platforms
    assert platforms["shanghai_gov"]["base_url"] == "https://data.sh.gov.cn"


def test_gov_adapter_falls_back_on_registry_failure(monkeypatch):
    from app.adapters.gov import gov_data_adapter as mod

    def boom():
        raise RuntimeError("registry down")

    class _BrokenSvc:
        def gov_platforms(self):
            return boom()

    monkeypatch.setattr(mod, "logger", mod.logger)
    import app.services.data_fabric.source_registry as sr

    monkeypatch.setattr(sr, "source_registry_service", _BrokenSvc())
    platforms = mod.GovDataAdapter._platforms()
    assert "beijing" in platforms  # deprecated alias kept until zero-ref cleanup (DS9)


# ── Validation (loud, file-attributed) ───────────────────────────────────────


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


_VALID = {
    "source_id": "test_src",
    "name": "Test Source",
    "protocol": "ogc_api",
    "endpoint": "https://ads-test.example/ogc",
}


def test_duplicate_source_id_is_loud(tmp_path):
    _write(tmp_path, "a.yaml", dict(_VALID))
    _write(tmp_path, "b.yaml", dict(_VALID, name="Dup"))
    service = SourceRegistryService(tmp_path)
    with pytest.raises(SourceRegistryError) as exc:
        service.load()
    assert "duplicate source_id" in str(exc.value)


def test_unknown_protocol_is_loud(tmp_path):
    _write(tmp_path, "a.yaml", dict(_VALID, protocol="carrier_pigeon"))
    with pytest.raises(SourceRegistryError) as exc:
        SourceRegistryService(tmp_path).load()
    assert "unknown protocol" in str(exc.value)


def test_missing_required_field_is_loud(tmp_path):
    _write(tmp_path, "a.yaml", {"source_id": "bad_src", "protocol": "stac"})
    with pytest.raises(SourceRegistryError):
        SourceRegistryService(tmp_path).load()


def test_plaintext_credential_in_options_is_rejected(tmp_path):
    _write(tmp_path, "a.yaml", dict(_VALID, options={"api_key": "sk-live-123"}))
    with pytest.raises(SourceRegistryError) as exc:
        SourceRegistryService(tmp_path).load()
    assert "plaintext" in str(exc.value)


def test_env_ref_credential_is_accepted(tmp_path):
    _write(tmp_path, "a.yaml", dict(_VALID, options={"api_key": "${MY_TOKEN_ENV}"}))
    service = SourceRegistryService(tmp_path).load()
    assert service.get("test_src").options["api_key"] == "${MY_TOKEN_ENV}"


def test_bad_source_id_pattern_rejected(tmp_path):
    _write(tmp_path, "a.yaml", dict(_VALID, source_id="9starts-with-digit"))
    with pytest.raises(SourceRegistryError):
        SourceRegistryService(tmp_path).load()


# ── Hot reload ────────────────────────────────────────────────────────────────


def test_hot_reload_picks_up_new_file(tmp_path):
    service = SourceRegistryService(tmp_path).load()
    assert service.list_sources() == []
    _write(tmp_path, "later.yaml", dict(_VALID))
    assert service.reload_if_changed() is True
    assert [s.source_id for s in service.list_sources()] == ["test_src"]
    # unchanged second call is a no-op
    assert service.reload_if_changed() is False


def test_unknown_get_is_loud():
    service = SourceRegistryService().load()
    with pytest.raises(SourceRegistryError) as exc:
        service.get("no_such_source")
    assert "unknown source_id" in str(exc.value)


# ── Fabric bridge ────────────────────────────────────────────────────────────


def test_to_profile_and_build_adapter_for_fabric_protocol():
    adapter = source_registry_service.build_adapter("planetary_computer")
    assert adapter.profile.source_type == "stac"
    assert adapter.profile.endpoint_url == "https://planetarycomputer.microsoft.com/api/stac/v1"


def test_to_profile_rejects_explorer_only_protocol():
    with pytest.raises(SourceRegistryError):
        source_registry_service.to_profile("beijing_gov")


def test_new_protocols_registered_in_fabric_registry():
    from app.services.data_fabric.registry import get_registry

    supported = get_registry().supported_source_types()
    assert {"geopackage", "local_file", "cog", "stats_api"} <= set(supported)


def test_definition_defaults_are_honest():
    d = SourceDefinition(
        source_id="abc_def",
        name="x",
        protocol="stac",
        auth=AuthDecl(),
    )
    assert d.verified is False  # unverified until proven
    assert d.pushdown.bbox is False
    assert d.quota.requests_per_minute is None
