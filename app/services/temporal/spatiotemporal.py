"""
Spatiotemporal Cluster Engine.
Adapts space-time clustering (ST-DBSCAN) into structured SpatiotemporalHotspotResult models.
"""

import logging
from typing import Any, Dict, List

from app.services.temporal.models import SpatiotemporalHotspotResult
from app.lib.geo_analysis.statistics import st_dbscan_narrated

logger = logging.getLogger(__name__)


class SpatiotemporalClusterEngine:
    """
    Spatio-Temporal Clustering Engine. Wraps ST-DBSCAN and formats outputs.
    """

    def cluster(
        self,
        geojson: Dict[str, Any],
        eps1_spatial_meters: float = 1000.0,
        eps2_temporal_seconds: float = 3600.0,
        min_samples: int = 5,
        timestamp_field: str = "timestamp",
    ) -> SpatiotemporalHotspotResult:
        """
        Executes ST-DBSCAN space-time clustering and produces a SpatiotemporalHotspotResult.
        """
        if not geojson or not isinstance(geojson, dict):
            return SpatiotemporalHotspotResult(
                success=False,
                summary="Invalid input GeoJSON dictionary provided.",
                error_message="InvalidInput",
            )

        res = st_dbscan_narrated(
            geojson=geojson,
            eps1_spatial_meters=eps1_spatial_meters,
            eps2_temporal_seconds=eps2_temporal_seconds,
            min_samples=min_samples,
            timestamp_field=timestamp_field,
        )

        if not res.success or res.data is None:
            return SpatiotemporalHotspotResult(
                success=False,
                summary=res.summary or "ST-DBSCAN failed",
                error_message=res.error_type or "ClusteringError",
            )

        data = res.data
        stats = data.get("cluster_stats", {})
        features = data.get("features", [])

        # Build list of cluster summaries
        clusters_list: List[Dict[str, Any]] = []
        cluster_groups: Dict[int, List[Dict[str, Any]]] = {}

        for f in features:
            cid = f.get("properties", {}).get("cluster_id", -1)
            if cid >= 0:
                if cid not in cluster_groups:
                    cluster_groups[cid] = []
                cluster_groups[cid].append(f)

        for cid, f_list in cluster_groups.items():
            clusters_list.append({
                "cluster_id": cid,
                "point_count": len(f_list),
            })

        return SpatiotemporalHotspotResult(
            success=True,
            total_clusters=stats.get("total_clusters", len(clusters_list)),
            clustered_points=stats.get("clustered_points", 0),
            noise_points=stats.get("noise_points", 0),
            temporal_span_hours=stats.get("temporal_span_hours", 0.0),
            cluster_stats=stats,
            clusters=clusters_list,
            features=features,
            summary=res.summary,
        )
