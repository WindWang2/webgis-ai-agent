"""
Fetch Stage - Pure async stage runner for content fetching.
"""
import asyncio
import base64
import logging
from typing import Any, Callable, Dict, List, Optional

from app.adapters.base import DataSource
from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.services.explorer.models import StageResult

logger = logging.getLogger(__name__)


async def run_fetch_stage(
    task_id: str,
    selected_sources: List[Dict[str, Any]],
    adapter: Optional[Any] = None,
    store_ref: Optional[Callable[[dict, str], str]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> StageResult:
    """
    Execute the content fetch stage.
    Fetches raw content from data sources and stores payloads via injected store_ref seam.

    Sources are fetched concurrently via ``asyncio.gather`` (with per-source
    error isolation via ``return_exceptions=True``) rather than serially, so
    the stage stays within the Celery ``soft_time_limit`` when multiple sources
    each have a multi-second HTTP timeout.
    """
    if on_progress:
        on_progress(10)

    data_adapter = adapter or GovDataAdapter()

    async def _fetch_one(item: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch one source, store its payload, return the result dict.

        Raises on fetch failure so ``gather(return_exceptions=True)`` captures
        it as an exception alongside the successful results - per-source
        isolation without cancelling sibling fetches.
        """
        source_dict = item.get("source", {})
        source = DataSource(**source_dict)
        raw = await data_adapter.fetch(source)
        # #775: base64 (1.33× overhead), not hex (2×) — a 50 MB gov CSV per
        # source was doubled to a 100 MB JSON string in the session store.
        payload = {
            "data": base64.b64encode(raw.data).decode("ascii"),
            "codec": "base64",
            "content_type": raw.content_type,
            "encoding": raw.encoding,
        }
        ref_id = store_ref(payload, "fetch") if store_ref else f"ref_fetch_{source.id}"
        return {
            "source_id": source.id,
            "ref_id": ref_id,
            "size_bytes": len(raw.data),
            "format": source.format,
        }

    # Concurrent fetch with per-source isolation: a failing source becomes an
    # exception in the results list, not a cancellation of the others.
    # Input order is preserved by gather's positional result alignment.
    gather_results = await asyncio.gather(
        *(_fetch_one(item) for item in selected_sources),
        return_exceptions=True,
    )

    # Build the results list in input order, discriminating success vs failure.
    results: List[Dict[str, Any]] = []
    for item, outcome in zip(selected_sources, gather_results):
        source_dict = item.get("source", {})
        source = DataSource(**source_dict)
        if isinstance(outcome, BaseException):
            logger.warning(f"[Explorer:{task_id}] Fetch failed for {source.id}: {outcome}")
            results.append({
                "source_id": source.id,
                "error": str(outcome),
            })
        else:
            results.append(outcome)

    successful = [r for r in results if "ref_id" in r]
    failed = [r for r in results if "ref_id" not in r]
    if not successful:
        # #774: distinguish "nothing to fetch" (discovery found no sources)
        # from "every candidate failed" — the empty-list "all failed" message
        # used to mislead debugging.
        if not selected_sources:
            return StageResult(
                stage="fetch",
                data={"task_id": task_id, "errors": [], "fetch_errors": []},
                success=False,
                message="Discovery found no sources to fetch (no candidate sources were selected).",
            )
        return StageResult(
            stage="fetch",
            data={"task_id": task_id, "errors": results, "fetch_errors": failed},
            success=False,
            message=f"All source fetches failed: {results}",
        )

    if on_progress:
        on_progress(100)

    # #774: partial success keeps going, but the per-source failures ride
    # along in the StageResult data (keyed ``fetch_errors``) instead of being
    # log-only — parse/geocode/validate and the final status payload can then
    # say that e.g. 2 of 3 sources were dropped.
    return StageResult(
        stage="fetch",
        data={
            "task_id": task_id,
            "fetch_results": successful,
            "fetch_errors": failed,
        },
        success=True,
    )
