"""GIS-104 regression: redline bbox crossing + radius cos(lat).

Deep-review counterexample: zone 116.3-116.6E x 39.9-40.2N crossed by
bbox 116.0-117.0E x 40.0-40.1N was NOT blocked by the corner-only check.
"""
import pytest

from app.services.spatial_guardrails.errors import GeofenceRedlineViolationError
from app.services.spatial_guardrails.redlines import (
    RedlineRegistry,
    RedlineZone,
)


ZONE = RedlineZone(
    name="counterexample",
    ring=[(116.3, 39.9), (116.6, 39.9), (116.6, 40.2), (116.3, 40.2)],
    policy="no_fetch",
)


def _registry(*zones):
    return RedlineRegistry(zones=list(zones) or [ZONE])


def test_bbox_crossing_zone_interior_is_blocked():
    reg = _registry()
    with pytest.raises(GeofenceRedlineViolationError):
        reg.check_bbox([116.0, 40.0, 117.0, 40.1], action="fetch")


def test_bbox_clear_of_zone_is_allowed():
    reg = _registry()
    reg.check_bbox([10.0, 10.0, 11.0, 11.0], action="fetch")
    # directly south of the zone (no overlap)
    reg.check_bbox([116.3, 39.5, 116.6, 39.8], action="fetch")


def test_bbox_fully_inside_zone_still_blocked():
    reg = _registry()
    with pytest.raises(GeofenceRedlineViolationError):
        reg.check_bbox([116.4, 40.0, 116.5, 40.1], action="fetch")


def test_bbox_touching_zone_edge_is_blocked():
    reg = _registry()
    with pytest.raises(GeofenceRedlineViolationError):
        reg.check_bbox([116.6, 40.0, 117.0, 40.1], action="fetch")


def test_non_restricted_action_is_exempt():
    reg = _registry()
    reg.check_bbox([116.0, 40.0, 117.0, 40.1], action="metadata")


def test_check_radius_uses_cos_lat():
    """At 40N a 4.3km radius reaches ~0.0504 deg of longitude (corrected)."""
    zone = RedlineZone(
        name="thin-zone",
        ring=[(116.40, 39.99), (116.41, 39.99), (116.41, 40.01), (116.40, 40.01)],
        policy="no_fetch",
    )
    reg = _registry(zone)
    # corrected bbox max lng = 116.35 + 4.3/(111.32*cos40) ~= 116.4004 -> hit
    with pytest.raises(GeofenceRedlineViolationError):
        reg.check_radius(116.35, 40.0, 4.3, action="fetch")
    # 4.0 km stays short of the zone (~116.3969)
    reg.check_radius(116.35, 40.0, 4.0, action="fetch")
