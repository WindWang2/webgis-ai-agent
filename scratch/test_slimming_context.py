import sys
import os
import json

# Adjust python path
sys.path.append(os.getcwd())

from app.services.chat_engine import _slim_tool_result

def test_slimming():
    # 1. Standard GeoJSON result
    result_std = {
        "geojson": {
            "type": "FeatureCollection",
            "features": [{"properties": {"value": i, "name": f"P{i}"}} for i in range(1000)]
        },
        "stats": {"total": 1000}
    }
    res_str_std = json.dumps(result_std)
    
    slimmed_std = _slim_tool_result(result_std, res_str_std, "ref:123")
    slimmed_std_dict = json.loads(slimmed_std)
    
    print("--- Standard Test ---")
    print(f"Stats preserved: {'stats' in slimmed_std_dict}")
    print(f"GeoJSON summary present: {'geojson_summary' in slimmed_std_dict}")
    print(f"Available properties: {slimmed_std_dict['geojson_summary']['available_properties']}")
    print(f"Features removed: {'features' not in slimmed_std_dict.get('geojson', {})}")

    # 2. Direct FeatureCollection (Heatmap Grid style)
    result_grid = {
        "type": "FeatureCollection",
        "features": [{"properties": {"weight": i/1000, "count": i}} for i in range(1000)],
        "metadata": {"field": "weight", "render_type": "grid"}
    }
    res_str_grid = json.dumps(result_grid)
    
    slimmed_grid = _slim_tool_result(result_grid, res_str_grid, "ref:456")
    slimmed_grid_dict = json.loads(slimmed_grid)
    
    print("\n--- Direct FC Test (Grid) ---")
    print(f"Type preserved: {slimmed_grid_dict.get('type')}")
    print(f"Metadata preserved: {slimmed_grid_dict.get('metadata')}")
    print(f"GeoJSON summary present: {'geojson_summary' in slimmed_grid_dict}")
    print(f"Available properties: {slimmed_grid_dict['geojson_summary']['available_properties']}")
    print(f"Features removed: {'features' not in slimmed_grid_dict}")

if __name__ == "__main__":
    test_slimming()
