"""Independent consumer checks for #1951S49-L3 and #1939S96-006."""
from types import SimpleNamespace

import numpy as np


def test_source_namespaced_drift_invalidates_only_corresponding_classification():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core.database import Base
    from app.models.project import Project, CartoProjectFact
    from app.services.cartography.memory_harvest import _numeric_field_profiles
    from app.services.cartography.project_memory import (
        apply_distribution_drift, harvest_facts_from_review, get_shared_classification,
    )
    from app.services.spatial_meta_profiler import profile_geojson_source
    def profile(offset):
        return profile_geojson_source({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [110, 30]}, "properties": {"value": offset + i}} for i in range(100)]})
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    spec = {"sources": {"one": {"profile": profile(1)}, "two": {"profile": profile(2)}, "unused": {"profile": profile(3)}}, "layers": [
        {"id": "a", "source": "one", "legend_spec": {"type": "graduated", "field": "value", "breaks": [0, 50, 100]}},
        {"id": "b", "source": "two", "legend_spec": {"type": "graduated", "field": "value", "breaks": [0, 40, 100]}},
    ]}
    with Session(engine) as db:
        db.add(Project(id="p", name="p"))
        db.commit()
        profiles = _numeric_field_profiles(spec)
        assert set(profiles) == {"one::value", "two::value"}
        apply_distribution_drift(db, "p", profiles)
        harvest_facts_from_review(db, "p", spec, {"overall_passed": True, "cartography": {"status": "passed"}})
        assert get_shared_classification(db, "p", "value") is None
        assert get_shared_classification(db, "p", "one::value") is not None
        spec["sources"]["one"]["profile"] = profile(1000)
        events = apply_distribution_drift(db, "p", _numeric_field_profiles(spec))
        assert len(events) == 1 and events[0]["subject"] == "one::value"
        assert events[0]["invalidated_classifications"] == 1
        assert get_shared_classification(db, "p", "one::value") is None
        assert get_shared_classification(db, "p", "two::value") is not None
        assert db.query(CartoProjectFact).filter_by(kind="shared_classification", subject="two::value").one().status == "active"
    engine.dispose()


def test_source_field_subject_avoids_delimiter_collision_and_database_overflow():
    from app.services.cartography.memory_subjects import field_subject
    assert field_subject("a::b", "c") != field_subject("a", "b::c")
    assert len(field_subject("x" * 300, "y" * 300)) <= 255


def test_nature_custom_data_root_and_foreign_paths_are_enforced(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    from app.core.config import settings
    from app.services.nature_resource_analyzer import NatureResourceAnalyzer
    data = tmp_path / "custom-data"
    own = data / "owner"
    other = data / "other"
    own.mkdir(parents=True)
    other.mkdir()
    for folder in [own, other]:
        with rasterio.open(folder / "image.tif", "w", driver="GTiff", width=2, height=2, count=2, dtype="float32", crs="EPSG:4326", transform=from_origin(110, 30, .1, .1)) as target:
            target.write(np.ones((2, 2, 2), dtype="float32"))
    monkeypatch.setattr(settings, "DATA_DIR", str(data))
    monkeypatch.setattr("app.services.scoped_raster_paths.scoped_raster_roots", lambda **kwargs: [own, tmp_path / "ledger-owner"])
    called = []
    def analyze(path, index, *, band_map, out_path):
        called.append((path, out_path))
        return {"stats": {}, "algorithm": "fixture", "band_map": band_map, "descriptor": SimpleNamespace(width=2, height=2, to_dict=lambda: {}), "output_path": out_path, "content_fingerprint": "fixture"}
    monkeypatch.setattr("app.lib.geo_analysis.raster_windowed.windowed_band_index", analyze)
    out = NatureResourceAnalyzer.calculate_index("owner/image.tif", session_id="owner", red_band=1, nir_band=2, output_dir="owner/out")
    assert out["success"], out
    assert called[0][0] == str(own / "image.tif")
    assert str(own / "out") in called[0][1]
    called.clear()
    assert not NatureResourceAnalyzer.calculate_index("other/image.tif", session_id="owner", red_band=1, nir_band=2)["success"]
    assert not NatureResourceAnalyzer.calculate_index("owner/image.tif", session_id="owner", red_band=1, nir_band=2, output_dir="other/out")["success"]
    assert called == []


def test_nature_band_range_missing_min_and_sanitized_failure(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    from app.core.config import settings
    from app.services.nature_resource_analyzer import NatureResourceAnalyzer
    own = tmp_path / 'owner'
    own.mkdir()
    with rasterio.open(own / 'image.tif', 'w', driver='GTiff', width=2, height=2,
                       count=2, dtype='float32', crs='EPSG:4326', transform=from_origin(110, 30, .1, .1)) as target:
        target.write(np.ones((2, 2, 2), dtype='float32'))
    monkeypatch.setattr(settings, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr('app.services.scoped_raster_paths.scoped_raster_roots', lambda **kwargs: [own])
    calls = []
    def analyze(path, index, *, band_map, out_path):
        calls.append(path)
        return {'stats': {'min': None, 'max': .8, 'mean': .5}, 'algorithm': 'fixture',
            'band_map': band_map, 'descriptor': SimpleNamespace(width=2, height=2, to_dict=lambda: {}),
            'output_path': out_path, 'content_fingerprint': 'fixture'}
    monkeypatch.setattr('app.lib.geo_analysis.raster_windowed.windowed_band_index', analyze)
    result = NatureResourceAnalyzer.calculate_index('owner/image.tif', session_id='owner', red_band=1, nir_band=3)
    assert result['error_type'] == 'invalid_band_index' and not calls
    result = NatureResourceAnalyzer.calculate_index('owner/image.tif', session_id='owner', red_band=1, nir_band=2)
    assert result['success'] and result['stats'] == {'min': None, 'max': .8, 'mean': .5}
    def fail(*args, **kwargs):
        raise RuntimeError('/server/private/source.tif contains secrets')
    monkeypatch.setattr('app.lib.geo_analysis.raster_windowed.windowed_band_index', fail)
    result = NatureResourceAnalyzer.calculate_index('owner/image.tif', session_id='owner', red_band=1, nir_band=2)
    assert not result['success'] and result['error'] == 'raster analysis failed'
    assert '/server' not in str(result)
