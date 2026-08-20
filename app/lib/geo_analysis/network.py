import logging
import networkx as nx
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, LineString, MultiLineString, mapping
from shapely.ops import unary_union, substring
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf
# ADR-0052: 协作式取消检查点。cancellable() 在 chunk 边界读一次 contextvar，
# 未绑定 token 时开销为零；用户取消后长循环立即抛 OperationCancelled 退出，
# 真正释放 CPU 而不是只改 UI 状态。
from app.services.jobs.cancellation import cancellable

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
        
        # Build NetworkX graph (MultiGraph to preserve parallel edges).
        # #443: iterate the geometry GeoSeries directly — the previous
        # ``iterrows()`` materialized a pandas (Geo)Series per feature, which
        # dominated the call on 20k+ feature networks (only .geometry is used).
        G = nx.MultiGraph()

        for geom in gdf_network.geometry:
            # GIS-P3-4: MultiLineString roads must contribute every part —
            # skipping them silently understated isochrone coverage.
            if isinstance(geom, MultiLineString):
                line_parts = list(geom.geoms)
            elif isinstance(geom, LineString):
                line_parts = [geom]
            else:
                continue
            for part in line_parts:
                coords = list(part.coords)
                start_node = coords[0]
                end_node = coords[-1]

                # Weight by edge length in METERS. The GeoDataFrame has already
                # been reprojected to a metric UTM CRS (to_utm_gdf above), so
                # the geometry length is always in meters. The previous code trusted
                # a source ``length`` attribute verbatim — but the attribute is
                # NOT reprojected, so a geographic (EPSG:4326) source carried
                # degree-valued lengths (~0.01). With max_dist in meters that
                # made every connected edge "reachable" and the isochrone
                # swallowed the whole network. Derive from geometry instead.
                weight = float(part.length) if part.length else 0.0
                G.add_edge(start_node, end_node, weight=weight, geometry=part)
        
        isochrone_features = []
        max_dist = float(travel_time_min) * _speed_m_per_min(mode)
        total_clip_failures = 0

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

        for idx, facility in cancellable(gdf_facilities.iterrows()):
            fac_geom = facility.geometry
            # GIS-P3-3: a Polygon/LineString facility has no .x/.y — that
            # AttributeError used to fail the WHOLE analysis for one bad
            # feature. Degrade to its representative point instead (mirrors
            # the nearest-neighbor path's Point filter).
            if fac_geom is None or fac_geom.is_empty:
                continue
            if fac_geom.geom_type != "Point":
                fac_geom = fac_geom.representative_point()
            start_point = np.array([fac_geom.x, fac_geom.y])

            # Find nearest node via cKDTree
            _, nearest_node_idx = node_tree.query(start_point, k=1)
            nearest_node = nodes[nearest_node_idx]

            lengths = nx.single_source_dijkstra_path_length(
                G, nearest_node, cutoff=max_dist, weight="weight"
            )

            # Collect reachable EDGE geometry (network-constrained), not the
            # convex hull of reachable point samples. Partially-reachable
            # edges (one endpoint beyond the cutoff) are clipped at the
            # travel-budget fraction so the polygon does not over-extend past
            # the last reachable point (review C).
            #
            # Issue #443: this used to scan ALL E graph edges per facility
            # (O(F x E) Python-level iteration plus per-edge shapely substring
            # clipping on boundary edges; measured 7.2 s for a tiny-budget
            # query over a 19.8k-edge grid with F=2, dominated by that scan
            # plus the iterrows() graph build). An edge is reachable iff at
            # least one endpoint is in the cutoff-bounded Dijkstra set, so
            # the scan walks the adjacency of the reachable nodes only — in
            # G.nodes order with undirected-(u, v, key) dedup, reproducing
            # networkx's EdgeView traversal exactly, so the reachable-edge
            # sets, clipping and polygons are IDENTICAL to the full scan.
            # Per-facility Dijkstra trees are kept deliberately: the output
            # is one polygon per facility, which a merged multi-source tree
            # cannot produce.
            # (#681) for auditing _clip_failures is counted in `summary`.
            _clip_failures = 0
            reachable_edges = []
            seen_edge = set()
            reachable_nodes_ordered = [n for n in G.nodes if n in lengths]
            for u0 in cancellable(reachable_nodes_ordered, every=1024):
                for v0, keys in G[u0].items():
                    for key, edata in keys.items():
                        if (u0, v0, key) in seen_edge:
                            continue
                        seen_edge.add((u0, v0, key))
                        seen_edge.add((v0, u0, key))
                        eg = edata.get("geometry")
                        if eg is None:
                            continue
                        du = lengths.get(u0)
                        dv = lengths.get(v0)
                        if du is None and dv is None:
                            continue
                        du_ok = du is not None and du <= max_dist
                        dv_ok = dv is not None and dv <= max_dist
                        if du_ok and dv_ok:
                            reachable_edges.append(eg)  # fully within budget
                            continue
                        # Partially reachable: clip at the budget fraction(s).
                        # (#681): clip side is anchored to the REACHABLE endpoint,
                        # NOT to node insertion order. eg's coordinate direction is
                        # the source GeoJSON writing direction (start_node=coords[0]),
                        # which may be opposite to node_order — anchoring to
                        # node_order clips the unreachable tail instead. Aligns with
                        # V2 service_area.py which clips from the reachable node
                        # outward along the directed edge geometry.
                        try:
                            w = edata.get("weight") or eg.length
                            if w <= 0:
                                w = eg.length or 1.0
                            # Geometry-anchored orientation: does eg start at u0
                            # or at v0? Compare eg.coords[0] to the endpoint
                            # coordinates (cheap) rather than O(n) reprojection.
                            c0 = eg.coords[0]
                            # u0/v0 are the actual endpoint tuples used as graph
                            # nodes (hashable float pairs); exact tuple equality
                            # holds because add_edge stores those same objects.
                            eg_starts_at_u0 = (c0[0] == u0[0] and c0[1] == u0[1])
                            if not eg_starts_at_u0:
                                # Verify against v0 to handle floating noise from
                                # UTM round-trips on degenerate edges; fall back
                                # to project distance if neither matches exactly.
                                if not (c0[0] == v0[0] and c0[1] == v0[1]):
                                    try:
                                        d_u0 = eg.project(Point(u0))
                                        d_v0 = eg.project(Point(v0))
                                        eg_starts_at_u0 = d_u0 < d_v0
                                    except Exception:
                                        eg_starts_at_u0 = True
                            if du_ok:
                                frac = min(1.0, max(0.0, (max_dist - du) / w))
                                if frac > 0:
                                    if eg_starts_at_u0:
                                        seg = substring(eg, 0.0, frac, normalized=True)
                                    else:
                                        seg = substring(eg, 1.0 - frac, 1.0, normalized=True)
                                    if not seg.is_empty:
                                        reachable_edges.append(seg)
                            if dv_ok:
                                frac = min(1.0, max(0.0, (max_dist - dv) / w))
                                if frac > 0:
                                    if eg_starts_at_u0:
                                        seg = substring(eg, 1.0 - frac, 1.0, normalized=True)
                                    else:
                                        seg = substring(eg, 0.0, frac, normalized=True)
                                    if not seg.is_empty:
                                        reachable_edges.append(seg)
                        except Exception as exc:
                            _clip_failures += 1
                            logger.warning(
                                "isochrone clip failed for edge %r-%r: %s — skipping edge",
                                u0, v0, exc,
                            )

            total_clip_failures += _clip_failures
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
        if total_clip_failures:
            logger.warning("isochrone clipping failed for %d edge(s)", total_clip_failures)
            summary += f" ({total_clip_failures} edge(s) skipped: clip failure)"
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
        
        # Pre-compute properties and geometries outside loop (audit S40).
        # Reproject back to WGS84 — gdf_src is still in UTM after the KDTree.
        # Isochrones in this file already do this; leaving metres here made
        # the result FeatureCollection plot as easting/northing-as-lon/lat.
        props = gdf_src.drop(columns='geometry').to_dict('records')
        geom_maps = [mapping(g) for g in gdf_src.to_crs("EPSG:4326").geometry]
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
