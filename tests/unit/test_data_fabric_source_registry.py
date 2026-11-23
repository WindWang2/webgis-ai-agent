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
    """DS9：注册表失败 → fail-loud 空清单（宁缺毋假），硬编码已清零。"""
    from app.adapters.gov import gov_data_adapter as mod

    class _BrokenSvc:
        def gov_platforms(self):
            raise RuntimeError("registry down")

    import app.services.data_fabric.source_registry as sr

    monkeypatch.setattr(sr, "source_registry_service", _BrokenSvc())
    platforms = mod.GovDataAdapter._platforms()
    assert platforms == {}  # no hardcoded revival — loud absence, not faked data


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


def test_failed_reload_keeps_previous_registry(tmp_path):
    """评审 P2：load() 不得先清空再解析 —— 任一坏 YAML 抛错离开时，上一份
    完好注册表必须保持可用（否则每次 get() 都报 known: []，retrieval 侧
    静默降级成零卡片）。"""
    _write(tmp_path, "a.yaml", dict(_VALID))
    service = SourceRegistryService(tmp_path).load()
    assert service.get("test_src").name == "Test Source"
    _write(tmp_path, "b.yaml", dict(_VALID, source_id="broken_src", protocol="carrier_pigeon"))
    with pytest.raises(SourceRegistryError):
        service.load()
    # 旧注册表原样可用：不掏空、不静默跳过
    assert service.get("test_src").name == "Test Source"
    assert [s.source_id for s in service.list_sources()] == ["test_src"]


def test_get_during_reload_never_sees_emptied_registry(tmp_path, monkeypatch):
    """并发：A 线程热重载解析期间，B 线程 get() 绝不读到空注册表。"""
    import threading
    import time as _time

    _write(tmp_path, "a.yaml", dict(_VALID))
    service = SourceRegistryService(tmp_path).load()
    _write(tmp_path, "b.yaml", dict(_VALID, source_id="test_src2", name="Second"))
    in_parse = threading.Event()
    release = threading.Event()
    real_parse = service._parse_file

    def _slow_parse(path):
        if path.name == "b.yaml":
            in_parse.set()
            assert release.wait(10)
        return real_parse(path)

    monkeypatch.setattr(service, "_parse_file", _slow_parse)
    failures = []

    def _reload():
        try:
            service.load()
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)

    def _reader():
        assert in_parse.wait(10)
        # 0.5s 节流窗内 force=False → 不触发重载，直读 _loaded（正是缺陷窗口）
        service._last_reload_check = _time.monotonic()
        for _ in range(20):
            try:
                service.get("test_src")
            except Exception as exc:  # noqa: BLE001
                failures.append(exc)
                return

    t_reload = threading.Thread(target=_reload, daemon=True)
    t_reader = threading.Thread(target=_reader, daemon=True)
    t_reload.start()
    t_reader.start()
    assert in_parse.wait(10)
    release.set()
    t_reload.join(10)
    t_reader.join(10)
    assert not t_reload.is_alive() and not t_reader.is_alive()
    assert failures == []
    assert service.get("test_src").name == "Test Source"


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
