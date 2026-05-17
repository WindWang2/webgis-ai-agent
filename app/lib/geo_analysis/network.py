import networkx as nx
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, LineString, mapping, MultiPoint
from app.lib.geoprocessing.interface import GeoAnalysisResult
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
