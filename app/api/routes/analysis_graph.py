"""Explicit Analysis Graph — read-only session projection endpoint (ADR-0097).

GET /sessions/{session_id}/analysis-graph returns the derived, bounded graph
(goal + execution DAG + product facets + next action). It is a projection of
SessionPlan + MapSpec + observation evidence — never a second persisted truth.
"""
from fastapi import APIRouter, Depends

from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.gis_harness.analysis_graph import build_analysis_graph_for_session

router = APIRouter(prefix="/sessions", tags=["Agent Workbench"])


@router.get("/{session_id}/analysis-graph")
async def get_analysis_graph(
    conversation: Conversation = Depends(require_owned_session),
) -> dict:
    return await build_analysis_graph_for_session(str(conversation.session_id))
