"""
Network Graph Builder Component.
Builds directed graph (NetworkX DiGraph) and NetworkDataset from GeoJSON or OSM data.
Supports intersection splitting, endpoint snapping, one-way enforcement, speed/cost computation,
and LRU fingerprint caching.
"""
from __future__ import annotations
import math
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple, Union
from collections import OrderedDict

import networkx as nx
from shapely import STRtree
from shapely.geometry import shape, LineString, MultiLineString, Point, MultiPoint, GeometryCollection, mapping
from shapely.ops import unary_union, split

from app.services.network.models import (
    Node,
    Edge,
    NetworkDataset,
    TravelProfile,
)


def haversine_distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Calculates haversine geodesic distance in meters between two (lng, lat) points."""
    lng1, lat1 = p1
    lng2, lat2 = p2
    if abs(lng1 - lng2) < 1e-9 and abs(lat1 - lat2) < 1e-9:
        return 0.0
    r = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(min(1.0, a)), math.sqrt(max(0.0, 1.0 - a)))
    return r * c


def linestring_length_m(coords: List[Tuple[float, float]]) -> float:
    """Calculates total haversine length in meters for a coordinate sequence."""
    total = 0.0
    for i in range(len(coords) - 1):
        total += haversine_distance(coords[i], coords[i + 1])
    return total


import threading


class NetworkGraphBuilder:
    """
    Builds, optimizes, and caches network graphs for routing and spatial network analysis.
    """

    def __init__(self, max_cache_size: int = 32):
        self.max_cache_size = max_cache_size
        # The third slot pins the *input* NetworkDataset alive so its id()
        # (used in the fingerprint) cannot be reused by CPython while the
        # entry is live — the cache value itself is the freshly-built dataset,
        # not the keyed input, so without this pin a freed input's address
        # could be reused by a later dataset and silently return the wrong
        # cached graph (review N-F05-regression).
        self._cache: OrderedDict[str, Tuple[nx.DiGraph, NetworkDataset, Any]] = OrderedDict()
        self._cache_lock = threading.Lock()
        # Fingerprint memo: id(data) -> (serialized data string, strong ref).
        # Rebuilding a graph from the same GeoJSON dict used to re-run
        # json.dumps over the whole network on EVERY build_graph call (cache
        # hit included). The strong ref prevents CPython from reusing the id()
        # of a freed object for a different dataset (same pinning argument as
        # the graph cache entries). Bounded by max_cache_size (LRU).
        self._fp_memo: OrderedDict[int, Tuple[str, Any]] = OrderedDict()

    def clear_cache(self) -> None:
        """Clears the LRU graph cache."""
        with self._cache_lock:
            self._cache.clear()
            self._fp_memo.clear()

    def get_cache_info(self) -> Dict[str, int]:
        """Returns cache capacity and current entry count."""
        return {"size": len(self._cache), "max_size": self.max_cache_size}

    def _memoized_data_str(self, data: Dict[str, Any]) -> str:
        """Serializes a GeoJSON dict once per object identity.

        ``json.dumps(sort_keys=True)`` of a 100k-line city network is the
        dominant cost of every ``build_graph`` call (cache hit or not); the
        memo makes repeated builds of the SAME dict object a dict lookup.
        """
        key = id(data)
        with self._cache_lock:
            entry = self._fp_memo.get(key)
            if entry is not None and entry[1] is data:
                self._fp_memo.move_to_end(key)
                return entry[0]
            data_str = json.dumps(data, sort_keys=True)
            self._fp_memo[key] = (data_str, data)
            while len(self._fp_memo) > self.max_cache_size:
                self._fp_memo.popitem(last=False)
            return data_str

    def compute_fingerprint(
        self,
        data: Any,
        profile: Optional[TravelProfile],
        snap_tolerance: float,
        split_intersections: bool,
    ) -> str:
        """Computes deterministic SHA-256 fingerprint hash for LRU caching."""
        try:
            if isinstance(data, dict):
                data_str = self._memoized_data_str(data)
            elif isinstance(data, NetworkDataset):
                # Object-identity fingerprint for NetworkDataset: dataset_id
                # alone collides (it historically hashed only the edge count,
                # so two different networks with equal edge counts shared a
                # cached graph — N-F05). The cache stores a strong reference
                # to the dataset, so id() cannot be reused while an entry is
                # live (same pinning argument as PointSnappingService). A
                # distinct dataset object always fingerprints distinctly.
                data_str = f"nds:{id(data)}:{data.edge_count}:{data.node_count}"
            else:
                data_str = str(data)
        except Exception:
            data_str = str(data)

        profile_str = profile.model_dump_json() if profile else "default"
        params_str = f"snap:{snap_tolerance}_split:{split_intersections}"
        combined = f"{data_str}|{profile_str}|{params_str}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def build_graph(
        self,
        data: Union[Dict[str, Any], NetworkDataset, List[Dict[str, Any]]],
        profile: Optional[TravelProfile] = None,
        snap_tolerance: float = 1e-5,
        split_intersections: bool = True,
        use_cache: bool = True,
    ) -> Tuple[nx.DiGraph, NetworkDataset]:
        """
        Builds a NetworkX DiGraph and NetworkDataset from input lines/GeoJSON/OSM.

        Args:
            data: GeoJSON FeatureCollection/dict, NetworkDataset, or list of features.
            profile: TravelProfile defining speeds, allowed road types, directionality.
            snap_tolerance: Node coordinate snapping tolerance in degrees.
            split_intersections: Whether to split intersecting lines at intersection points.
            use_cache: Whether to reuse cached graph if fingerprint matches.

        Returns:
            Tuple of (nx.DiGraph, NetworkDataset)
        """
        fp = self.compute_fingerprint(data, profile, snap_tolerance, split_intersections) if use_cache else ""
        if use_cache:
            with self._cache_lock:
                if fp in self._cache:
                    self._cache.move_to_end(fp)
                    graph, dataset, _pin = self._cache[fp]
                    return graph, dataset

        # Extract features and raw line geometries
        line_items = self._extract_line_items(data)
        if not line_items:
            g = nx.DiGraph()
            ds = NetworkDataset(
                dataset_id=f"net_{hashlib.md5(b'empty', usedforsecurity=False).hexdigest()[:8]}",
                nodes=[],
                edges=[],
            )
            return g, ds

        # Perform intersection splitting if enabled
        processed_lines = self._process_intersections(line_items, split_intersections)

        # Build Graph and Dataset
        graph = nx.DiGraph()
        node_map: Dict[Tuple[float, float], str] = {}
        nodes_list: List[Node] = []
        edges_list: List[Edge] = []

        def get_or_create_node(coord: Tuple[float, float]) -> str:
            snapped = (
                round(coord[0] / snap_tolerance) * snap_tolerance,
                round(coord[1] / snap_tolerance) * snap_tolerance,
            )
            snapped_clean = (round(snapped[0], 7), round(snapped[1], 7))
            if snapped_clean not in node_map:
                node_id = f"n{len(node_map)}"
                node_map[snapped_clean] = node_id
                node_obj = Node(id=node_id, x=snapped_clean[0], y=snapped_clean[1])
                nodes_list.append(node_obj)
                graph.add_node(
                    node_id,
                    x=snapped_clean[0],
                    y=snapped_clean[1],
                    properties={},
                )
            return node_map[snapped_clean]

        edge_counter = 0
        default_speed = profile.speed_kmh if profile else 40.0
        one_way_strict = profile.one_way_strict if profile else True

        min_x, min_y, max_x, max_y = float("inf"), float("inf"), float("-inf"), float("-inf")

        for line_geom, props in processed_lines:
            coords = list(line_geom.coords)
            if len(coords) < 2:
                continue

            for c in coords:
                min_x = min(min_x, c[0])
                min_y = min(min_y, c[1])
                max_x = max(max_x, c[0])
                max_y = max(max_y, c[1])

            u_id = get_or_create_node((coords[0][0], coords[0][1]))
            v_id = get_or_create_node((coords[-1][0], coords[-1][1]))

            if u_id == v_id:
                continue

            length_m = linestring_length_m(coords)

            speed = props.get("speed_kmh")
            if speed is None or speed <= 0:
                speed = default_speed

            travel_time_s = length_m / ((speed * 1000.0) / 3600.0) if speed > 0 else length_m / 10.0
            hw_type = props.get("highway_type", props.get("highway", "unclassified"))

            one_way_raw = props.get("one_way", props.get("oneway", False))
            is_one_way = False
            if one_way_strict:
                if isinstance(one_way_raw, bool):
                    is_one_way = one_way_raw
                elif isinstance(one_way_raw, str):
                    is_one_way = one_way_raw.lower() in ["yes", "1", "true", "t"]
                elif isinstance(one_way_raw, (int, float)):
                    is_one_way = bool(one_way_raw)

            # Add u -> v edge.
            #
            # #457: the DiGraph holds exactly ONE edge per directed (u, v)
            # pair — a second add with the same pair used to silently REPLACE
            # the first in the graph while the dataset kept both ids,
            # desyncing dataset edge ids from the graph (the snapper returns
            # dataset edge ids that routing then looks up in the graph).
            # Duplicate/parallel linework between the same node pair is now
            # deduplicated in BOTH structures, keeping them 1:1 (the first
            # geometry wins). The check is per DIRECTED pair so a two-way
            # duplicate still contributes the v->u direction when the first
            # line was one-way.
            if not graph.has_edge(u_id, v_id):
                edge_id_uv = f"e{edge_counter}"
                edge_counter += 1
                geom_dict_uv = mapping(line_geom)

                graph.add_edge(
                    u_id,
                    v_id,
                    id=edge_id_uv,
                    u=u_id,
                    v=v_id,
                    length_m=length_m,
                    speed_kmh=speed,
                    travel_time_s=travel_time_s,
                    highway_type=hw_type,
                    geometry=geom_dict_uv,
                    one_way=is_one_way,
                    properties=props,
                )

                edge_obj_uv = Edge(
                    id=edge_id_uv,
                    u=u_id,
                    v=v_id,
                    length_m=length_m,
                    speed_kmh=speed,
                    travel_time_s=travel_time_s,
                    highway_type=hw_type,
                    geometry=geom_dict_uv,
                    one_way=is_one_way,
                    properties=props,
                )
                edges_list.append(edge_obj_uv)

            # Add reverse v -> u edge if not one-way
            if not is_one_way and not graph.has_edge(v_id, u_id):
                edge_id_vu = f"e{edge_counter}"
                edge_counter += 1
                rev_line = LineString(list(reversed(coords)))
                geom_dict_vu = mapping(rev_line)

                graph.add_edge(
                    v_id,
                    u_id,
                    id=edge_id_vu,
                    u=v_id,
                    v=u_id,
                    length_m=length_m,
                    speed_kmh=speed,
                    travel_time_s=travel_time_s,
                    highway_type=hw_type,
                    geometry=geom_dict_vu,
                    one_way=False,
                    properties=props,
                )

                edge_obj_vu = Edge(
                    id=edge_id_vu,
                    u=v_id,
                    v=u_id,
                    length_m=length_m,
                    speed_kmh=speed,
                    travel_time_s=travel_time_s,
                    highway_type=hw_type,
                    geometry=geom_dict_vu,
                    one_way=False,
                    properties=props,
                )
                edges_list.append(edge_obj_vu)

        bbox = [min_x, min_y, max_x, max_y] if min_x != float("inf") else [0.0, 0.0, 0.0, 0.0]
        dataset_id = f"net_{hashlib.sha256(str(len(edges_list)).encode()).hexdigest()[:8]}"

        dataset = NetworkDataset(
            dataset_id=dataset_id,
            crs="EPSG:4326",
            node_count=len(nodes_list),
            edge_count=len(edges_list),
            bounding_box=bbox,
            nodes=nodes_list,
            edges=edges_list,
        )

        if use_cache:
            with self._cache_lock:
                # Pin the input NetworkDataset so its id() (in the fingerprint)
                # cannot be reused while this entry is live.
                pin = data if isinstance(data, NetworkDataset) else None
                self._cache[fp] = (graph, dataset, pin)
                if len(self._cache) > self.max_cache_size:
                    self._cache.popitem(last=False)

        return graph, dataset

    def _extract_line_items(self, data: Any) -> List[Tuple[LineString, Dict[str, Any]]]:
        """Parses inputs into a list of (LineString, properties)."""
        items: List[Tuple[LineString, Dict[str, Any]]] = []

        if isinstance(data, NetworkDataset):
            node_by_id = {n.id: n for n in data.nodes}
            for edge in data.edges:
                if edge.geometry:
                    geom = shape(edge.geometry)
                    if isinstance(geom, LineString):
                        items.append((geom, edge.properties))
                else:
                    u_node = node_by_id.get(edge.u)
                    v_node = node_by_id.get(edge.v)
                    if u_node and v_node:
                        geom = LineString([(u_node.x, u_node.y), (v_node.x, v_node.y)])
                        items.append((geom, edge.properties))
            return items

        if isinstance(data, dict):
            if data.get("type") == "FeatureCollection":
                features = data.get("features", [])
            elif data.get("type") == "Feature":
                features = [data]
            else:
                features = data.get("features", [])
        elif isinstance(data, list):
            features = data
        else:
            features = []

        for feat in features:
            if not isinstance(feat, dict):
                continue
            props = feat.get("properties", {})
            geom_raw = feat.get("geometry")
            if not geom_raw:
                continue
            geom = shape(geom_raw)
            if isinstance(geom, LineString):
                items.append((geom, props))
            elif isinstance(geom, MultiLineString):
                for sub_line in geom.geoms:
                    items.append((sub_line, props))

        return items

    def _process_intersections(
        self,
        line_items: List[Tuple[LineString, Dict[str, Any]]],
        split_intersections: bool,
    ) -> List[Tuple[LineString, Dict[str, Any]]]:
        """Splits lines at intersection points while maintaining line directionality.

        Issue #457: COLLILINEAR OVERLAPS are split too. The previous code
        collected only Point/MultiPoint intersections, so a pair of overlapping
        lines — whose intersection is a LineString (or a GeometryCollection
        containing one) — was silently never split, leaving the shared
        sub-segment as a disconnected parallel edge. Now the overlap's
        ENDPOINTS join the cutter set, cutting both lines where the shared run
        begins/ends so it becomes real junction-connected edges.
        """
        if not split_intersections or len(line_items) <= 1:
            return line_items

        lines = [item[0] for item in line_items]

        # Find all intersection points between lines. The naive O(n²) pairwise
        # loop performed ~5×10⁹ shapely intersection tests for a 100k-line
        # city network (split_intersections=True is the default on every cache
        # miss). A STRtree candidate query restricts the precise intersects
        # test to bounding-box-overlapping pairs (typically a handful per
        # line), giving the exact same point set with a fraction of the work.
        intersection_points: List[Point] = []

        def _collect_split_points(inter: Any) -> None:
            """Add every split-worthy location of an intersection geometry."""
            if isinstance(inter, Point):
                intersection_points.append(inter)
            elif isinstance(inter, MultiPoint):
                intersection_points.extend(inter.geoms)
            elif isinstance(inter, LineString):
                # Collinear overlap (#457): cut at both ends of the shared run.
                if not inter.is_empty and len(inter.coords) >= 2:
                    intersection_points.append(Point(inter.coords[0]))
                    intersection_points.append(Point(inter.coords[-1]))
            elif isinstance(inter, MultiLineString):
                for part in inter.geoms:
                    _collect_split_points(part)
            elif isinstance(inter, GeometryCollection):
                for part in inter.geoms:
                    _collect_split_points(part)

        tree = STRtree(lines)
        for i, line in enumerate(lines):
            for j in tree.query(line, predicate="intersects"):
                j = int(j)
                if j <= i:
                    # Skip self (j == i) and pairs already handled when the
                    # earlier line was queried (j < i).
                    continue
                inter = line.intersection(lines[j])
                if not inter.is_empty:
                    _collect_split_points(inter)

        if not intersection_points:
            return line_items

        split_cutter = unary_union(intersection_points)
        result_items: List[Tuple[LineString, Dict[str, Any]]] = []

        for line, props in line_items:
            try:
                split_res = split(line, split_cutter)
                for sub_geom in split_res.geoms:
                    if isinstance(sub_geom, LineString) and sub_geom.length > 1e-9:
                        # Ensure directional orientation matches original line
                        sub_coords = list(sub_geom.coords)
                        d_start = line.project(Point(sub_coords[0]))
                        d_end = line.project(Point(sub_coords[-1]))
                        if d_start > d_end:
                            sub_coords = list(reversed(sub_coords))
                            sub_geom = LineString(sub_coords)
                        result_items.append((sub_geom, props))
            except Exception:
                result_items.append((line, props))

        return result_items
