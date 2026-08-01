"""
Discover Stage — Pure async stage runner for data discovery.
"""
import logging
from typing import Any, Callable, Dict, List, Optional

from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.services.explorer.models import SearchContext, StageResult

logger = logging.getLogger(__name__)


async def run_discover_stage(
    task_id: str,
    query: str,
    context: Dict[str, Any],
    adapter: Optional[Any] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> StageResult:
    """
    Execute the data discovery stage.
    Identifies candidate data sources and performs quick quality pre-assessments.
    """
    if on_progress:
        on_progress(10)

    if isinstance(context, dict):
        ctx_dict = dict(context)
        ctx_dict.setdefault("query", query)
        ctx = SearchContext(**ctx_dict)
    else:
        ctx = context
    data_adapter = adapter or GovDataAdapter()

    sources = await data_adapter.discover(query, ctx)

    scored: List[Dict[str, Any]] = []
    for source in sources[:3]:  # Top 3 candidates
        score = await data_adapter.quick_assess(query, source)
        scored.append({
            "source": source.model_dump(),
            "score": score.model_dump(),
        })

    scored.sort(key=lambda x: x["score"]["overall"], reverse=True)

    if on_progress:
        on_progress(100)

    return StageResult(
        stage="discover",
        data={"task_id": task_id, "selected_sources": scored},
        success=True,
    )
