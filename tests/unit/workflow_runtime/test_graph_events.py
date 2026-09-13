"""workflow_graph 事件族单测 —— 有界投影契约（方向 5 E8）。"""
from __future__ import annotations

from app.services.session_plan import CANONICAL_PLAN_EVENT_NAMES
from app.services.workflow_runtime.graph_events import (
    WORKFLOW_GRAPH_EVENT,
    intent_graph_event,
)


def test_intent_event_bounded_and_shaped():
    facts = {
        "fine_dims": ["subject", "scope", "time"],
        "global_reshape": False,
        "carried": {f"cap_{i:02d}": f"ref:{i}" for i in range(20)},
        "lost": [{"capability": f"lost_{i:02d}", "dimension": "data",
                  "detail": "x"} for i in range(40)],
    }
    summary = {"stale": [f"cap:{i}" for i in range(50)],
               "carried": ["cap:boundary"],
               "instances": [{"applied": True, "deferred": False}]}
    event = intent_graph_event(facts, summary)
    assert event.event == WORKFLOW_GRAPH_EVENT
    assert event.event not in CANONICAL_PLAN_EVENT_NAMES
    data = event.data
    assert data["type"] == "replanned"
    assert len(data["carried"]) <= 8 and data["carried_total"] == 20
    assert len(data["lost"]) <= 8 and data["lost_total"] == 40
    assert len(data["stale_nodes"]) <= 8
    assert data["applied"] is True and data["deferred"] is False


def test_intent_event_without_v5_summary():
    event = intent_graph_event({"fine_dims": ["style"], "carried": {},
                                "lost": [], "global_reshape": True})
    assert event.data["global_reshape"] is True
    assert "stale_nodes" not in event.data
