"""Independent PR #1972 scientific correctness regressions (no LLM)."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from app.services.gis_harness.analysis_workflows.contracts import DataRoleBinding, contract_fingerprint
from app.services.gis_harness.analysis_workflows.executor import WorkflowExecutor
from app.services.gis_harness.analysis_workflows.families import build_contract
from app.services.gis_harness.analysis_workflows.state import new_state as _new_state
from app.services.gis_harness.analysis_workflows.evidence import run_final_gate


def new_state(contract):
    return _new_state(contract, contract_fingerprint=contract_fingerprint(contract))


def _fc(n=1):
    return {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {},
        'geometry': {'type': 'Point', 'coordinates': [0, 0]}} for _ in range(n)]}


class Store:
    def __init__(self):
        self.data = {f'ref:{name}': _fc() for name in ['network', 'subject', 'facility', 'boundary']}
        self.revisions = {key: 1 for key in self.data}
        self.serial = 0

    async def get(self, sid, ref):
        return copy.deepcopy(self.data.get(ref))

    async def get_ref_descriptor(self, sid, ref):
        if ref not in self.data:
            return None
        return {'feature_count': len(self.data[ref].get('features', [])), 'content_revision': self.revisions[ref]}

    async def store(self, sid, payload, **kwargs):
        self.serial += 1
        ref = f'ref:out{self.serial}'
        self.data[ref] = copy.deepcopy(payload)
        self.revisions[ref] = 1
        return ref


class Registry:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def has(self, name):
        return True

    async def dispatch(self, name, args, **kwargs):
        self.calls.append((name, args))
        return copy.deepcopy(self.result) if name == 'network_closest_facility' else {'success': True, 'data': _fc()}


def _network_contract():
    return build_contract('network_proximity', workflow_id='network-integrity', bindings={
        name: DataRoleBinding(role=name, ref='ref:' + name)
        for name in ['network', 'subject', 'facility', 'boundary']})


def _routes(n):
    return [{'route_id': str(i), 'total_cost': 1 if i < 50 else 100,
        'geometry': {'type': 'LineString', 'coordinates': [[0, 0], [.001, .001]]},
        'properties': {'padding': 'x' * 300}} for i in range(n)]


async def test_200_routes_transport_preview_never_becomes_full_population_mean():
    from app.tools.network_tools import trim_network_result
    routes = _routes(200)
    assert sum(route['total_cost'] for route in routes) / len(routes) == 75.25
    result = trim_network_result({'status': 'success', 'routes': routes,
        'summary': {'demand_count': 200, 'routes_found': 200, 'unmatched_demand_count': 0}})
    assert len(result['routes']) == 50
    assert sum(route['total_cost'] for route in result['routes']) / 50 == 1
    contract = _network_contract()
    registry = Registry(result)
    state = await WorkflowExecutor(registry=registry, session_data=Store()).execute('s', contract, new_state(contract))
    assert state.steps['nearest_facility'].reason_code == 'AWF_INCOMPLETE_NETWORK_RESULT'
    assert state.status == 'blocked'
    assert not state.envelopes
    assert [name for name, _ in registry.calls] == ['network_closest_facility']


@pytest.mark.parametrize('routes, summary', [
    (_routes(1), {'routes_found': 200}),
    ([{'geometry': {'type': 'LineString', 'coordinate_count': 100}}], {}),
])
async def test_incomplete_route_population_is_blocked_without_transport_marker(routes, summary):
    contract = _network_contract()
    registry = Registry({'status': 'success', 'routes': routes, 'summary': summary})
    state = await WorkflowExecutor(registry=registry, session_data=Store()).execute(
        's', contract, new_state(contract))
    assert state.steps['nearest_facility'].reason_code == 'AWF_INCOMPLETE_NETWORK_RESULT'
    assert not state.envelopes


async def test_unmatched_demand_survives_envelope_and_final_gate_as_partial():
    from app.services.gis_harness.analysis_workflows.product_gate import assess_refs_eligibility
    contract = _network_contract()
    registry = Registry({'status': 'success', 'routes': _routes(1), 'summary': {
        'demand_count': 200, 'routes_found': 1, 'unmatched_demand_count': 199,
        'unmatched_demand_ids': [str(i) for i in range(199)]}})
    state = await WorkflowExecutor(registry=registry, session_data=Store()).execute('s', contract, new_state(contract))
    facts = state.envelopes['nearest_facility'].network
    assert facts['unmatched_demand_count'] == 199
    assert facts['population_scope'] == 'matched_demand_only'
    assert len(facts['unmatched_demand_ids']) == 16 and facts['unmatched_ids_truncated']
    assert state.status == 'partial'
    gate = run_final_gate(state)
    assert any(f['code'] == 'AWF_NETWORK_UNMATCHED_DEMAND' for f in gate['findings'])
    assert gate['disclosures']
    assert not assess_refs_eligibility({'w': state}, [state.envelopes['accessibility_summary'].ref])[0]['eligible']


def test_optional_resample_is_opt_in_and_rewires_native_raster():
    contract = build_contract('raster_zonal_summary', workflow_id='native')
    assert [step.step_id for step in contract.steps] == ['zonal_stats']
    assert contract.steps[0].role_inputs['raster_path'] == 'role:subject'
    explicit = build_contract('raster_zonal_summary', workflow_id='metric', params_overrides={
        'resample': {'target_resolution': 30}})
    assert explicit.steps[0].step_params['resolution_unit'] == 'crs'
    assert explicit.steps[0].unit_contract == {}
    omitted = build_contract('raster_zonal_summary', workflow_id='omit', include_optional_steps=False,
        params_overrides={'resample': {'target_resolution': 30}})
    assert [step.step_id for step in omitted.steps] == ['zonal_stats']


@pytest.mark.parametrize('resolution', [0, -1, float('inf'), float('nan'), True])
def test_invalid_explicit_resolution_is_not_silently_ignored(resolution):
    with pytest.raises(ValueError):
        build_contract('raster_zonal_summary', workflow_id='invalid', params_overrides={
            'resample': {'target_resolution': resolution}})


def _write(path, value=1, crs='EPSG:32631', pixel=30):
    import rasterio
    from rasterio.transform import from_origin
    with rasterio.open(path, 'w', driver='GTiff', height=10, width=10, count=1,
            dtype='float32', crs=crs, transform=from_origin(0, 10 * pixel, pixel, pixel)) as dst:
        dst.write(np.full((10, 10), value, dtype='float32'), 1)


def test_explicit_resolution_uses_target_crs_units_not_legacy_guess(tmp_path, monkeypatch):
    import rasterio
    from app.lib import artifact_cache
    from app.lib.geo_analysis.raster_math import resample_raster
    monkeypatch.setattr(artifact_cache, 'ARTIFACT_DIR', str(tmp_path / 'artifacts'))
    metric = tmp_path / 'metric.tif'
    _write(metric)
    result = resample_raster(str(metric), 30, resolution_unit='crs')
    assert result['new_shape'] == [10, 10] and result['resolution_unit'] == 'crs'
    geographic = tmp_path / 'geographic.tif'
    _write(geographic, crs='EPSG:4326', pixel=.004)
    explicit = resample_raster(str(geographic), 2, resolution_unit='crs')
    with rasterio.open(explicit['output_path']) as dataset:
        assert dataset.width == dataset.height == 1
        assert dataset.transform.a == 2


def test_projected_point_fc_is_reprojected_before_unpacking():
    from pyproj.exceptions import CRSError
    from app.tools.network_tools import _point_items_wgs84
    layer = _fc()
    layer['crs'] = {'type': 'name', 'properties': {'name': 'EPSG:3857'}}
    layer['features'][0]['geometry']['coordinates'] = [50, 50]
    original = copy.deepcopy(layer)
    point = _point_items_wgs84(layer)[0]['geometry']['coordinates']
    assert point[0] == pytest.approx(.000449157642, abs=1e-10)
    assert point[1] == pytest.approx(.000449157642, abs=1e-10)
    assert layer == original
    layer['crs'] = {'properties': {'name': 'invalid-projection'}}
    with pytest.raises(CRSError):
        _point_items_wgs84(layer)


async def test_network_tool_honors_projected_fc_for_both_endpoint_roles(monkeypatch):
    from app.services.network.engine import NetworkGraphEngine
    from app.services.network.models import NetworkAnalysisResult
    from app.tools.network_tools import register_network_tools
    from app.tools.registry import ToolRegistry
    seen = []

    async def solve(self, **kwargs):
        seen.append(kwargs)
        return NetworkAnalysisResult(analysis_type='closest_facility', status='success')

    monkeypatch.setattr(NetworkGraphEngine, 'solve_closest_facility', solve)
    registry = ToolRegistry()
    register_network_tools(registry)
    layer = _fc()
    layer['crs'] = {'type': 'name', 'properties': {'name': 'EPSG:3857'}}
    layer['features'][0]['geometry']['coordinates'] = [50, 50]
    result = await registry.dispatch('network_closest_facility', {
        'network': _fc(), 'incidents': layer, 'facilities': layer}, session_id='projected-points')
    assert result['status'] == 'success', result
    for role in ['incidents', 'facilities']:
        assert seen[0][role][0]['geometry']['coordinates'][0] == pytest.approx(.000449157642)
    assert layer['features'][0]['geometry']['coordinates'] == [50, 50]


async def test_same_path_raster_overwrite_invalidates_real_workflow_reuse(tmp_path, monkeypatch):
    import rasterio
    monkeypatch.chdir(tmp_path)
    Path('data').mkdir()
    path = (tmp_path / 'data' / 'same.tif')
    _write(path)
    store = Store()
    contract = build_contract('raster_zonal_summary', workflow_id='same-path', bindings={
        'subject': DataRoleBinding(role='subject', role_kind='raster', ref=str(path)),
        'boundary': DataRoleBinding(role='boundary', ref='ref:boundary')})
    registry = Registry({})
    means = []
    async def dispatch(name, args, **kwargs):
        with rasterio.open(args['raster_path']) as dataset:
            means.append(float(dataset.read(1).mean()))
        return {'success': True, 'data': _fc()}
    registry.dispatch = dispatch
    executor = WorkflowExecutor(registry=registry, session_data=store)
    state = await executor.execute('s', contract, new_state(contract))
    first = state.steps['zonal_stats'].fingerprint
    await executor.execute('s', contract, state)
    assert means == [1]  # verified unchanged file really can reuse
    _write(path, value=7)
    await executor.execute('s', contract, state)
    assert means == [1, 7] and state.steps['zonal_stats'].fingerprint != first


async def test_same_executor_reloads_vector_role_revision_after_overwrite():
    contract = build_contract('admin_facility_distribution', workflow_id='vector-live', bindings={
        'subject': DataRoleBinding(role='subject', ref='ref:subject'),
        'boundary': DataRoleBinding(role='boundary', ref='ref:boundary')})
    contract.steps = contract.steps[:1]
    registry, store = Registry({}), Store()
    executor = WorkflowExecutor(registry=registry, session_data=store)
    state = await executor.execute('s', contract, new_state(contract))
    store.data['ref:subject'] = _fc(2)
    store.revisions['ref:subject'] = 2
    await executor.execute('s', contract, state)
    assert len(registry.calls) == 2
    assert len(registry.calls[-1][1]['points']['features']) == 2


async def test_public_raster_recompute_reuses_unchanged_and_detects_same_path_write(tmp_path, monkeypatch):
    """Public plan/run/recompute share the executor's live raster identity."""
    import rasterio
    from types import SimpleNamespace
    from app import agent_pi_bridge
    from app.services.session_data import session_data_manager
    from app.tools.analysis_workflow_tools import register_analysis_workflow_tools
    from app.tools.registry import ToolRegistry

    monkeypatch.chdir(tmp_path)
    Path('data').mkdir()
    path = tmp_path / 'data' / 'live.tif'
    _write(path)
    session, workflow = 'pr1972-public-raster', 'public-live-raster'
    boundary = await session_data_manager.store(session, _fc(), prefix='geojson')
    registry = ToolRegistry()
    register_analysis_workflow_tools(registry)
    monkeypatch.setattr(agent_pi_bridge, 'try_get_tool_registry', lambda: registry)
    means = []

    class Service:
        async def dispatch(self, call, sid, completed):
            assert call['function']['name'] == 'zonal_stats'
            with rasterio.open(call['function']['arguments']['raster_path']) as dataset:
                means.append(float(dataset.read(1).mean()))
            return SimpleNamespace(status='success', raw_result={'success': True, 'data': _fc()})

    monkeypatch.setattr(agent_pi_bridge, '_get_shared_dispatch_service', lambda: Service())
    planned = await registry.dispatch('webgis_analysis_workflow_plan', {
        'family': 'raster_zonal_summary', 'workflow_id': workflow, 'session_id': session,
        'role_refs': json.dumps({'subject': {'ref': str(path), 'role_kind': 'raster'},
            'boundary': boundary}),
        'params': json.dumps({'resample': {'target_resolution': 30}}),
        'include_optional_steps': False,
    }, session_id=session)
    assert planned['success'], planned
    assert [step['step_id'] for step in planned['steps']] == ['zonal_stats']
    run = await registry.dispatch('webgis_analysis_workflow_run', {
        'workflow_id': workflow, 'session_id': session}, session_id=session)
    assert run['status'] == 'satisfied', run
    unchanged = await registry.dispatch('webgis_analysis_workflow_recompute', {
        'workflow_id': workflow, 'session_id': session}, session_id=session)
    assert unchanged['status'] == 'satisfied', unchanged
    assert unchanged['recomputed_steps'] == [] and means == [1]
    _write(path, value=7)
    changed = await registry.dispatch('webgis_analysis_workflow_recompute', {
        'workflow_id': workflow, 'session_id': session}, session_id=session)
    assert changed['status'] == 'satisfied', changed
    assert changed['recomputed_steps'] == ['zonal_stats'] and means == [1, 7]


