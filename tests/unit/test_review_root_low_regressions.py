"""Behavioral regressions from the root-owned Low issue verification."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest


def test_call_signatures_include_tail_and_nested_content():
    from app.services.chat.no_progress import canonical_call_signature as signature

    assert signature('buffer', {'bbox': [0, 0, 1, 1, 2]}) != signature('buffer', {'bbox': [0, 0, 1, 1, 3]})
    assert signature('buffer', {'wkt': 'x' * 150 + 'A'}) != signature('buffer', {'wkt': 'x' * 150 + 'B'})
    assert signature('buffer', {'data': {'a': {'b': {'c': {'d': 1}}}}}) != signature('buffer', {'data': {'a': {'b': {'c': {'d': 2}}}}})


def test_proxy_decodes_valid_json_object_and_rejects_nonobjects():
    from app.services.chat.pi_native_surface import resolve_pi_tool_call

    resolved = resolve_pi_tool_call('webgis_execute', {'toolName': 'buffer', 'arguments': '{"distance": 3}'})
    assert resolved.arguments['distance'] == 3
    for arguments in ('[1, 2]', 'bad json', [1, 2]):
        assert resolve_pi_tool_call('webgis_execute', {'toolName': 'buffer', 'arguments': arguments}).kind == 'reject'


def test_pep604_scalar_annotations_use_input_gate():
    from app.services.chat.pi_input_gate import _scalar_only_annotation

    assert _scalar_only_annotation(int | None)
    assert not _scalar_only_annotation(int | list[int])


def test_context_control_marker_is_not_cut_and_fence_updates_accounting():
    from app.services.context_assembly.contract import ContextDomain, bounded_item, content_fingerprint
    from app.services.context_assembly.fence import apply_domain_fence, scrub_item

    marker = '[WEBGIS_ACTIVE_TOOLS:' + 'a' * 3000 + ']'
    item = bounded_item(item_id='control', provider_id='surface', domain=ContextDomain.ACTIVE_TOOLS, content=marker)
    assert item.content == marker
    memory = bounded_item(item_id='memory', provider_id='test', domain=ContextDomain.GIS_MEMORY, content='<data>secret</data>')
    fenced = scrub_item(apply_domain_fence(memory))
    assert fenced.fingerprint == content_fingerprint(fenced.content)
    assert fenced.est_tokens > memory.est_tokens


@pytest.mark.parametrize('cap', range(40))
def test_v6_tiny_byte_caps_remain_hard_caps(cap):
    from app.services.chat.v6_context_blocks import _cap_text

    text, _ = _cap_text('地图内容' * 100, cap)
    assert len(text.encode()) <= cap


def test_payload_cache_invalidated_key_cannot_resurrect_after_epoch_prune():
    from app.services.ref_payload_cache import RefPayloadCache

    cache = RefPayloadCache(max_entries=1)
    old = cache.current_epoch('s', 'old')
    cache.invalidate('s', 'old')
    for index in range(12):
        cache.invalidate('other', str(index))
    assert not cache.put_if_current('s', 'old', {'stale': True}, 10, old)


def test_spatial_index_slow_build_keeps_its_invalidation_tombstone(monkeypatch):
    import app.services.mvt as mvt

    cache = mvt.SpatialIndexCache(max_refs=1)
    started, release = Event(), Event()
    entry = SimpleNamespace(estimated_bytes=1)
    clock = [100.0]
    monkeypatch.setattr(mvt.time, 'monotonic', lambda: clock[0])

    def build():
        started.set()
        assert release.wait(5)
        return entry

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.get_or_build, ('s', 'old'), build)
        assert started.wait(5)
        cache.invalidate_ref('s', 'old')
        for index in range(8):
            cache.invalidate_ref('other', str(index))
        clock[0] = 1000.0
        cache.get_or_build(('other', 'fresh'), lambda: entry)
        release.set()
        with pytest.raises(mvt.RefDataUnavailableError):
            future.result(5)
    assert cache.get(('s', 'old')) is None


def test_session_invalidation_preserves_another_sessions_inflight_index():
    from app.services.mvt import SpatialIndexCache

    cache = SpatialIndexCache()
    started, release = Event(), Event()
    entry = SimpleNamespace(estimated_bytes=1)

    def build():
        started.set()
        assert release.wait(5)
        return entry

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.get_or_build, ('unrelated', 'ref'), build)
        assert started.wait(5)
        cache.invalidate_session('target')
        release.set()
        assert future.result(5) is entry


def test_tier3_approval_is_bound_to_step_arguments():
    from app.services.plan_mode import _CONFIRMATION_FIELD, _confirmation_covers, _confirmation_fingerprint

    plan = {'steps': [{'id': 's1', 'tool': 'delete', 'args': {'target': 'a'}}]}
    plan[_CONFIRMATION_FIELD] = {'approved': True, 'approved_at': __import__('time').time(), 'plan_id': 'p', 'steps': ['s1'], 'args_fingerprint': _confirmation_fingerprint(plan, ['s1'])}
    assert _confirmation_covers(plan, 'p', ['s1'])
    plan['steps'][0]['args']['target'] = 'b'
    assert not _confirmation_covers(plan, 'p', ['s1'])


def test_raster_stretch_reaches_last_palette_stop():
    import numpy as np
    from app.services.raster_stretch import build_raster_stretch_payload, decode_payload_rgba

    payload = build_raster_stretch_payload(np.array([[0, 1.0]]), 'Viridis', clip_percentiles=(0, 100))
    _, _, image = decode_payload_rgba(payload)
    assert image[0, 1].tolist() == [253, 231, 37, 255]


@pytest.mark.parametrize('mode', ['hillshade', 'hillshade_blend'])
def test_hillshade_requires_explicit_pixel_spacing(mode):
    import numpy as np
    from app.services.raster_cartography_converter import build_raster_layer

    dem = np.arange(25, dtype=float).reshape(5, 5)
    for size in (None, 0, -1, float('nan'), float('inf'), True):
        params = {} if size is None else {'cell_size': size}
        _, _, png = build_raster_layer('dem', [0, 0, 1, 1], dem, render_mode=mode, render_params=params)
        assert png is None
    _, _, png = build_raster_layer('dem', [0, 0, 1, 1], dem, render_mode=mode, render_params={'cell_size': 30})
    assert png and png.startswith(b'\x89PNG')


def test_report_recovery_expires_only_orphaned_processing_rows():
    from datetime import datetime, timedelta, timezone
    from app.models.report import Report
    from app.services.report_service import REPORT_STATUS_IN_PROGRESS, recover_interrupted_report

    now = datetime.now(timezone.utc)
    old = Report(status=REPORT_STATUS_IN_PROGRESS, created_at=(now - timedelta(hours=1)).replace(tzinfo=None))
    assert recover_interrupted_report(old, now=now)
    assert old.status == 'failed' and old.error_message
    recent = Report(status=REPORT_STATUS_IN_PROGRESS, created_at=now)
    complete = Report(status='completed', created_at=now - timedelta(hours=1))
    assert not recover_interrupted_report(recent, now=now)
    assert not recover_interrupted_report(complete, now=now)


async def test_report_compile_cannot_raise_server_resource_ceiling(monkeypatch):
    from app.services import report_service
    from app.lib.cartography.data_tiers import TIER_EXPORT_FEATURES

    observed = {}

    def compile_map(*args, **kwargs):
        observed.update(kwargs)
        return 'compiled'

    monkeypatch.setattr(report_service, 'compile_mapspec_to_svg_detailed', compile_map)
    result = await report_service.ReportService()._compile_vector_svg_bounded(
        {'thresholds': {'maxFeatures': 10**9, 'timeoutMs': 10**9}}, timeout_s=10**9)
    assert result == 'compiled'
    assert observed['max_features'] == TIER_EXPORT_FEATURES
    assert observed['timeout_ms'] == 30_000


def test_pi_harness_bounds_result_and_mutation_payloads():
    import json
    from app.lib.harness.pi_agent_harness import MAPSPEC_MUTATION_TOOLS, PiAgentHarness

    harness = PiAgentHarness('retention-session')
    name = next(iter(MAPSPEC_MUTATION_TOOLS))
    huge = {'features': [{'properties': {'text': 'x' * 4000}} for _ in range(5000)]}
    harness.record_tool_call('call', name, huge)
    harness.record_tool_result('call', name, {'data': huge, 'success': True, 'is_compiled': True, 'mapspec_fingerprint': 'fp:1'})
    assert len(json.dumps(harness.tool_results)) < 20_000
    assert len(json.dumps(harness.mapspec_mutations)) < 4096
    assert harness.tool_results[0]['result']['success'] is True
    assert harness.mapspec_mutations[0]['is_valid'] is True
    huge['features'].append({'mutated': True})
    assert len(json.dumps(harness.mapspec_mutations)) < 4096
