"""
Discover Stage — Pure async stage runner for data discovery.
"""
import asyncio
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

    semaphore = asyncio.Semaphore(4)

    async def assess(source: Any) -> Optional[Dict[str, Any]]:
        try:
            async with semaphore:
                score = await data_adapter.quick_assess(query, source)
            return {"source": source.model_dump(), "score": score.model_dump()}
        except Exception as exc:
            logger.warning("Explorer candidate assessment failed: %s", type(exc).__name__)
            return None

    assessed = await asyncio.gather(*(assess(source) for source in sources[:32]))
    scored: List[Dict[str, Any]] = [item for item in assessed if item is not None]
    scored.sort(key=lambda x: x["score"]["overall"], reverse=True)

    if on_progress:
        on_progress(100)

    return StageResult(
        stage="discover",
        data={
            "task_id": task_id, "selected_sources": scored[:3],
            "assessment_failures": len(assessed) - len(scored),
            "candidates_truncated": max(0, len(sources) - len(assessed)),
        },
        success=not sources or bool(scored),
        message="All candidate assessments failed" if sources and not scored else "",
    )