async def test_public_recompute_preserves_optional_step_exclusion(monkeypatch):
    from types import SimpleNamespace
    from app import agent_pi_bridge
    from app.services.session_data import session_data_manager
    from app.tools.analysis_workflow_tools import register_analysis_workflow_tools
    from app.tools.registry import ToolRegistry
    session, workflow = 'pr1972-optional', 'public-optional'
    ref = await session_data_manager.store(session, _fc(), prefix='geojson')
    registry = ToolRegistry()
    register_analysis_workflow_tools(registry)
    monkeypatch.setattr(agent_pi_bridge, 'try_get_tool_registry', lambda: registry)
    calls = []

    class Service:
        async def dispatch(self, call, sid, completed):
            calls.append(call['function']['name'])
            return SimpleNamespace(status='success', raw_result={'success': True, 'data': _fc()})

    monkeypatch.setattr(agent_pi_bridge, '_get_shared_dispatch_service', lambda: Service())
    plan = await registry.dispatch('webgis_analysis_workflow_plan', {
        'family': 'admin_facility_distribution', 'workflow_id': workflow,
        'session_id': session, 'include_optional_steps': False,
        'role_refs': json.dumps({'subject': ref, 'boundary': ref})}, session_id=session)
    assert [step['step_id'] for step in plan['steps']] == ['zones_count', 'area_density']
    run = await registry.dispatch('webgis_analysis_workflow_run', {
        'workflow_id': workflow, 'session_id': session}, session_id=session)
    assert run['status'] == 'satisfied', run
    result = await registry.dispatch('webgis_analysis_workflow_recompute', {
        'workflow_id': workflow, 'session_id': session}, session_id=session)
    assert result['status'] == 'satisfied', result
    assert result['recomputed_steps'] == [] and len(calls) == 2


