"""Off-loop raster evidence snapshots constrained to an authoritative owner."""
import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

async def snapshot_scoped_raster_inputs(session_id: Optional[str], args: Any) -> dict[str, str]:
    from app.lib.gis.analysis_reuse import snapshot_raster_fingerprints
    from app.services.scoped_raster_paths import scoped_raster_roots

    roots: list[str] = []
    if session_id:
        try:
            resolved = await asyncio.to_thread(scoped_raster_roots, session_id=session_id)
            roots = [str(root) for root in resolved]
        except Exception:
            logger.warning("analysis reuse raster scope unavailable", exc_info=True)
    return await asyncio.to_thread(snapshot_raster_fingerprints, args, allowed_roots=roots)
