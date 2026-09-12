"""Data Profile V9 —— 矢量/栅格统一画像 + 规则联动（P2）。

任务书两项：

1. **统一画像**：一次调用产出有界 dict —— 矢量（字段统计/几何族/空间分布
   H3 直方图）或栅格（波段统计/nodata 映射/分辨率），供 F 线面板与规则
   引擎消费；
2. **画像 → 规则联动**：``suggest_rule_params`` 从画像实测统计推导规则
   阈值参数（值域 = 观测 min/max ± padding、时间断裂字段 = 时间候选、
   主键字段 = 高唯一度候选）—— 规则引用画像统计做阈值，不再拍脑袋。

失效：``install_invalidation_hook`` 把 DatasetProfiler 缓存接入
ref_lifecycle 单一失效权威（R6 观察者模式；数据变更 → 画像过期）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_MAX_INLINE_FEATURES = 20000


# ── 统一画像 ─────────────────────────────────────────────────────────


def build_unified_profile(
    payload: Dict[str, Any],
    *,
    distribution_resolution: int = 7,
    max_features: int = _MAX_INLINE_FEATURES,
) -> Dict[str, Any]:
    """payload（geojson | raster_stats）→ 统一有界画像 dict。"""
    from app.lib.data.profile import profile_features
    from app.services.data_profile.distribution import spatial_distribution

    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 dict")
    raster_stats = payload.get("raster_stats")
    geojson = payload.get("geojson")

    if isinstance(raster_stats, dict) and raster_stats:
        return {
            "kind": "raster",
            "crs": str(raster_stats.get("crs") or payload.get("crs") or ""),
            "raster": _raster_section(raster_stats),
            "vector": None,
            "distribution": None,
            "diagnostics": [],
        }
    if isinstance(geojson, dict) and isinstance(geojson.get("features"), list):
        features = geojson["features"]
        vp, quality = profile_features(
            features, crs=str(payload.get("crs") or ""),
            max_scan_rows=min(max_features, 50000),
        )
        return {
            "kind": "vector",
            "crs": str(payload.get("crs") or ""),
            "vector": {
                "quality": str(getattr(quality, "value", quality)),
                "row_count": int(vp.row_count),
                "scanned_rows": int(vp.scanned_rows),
                "geometry_types": [str(g) for g in vp.geometry_types[:8]],
                "extent": vp.extent,
                "empty_geometry_count": int(vp.empty_geometry_count),
                "impossible_coordinate_count": int(vp.impossible_coordinate_count),
                "duplicate_coordinate_count": int(vp.duplicate_coordinate_count),
                "temporal_fields": [str(t) for t in vp.temporal_fields[:8]],
                "fields": {
                    name: {
                        "dtype": str(getattr(fp, "dtype", ""))[:16],
                        "null_rate": getattr(fp, "null_rate", None),
                        "min": getattr(fp, "min", None),
                        "max": getattr(fp, "max", None),
                        "mean": getattr(fp, "mean", None),
                        "std": getattr(fp, "std", None),
                        "unique_count": getattr(fp, "unique_count", None),
                    }
                    for name, fp in list(vp.fields.items())[:24]
                },
            },
            "raster": None,
            "distribution": spatial_distribution(
                features, resolution=distribution_resolution,
                max_features=max_features,
            ),
            "diagnostics": [],
        }
    raise ValueError("payload 需含 geojson FeatureCollection 或 raster_stats")


def _raster_section(raster_stats: Dict[str, Any]) -> Dict[str, Any]:
    """栅格段：波段统计 + nodata 映射（按波段）+ 分辨率。"""
    bands_in = raster_stats.get("band_stats") or raster_stats.get("bands") or []
    nodata_list = raster_stats.get("nodata") or []
    bands_out: List[Dict[str, Any]] = []
    for i, b in enumerate(bands_in[:16]):
        if not isinstance(b, dict):
            continue
        nd = nodata_list[i] if i < len(nodata_list) else None
        bands_out.append({
            "band": b.get("band", i + 1),
            "dtype": str(b.get("dtype") or "")[:16],
            "min": b.get("min"), "max": b.get("max"),
            "mean": b.get("mean"), "std": b.get("std"),
            "valid_pixel_ratio": b.get("valid_pixel_ratio"),
            "nodata": nd if (nd is None or isinstance(nd, (int, float))) else str(nd)[:32],
        })
    return {
        "width": raster_stats.get("width"),
        "height": raster_stats.get("height"),
        "band_count": raster_stats.get("band_count", len(bands_out)),
        "resolution_x": raster_stats.get("resolution_x"),
        "resolution_y": raster_stats.get("resolution_y"),
        "nodata_ratio": raster_stats.get("nodata_ratio"),
        "bands": bands_out,
    }


# ── 画像 → 规则联动 ──────────────────────────────────────────────────


def suggest_rule_params(profile: Dict[str, Any], *, domain_padding: float = 0.1) -> List[Dict[str, Any]]:
    """画像实测统计 → 建议规则集（parse_rule_defs 兼容的 dict 列表）。

    只从**有证据**的字段推导（无观测就无建议，绝不虚构阈值）。
    """
    rules: List[Dict[str, Any]] = []
    if profile.get("kind") == "vector" and profile.get("vector"):
        vector = profile["vector"]
        fields = vector.get("fields") or {}
        domain_fields: Dict[str, Any] = {}
        for name, fp in list(fields.items())[:16]:
            mn, mx = fp.get("min"), fp.get("max")
            if isinstance(mn, (int, float)) and isinstance(mx, (int, float)) \
                    and not isinstance(mn, bool):
                pad = abs(mx - mn) * domain_padding or 1.0
                domain_fields[str(name)[:96]] = {"min": mn - pad, "max": mx + pad}
        if domain_fields:
            rules.append({
                "rule_id": "dq.suggested.attribute_domain",
                "rule_type": "attribute_domain",
                "severity": "warn",
                "params": {"fields": domain_fields},
                "description": "由画像观测值域 ±10% 推导（P2→P1 联动）",
            })
        temporal = vector.get("temporal_fields") or []
        if temporal:
            rules.append({
                "rule_id": "dq.suggested.temporal_gaps",
                "rule_type": "temporal_gaps",
                "severity": "warn",
                "params": {"time_field": str(temporal[0])[:96], "gap_factor": 4.0},
                "description": "由画像时间候选字段推导",
            })
        crs = str(profile.get("crs") or "")
        if not crs:
            rules.append({
                "rule_id": "dq.suggested.crs",
                "rule_type": "crs_validity",
                "severity": "error",
                "params": {"target_crs": "EPSG:4326"},
                "description": "画像未观察到 CRS",
            })
    elif profile.get("kind") == "raster" and profile.get("raster"):
        raster = profile["raster"]
        rules.append({
            "rule_id": "dq.suggested.nodata",
            "rule_type": "nodata_ratio",
            "severity": "warn",
            "params": {"max_nodata_ratio": 0.6},
            "description": "栅格画像 nodata 基线",
        })
        if raster.get("resolution_x"):
            rules.append({
                "rule_id": "dq.suggested.resolution",
                "rule_type": "resolution_drift",
                "severity": "warn",
                "params": {"expected_resolution": float(raster["resolution_x"]),
                           "max_anisotropy": 1.05},
                "description": "以画像观测分辨率为基线（P2→P1 联动）",
            })
    return rules


# ── 失效钩子（数据变更 → 画像过期） ─────────────────────────────────

_HOOK_INSTALLED = False


def install_invalidation_hook() -> bool:
    """把画像缓存接入 ref_lifecycle 单一失效权威（幂等；R6 观察者）。"""
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return True
    try:
        from app.services.ref_lifecycle import register_ref_invalidation_hook

        def _on_ref_invalidation(session_id: str, ref_id: str, reason: Any) -> None:
            try:
                from app.services.data_profile.profiler import get_dataset_profiler

                get_dataset_profiler().invalidate(session_id=session_id, ref=ref_id)
            except Exception:  # noqa: BLE001 — 观察者绝不影响失效权威
                logger.debug("[data-profile] invalidation hook failed", exc_info=True)

        register_ref_invalidation_hook(_on_ref_invalidation)
        _HOOK_INSTALLED = True
        return True
    except Exception:  # noqa: BLE001 — 注册失败仅失去主动失效（TTL/修订绑定兜底）
        logger.warning("[data-profile] invalidation hook registration failed",
                       exc_info=True)
        return False


install_invalidation_hook()


__all__ = [
    "build_unified_profile",
    "suggest_rule_params",
    "install_invalidation_hook",
]