def test_live_raster_identity_detects_unsampled_middle_write(tmp_path, monkeypatch):
    import os
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.windows import Window
    from app.lib.geo_raster.fingerprint import raster_content_fingerprint_v5
    from app.services.gis_harness.analysis_workflows.runtime_integrity import local_raster_identity
    monkeypatch.chdir(tmp_path)
    Path('data').mkdir()
    path = tmp_path / 'data' / 'middle.tif'
    with rasterio.open(path, 'w', driver='GTiff', height=200, width=300, count=1,
            dtype='float32', crs='EPSG:32631', transform=from_origin(0, 6000, 30, 30)) as dst:
        dst.write(np.ones((200, 300), dtype='float32'), 1)
    sampled = raster_content_fingerprint_v5(path)
    identity = local_raster_identity(str(path))
    stat = path.stat()
    with rasterio.open(path, 'r+') as dst:
        dst.write(np.full((1, 1), 7, dtype='float32'), 1, window=Window(150, 100, 1, 1))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert raster_content_fingerprint_v5(path) == sampled
    assert local_raster_identity(str(path)) != identity


@pytest.mark.parametrize('ref', ['https://example.invalid/raster.tif', '/etc/passwd'])
def test_untrusted_raster_identity_does_not_open_remote_or_outside_sandbox(ref, monkeypatch):
    from app.lib.geo_raster import fingerprint
    from app.services.gis_harness.analysis_workflows.runtime_integrity import local_raster_identity

    def forbidden_read(path):
        pytest.fail('unsafe raster input reached content reader')

    monkeypatch.setattr(fingerprint, 'raster_content_fingerprint_v5', forbidden_read)
    first, second = local_raster_identity(ref), local_raster_identity(ref)
    assert first.startswith('unverifiable:') and first != second
