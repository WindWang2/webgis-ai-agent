"""
Safe Spatial Repair Pipeline: Performs non-destructive remediation operations on spatial datasets.
Produces clean Derived GeoJSON and an audit log of changes.
"""

import copy
import logging
import math
from typing import List, Dict, Any, Tuple, Optional
from shapely.geometry import (
    shape,
    mapping,
    MultiPolygon,
    MultiLineString,
    MultiPoint,
)
from shapely.validation import make_valid
import shapely

logger = logging.getLogger(__name__)


class SpatialRepairPipeline:
    @staticmethod
    def repair_dataset(
        geojson_data: Dict[str, Any],
        ops: Optional[List[str]] = None,
        tolerance: float = 1e-5,
        operations: Optional[List[str]] = None,
        source_crs: str = "EPSG:4326",
        target_crs: str = "EPSG:4326",
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Remediates GeoJSON dataset without mutating original input.
        Returns (repaired_geojson, audit_logs).

        Note: when ``crs_transform`` is active, ``snap_within_tolerance`` is
        applied AFTER reprojection, so ``tolerance`` is interpreted in the
        TARGET CRS units (e.g. meters for a projected CRS, degrees for
        EPSG:4326) — not in the source CRS units.
        """
        active_ops = ops if ops is not None else (operations or ["make_valid", "remove_empty"])

        # NON-DESTRUCTIVE: Deep copy input GeoJSON
        repaired_geojson = copy.deepcopy(geojson_data)
        features = repaired_geojson.get("features", [])
        if not isinstance(features, list) and repaired_geojson.get("type") == "Feature":
            features = [repaired_geojson]

        logs: List[str] = []
        cleaned_features = []

        # ----------------------------------------------------
        # Operation: crs_transform
        # ----------------------------------------------------
        transformer = None
        crs_transform_failures = 0
        if "crs_transform" in active_ops and source_crs != target_crs:
            try:
                from pyproj import Transformer
                transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
            except Exception as e:
                logs.append(f"crs_transform: Failed to initialize transformer from {source_crs} to {target_crs}: {e}")

        for idx, feat in enumerate(features):
            if not isinstance(feat, dict):
                logs.append(f"Feature {idx}: Skipped non-dictionary record")
                continue

            geom_raw = feat.get("geometry")

            # ----------------------------------------------------
            # Operation: remove_empty
            # ----------------------------------------------------
            if geom_raw is None:
                if "remove_empty" in active_ops:
                    logs.append(f"remove_empty: Removed feature at index {idx} with null geometry")
                    continue
                else:
                    cleaned_features.append(feat)
                    continue

            try:
                geom = shape(geom_raw)
            except Exception as e:
                logs.append(f"Feature {idx}: Skipped due to unparseable geometry: {e}")
                continue

            if geom.is_empty:
                if "remove_empty" in active_ops:
                    logs.append(f"remove_empty: Removed feature at index {idx} with empty geometry")
                    continue
                else:
                    cleaned_features.append(feat)
                    continue

            # ----------------------------------------------------
            # Operation: make_valid
            # ----------------------------------------------------
            if "make_valid" in active_ops and not geom.is_valid:
                try:
                    geom = make_valid(geom)
                    logs.append(f"make_valid: Repaired invalid geometry at feature index {idx}")
                except Exception as e:
                    logs.append(f"make_valid: Failed to repair feature index {idx}: {e}")

            # ----------------------------------------------------
            # Operation: normalize_geometry_type
            # ----------------------------------------------------
            if "normalize_geometry_type" in active_ops:
                old_type = geom.geom_type
                if old_type == "Polygon":
                    geom = MultiPolygon([geom])
                    logs.append(f"normalize_geometry_type: Converted Polygon to MultiPolygon at feature index {idx}")
                elif old_type == "LineString":
                    geom = MultiLineString([geom])
                    logs.append(f"normalize_geometry_type: Converted LineString to MultiLineString at feature index {idx}")
                elif old_type == "Point":
                    geom = MultiPoint([geom])
                    logs.append(f"normalize_geometry_type: Converted Point to MultiPoint at feature index {idx}")

            # ----------------------------------------------------
            # Operation: crs_transform (Coordinate Reprojection)
            # ----------------------------------------------------
            # Run BEFORE snapping so grid precision is interpreted in the
            # TARGET CRS units (audit GIS-17). Snapping in source units first
            # (e.g. degrees) then reprojecting would apply a geodesically
            # meaningless grid and leave vertices that snap to the wrong
            # coordinate in the target CRS.
            if transformer is not None:
                try:
                    from shapely.ops import transform
                    geom = transform(transformer.transform, geom)
                    logs.append(f"crs_transform: Reprojected geometry at feature index {idx}")
                except Exception as e:
                    crs_transform_failures += 1
                    logs.append(f"crs_transform: Failed to reproject feature index {idx}: {e}")

            # ----------------------------------------------------
            # Operation: snap_within_tolerance
            # ----------------------------------------------------
            if "snap_within_tolerance" in active_ops:
                try:
                    geom = shapely.set_precision(geom, grid_size=tolerance)
                    logs.append(
                        f"snap_within_tolerance: Snapped vertices of feature index {idx} with grid precision {tolerance} (target CRS units)"
                    )
                except Exception as e:
                    logs.append(f"snap_within_tolerance: Snapping failed for feature index {idx}: {e}")

            feat["geometry"] = mapping(geom)
            cleaned_features.append(feat)

        # #618-15: 数据集级 CRS 声明必须在「所有要素都成功投影」之后才更新。
        # 只要有任一要素投影失败（坐标停留在源 CRS），把数据集标记成目标 CRS
        # 就会让下游按错误基准解释这些坐标 —— 部分失败时保持源 CRS 声明并披露。
        if transformer is not None:
            if crs_transform_failures == 0:
                repaired_geojson["crs"] = {
                    "type": "name",
                    "properties": {"name": target_crs},
                }
                logs.append(f"crs_transform: Updated dataset CRS definition from {source_crs} to {target_crs}")
            else:
                logs.append(
                    f"crs_transform: {crs_transform_failures} feature(s) failed to reproject — "
                    f"dataset CRS definition left as {source_crs}"
                )

        # ----------------------------------------------------
        # Operation: deduplicate
        # ----------------------------------------------------
        if "deduplicate" in active_ops and cleaned_features:
            unique_features = []
            seen_hashes = set()
            for f_idx, f in enumerate(cleaned_features):
                geom_dict = f.get("geometry")
                props_dict = f.get("properties")

                try:
                    g_shape = shape(geom_dict)
                    wkb_key = g_shape.wkb
                except Exception:
                    wkb_key = str(geom_dict)

                props_key = str(sorted(props_dict.items())) if isinstance(props_dict, dict) else ""
                combined_key = (wkb_key, props_key)

                if combined_key not in seen_hashes:
                    seen_hashes.add(combined_key)
                    unique_features.append(f)
                else:
                    logs.append(f"deduplicate: Removed duplicate feature at index {f_idx}")
            cleaned_features = unique_features

        # ----------------------------------------------------
        # Operation: attribute_type_normalization
        # ----------------------------------------------------
        if "attribute_type_normalization" in active_ops and cleaned_features:
            all_keys = set()
            for f in cleaned_features:
                if isinstance(f.get("properties"), dict):
                    all_keys.update(f["properties"].keys())

            for f in cleaned_features:
                if not isinstance(f.get("properties"), dict):
                    f["properties"] = {}
                props = f["properties"]

                for k in all_keys:
                    if k not in props:
                        props[k] = None

                for k, v in list(props.items()):
                    if isinstance(v, str):
                        v_stripped = v.strip()
                        if v_stripped.isdigit() or (v_stripped.startswith("-") and v_stripped[1:].isdigit()):
                            props[k] = int(v_stripped)
                        else:
                            try:
                                f_val = float(v_stripped)
                                if not math.isnan(f_val) and not math.isinf(f_val):
                                    props[k] = f_val
                                else:
                                    props[k] = v_stripped
                            except ValueError:
                                props[k] = v_stripped
                    elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                        props[k] = None

            logs.append(
                f"attribute_type_normalization: Standardized property schemas and normalized attribute values across {len(cleaned_features)} features"
            )

        repaired_geojson["features"] = cleaned_features
        logger.info(f"[SpatialRepairPipeline] Applied {len(active_ops)} operations; generated {len(logs)} audit log entries.")
        return repaired_geojson, logs
