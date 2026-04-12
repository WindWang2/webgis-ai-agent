import sys
import os
import json
import numpy as np

# Mocking parts of the system if needed, but here we can just test the function directly if dependencies are available.
# Since I'm in the workspace, I can try to import it.

# Adjust python path to include the project root
sys.path.append(os.getcwd())

from app.services.spatial_tasks import run_heatmap_generation

def test_grid_heatmap():
    # Sample features
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.41, 39.91]}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.42, 39.92]}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.41, 39.91]}}, # Overlap
    ]
    
    # Test grid mode
    result = run_heatmap_generation.apply(kwargs={
        "features": features,
        "cell_size": 1000,
        "render_type": "grid"
    }).result
    
    if not result["success"]:
        print(f"FAILED: {result.get('error')}")
        return

    data = result["data"]
    print("Grid mode SUCCESS")
    print(f"Feature count: {len(data['features'])}")
    print(f"Metadata: {data['metadata']}")
    
    # Check weight calculation
    weights = [f["properties"]["weight"] for f in data["features"]]
    print(f"Max weight: {max(weights)}")
    
    # Test raster mode
    result_raster = run_heatmap_generation.apply(kwargs={
        "features": features,
        "cell_size": 1000,
        "render_type": "raster"
    }).result
    
    if not result_raster["success"]:
        print(f"FAILED Raster: {result_raster.get('error')}")
        return
        
    print("Raster mode SUCCESS")
    print(f"Bbox: {result_raster['data']['bbox']}")

if __name__ == "__main__":
    test_grid_heatmap()
