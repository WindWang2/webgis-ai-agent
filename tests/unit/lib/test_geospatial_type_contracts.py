"""Spatial records have useful static shapes without changing their wire values."""
from pathlib import Path
import subprocess
import sys

import numpy as np

from app.lib.geo_analysis.terrain import _meta_base, topographic_position_index
from app.lib.geo_analysis.measurements import surface_distance
from app.lib.geo_analysis.viewshed_geometry import visibility_geometry


def test_typed_terrain_evidence_retains_omission_and_cell_budgets() -> None:
    valid = np.ones((3, 3), dtype=bool)
    meta = _meta_base("terrain.test", valid, fill_meta_volume=None, window=3)
    assert "fill_meta_volume" not in meta
    assert meta["cells_valid"] == meta["cells_total"] == 9
    assert meta["window"] == 3
    _, result = topographic_position_index(np.arange(9).reshape(3, 3))
    assert result["window_includes_center"] is True
    fc, geometry = visibility_geometry(
        valid, valid, (1.0, 0.0, 0.0, 0.0, -1.0, 3.0), cell_size=1.0,
    )
    assert fc["type"] == "FeatureCollection"
    assert geometry["attributed_visible_area_m2"] == 9.0
    assert geometry["unattributed_visible_area_m2"] == 0.0


def test_measurement_records_keep_optional_samples() -> None:
    stats, metadata = surface_distance(
        np.zeros((4, 4)), vertices=[(0.5, 3.5), (2.5, 1.5)],
        cell_size=1.0, transform=(1.0, 0.0, 0.0, 0.0, -1.0, 4.0),
        include_samples=True,
    )
    assert stats["z_diff_m"] == 0.0
    assert metadata["sample_point_stride"] == 1
    assert metadata["sample_points"][0]["z_m"] == 0.0


def test_geojson_terrain_and_network_contracts_reject_bad_shapes(tmp_path: Path) -> None:
    fixture = tmp_path / "spatial_contract_consumers.py"
    fixture.write_text('''from app.contracts.geojson import GeoJSONFeatureCollection, PointGeometry
from app.lib.geo_analysis.terrain_types import TerrainFacts, ViewshedResult
from app.lib.geo_analysis.measurement_types import ProfilePoint
from app.services.network.contracts import FacilityInput, ODPairInfo, RouteDirection

point: PointGeometry = {"type": "Point", "coordinates": [1.0, 2.0]}
collection: GeoJSONFeatureCollection = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": point, "properties": {"value": 3}}
]}
facts: TerrainFacts = {"window": 3, "filled_volume": 2.5}
facility: FacilityInput = {"geometry": point, "capacity": 10.0}
profile: ProfilePoint = {"x": 1.0, "y": 2.0, "z_m": None, "distance_m": 0.0}
route: RouteDirection = {"step": 1, "action": "Depart", "street_name": "Main", "distance_m": 5.0, "time_s": 1.0, "instruction": "Head on Main"}
bad_point: PointGeometry = {"type": "Polygon", "coordinates": [1.0, 2.0]}
bad_coordinates: PointGeometry = {"type": "Point", "coordinates": [[1.0, 2.0]]}
bad_collection: GeoJSONFeatureCollection = {"type": "FeatureCollection", "features": [point]}
bad_fact: TerrainFacts = {"filled_volume": "two metres"}
bad_profile: ProfilePoint = {"x": 1.0, "y": 2.0, "z_m": "unknown", "distance_m": 0.0}
bad_facility: FacilityInput = {"capacity": "many"}
bad_path: ODPairInfo = {"path": [], "cost": 1.0, "distance_m": 0.0, "time_s": 0.0, "reachable": "yes"}
''')
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--follow-imports=silent",
         "--ignore-missing-imports", "--no-incremental", "--show-error-codes",
         "--cache-dir", str(tmp_path / "cache"), str(fixture)],
        cwd=Path(__file__).resolve().parents[3],
        text=True, capture_output=True, check=False,
    )
    errors = [line for line in completed.stdout.splitlines() if ": error:" in line]
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert len(errors) == 7, completed.stdout + completed.stderr
    assert all("[typeddict-item]" in line or "[list-item]" in line for line in errors), errors
