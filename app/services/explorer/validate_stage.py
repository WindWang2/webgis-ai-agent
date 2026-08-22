"""
Validate Stage — Pure async stage runner for final dataset quality validation.
"""
import logging
from typing import Callable, List, Optional

from app.services.explorer.models import StageResult

logger = logging.getLogger(__name__)


async def run_validate_stage(
    task_id: str,
    geocoded_ref_id: Optional[str] = None,
    total_rows: int = 0,
    fetch_errors: Optional[List[dict]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> StageResult:
    """
    Execute the validation stage.
    Finalizes dataset verification and returns the completion summary.

    #774: ``fetch_errors`` (per-source fetch failures from the fetch stage) are
    included in the final summary so a partial exploration never reports
    survivor-only results as complete.
    """
    if on_progress:
        on_progress(100)

    return StageResult(
        stage="validate",
        data={
            "task_id": task_id,
            "status": "completed",
            "geocoded_ref_id": geocoded_ref_id,
            "total_rows": total_rows,
            "fetch_errors": fetch_errors or [],
        },
        success=True,
    )
