"""Dataset Semantics 生产接线测试（ADR-0215 V5/V6 验收矩阵）。

- V5 四路径同一指纹：ingest 铸造的 descriptor 指纹 = mapspec ref 路径
  解析的指纹 = store 现读指纹（同一数据集 → 同一语义身份）；
- fabric 零扫描投影（explain 证据带 descriptor_fingerprint）；
- mapspec source 携带 descriptor_fingerprint（inline 路径 + schema 字段）；
- qualification：fresh → 与 profile 路径同结果；指纹漂移 →
  DESCRIPTOR_STALE_* 诚实降级；无 descriptor → 现状不变；
- harvest version_token 生产触发（descriptor 指纹 → 记忆版本对账）。
"""
import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import (
    derive_descriptor,
    descriptor_resolver_profile,
)
from app.services.dataset_semantics.store import (
    DatasetSemanticStore,
    reset_dataset_semantic_store,
)


def _fc(n=30):
    return {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
             "properties": {"学校数量": i, "name": f"x{i % 3}", "年份": 2020 + i % 4}}
            for i in range(n)
        ],
    }


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    import app.services.dataset_semantics.store as store_mod

    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    reset_dataset_semantic_store()


# ── V5：ingest ↔ store ↔ mapspec 同一指纹 ─────────────────────────────────


class TestIngestWiring:
    async def test_ingest_builds_and_stores_descriptor(self, sem_store):
        from app.services.data_ingest.pipeline import (
            get_ingest_pipeline,
            reset_ingest_pipeline,
        )

        try:
            result = await get_ingest_pipeline().ingest(
                "f01-ingest-1", _fc(), name="学校", crs="EPSG:4326")
        finally:
            reset_ingest_pipeline()
        assert result.ok
        fp = result.profile_summary.get("descriptor_fingerprint")
        assert fp and fp.startswith("dsd-v1:")
        # store 现读 = ingest 铸造（同一语义身份）。
        rec = await sem_store.get("f01-ingest-1", result.ref_id)
        assert rec.ok
        assert rec.descriptor.descriptor_fingerprint == fp
        # artifact metadata 带 descriptor 指针（registry 本体零改动）。
        from app.services.artifact_registry import get_artifact

        art = await get_artifact("f01-ingest-1", result.ref_id)
        assert art.metadata.get("descriptor_fingerprint") == fp

    async def test_ingest_survives_descriptor_failure(self, sem_store, monkeypatch):

        async def _boom(*a, **k):
            raise RuntimeError("store down")

        monkeypatch.setattr(
            DatasetSemanticStore, "put", _boom)
        from app.services.data_ingest.pipeline import (
            get_ingest_pipeline,
            reset_ingest_pipeline,
        )

        try:
            result = await get_ingest_pipeline().ingest(
                "f01-ingest-2", _fc(), crs="EPSG:4326")
        finally:
            reset_ingest_pipeline()
        # additive evidence：descriptor 失败不阻断 ingest 主链。
        assert result.ok
        assert "descriptor_fingerprint" not in result.profile_summary


# ── V5：mapspec source 携带指纹 ────────────────────────────────────────────


