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

  profile = profile_geojson_source(file_path)

  assert profile["featureCount"] == 1
  assert profile["geometryTypes"] == ["Polygon"]
  assert profile["bbox"] == [0.0, 0.0, 1.0, 1.0]
