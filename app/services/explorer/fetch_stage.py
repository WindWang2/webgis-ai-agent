"""
Fetch Stage — Pure async stage runner for content fetching.
"""
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
    """
    if on_progress:
        on_progress(10)

    data_adapter = adapter or GovDataAdapter()
    results: List[Dict[str, Any]] = []

    for item in selected_sources:
        source_dict = item.get("source", {})
        source = DataSource(**source_dict)

        try:
            raw = await data_adapter.fetch(source)
            payload = {
                "data": raw.data.hex(),
                "content_type": raw.content_type,
                "encoding": raw.encoding,
            }
            ref_id = store_ref(payload, "fetch") if store_ref else f"ref_fetch_{source.id}"

            results.append({
                "source_id": source.id,
                "ref_id": ref_id,
                "size_bytes": len(raw.data),
                "format": source.format,
            })
        except Exception as e:
            logger.warning(f"[Explorer:{task_id}] Fetch failed for {source.id}: {e}")
            results.append({
                "source_id": source.id,
                "error": str(e),
            })

    successful = [r for r in results if "ref_id" in r]
    if not successful:
        return StageResult(
            stage="fetch",
            data={"task_id": task_id, "errors": results},
            success=False,
            message=f"All source fetches failed: {results}",
        )

    if on_progress:
        on_progress(100)

    return StageResult(
        stage="fetch",
        data={"task_id": task_id, "fetch_results": successful},
        success=True,
    )