class TestMapspecWiring:
    async def test_ref_path_resolves_ingested_fingerprint(self, sem_store, monkeypatch):
        """同一 ref：ingest 铸造的指纹 == mapspec source 解析的指纹（DoD #1）。"""
        from app.services.data_ingest.pipeline import (
            get_ingest_pipeline,
            reset_ingest_pipeline,
        )
        from app.services.mapspec_store import mapspec_store
        from app.services.mapspec import MapSpecResult
        from app.services import session_data as session_data_mod

        try:
            result = await get_ingest_pipeline().ingest(
                "f01-map-1", _fc(), crs="EPSG:4326")
        finally:
            reset_ingest_pipeline()
        assert result.ok

        ref_descriptor = {
            "field_schema": {"学校数量": {"type": "number"},
                             "name": {"type": "string"},
                             "年份": {"type": "number"}},
            "feature_count": 30,
            "geometry_types": ["Point"],
            "crs": "EPSG:4326",
        }

        async def _fake_resolve_alias(session_id, ref):
            return ref.replace("ref:", "ref:", 1)

        async def _fake_get_ref_descriptor(session_id, ref):
            return ref_descriptor

        intents = []

        async def _fake_apply(session_id, intent, **kwargs):
            intents.append(intent)
            return MapSpecResult(mapspec={"sources": {intent.source_id: intent.source}})

        monkeypatch.setattr(session_data_mod.session_data_manager,
                            "resolve_alias", _fake_resolve_alias)
        monkeypatch.setattr(session_data_mod.session_data_manager,
                            "get_ref_descriptor", _fake_get_ref_descriptor)
        monkeypatch.setattr(mapspec_store.engine, "apply_mutation", _fake_apply)

        await mapspec_store.source_profile(
            "f01-map-1", "s1", f"ref:{result.ref_id.split(':', 1)[1]}")
        src = intents[0].source
        # ref 路径：descriptor 从 store 解析（ingest 铸造的那一份）→ 同一指纹。
        assert src["descriptor_fingerprint"] == \
            result.profile_summary["descriptor_fingerprint"]

    async def test_inline_path_attaches_fingerprint(self, sem_store, monkeypatch):
        from app.services.mapspec_store import mapspec_store
        from app.services.mapspec import MapSpecResult
        from app.services import session_data as session_data_mod

        async def _fake_store(session_id, payload, prefix="geojson"):
            return f"ref:{prefix}-fake"

        intents = []

        async def _fake_apply(session_id, intent, **kwargs):
            intents.append(intent)
            return MapSpecResult(mapspec={"sources": {intent.source_id: intent.source}})

        def _sync_profile(geojson_data):
            # profile_geojson_source 是同步函数（source_profile 经 to_thread
            # 卸载调用，fake 必须同步）。
            return {
                "featureCount": 3,
                "geometryTypes": ["Point"],
                "fields": {"学校数量": {"type": "number"}},
                "fields_status": "explicit",
                "bbox": [116.0, 39.0, 116.3, 39.0],
                "crs": "EPSG:4326",
            }

        monkeypatch.setattr(session_data_mod.session_data_manager,
                            "store", _fake_store)
        monkeypatch.setattr(mapspec_store.engine, "apply_mutation", _fake_apply)
        monkeypatch.setattr(
            "app.services.spatial_meta_profiler.profile_geojson_source",
            _sync_profile)

        await mapspec_store.source_profile(
            "f01-map-2", "s1", _fc(3))
        src = intents[0].source
        # inline 路径：从授权扫描产物就地投影 → 指纹在场且格式正确。
        assert src["descriptor_fingerprint"].startswith("dsd-v1:")


class TestMapspecSchemaField:
    def test_geojson_source_accepts_descriptor_fingerprint(self):
        from app.lib.cartography.mapspec_schema import GeoJSONMapSpecSource

        src = GeoJSONMapSpecSource(
            type="geojson", dataPath="x.json",
            descriptor_fingerprint="dsd-v1:abc")
        assert src.descriptor_fingerprint == "dsd-v1:abc"

    def test_data_fabric_source_accepts_descriptor_fingerprint(self):
        from app.lib.cartography.mapspec_schema import DataFabricMapSpecSource

        src = DataFabricMapSpecSource(
            type="data_fabric", ref_id="ref:1",
            descriptor_fingerprint="dsd-v1:abc")
        assert src.descriptor_fingerprint == "dsd-v1:abc"

    def test_legacy_source_without_field_still_valid(self):
        from app.lib.cartography.mapspec_schema import GeoJSONMapSpecSource

        src = GeoJSONMapSpecSource(type="geojson", dataPath="x.json")
        assert src.descriptor_fingerprint is None


# ── fabric 零扫描投影 ──────────────────────────────────────────────────────


