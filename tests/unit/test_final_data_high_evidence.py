"""Final exact evidence for formerly pending High findings and S52-005."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import asyncio
import io

import pytest


@pytest.mark.parametrize('field', ['raster_path', 'raster_path_b', 'out_path'])
def test_1789_raster_operator_gates_every_path_before_io(tmp_path, monkeypatch, field):
    from app.services.geocompute.ops import _op_raster_window_operation
    from app.services.data_fabric.security import DataFabricSecurityError
    own = tmp_path / 'owner'
    own.mkdir()
    source = own / 'source.tif'
    source.touch()
    foreign = tmp_path / 'foreign.tif'
    foreign.touch()
    monkeypatch.setattr('app.services.scoped_raster_paths.scoped_raster_roots', lambda **_: [own])
    calls = []
    def no_calculation(**kwargs):
        calls.append(kwargs)
        raise AssertionError('untrusted paths reached IO')
    monkeypatch.setattr('app.lib.geo_analysis.raster_math.raster_calculator', no_calculation)
    params = {'raster_path': str(source), 'expression': 'A + B', field: str(foreign)}
    node = SimpleNamespace(node_id='raster', operation='raster_calculator', parameters=params, inputs=[])
    with pytest.raises(DataFabricSecurityError):
        _op_raster_window_operation(SimpleNamespace(session_id='owner'), node, {})
    assert calls == []


@pytest.mark.asyncio
async def test_1790_secondary_uri_and_metadata_entrypoints_share_owner_gate(tmp_path):
    from app.services.modelops.service import ModelOpsService
    from app.services.modelops.config import ModelOpsSettings
    from app.services.modelops.engine import InferenceRequest
    from app.lib.modelops.errors import ModelOpsError
    own, other = tmp_path / 'own', tmp_path / 'other'
    own.mkdir()
    other.mkdir()
    primary, secondary = own / 'a.tif', other / 'b.tif'
    primary.touch()
    secondary.touch()
    service = ModelOpsService(ModelOpsSettings(registry_dir=tmp_path / 'registry'))
    request = InferenceRequest(model_id='tiny-promptable-seg', source_uri=str(primary),
                               source_uri_b=str(secondary), owner_scope={'session_id': 'own'})
    with pytest.raises(ModelOpsError, match='data directory'):
        service.run_inference(request, allowed_roots=[own])
    with pytest.raises(ModelOpsError, match='data directory'):
        await service.run_inference_async(request, allowed_roots=[own])
    for method in [service.check_compatibility, service.estimate_resources]:
        with pytest.raises(ModelOpsError, match='data directory'):
            method('tiny-promptable-seg', str(secondary), allowed_roots=[own])
    assert primary.exists() and secondary.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['path', 'raster_path', 'file'])
async def test_1792_temporal_rejects_foreign_sources_before_analysis(tmp_path, monkeypatch, key):
    from app.services.temporal.engine import TemporalEngine
    from app.services.data_fabric.security import DataFabricSecurityError
    own = tmp_path / 'owner'
    own.mkdir()
    foreign = tmp_path / 'other.tif'
    foreign.touch()
    monkeypatch.setattr('app.services.scoped_raster_paths.scoped_raster_roots', lambda **_: [own])
    calls = []
    def analyze(**kwargs):
        calls.append(kwargs)
    engine = TemporalEngine()
    monkeypatch.setattr(engine, 'analyze_raster_series', analyze)
    with pytest.raises(DataFabricSecurityError):
        await engine.execute_temporal_raster([{key: str(foreign)}], session_id='owner')
    assert calls == []


def test_1804_isochrone_connects_shared_interior_vertices():
    from app.lib.geo_analysis.network import calculate_isochrones
    network = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': coords}, 'properties': {}}
        for coords in [[[116, 40], [116.01, 40], [116.02, 40]],
                       [[116.01, 39.99], [116.01, 40], [116.01, 40.01]]]]}
    facility = {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [116.01, 40]}, 'properties': {}}]}
    result = calculate_isochrones(network, facility, 30, 'walking')
    assert result.success, result.summary
    assert result.data['features'][0]['properties']['reachable_edges_count'] >= 4
    assert result.data['features'][0]['properties']['reachable_nodes_count'] >= 5


@pytest.mark.asyncio
async def test_s52005_oversized_response_fails_pending_future_immediately(monkeypatch):
    from app.services.chat import pi_rpc_client
    monkeypatch.setattr(pi_rpc_client, 'MAX_STDOUT_LINE_BYTES', 64)
    client = pi_rpc_client.PiRpcClient()
    future = asyncio.get_running_loop().create_future()
    client._pending_requests['pending-request'] = future
    process = MagicMock()
    process.stdout = io.BytesIO(b'x' * 100)
    process.stderr = io.BytesIO()
    process.poll.return_value = 1
    client._process = process
    await asyncio.wait_for(client._read_responses(), timeout=1)
    assert future.done() and 'byte budget' in str(future.exception())
    process.terminate.assert_called_once()


@pytest.mark.parametrize('aggregate, output_field, expected', [
    ({'func': 'sum', 'field': 'pop'}, 'sum_pop', 7),
    ({'func': 'count'}, 'count', 1),
])
def test_s59001_filtered_sum_spatial_join_never_uses_count_shortcut(aggregate, output_field, expected):
    from app.schemas.data_fabric_schema import QueryResult
    from app.services.data_fabric.query.federation import FederatedExecutor, FederatedQueryRequest
    observed = []
    class Adapter:
        def server_spatial_join(self, *args, **kwargs):
            raise AssertionError('filtered sum cannot use count-only fast path')
        def query(self, dataset, spec):
            observed.append((dataset, spec))
            if dataset == 'left':
                features = [{'type': 'Feature', 'properties': {'pop': 7},
                    'geometry': {'type': 'Point', 'coordinates': [.5, .5]}}]
            else:
                features = [{'type': 'Feature', 'properties': {'grp': 'A'}, 'geometry': {
                    'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}]
            return QueryResult(dataset_id=dataset, features=features, total_count=1, has_more=False)
    adapter = Adapter()
    request = FederatedQueryRequest('same', 'left', 'same', 'right', spatial_op='within',
        left_where='pop > 5', right_where="grp = 'A'", bbox=[0, 0, 1, 1],
        aggregates=[aggregate], group_by_right=['grp'])
    result = FederatedExecutor(lambda _: adapter).execute(request)
    assert result['strategy'].startswith('local')
    assert result['rows'][0][output_field] == expected
    assert any(spec.where == 'pop > 5' and spec.bbox == [0, 0, 1, 1] for _, spec in observed)
    assert any(spec.where == "grp = 'A'" for _, spec in observed)


@pytest.mark.parametrize('key', ['as', 'alias'])
def test_s59001_unknown_alias_rejected_before_any_adapter_io(key):
    from app.services.data_fabric.query.federation import FederatedExecutor, FederatedQueryRequest, FederatedQueryError
    called = []
    def adapter_factory(_):
        called.append(True)
        raise AssertionError('invalid aggregation reached adapter IO')
    with pytest.raises(FederatedQueryError, match='invalid aggregate'):
        FederatedExecutor(adapter_factory).execute(FederatedQueryRequest(
            'same', 'left', 'same', 'right', spatial_op='within',
            aggregates=[{'func': 'count', key: 'custom_count'}], group_by_right=['grp']))
    assert called == []
