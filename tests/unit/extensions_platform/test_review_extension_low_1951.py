"""Behavioral regressions for extension low-priority security/contract findings."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


def test_unsigned_pack_is_not_certified(v4_env):
    from app.extensions_platform.capability_certification import run_pack_certification
    pack = v4_env.build()
    (pack / "signature.json").unlink()
    host, _ = v4_env.make_host()
    report = run_pack_certification(host, "certv4.pack")
    assert not report["certified"]
    assert any(check["stage"] == "supply_chain" and check["status"] == "fail" for check in report["checks"])


def test_certification_binds_initial_fingerprint(v4_env, monkeypatch):
    import app.extensions_platform.capability_certification as cert
    from app.extensions_platform.discovery import compute_fingerprint
    pack = v4_env.build()
    host, _ = v4_env.make_host()
    original = cert._stage_schema
    initial, _ = compute_fingerprint(pack)
    def mutate(manifest):
        (pack / "changed.txt").write_text("after certification started")
        return original(manifest)
    monkeypatch.setattr(cert, "_stage_schema", mutate)
    report = cert.run_pack_certification(host, "certv4.pack")
    assert report["fingerprint"] == initial
    assert not report["certified"]
    assert any("changed during certification" in check["detail"] for check in report["checks"])


def test_namespace_separator_is_unambiguous_and_purge_is_isolated():
    from app.extensions_platform.loader import module_name_for, purge_modules
    from app.extensions_platform.manifest import GisExtensionManifest
    assert module_name_for("aa_bb", "c", "abcdef") != module_name_for("aa", "bb_c", "abcdef")
    first = GisExtensionManifest.namespaced_tool_name(SimpleNamespace(namespace="aa_bb"), "c")
    second = GisExtensionManifest.namespaced_tool_name(SimpleNamespace(namespace="aa"), "bb_c")
    assert first != second
    one, two = module_name_for("aa_bb", "c", "abcdef"), module_name_for("aa", "bb_c", "abcdef")
    sys.modules[one] = SimpleNamespace()
    sys.modules[two] = SimpleNamespace()
    try:
        purge_modules("aa_bb", "c", "abcdef")
        assert one not in sys.modules and two in sys.modules
    finally:
        sys.modules.pop(one, None)
        sys.modules.pop(two, None)


@pytest.mark.parametrize("worker", [False, True])
def test_failed_sibling_import_does_not_poison_module_cache(tmp_path, worker):
    if worker:
        from app.extensions_platform.worker.context import WorkerContext as Context
    else:
        from app.extensions_platform.context import ExtensionContext as Context
    context = Context.__new__(Context)
    context._entry_module_name = "webgis_ext_review_sibling_fp"
    context._module_dir = tmp_path
    context.extension_id = "review.sibling"
    sibling = tmp_path / "broken.py"
    sibling.write_text("raise RuntimeError('broken import')")
    with pytest.raises(RuntimeError):
        context.load_sibling("broken")
    assert f"{context._entry_module_name}.broken" not in sys.modules
    sibling.write_text("READY = True")
    module = context.load_sibling("broken")
    assert module.READY
    sys.modules.pop(module.__name__, None)


def test_concurrent_revocations_are_merged(tmp_path):
    from app.extensions_platform.trust_store import TrustStore
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"publishers": {}, "revoked": {"packages": []}}))
    stores = [TrustStore.load(path), TrustStore.load(path)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda row: row[1].revoke_package(f"review.pack{row[0]}"), enumerate(stores)))
    assert {item[0] for item in TrustStore.load(path).revoked_packages} == {"review.pack0", "review.pack1"}


def test_binary_suffix_does_not_hide_secret_from_sbom(tmp_path):
    from app.extensions_platform.sbom import _content_files, _scan_secrets
    # The scan itself accepts decoded-with-replacement bytes including binary noise.
    findings = []
    _scan_secrets(findings, "config.bin", b"\xff API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456".decode("utf-8", errors="replace"))
    assert findings
    outside = tmp_path / "secret"
    outside.write_bytes(b"private")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "linked").symlink_to(outside)
    assert _content_files(pack) == []


def test_integer_smoke_expectation_uses_tolerance_and_scale_guard():
    from app.extensions_platform.sdk.algorithm import AlgorithmExtensionSpec, NumericalSmokeCase, run_authoring_checks
    spec = AlgorithmExtensionSpec(id="review", name="review", smoke_cases=[NumericalSmokeCase({}, "value", 2, tolerance=.1)])
    assert run_authoring_checks(spec, lambda: {"value": 2.05}) == []
    spec.scale_guard_features = 1
    spec.smoke_cases = [NumericalSmokeCase({"features": [{}, {}]}, "value", 2)]
    called = []
    result = run_authoring_checks(spec, lambda **kwargs: called.append(True) or {"value": 2})
    assert result and not called


def test_stream_aggregation_has_hard_event_bound():
    from app.extensions_platform.sdk.model import aggregate_stream_events
    with pytest.raises(ValueError, match="budget"):
        aggregate_stream_events(({} for _ in range(4)), max_events=3)


def test_collaboration_hello_document_uses_utf8_byte_limit():
    from app.api.routes.ws_collab import _hello_document
    small = {"title": "小文档"}
    assert _hello_document(small) is small
    assert _hello_document({"text": "汉" * 23000}) is None


@pytest.mark.asyncio
async def test_unchanged_collab_sync_reads_only_revision(monkeypatch):
    from app.api.routes.ws_collab import _read_authoritative_doc
    from app.services.session_data import session_data_manager
    calls = []
    async def get_field(session_id, key):
        calls.append(key)
        return 7
    async def forbidden(*args, **kwargs):
        raise AssertionError("whole-session read is not needed")
    monkeypatch.setattr(session_data_manager, "get_state_field", get_field)
    monkeypatch.setattr(session_data_manager, "get_map_state", forbidden)
    revision, doc = await _read_authoritative_doc("s", known_revision=7)
    assert revision == 7 and doc is None
    assert calls == ["_cartographic_mutation_revision"]


@pytest.mark.asyncio
async def test_changed_collab_sync_reads_fresh_document_without_l1_eviction(monkeypatch):
    from app.api.routes.ws_collab import _read_authoritative_doc
    from app.services.session_data import session_data_manager
    calls = []
    document = {"layout": {"panels": []}}
    async def get_field(session_id, key):
        calls.append(key)
        return {"_cartographic_mutation_revision": 8, "mapspec": {"workbench": document}}.get(key)
    def forbidden(*args, **kwargs):
        raise AssertionError("L1 must remain warm")
    monkeypatch.setattr(session_data_manager, "get_state_field", get_field)
    monkeypatch.setattr(session_data_manager, "invalidate_local_cache", forbidden, raising=False)
    revision, doc = await _read_authoritative_doc("s", known_revision=7)
    assert revision == 8 and doc is document
    assert "mapspec" in calls


def test_strict_catalog_requires_signed_certification_report(v4_env):
    from app.extensions_platform.capability_certification import run_pack_certification
    from app.extensions_platform.pack_catalog import build_pack_catalog
    key = v4_env.tmp_path / "report-hmac.key"
    key.write_text("report-signing-test-key")
    v4_env.build()
    host, _ = v4_env.make_host(certification_trust="strict", certification_key=key)
    assert run_pack_certification(host, "certv4.pack", save=True)["certified"]
    assert build_pack_catalog(host, certified_only=True)["namespaces"] == []
    run_pack_certification(host, "certv4.pack", save=True, sign_key_file=key)
    assert build_pack_catalog(host, certified_only=True)["namespaces"]


def test_cli_archive_excludes_links_and_local_certification(tmp_path):
    import io
    import tarfile
    from app.extensions_platform.cli import _pack_blob_bytes
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "main.py").write_text("READY=True")
    (pack / ".certification.json").write_text("local-only")
    outside = tmp_path / "private"
    outside.write_text("secret")
    (pack / "linked.py").symlink_to(outside)
    with tarfile.open(fileobj=io.BytesIO(_pack_blob_bytes(pack)), mode="r:gz") as archive:
        assert archive.getnames() == ["main.py"]


def test_worker_profile_cache_uses_full_digest_and_reclaims_instances(monkeypatch):
    import app.extensions_platform.worker.context as module
    monkeypatch.setattr(module, "hash", lambda value: 1, raising=False)
    prefix = "same canonical prefix" * 8
    assert module._profile_cache_key("source", {"prefix": prefix, "value": 1}) != module._profile_cache_key("source", {"prefix": prefix, "value": 2})
    context = module.WorkerContext.__new__(module.WorkerContext)
    context.extension_id = "review.cache"
    context._provider_instances = {}
    closed = []
    context._provider_factories = {"source": lambda ctx, profile: SimpleNamespace(close=lambda: closed.append(True))}
    for number in range(130):
        context.get_provider_instance("source", {"value": number})
    assert len(context._provider_instances) == 128 and len(closed) == 2


@pytest.mark.parametrize("state", ["", "missing", "unknown"])
def test_extension_certificate_absence_is_fail_closed(state):
    from app.lib.capability_policy import CapabilityPolicyFact, evaluate_capability_policy
    assert not evaluate_capability_policy(CapabilityPolicyFact(certification_state=state, fail_closed=True)).allowed
