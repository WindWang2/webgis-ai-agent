"""MapSpec presentation for moving-object trajectory products (WP4).

Mirrors the OD-flow product fragment discipline: sources reference session
refs (never inline data), layers carry legend_spec + provenance + a
``trajectory`` contract block for the frontend panel, and every visual
encoding is derived from real measured quantities (segment mean speed
km/h) with the unit stated in the legend.

Honesty:
- Segments whose path length is zero (all fixes coincident in space or
  time) draw nothing — a zero-length line is not motion — and are counted
  in ``stats.segments_zero_extent``.
- Speed domain: measured p05..p95 of segment mean speeds (clamped);
  the legend states the unit (km/h) and the clamping policy. Never a
  fabricated 0..max domain when data says otherwise.
- Dwell radius is display-scale, not analysis truth — the analysis
  thresholds live in the product config and every dwell feature property.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

#: Default speed palette (cool → hot), used when config.palette is absent.
DEFAULT_SPEED_PALETTE = ["#2c7bb6", "#abd9e9", "#ffffbf", "#fdae61", "#d7191c"]
NEUTRAL_COLOR = "#94a3b8"
DWELL_COLOR = "#f59e0b"
EVENT_COLOR = "#7c3aed"

TRAJECTORY_LAYER_PARTS = ("lines", "direction", "dwell", "events")
TRAJECTORY_SOURCE_PARTS = ("lines", "dwell", "events")


def trajectory_layer_ids(family_id: str) -> List[str]:
    return [f"traj-{family_id}-{part}" for part in TRAJECTORY_LAYER_PARTS]


def trajectory_source_ids(family_id: str) -> List[str]:
    return [f"traj-{family_id}-{part}-source" for part in TRAJECTORY_SOURCE_PARTS]


def compute_speed_domain(
    speed_sample: List[Any],
) -> Tuple[List[float], bool]:
    """Measured p05/p95 speed domain (km/h) from real segment mean speeds.

    ``speed_sample`` is a bounded flat list of km/h floats (product stats
    carry it capped at 5k). Returns (domain, constant) — constant=True when
    fewer than 2 distinct finite speeds exist (single stop data, tiny
    sets): the legend renders categorical instead of pretending a gradient.
    """
    speeds = sorted(
        s for s in speed_sample
        if isinstance(s, (int, float)) and not isinstance(s, bool)
        and s == s and 0 <= s
    )
    if len(speeds) < 2:
        return ([speeds[0], speeds[0]] if speeds else [0.0, 0.0]), True
    def _pct(q: float) -> float:
        idx = min(len(speeds) - 1, max(0, int(round(q * (len(speeds) - 1)))))
        return float(speeds[idx])
    lo, hi = _pct(0.05), _pct(0.95)
    if hi <= lo:
        hi = lo + 1e-9
    return [lo, hi], False


def _speed_paint(domain: List[float], palette: List[str], width_range: Tuple[float, float]) -> Dict[str, Any]:
    lo, hi = domain
    return {
        "line-color": {
            "method": "interpolate",
            "field": "speed_encoded",
            "stops": [[lo, palette[0]], [(lo + hi) / 2.0, palette[len(palette) // 2]], [hi, palette[-1]]],
        },
        "line-width": {
            "method": "interpolate",
            "field": "speed_encoded",
            "stops": [[lo, width_range[0]], [hi, width_range[1]]],
        },
        "line-opacity": 0.9,
    }


def build_map_fragment(product: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the trajectory MapSpec fragment (sources by ref_id only)."""
    family_id = str(product["family_id"])
    line_ref = str(product.get("line_ref") or "")
    dwell_ref = str(product.get("dwell_ref") or "")
    event_ref = str(product.get("event_ref") or "")
    stats = product.get("stats", {})
    domain, constant = compute_speed_domain(stats.get("speed_sample") or [])
    palette = list(config.get("palette") or DEFAULT_SPEED_PALETTE)
    width_range = (
        float(config.get("width_min_px", 1.5)),
        float(config.get("width_max_px", 7.0)),
    )

    legend_spec: Dict[str, Any] = {
        "type": "categorical" if constant else "continuous",
        "field": "speed_encoded",
        "source_field": "mean_speed_kmh（观测区间估计）",
        "source_domain": domain,
        "min": domain[0] if constant else 0.0,
        "max": domain[1],
        "palette_colors": [palette[0]] if constant else [palette[0], palette[-1]],
        "title": "轨迹平均速度（km/h，段间观测估计；灰=零历时段/缺失）",
        "unit": "km/h",
        "fixed_domain": False,
        "note": (
            "速度为相邻观测间估计（区间语义，非瞬时值）；断开段=数据断点；"
            "驻留半径为显示标注，分析阈值见参数"
        ),
        "time_window": None,
    }

    trajectory_block = {
        "schema_version": "moving_object_trajectory.v1",
        "product_ref": product.get("product_ref"),
        "family_id": family_id,
        "source_ref": product.get("source_ref"),
        "source_revision": int(product.get("source_revision") or 0),
        "source_fingerprint": product.get("source_fingerprint"),
        "product_fingerprint": product.get("fingerprint"),
        "stats": stats,
        "encoding": {
            "speed_field": "speed_encoded",
            "speed_unit": "km/h",
            "domain": domain,
            "palette": palette,
            "width_min_px": width_range[0],
            "width_max_px": width_range[1],
            "show_direction": bool(config.get("show_direction", True)),
            "dwell_color": DWELL_COLOR,
            "event_color": EVENT_COLOR,
        },
        "coverage": product.get("coverage"),
        "assumptions": product.get("assumptions"),
    }

    layers: List[Dict[str, Any]] = []
    sources: Dict[str, Any] = {}
    if line_ref:
        sources[f"traj-{family_id}-lines-source"] = {"type": "geojson", "ref_id": line_ref}
        layers.append({
            "id": f"traj-{family_id}-lines",
            "type": "line",
            "source": f"traj-{family_id}-lines-source",
            "paint": _speed_paint(domain, palette, width_range),
            "legend_spec": legend_spec,
            "trajectory": trajectory_block,
            "data_binding": {"field": "speed_encoded", "field_type": "number"},
            "metadata": {"trajectory_family_id": family_id, "trajectory_part": "lines"},
            "provenance": {"result_ref": line_ref, "product_ref": product.get("product_ref")},
        })
        if trajectory_block["encoding"]["show_direction"]:
            layers.append({
                "id": f"traj-{family_id}-direction",
                "type": "symbol",
                "source": f"traj-{family_id}-lines-source",
                "layout": {
                    "symbol-placement": "line",
                    "symbol-spacing": 60,
                    "text-field": "▶",
                    "text-size": 10,
                    "text-rotation-alignment": "map",
                    "text-keep-upright": False,
                    "text-allow-overlap": True,
                    "text-ignore-placement": True,
                },
                "paint": {
                    "text-color": palette[-1],
                    "text-halo-color": "#ffffff",
                    "text-halo-width": 1.0,
                },
                "metadata": {"trajectory_family_id": family_id, "trajectory_part": "direction"},
            })
    if dwell_ref:
        sources[f"traj-{family_id}-dwell-source"] = {"type": "geojson", "ref_id": dwell_ref}
        layers.append({
            "id": f"traj-{family_id}-dwell",
            "type": "circle",
            "source": f"traj-{family_id}-dwell-source",
            "paint": {
                "circle-radius": 6,
                "circle-color": DWELL_COLOR,
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 1.2,
                "circle-opacity": 0.9,
            },
            "metadata": {"trajectory_family_id": family_id, "trajectory_part": "dwell"},
            "provenance": {"result_ref": dwell_ref, "product_ref": product.get("product_ref")},
        })
    if event_ref:
        sources[f"traj-{family_id}-events-source"] = {"type": "geojson", "ref_id": event_ref}
        layers.append({
            "id": f"traj-{family_id}-events",
            "type": "circle",
            "source": f"traj-{family_id}-events-source",
            "paint": {
                "circle-radius": 5,
                "circle-color": EVENT_COLOR,
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 1.0,
                "circle-opacity": 0.85,
            },
            "metadata": {"trajectory_family_id": family_id, "trajectory_part": "events"},
            "provenance": {"result_ref": event_ref, "product_ref": product.get("product_ref")},
        })
    return {"sources": sources, "layers": layers}
