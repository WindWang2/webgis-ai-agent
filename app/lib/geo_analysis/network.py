import logging
import networkx as nx
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, LineString, mapping
from shapely.ops import unary_union
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf

logger = logging.getLogger(__name__)

# Mode -> cruise speed in metres/minute, consistent with the V2
# TravelProfile speed table. Replaces the binary 80/400 m/min model that
# silently treated every non-walking mode as driving (audit N-F04).
_MODE_SPEED_M_PER_MIN = {
    "walking": 80.0,    # 4.8 km/h
    "cycling": 250.0,   # 15 km/h
    "driving": 667.0,   # ~40 km/h
    "transit": 417.0,   # 25 km/h
}


def _speed_m_per_min(mode: str) -> float:
    return _MODE_SPEED_M_PER_MIN.get(mode, _MODE_SPEED_M_PER_MIN["driving"])

def calculate_isochrones(network_geojson: dict | str, facility_points: dict | str, travel_time_min: float, mode: str = 'walking') -> GeoAnalysisResult:
    """
    Build a true network graph and generate service areas (polygons) based on travel time.
    """
    try:
        # Use geo_processor for pre-processing
        res_net = to_utm_gdf(network_geojson)
        res_fac = to_utm_gdf(facility_points)

        # GIS-13: to_utm_gdf returns (None, None) on failure. A non-empty tuple
        # is always truthy, so `if not res_net` never fires — unpacking then
        # crashed on the next .iterrows()/.geometry access. Match density.py /
        # geometry_ops.py's `if result is None` pattern.
        if res_net is None or res_net[0] is None or res_fac is None or res_fac[0] is None:
            return GeoAnalysisResult(False, None, "Invalid input GeoJSON")
            
        gdf_network, utm_crs = res_net
        gdf_facilities, fac_crs = res_fac
        
        if utm_crs != fac_crs:
            gdf_facilities = gdf_facilities.to_crs(utm_crs)
        
        # Build NetworkX graph (MultiGraph to preserve parallel edges)
        G = nx.MultiGraph()
        
        for idx, row in gdf_network.iterrows():
            geom = row.geometry
            if isinstance(geom, LineString):
                coords = list(geom.coords)
                start_node = coords[0]
                end_node = coords[-1]
                
                # Weight by length (meters in UTM)
                if "length" in row and row["length"] is not None:
                    weight = float(row["length"])
                else:
                    weight = geom.length
                    logger.warning(
                        "Edge at index %d has no 'length' property; using geometry.length (%fm). "
                        "Accuracy depends on input CRS being projected (meters).",
                        idx, weight,
                    )
                G.add_edge(start_node, end_node, weight=weight, geometry=geom)
        
        isochrone_features = []
        max_dist = float(travel_time_min) * _speed_m_per_min(mode)

        nodes = list(G.nodes())
        if not nodes:
            return GeoAnalysisResult(False, None, "Network graph is empty")

        nodes_arr = np.array(nodes)

        # cKDTree for nearest-node search (O(n log n), audit S40)
        from scipy.spatial import cKDTree
        node_tree = cKDTree(nodes_arr)

        # Road-width buffer for the network-constrained polygonization. The
        # previous ``MultiPoint(reachable_nodes).convex_hull`` enclosed rivers,
        # parks and any road-network hole in the convex envelope of reachable
        # nodes, and collapsed to a LineString for collinear roads (N-F01).
        # Buffering the union of reachable EDGE geometry yields a polygon that
        # follows the actual road network.
        _buffer_m = 30.0 if mode == "walking" else 20.0

        for idx, facility in gdf_facilities.iterrows():
            start_point = np.array([facility.geometry.x, facility.geometry.y])

            # Find nearest node via cKDTree
            _, nearest_node_idx = node_tree.query(start_point, k=1)
            nearest_node = nodes[nearest_node_idx]

            lengths = nx.single_source_dijkstra_path_length(
                G, nearest_node, cutoff=max_dist, weight="weight"
            )

            # Collect reachable EDGE geometry (network-constrained), not the
            # convex hull of reachable point samples. An edge qualifies if
            # either endpoint is within the travel budget.
            reachable_edges = []
            for u, v, edata in G.edges(data=True):
                eg = edata.get("geometry")
                if eg is None:
                    continue
                if (u in lengths) or (v in lengths):
                    reachable_edges.append(eg)

            reachable = True
            if reachable_edges:
                poly = unary_union(reachable_edges).buffer(_buffer_m)
                if poly.is_empty or poly.geom_type not in ("Polygon", "MultiPolygon"):
                    # Collinear/degenerate edge union can collapse back to a
                    # line/point — fall back to the node-hull buffered so the
                    # output is always 2D, but flag lower confidence.
                    pts = [Point(n) for n in lengths.keys()]
                    poly = (unary_union(pts).convex_hull if len(pts) >= 3
                            else Point(start_point).buffer(_buffer_m))
            else:
                # Genuinely unreachable: disconnected network, or facility
                # isolated from any road. Report honestly with a small
                # visibility marker instead of fabricating a 10 m disc
                # labelled as the real isochrone (N-F08).
                poly = Point(start_point).buffer(5.0)
                reachable = False

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
                    "reachable": reachable,
                    "reachable_nodes_count": len(lengths),
                    "reachable_edges_count": len(reachable_edges),
                }
            })

        result_geojson = {
            "type": "FeatureCollection",
            "features": isochrone_features,
        }

        summary = (
            f"Generated {len(isochrone_features)} isochrones for "
            f"{travel_time_min} minutes ({mode})."
        )
        n_unreachable = sum(1 for f in isochrone_features if not f["properties"]["reachable"])
        if n_unreachable:
            summary += (
                f" {n_unreachable} facility(ies) unreachable "
                "(disconnected from the road network)."
            )

        return GeoAnalysisResult(
            success=True,
            data=result_geojson,
            summary=summary,
        )
        
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Failed to calculate isochrones: {str(e)}",
            error_type="ProcessingError"
        )

