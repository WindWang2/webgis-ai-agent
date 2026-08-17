"""Issue #582: honest network-result trimming + od_matrix input bound.

Pre-fix defects (audit-verified, baseline master @ 4902905):
  1. ``trim_network_result``'s >40k-character last resort only cleared
     ``features`` keys, left ``od_matrix`` rows / ``routes[].coordinates`` /
     ``directions`` intact, and appended a "Payload truncated for context
     safety" notice unconditionally — payloads stayed multi-MB while claiming
     they were trimmed (90000 ODPair rows → 12MB, 60 routes × 2000 coords →
     5.7MB).
  2. ``network_od_matrix`` had no origins × destinations bound (contrast
     ``MAX_OPTIMIZE_STOPS`` for optimize_route), so multi-MB payloads flowed
     straight into the LLM context.

The fix: the fallback truncation now compresses od rows / route coordinates /
directions for real, records ``_*_trimmed`` summaries, and only claims
truncation when it happened; the od_matrix tool rejects oversized requests with
an explicit error (never silent truncation).
"""
import json

import pytest

from app.tools.network_tools import (
    MAX_OD_MATRIX_PAIRS,
    trim_network_result,
)


def _fake_route(coord_count: int, waypoint: int = 0) -> dict:
    """A Route-shaped dict (mirrors services/network/models.Route fields)."""
    return {
        "route_id": f"r{waypoint}",
        "origin_id": f"o{waypoint}",
        "destination_id": f"d{waypoint}",
        "profile_name": "driving",
        "total_distance_m": 1000.0 + waypoint,
        "total_time_s": 100.0 + waypoint,
        "total_cost": 100.0 + waypoint,
        "geometry": {
            "type": "LineString",
            "coordinates": [[116.0 + i * 1e-4, 39.0 + i * 1e-4] for i in range(coord_count)],
        },
        "path_node_ids": [f"n{i}" for i in range(10)],
        "path_edge_ids": [f"e{i}" for i in range(10)],
        "directions": [{"step": i, "instruction": f"直行 {i} 米"} for i in range(25)],
    }


def _fake_od_pairs(n: int) -> list:
    return [
        {
            "origin_id": f"o{i % 10}",
            "destination_id": f"d{i % 10}",
            "distance_m": float(i),
            "travel_time_s": float(i * 2),
            "reachable": True,
        }
        for i in range(n)
    ]


def _json_len(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


# ─── 1. honest fallback truncation: real compression, truthful notice ────────


def test_trim_od_matrix_90k_rows_bounded_and_notice_honest():
    """The 90000-row ODPair payload (the audit's 12MB repro) must be compressed
    for real — under the 40k-char budget with a truthful notice and an explicit
    _od_matrix_trimmed summary."""
    payload = {
        "analysis_type": "od_matrix",
        "status": "success",
        "summary": {"origin_count": 300, "destination_count": 300},
        "od_matrix": _fake_od_pairs(90000),
        "warnings": [],
    }
    out = trim_network_result(payload)
    assert len(out["od_matrix"]) <= 50, "od rows must be genuinely capped"
    trimmed = out["_od_matrix_trimmed"]
    assert trimmed["original_pair_count"] == 90000
    assert trimmed["kept_pair_count"] == 50
    assert _json_len(out) < 40000, "output must land under the context budget"
    # The notice is only attached because truncation really happened.
    assert "_payload_notice" in out
    assert "trimmed" in out["_payload_notice"].lower()


def test_trim_60_routes_x_2000_coords():
    """The 60-route × 2000-coordinate payload (audit's 5.7MB repro): routes are
    capped (with _routes_trimmed), oversized route geometries collapse to
    descriptors, long directions lists are capped — all under budget."""
    payload = {
        "analysis_type": "optimize_route",
        "status": "success",
        "summary": {"stop_count": 60},
        "routes": [_fake_route(2000, waypoint=i) for i in range(60)],
    }
    out = trim_network_result(payload)
    assert len(out["routes"]) <= 50
    assert out["_routes_trimmed"]["original_route_count"] == 60
    for route in out["routes"]:
        geom = route["geometry"]
        # Large route geometries collapse to mininal summaries that preserve
        # the original coordinate count for the LLM (_count_coords counts leaf
        # positions: 2000 [x, y] points = 4000 leaves).
        assert geom.get("coordinate_count") == 4000
        assert "coordinates" not in geom
        # directions beyond the cap record an explicit summary.
        assert len(route["directions"]) <= 5
        assert route["_directions_trimmed"]["original_count"] == 25
    assert _json_len(out) < 40000
    assert "_payload_notice" in out


def test_trim_small_payload_untouched_no_notice():
    """Small results never hit the fallback: no notice, no summaries, geometry
    left intact (short routes keep their full coordinates)."""
    payload = {
        "analysis_type": "shortest_path",
        "status": "success",
        "summary": {"distance_m": 1.5},
        "routes": [_fake_route(10, waypoint=0)],
        "od_matrix": _fake_od_pairs(5),
    }
    out = trim_network_result(payload)
    assert "_payload_notice" not in out
    assert "_od_matrix_trimmed" not in out
    assert len(out["routes"]) == 1
    geom = out["routes"][0]["geometry"]
    assert geom["type"] == "LineString"
    assert len(geom["coordinates"]) == 10
    assert len(out["od_matrix"]) == 5


# ─── 2. od_matrix input cap: explicit rejection, never silent truncation ─────


@pytest.mark.asyncio
async def test_network_od_matrix_rejects_oversized_input(monkeypatch):
    """origins × destinations beyond the cap must be rejected with an EXPLICIT
    error before the engine runs (mirrors MAX_OPTIMIZE_STOPS, issue #540)."""
    from app.tools import network_tools as nt
    from app.tools.registry import ToolRegistry

    calls = {"solve": 0}

    class _StubEngine:
        async def solve_od_matrix(self, **kwargs):
            calls["solve"] += 1
            raise AssertionError("engine must not run for oversized input")

    monkeypatch.setattr(nt, "NetworkGraphEngine", lambda: _StubEngine())
    reg = ToolRegistry()
    nt.register_network_tools(reg)
    tool_fn = reg._tools["network_od_matrix"]

    origins = [[116.0 + i * 0.01, 39.0] for i in range(301)]
    destinations = [[116.0 + i * 0.01, 39.0] for i in range(301)]
    result = await tool_fn(
        network={}, origins=origins, destinations=destinations, profile="driving"
    )
    assert result["type"] == "error"
    assert "上限" in result["message"] or str(MAX_OD_MATRIX_PAIRS) in result["message"]
    assert calls["solve"] == 0, "cap must be enforced before the engine runs"


@pytest.mark.asyncio
async def test_network_od_matrix_under_cap_reaches_engine(monkeypatch):
    """Requests within the cap still reach the engine and return normally."""
    from app.tools import network_tools as nt
    from app.tools.registry import ToolRegistry
    from app.services.network.models import NetworkAnalysisResult

    class _FakeEngine:
        async def solve_od_matrix(self, **kwargs):
            return NetworkAnalysisResult(
                analysis_type="od_matrix",
                summary={"pair_count": 4},
            )

    monkeypatch.setattr(nt, "NetworkGraphEngine", _FakeEngine)
    reg = ToolRegistry()
    nt.register_network_tools(reg)
    tool_fn = reg._tools["network_od_matrix"]

    result = await tool_fn(
        network={},
        origins=[[116.0, 39.0], [116.01, 39.0]],
        destinations=[[116.02, 39.0], [116.03, 39.0]],
        profile="driving",
    )
    assert result["status"] == "success"
    assert result["summary"]["pair_count"] == 4