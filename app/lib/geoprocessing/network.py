import networkx as nx
import geopandas as gpd
from shapely.geometry import Point, LineString, mapping, shape, MultiPoint
from app.lib.geoprocessing.interface import GeoAnalysisResult
import pandas as pd
import numpy as np
from scipy.spatial import KDTree

def calculate_isochrones(network_geojson, facility_points, travel_time_min, mode='walking'):
    """
    Build a true network graph and generate service areas (polygons) based on travel time.
    """
    try:
        # 1. Load network data
        gdf_network = gpd.GeoDataFrame.from_features(network_geojson["features"])
        gdf_facilities = gpd.GeoDataFrame.from_features(facility_points["features"])
        
        # 2. Build NetworkX graph
        G = nx.Graph()
        
        for idx, row in gdf_network.iterrows():
            geom = row.geometry
            if isinstance(geom, LineString):
                coords = list(geom.coords)
                start_node = coords[0]
                end_node = coords[-1]
                
                # Weight by length or travel time
                # Simple version: use length as cost (assuming unit speed if time is requested)
                # In real scenarios, length / speed = time
                weight = row.get("length", geom.length)
                
                G.add_edge(start_node, end_node, weight=weight, geometry=geom)
        
        # 3. Calculate isochrones for each facility
        isochrone_features = []
        for idx, facility in gdf_facilities.iterrows():
            start_point = (facility.geometry.x, facility.geometry.y)
            
            # Find nearest node in graph to start_point
            nodes = list(G.nodes())
            if not nodes:
                continue
                
            # Very simple nearest node search
            nodes_arr = np.array(nodes)
            dist_sq = np.sum((nodes_arr - np.array(start_point))**2, axis=1)
            nearest_node_idx = np.argmin(dist_sq)
            nearest_node = nodes[nearest_node_idx]
            
            # Calculate distances from nearest_node
            # travel_time_min * speed = max_dist. Assuming speed=1 for simplicity if not provided.
            max_dist = travel_time_min 
            
            lengths = nx.single_source_dijkstra_path_length(G, nearest_node, cutoff=max_dist, weight='weight')
            
            reachable_nodes = list(lengths.keys())
            if len(reachable_nodes) < 3:
                # Not enough points for a polygon, return a point or small buffer
                poly = Point(start_point).buffer(0.1)
            else:
                # Create a polygon from reachable nodes
                points = [Point(n) for n in reachable_nodes]
                poly = MultiPoint(points).convex_hull
                
            isochrone_features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "facility_id": facility.get("id", idx),
                    "travel_time": travel_time_min,
                    "mode": mode,
                    "reachable_nodes_count": len(reachable_nodes)
                }
            })
            
        result_geojson = {
            "type": "FeatureCollection",
            "features": isochrone_features
        }
        
        return GeoAnalysisResult(
            success=True,
            data=result_geojson,
            summary=f"Generated {len(isochrone_features)} isochrones for {travel_time_min} minutes ({mode})."
        )
        
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Failed to calculate isochrones: {str(e)}",
            error_type="ProcessingError"
        )

def nearest_neighbor_features(source_points, target_points):
    """
    For each source point, find the closest target point.
    """
    try:
        gdf_source = gpd.GeoDataFrame.from_features(source_points["features"])
        gdf_target = gpd.GeoDataFrame.from_features(target_points["features"])
        
        if gdf_source.empty or gdf_target.empty:
             return GeoAnalysisResult(
                success=True,
                data={"type": "FeatureCollection", "features": []},
                summary="Source or target points are empty."
            )

        # Build KDTree from target points
        target_coords = np.array([(p.x, p.y) for p in gdf_target.geometry])
        tree = KDTree(target_coords)
        
        source_coords = np.array([(p.x, p.y) for p in gdf_source.geometry])
        dists, indices = tree.query(source_coords)
        
        output_features = []
        for i, (dist, idx) in enumerate(zip(dists, indices)):
            source_feat = source_points["features"][i]
            target_feat = target_points["features"][idx]
            
            # Create a combined feature or just add properties to source
            new_properties = source_feat.get("properties", {}).copy()
            new_properties["nearest_id"] = target_feat.get("properties", {}).get("name", target_feat.get("properties", {}).get("id", str(idx)))
            new_properties["distance"] = float(dist)
            
            output_features.append({
                "type": "Feature",
                "geometry": source_feat["geometry"],
                "properties": new_properties
            })
            
        return GeoAnalysisResult(
            success=True,
            data={"type": "FeatureCollection", "features": output_features},
            summary=f"Found nearest neighbors for {len(output_features)} points."
        )
        
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Failed to find nearest neighbors: {str(e)}",
            error_type="ProcessingError"
        )