def nearest_neighbor_features(source_points: dict | str, target_points: dict | str) -> GeoAnalysisResult:
    """
    For each source point, find the closest target point (O(n log n) via cKDTree).
    """
    try:
        from scipy.spatial import cKDTree
        from app.lib.geo_processor.core import to_utm_gdf

        res_src = to_utm_gdf(source_points)
        res_tgt = to_utm_gdf(target_points)

        # GIS-13 / N-F09: to_utm_gdf returns (None, None) on failure but a
        # non-empty tuple is always truthy, so `if not res_src` never fired.
        if res_src is None or res_src[0] is None or res_tgt is None or res_tgt[0] is None:
            return GeoAnalysisResult(
                False, None, "无效或空的输入点要素",
                error_type="ValueError",
                correction_hint="提供有效的 Point 要素 GeoJSON",
            )

        gdf_src, utm_crs = res_src
        gdf_tgt, tgt_crs = res_tgt

        # Contract (N-F09): this is Point<->Point Euclidean nearest-neighbour.
        # Filter to Point geometries and validate, instead of crashing on
        # `.geometry.x` for Polygon/LineString input.
        gdf_src = gdf_src[gdf_src.geometry.geom_type == "Point"]
        gdf_tgt = gdf_tgt[gdf_tgt.geometry.geom_type == "Point"]
        if len(gdf_src) == 0:
            return GeoAnalysisResult(
                False, None, "源要素中无 Point 几何（nearest_neighbor 仅支持点）",
                error_type="UnsupportedGeometry",
                correction_hint="提供 Point 要素，或先用 representative_point/centroid。",
            )
        if len(gdf_tgt) == 0:
            return GeoAnalysisResult(
                True,
                {"type": "FeatureCollection", "features": []},
                "目标点集为空，无最近邻结果。",
            )
        
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
