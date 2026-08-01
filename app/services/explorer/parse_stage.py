"""
Parse Stage — Pure async stage runner for structured parsing.
"""
import logging
from typing import Any, Callable, Dict, List, Optional

from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.services.explorer.models import RawContent, StageResult

logger = logging.getLogger(__name__)


def auto_field_mapping(fields: list) -> dict:
    """Automatic field name mapping helper."""
    mapping = {}
    name_patterns = ["name", "名称", "title", "标题"]
    address_patterns = ["address", "地址", "addr", "location", "位置"]
    lat_patterns = ["lat", "latitude", "纬度", "y"]
    lon_patterns = ["lon", "lng", "longitude", "经度", "x"]

    for field in fields:
        fname = field.name.lower()
        if any(p in fname for p in name_patterns):
            mapping["name"] = field.name
        elif any(p in fname for p in address_patterns):
            mapping["address"] = field.name
        elif any(p in fname for p in lat_patterns):
            mapping["lat"] = field.name
        elif any(p in fname for p in lon_patterns):
            mapping["lon"] = field.name

    return mapping


def mapping_confidence(mapping: dict) -> float:
    """Calculate field mapping confidence score."""
    required = ["name", "address"]
    matched = sum(1 for k in required if k in mapping)
    return round(matched / len(required), 4)


async def run_parse_stage(
    task_id: str,
    fetch_results: List[Dict[str, Any]],
    adapter: Optional[Any] = None,
    load_ref: Optional[Callable[[str], Any]] = None,
    store_ref: Optional[Callable[[dict, str], str]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> StageResult:
    """
    Execute the structured parse stage.
    Parses raw fetch data into structured rows, performs auto-field mapping, and stores parsed results.
    """
    if on_progress:
        on_progress(10)

    data_adapter = adapter or GovDataAdapter()
    parsed_all: List[Dict[str, Any]] = []

    for result in fetch_results:
        ref_id = result.get("ref_id")
        stored = load_ref(ref_id) if load_ref and ref_id else None

        if not stored:
            logger.warning(f"[Explorer:{task_id}] Ref {ref_id} not found")
            continue

        raw = RawContent(
            data=bytes.fromhex(stored["data"]),
            content_type=stored["content_type"],
            encoding=stored["encoding"],
        )

        structured = await data_adapter.parse(raw)
        mapping = auto_field_mapping(structured.fields)
        confidence = mapping_confidence(mapping)

        payload = {
            "rows": structured.rows,
            "fields": [f.model_dump() for f in structured.fields],
            "mapping": mapping,
        }
        parsed_ref = store_ref(payload, "parsed") if store_ref else f"ref_parsed_{result.get('source_id')}"

        parsed_all.append({
            "source_id": result.get("source_id"),
            "ref_id": parsed_ref,
            "row_count": len(structured.rows),
            "mapping": mapping,
            "confidence": confidence,
        })

    if on_progress:
        on_progress(100)

    return StageResult(
        stage="parse",
        data={"task_id": task_id, "parsed_results": parsed_all},
        success=True,
    )
