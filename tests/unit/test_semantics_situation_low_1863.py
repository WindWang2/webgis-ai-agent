"""Behavioral boundaries for descriptor transactions and situation truth."""
import asyncio
import multiprocessing


from app.lib.gis.dataset_descriptor import GISDatasetDescriptor, TemporalSemantics
from app.services.dataset_semantics.builder import bounded_value_samples
from app.services.dataset_semantics.projections import descriptor_to_dataset_profile
from app.services.dataset_semantics.provenance import TransformationRecord, _append_sync, _load_sync
from app.services.dataset_semantics.reuse import evaluate_reuse
from app.services.dataset_semantics.store import DatasetSemanticStore, DescriptorRecord
from app.services.gis_memory.associative_index import _bbox_cells, _query_cells, parse_validated_ts
from app.services.gis_situation.canvas_affordance import _sanitize_geometry
from app.services.gis_situation.compiler import compile_interaction, compile_map
from app.services.gis_situation.contract import SituationEvidence
from app.services.gis_situation.facts import known
from app.services.gis_situation.projection import _Renderer


def test_generator_first_member_and_string_evidence_bounded():
    seen = []

    def features():
        for i in range(10):
            seen.append(i)
            yield {"properties": {"v": i, "category": f"c{i}"}}

    samples = bounded_value_samples(features(), numeric_fields=["v"], feature_cap=2)
    assert samples == {"v": [0, 1], "category": ["c0", "c1"]}
    assert seen == [0, 1]
    assert bounded_value_samples(iter([{"properties": {"v": 0}}])) == {"v": [0]}


def test_temporal_authority_and_missing_descriptor():
    descriptor = GISDatasetDescriptor(dataset_key="r", temporal=TemporalSemantics(time_field="observed"))
    assert descriptor_to_dataset_profile(descriptor).time_field == "observed"
    assert evaluate_reuse("fp", DescriptorRecord(status="ok")).verdict == "unknown"


def _append_worker(path, value):
    _append_sync(path, TransformationRecord(output_ref=str(value), output_fingerprint="fp"))


def test_cross_process_lineage_writers_preserve_every_record(tmp_path):
    path = tmp_path / "lineage.jsonl"
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=_append_worker, args=(path, value)) for value in range(12)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert {row.output_ref for row in _load_sync(path)} == {str(v) for v in range(12)}


async def test_concurrent_descriptor_head_and_retention(tmp_path):
    store = DatasetSemanticStore(base_dir=tmp_path)
    descriptors = [GISDatasetDescriptor(dataset_key="r", feature_count=v).with_fingerprints() for v in range(8)]
    results = await asyncio.gather(*(store.put("s", "r", value) for value in descriptors))
    assert all(result.ok for result in results)
    versions = await store.list_versions("s", "r")
    assert set(versions) == {value.descriptor_fingerprint for value in descriptors}
    assert (await store.get("s", "r")).ok
    records = await asyncio.gather(*(store.get_by_fingerprint("s", "r", fp) for fp in versions))
    assert all(record.ok for record in records)


def test_index_utc_and_nonfinite_or_huge_grid():
    assert parse_validated_ts("2026-01-01T08:00:00+08:00") == parse_validated_ts("2026-01-01T00:00:00")
    assert _bbox_cells([float("nan"), 0, 1, 1]) == []
    assert _query_cells([-180, -90, 180, 90]) is None


def test_geometry_rebuilt_and_invalid_vertex_rejected():
    shape, bbox = _sanitize_geometry({"type": "Point", "coordinates": [2, 3, float("nan")]})
    assert shape == {"type": "Point", "coordinates": [2.0, 3.0]}
    assert bbox == [2.0, 3.0, 2.0, 3.0]
    assert _sanitize_geometry({"type": "LineString", "coordinates": [[0, 0], ["bad", 1]]})[0] is None
    assert _sanitize_geometry({"type": "LineString", "coordinates": [[0, 0]] * 257}) == (None, "geometry_oversize")


def test_full_layer_ids_and_last_user_visibility():
    context = compile_map({}, {"layers": [{"id": str(i), "type": "circle"} for i in range(120)]}, None, SituationEvidence())
    assert len(context.layers.value) == 100
    assert len(context.layer_ids.value) == 120
    context = compile_interaction({"_gis_provenance": [None, {"detail": None},
        {"origin": "user", "kind": "PatchLayerPresentationIntent", "target": "x", "revision": 1, "detail": {"visible": False}},
        {"origin": "user", "kind": "PatchLayerPresentationIntent", "target": "x", "revision": 2, "detail": {"visible": True}}]}, [])
    assert context.user_hidden_layers.value == []


def test_bad_render_keeps_other_fact_lines():
    renderer = _Renderer(frozenset())
    assert renderer.fact_line("x", "viewport", known({"center": [None, "bad"]}, source="test"), "视口", lambda value: float(value["center"][0]))
    assert renderer.fact_line("x", "count", known(5, source="test"), "数量", str)
    assert len(renderer.lines) == 2 and "5" in renderer.lines[-1]


def test_layer_consistency_abstains_when_unknown_and_uses_complete_inventory():
    from types import SimpleNamespace
    from app.services.gis_situation.consistency import reconcile_fact_views
    from app.services.gis_situation.facts import unknown
    interaction = SimpleNamespace(selected_feature=known({"layer_id": "119"}, source="selection"),
                                  focus_layer_id=known("119", source="focus"))
    complete = compile_map({}, {"layers": [{"id": str(i)} for i in range(120)]}, None, SituationEvidence())
    situation = SimpleNamespace(map=complete, interaction=interaction)
    assert reconcile_fact_views(situation) is situation
    unknown_map = complete.model_copy(update={"layers": unknown(source="mapspec.layers"), "layer_ids": unknown(source="mapspec.layers"), "layer_count": unknown(source="mapspec.layers")})
    situation.map = unknown_map
    assert reconcile_fact_views(situation) is situation


def test_accessed_memory_entries_are_retained_before_cold_fifo_entries(monkeypatch):
    from app.services.gis_memory import associative_index as module
    monkeypatch.setattr(module, "MAX_ENTRIES_PER_ORG", 2)
    index = module.AssociativeIndex()

    def entry(mid):
        return module.MemoryIndexEntry(mid, "o", "user", "u", "fact", "theme", "theme", ("theme",),
                                       {"theme": 1}, 1, None, 1, False, 30, 1, 1, 0)

    index.upsert(entry("a"))
    index.upsert(entry("b"))
    index.touch("o", ["a"])
    index.upsert(entry("c"))
    assert index.get("o", "a") is not None and index.get("o", "b") is None
    assert index.search("o", "theme", bbox=[-180, -90, 180, 90])


def _store_worker(base, value):
    descriptor = GISDatasetDescriptor(dataset_key="r", feature_count=value).with_fingerprints()
    store = DatasetSemanticStore(base_dir=base)
    assert store._put_sync("s", "r", descriptor, descriptor.descriptor_fingerprint).ok


def test_cross_process_descriptor_history_and_head_stay_consistent(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=_store_worker, args=(tmp_path, value)) for value in range(8)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    store = DatasetSemanticStore(base_dir=tmp_path)
    head = store._dataset_dir("s", "r") / "head.json"
    import json
    payload = json.loads(head.read_text())
    assert len(payload["history"]) == 8
    assert all((head.parent / f"{fp}.json").is_file() for fp in payload["history"])
    assert store._get_sync("s", "r", None).ok