class TestFabricProjection:
    def test_build_from_fabric_descriptor(self):
        from app.services.dataset_semantics import (
            build_descriptor_from_fabric_descriptor,
        )

        class _Item:
            name = "poi"
            geometry_type = "Point"
            crs = "EPSG:4326"
            bbox = None
            feature_count = 42

        class _Descriptor:
            id = "poi"
            source_type = "generic"
            source_id = "poi"
            title = "poi"
            geometry_type = "Point"
            srs = "EPSG:4326"
            bbox = None
            feature_count = 42
            fields = [{"name": "school_count", "type": "number"}]
            metadata = {}

            def model_dump(self, exclude_none=False):
                return {
                    "id": self.id, "source_type": self.source_type,
                    "source_id": self.source_id, "title": self.title,
                    "geometry_type": self.geometry_type, "srs": self.srs,
                    "bbox": self.bbox, "feature_count": self.feature_count,
                    "fields": self.fields, "metadata": self.metadata,
                }

        d = build_descriptor_from_fabric_descriptor(
            _Descriptor(), dataset_key="catalog:poi")
        assert d.descriptor_fingerprint.startswith("dsd-v1:")
        assert d.sampling.strategy == "descriptor_projection"  # 零扫描证据域
        assert d.fields[0].name == "school_count"
        assert d.fields[0].measurement_kind == "count"


# ── V6：qualification 消费 descriptor ─────────────────────────────────────


class _Req:
    role = "measure"
    required = True
    missing_policy = "block"
    reason_code = ""
    degrade_disclosure = ""
    geometry_kinds = ("polygon",)
    min_numeric_samples = None
    nonzero_variance_required = False
    projected_crs_required = False


class TestQualificationDescriptor:
    def _profile(self, **overrides):
        base = dict(
            source="ref_descriptor", feature_count=10,
            geometry_types=["Polygon"], crs="EPSG:4326",
            fields={"population": "number", "name": "string"},
            numeric_fields=["population"], categorical_fields=["name"],
            null_ratios={"population": 0.0}, fields_status="explicit",
        )
        base.update(overrides)
        return DatasetProfile(**base)

    def test_descriptor_only_supply_runs_facts(self):
        from app.services.gis_harness.data_qualification import qualify_data_role

        d = derive_descriptor(
            self._profile(), dataset_key="ref:t",
            features=[{"properties": {"population": i, "name": f"x{i % 3}"}}
                      for i in range(20)])
        q = qualify_data_role(_Req(), "bound", descriptor=d)
        # 投影供给：事实检查在跑（无 profile 直传也产出生效事实面）。
        assert q.checks
        assert q.state in ("eligible", "degraded", "transform_required")

    def test_fresh_fingerprint_not_stale(self):
        from app.services.gis_harness.data_qualification import qualify_data_role

        p = self._profile()
        d = derive_descriptor(
            p, dataset_key="ref:t",
            features=[{"properties": {"population": i, "name": f"x{i % 3}"}}
                      for i in range(20)])
        q = qualify_data_role(
            _Req(), "bound",
            resolver_profile=descriptor_resolver_profile(d),
            descriptor=d, expected_descriptor_fingerprint=d.descriptor_fingerprint)
        assert q.reason_code != "DESCRIPTOR_FINGERPRINT_MISMATCH"

    def test_stale_fingerprint_degrades(self):
        from app.services.gis_harness.data_qualification import qualify_data_role

        p = self._profile()
        d = derive_descriptor(
            p, dataset_key="ref:t",
            features=[{"properties": {"population": i, "name": f"x{i % 3}"}}
                      for i in range(20)])
        q = qualify_data_role(
            _Req(), "bound", resolver_profile=p.to_resolver_profile(),
            descriptor=d, expected_descriptor_fingerprint="dsd-v1:stale")
        assert q.state == "degraded"
        assert q.reason_code == "DESCRIPTOR_FINGERPRINT_MISMATCH"

    def test_stale_with_recorded_descriptor_precise_code(self):
        from app.services.gis_harness.data_qualification import qualify_data_role

        p = self._profile()
        d_old = derive_descriptor(
            p, dataset_key="ref:t",
            features=[{"properties": {"population": i, "name": f"x{i % 3}"}}
                      for i in range(20)])
        d_new = derive_descriptor(
            self._profile(crs="EPSG:3857"), dataset_key="ref:t",
            features=[{"properties": {"population": i, "name": f"x{i % 3}"}}
                      for i in range(20)])
        q = qualify_data_role(
            _Req(), "bound", resolver_profile=p.to_resolver_profile(),
            descriptor=d_new,
            expected_descriptor_fingerprint=d_old.descriptor_fingerprint,
            recorded_descriptor=d_old)
        assert q.state == "degraded"
        assert q.reason_code == "DESCRIPTOR_STALE_CRS"

    def test_no_descriptor_legacy_unchanged(self):
        from app.services.gis_harness.data_qualification import qualify_data_role

        p = self._profile()
        q = qualify_data_role(_Req(), "bound",
                              resolver_profile=p.to_resolver_profile())
        assert q.state == "eligible"
        assert q.reason_code == "PROFILE_FACTS_SATISFIED"


