"""Bounded-label workflow dispatch gauges, registered independently of dispatch."""
from prometheus_client import Gauge

DISPATCH_ACTIVE = Gauge(
    "workflow_dispatch_slots_in_use",
    "Workflow dispatch slots currently held in this process.",
)
DISPATCH_CAPACITY = Gauge(
    "workflow_dispatch_slots_per_loop",
    "Configured workflow dispatch capacity per event loop.",
)
