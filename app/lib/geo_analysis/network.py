import networkx as nx
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, LineString, mapping, MultiPoint
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf

def calculate_isochrones(network_geojson, facility_points, travel_time_min, mode='walking'):
    """
    Build a true network graph and generate service areas (polygons) based on travel time.
    """
    try:
        # Use geo_processor for pre-processing
        res_net = to_utm_gdf(network_geojson)
        res_fac = to_utm_gdf(facility_points)
        
        if not res_net or not res_fac:
            return GeoAnalysisResult(False, None, "Invalid input GeoJSON")
            
        gdf_network, utm_crs = res_net
        gdf_facilities, fac_crs = res_fac
        
        if utm_crs != fac_crs:
            gdf_facilities = gdf_facilities.to_crs(utm_crs)
        
        # Build NetworkX graph
        G = nx.Graph()
        
        for idx, row in gdf_network.iterrows():
            geom = row.geometry
            if isinstance(geom, LineString):
                coords = list(geom.coords)
                start_node = coords[0]
                end_node = coords[-1]
                
                # Weight by length (meters in UTM)
                weight = row.get("length", geom.length)
                G.add_edge(start_node, end_node, weight=weight)
        
        isochrone_features = []
        # Assumption: travel_time_min is converted to meters using a default speed if not provided
        # For 'walking', approx 80m/min (4.8 km/h)
        speed_m_min = 80.0 if mode == 'walking' else 400.0 # simple default for 'driving'
        max_dist = travel_time_min * speed_m_min
        
        nodes = list(G.nodes())
        if not nodes:
             return GeoAnalysisResult(False, None, "Network graph is empty")
             
        nodes_arr = np.array(nodes)
        
        for idx, facility in gdf_facilities.iterrows():
            start_point = (facility.geometry.x, facility.geometry.y)
            
            # Find nearest node
            dist_sq = np.sum((nodes_arr - np.array(start_point))**2, axis=1)
            nearest_node_idx = np.argmin(dist_sq)
            nearest_node = nodes[nearest_node_idx]
            
            lengths = nx.single_source_dijkstra_path_length(G, nearest_node, cutoff=max_dist, weight='weight')
            
            reachable_nodes = list(lengths.keys())
            if len(reachable_nodes) < 3:
                poly = Point(start_point).buffer(10) # 10m buffer fallback
            else:
                points = [Point(n) for n in reachable_nodes]
                poly = MultiPoint(points).convex_hull
                
            # Convert back to WGS84
            poly_wgs84 = gpd.GeoSeries([poly], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                
            isochrone_features.append({
                "type": "Feature",
                "geometry": mapping(poly_wgs84),
                "properties": {
                    "facility_id": facility.get("id", idx),
                    "travel_time": travel_time_min,
                    "max_dist_m": max_dist,
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
    For each source point, find the closest target point (O(n log n) via cKDTree).
    """
    try:
        from scipy.spatial import cKDTree
        from app.lib.geo_processor.core import to_utm_gdf
        
        res_src = to_utm_gdf(source_points)
        res_tgt = to_utm_gdf(target_points)
        
        if not res_src or not res_tgt:
             return GeoAnalysisResult(False, None, "Invalid input GeoJSON")
             
        gdf_src, utm_crs = res_src
        gdf_tgt, tgt_crs = res_tgt
        
        if utm_crs != tgt_crs:
            gdf_tgt = gdf_tgt.to_crs(utm_crs)
            
        src_coords = np.column_stack((gdf_src.geometry.x.values, gdf_src.geometry.y.values))
        tgt_coords = np.column_stack((gdf_tgt.geometry.x.values, gdf_tgt.geometry.y.values))
        
        # cKDTree: O(n log n) time, O(n) memory (audit S40: was O(n²) distance_matrix)
        tree = cKDTree(tgt_coords)
        min_distances, min_indices = tree.query(src_coords, k=1)
        
        # Pre-compute properties and geometries outside loop (audit S40)
        props = gdf_src.drop(columns='geometry').to_dict('records')
        geom_maps = [mapping(g) for g in gdf_src.geometry]
        tgt_ids = gdf_tgt.index
        
        out_features = [
            {
                "type": "Feature",
                "geometry": geom_maps[i],
                "properties": {
                    **props[i],
                    "nearest_target_id": tgt_ids[min_indices[i]],
                    "distance_m": float(min_distances[i])
                }
            }
            for i in range(len(src_coords))
        ]
            
        avg_dist = float(min_distances.mean())
        
        return GeoAnalysisResult(
            success=True,
            data={"type": "FeatureCollection", "features": out_features},
            summary=f"Calculated nearest neighbors for {len(gdf_src)} points. Average distance: {avg_dist:.1f}m."
        )
    except Exception as e:
        return GeoAnalysisResult(False, None, f"Nearest neighbor analysis failed: {str(e)}")