# ── context 联动 ───────────────────────────────────────────────────────────


class TestContextBridge:
    def test_augment_source_fingerprints(self):
        from app.services.dataset_semantics.context_bridge import (
            augment_source_fingerprints,
        )

        entries = [{"ref": "ref:a", "fingerprint": "fp-a"},
                   {"ref": "ref:b", "fingerprint": "fp-b"}]
        out = augment_source_fingerprints(
            entries, {"ref:a": "dsd-v1:xyz"})
        assert out[0]["descriptor_fingerprint"] == "dsd-v1:xyz"
        assert "descriptor_fingerprint" not in out[1]   # 缺席不虚构

    def test_context_layers_data_domain_passthrough(self):
        from app.services.gis_harness.context_layers import _data_domain

        payload = _data_domain({"source_fingerprints": [
            {"ref": "ref:a", "fingerprint": "fp",
             "descriptor_fingerprint": "dsd-v1:abc123"}]})
        assert payload["refs"][0]["descriptor_fingerprint"] == "dsd-v1:abc123"
        legacy = _data_domain({"source_fingerprints": [
            {"ref": "ref:a", "fingerprint": "fp"}]})
        assert "descriptor_fingerprint" not in legacy["refs"][0]

    async def test_harvest_prefers_descriptor_fingerprint(self, monkeypatch):
        """harvest 的 version_token 现在取 source.descriptor_fingerprint。"""
        import app.services.gis_memory.harvest as harvest_mod

        captured = {}

        def _fake_record(db, req):
            captured[req.kind] = req.value
            return True

        monkeypatch.setattr(harvest_mod, "safe_record_memory", _fake_record)

        class _FakeDB:
            def flush(self):
                pass

            def rollback(self):
                pass

            def commit(self):
                pass

            def execute(self, *a, **k):
                return self

            def scalars(self):
                return self

            def all(self):
                return []

        def _fake_session_local():
            class _Ctx:
                def __enter__(self):
                    return _FakeDB()

                def __exit__(self, *a):
                    return False

            return _Ctx()

        harvest_mod.set_session_local_factory(_fake_session_local)
        sources = {
            "s1": {
                "ref": "ref:abc",
                "descriptor_fingerprint": "dsd-v1:cafe",
                "profile": {"featureCount": 10, "fields": {}},
            },
        }
        harvest_mod._harvest_sync(
            org_id="org", user_id=None, session_id="sess", project_id=None,
            pending_reqs=[], sources=sources, user_decisions=[],
            artifact_ledger=[],
        )
        value = captured.get("dataset_semantics") or {}
        assert value.get("version_token") == "dsd-v1:cafe"
