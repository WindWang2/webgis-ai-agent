from pathlib import Path
from app.services.spatial_meta_profiler import profile_geojson_source


def test_profile_geojson_source_basic():
  data = {
      "type": "FeatureCollection",
      "features": [
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [100.0, 20.0]},
              "properties": {"name": "Alpha", "mag": 2.5, "active": True},
          },
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [110.0, 30.0]},
              "properties": {"name": "Beta", "mag": 5.0, "active": False},
          },
      ],
  }

  profile = profile_geojson_source(data)

  assert profile["crs"] is None
  assert profile["crs_status"] == "unknown"
  assert profile["featureCount"] == 2
  assert profile["geometryTypes"] == ["Point"]
  assert profile["bbox"] == [100.0, 20.0, 110.0, 30.0]

  # Numeric coordinates without CRS evidence cannot truthfully be interpreted
  # as longitude/latitude for a map camera.
  assert profile["suggestedView"] == {}

  fields = profile["fields"]
  assert "name" in fields
  assert fields["name"]["type"] == "string"
  assert fields["name"]["sampleValues"] == ["Alpha", "Beta"]

  assert "mag" in fields
  assert fields["mag"]["type"] == "number"
  assert fields["mag"]["min"] == 2.5
  assert fields["mag"]["max"] == 5.0
  assert fields["mag"]["mean"] == 3.75
  assert fields["mag"]["null_count"] == 0

  assert "active" in fields
  assert fields["active"]["type"] == "boolean"
  assert fields["active"]["null_count"] == 0


def test_profile_preserves_explicit_crs_and_only_then_suggests_geographic_view():
  data = {
      "type": "FeatureCollection",
      "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
      "features": [
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [100.0, 20.0]},
              "properties": {"value": 1},
          },
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [110.0, 30.0]},
              "properties": {"value": None},
          },
      ],
  }

  profile = profile_geojson_source(data)

  assert profile["crs"] == "EPSG:4326"
  assert profile["crs_status"] == "explicit"
  assert profile["suggestedView"] == {"center": [105.0, 25.0], "zoom": 5}
  assert profile["fields"]["value"]["null_count"] == 1


def test_profile_geojson_source_empty_or_nulls():
  data = {"type": "FeatureCollection", "features": []}
  profile = profile_geojson_source(data)

  assert profile["featureCount"] == 0
  assert profile["bbox"] is None
  assert profile["geometryTypes"] == []
  assert profile["fields"] == {}


def test_profile_geojson_json_string(tmp_path: Path):
  data = {
      "type": "FeatureCollection",
      "features": [
          {
              "type": "Feature",
              "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
              "properties": {"id": 1},
          }
      ],
  }
  file_path = tmp_path / "test.geojson"
  file_path.write_text(file_path.read_text if False else str(data).replace("'", '"'))

  profile = profile_geojson_source(file_path.read_text())

  assert profile["featureCount"] == 1
  assert profile["geometryTypes"] == ["Polygon"]
  assert profile["bbox"] == [0.0, 0.0, 1.0, 1.0]


def test_profile_geojson_source_tolerates_non_dict_feature_entries():
  """P3: 畸形 FeatureCollection（features 含 null/标量）不得整函数崩溃 ——
  几何类型循环已有 isinstance 防护，字段循环必须同样跳过。"""
  data = {
      "type": "FeatureCollection",
      "features": [
          None,
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [10.0, 20.0]},
              "properties": {"v": 1},
          },
          "not-a-feature",
      ],
  }
  profile = profile_geojson_source(data)
  assert profile["featureCount"] == 3
  assert profile["geometryTypes"] == ["Point"]
  assert profile["fields"]["v"]["type"] == "number"
  assert profile["fields"]["v"]["null_count"] == 2


def test_profile_geojson_source_emits_derived_fact_keys():
  """P3: 降级全量路径必须产出与 profile_from_descriptor 同名的派生事实键
  （numeric/categorical/binary/hasTimeField + 非数值字段的 null_ratio）。"""
  data = {
      "type": "FeatureCollection",
      "features": [
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
              "properties": {
                  "mag": 2.5, "flag": True, "name": "A", "event_date": "2024-01-01",
              },
          },
          {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [1.1, 2.1]},
              "properties": {
                  "mag": 4.5, "flag": None, "name": None, "event_date": None,
              },
          },
      ],
  }
  profile = profile_geojson_source(data)
  assert profile["numericFields"] == ["mag"]
  assert profile["categoricalFields"] == ["event_date", "flag", "name"]
  assert profile["binaryFields"] == ["flag"]
  assert profile["hasTimeField"] is True
  # 非数值字段同样携带 null 率（与 profile_from_descriptor 同契约）
  assert profile["fields"]["flag"]["null_ratio"] == 0.5
  assert profile["fields"]["name"]["null_ratio"] == 0.5


def test_suggested_zoom_antimeridian_normalized():
  """Issue #598: a declared wrap-around bbox (west > east, e.g. 170..-170)
  crosses the antimeridian — the naive |east-west| = 340° maps every
  AM-crossing extent to zoom 1 despite a real 20° span. The true arc through
  ±180 is east+360-west = 20° → zoom 4, matching an ordinary 20° bbox."""
  am = {
      "type": "FeatureCollection",
      "bbox": [170.0, -10.0, -170.0, 10.0],
      "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
      "features": [
          {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [170.0, 0.0]}},
          {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-170.0, 0.0]}},
      ],
  }
  normal = {
      "type": "FeatureCollection",
      "bbox": [-10.0, -10.0, 10.0, 10.0],
      "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
      "features": [
          {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-10.0, 0.0]}},
          {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [10.0, 0.0]}},
      ],
  }
  sv_am = profile_geojson_source(am)["suggestedView"]
  sv_norm = profile_geojson_source(normal)["suggestedView"]
  assert sv_am, "wrap-around geographic bbox must produce a suggested view"
  assert sv_norm, "plain 20° bbox must produce a suggested view"
  assert sv_am["zoom"] == sv_norm["zoom"] == 4, (
      f"AM-crossing 20° span must zoom like a plain 20° span: {sv_am['zoom']} vs {sv_norm['zoom']}"
  )
