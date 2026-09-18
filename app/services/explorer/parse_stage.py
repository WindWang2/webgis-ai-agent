"""
Parse Stage — Pure async stage runner for structured parsing.
"""
import base64
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.services.explorer.models import RawContent, StageResult

logger = logging.getLogger(__name__)

_FIELD_TOKEN_RE = re.compile(r"[a-z0-9]+|[^\W\d_]+", re.UNICODE)


def _field_tokens(name: str) -> list[str]:
    """Split a field name into lowercase tokens (camelCase, separators, CJK)."""
    split_camel = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    return [t for t in _FIELD_TOKEN_RE.findall(split_camel.lower()) if t]


def _coord_pattern_matches(fname: str, pattern: str) -> bool:
    """Match a lat/lon pattern against a field name.

    Single-letter patterns (x/y) are exact-token only so ``year`` / ``index``
    never match. Multi-char patterns use token or left-word-boundary match
    (``lat`` → ``latitude``, not ``plate``).
    """
    fname_l = fname.lower()
    p = pattern.lower()
    tokens = _field_tokens(fname)
    if len(p) == 1:
        return p in tokens
    if fname_l == p or p in tokens:
        return True
    if re.search(r"[\u4e00-\u9fff]", p):
        return p in fname_l
    return re.search(rf"(?:^|[^a-z0-9]){re.escape(p)}", fname_l) is not None


def _best_coord_match(fname: str, patterns: list[Tuple[str, int]]) -> Optional[int]:
    best: Optional[int] = None
    for pattern, score in patterns:
        if _coord_pattern_matches(fname, pattern):
            if best is None or score > best:
                best = score
    return best


def decode_fetch_payload(stored: Dict[str, Any]) -> bytes:
    """Decode a stored fetch payload back to bytes.

    #775: fetch stores base64 (``codec: "base64"``); payloads written before
    that change carry no codec marker and are hex. Both decode deterministically
    here (the codec marker disambiguates — hex strings are also valid base64
    alphabet, so sniffing alone would be unreliable).
    """
    if stored.get("codec", "hex") == "base64":
        return base64.b64decode(stored["data"])
    return bytes.fromhex(stored["data"])


def auto_field_mapping(fields: list) -> dict:
    """Automatic field name mapping helper."""
    mapping: dict = {}
    scores: dict[str, int] = {}
    name_patterns = ["name", "名称", "title", "标题"]
    address_patterns = ["address", "地址", "addr", "location", "位置"]
    # Longer tokens outrank single-letter x/y so latitude/longitude win.
    lat_patterns = [("latitude", 3), ("纬度", 3), ("lat", 2), ("y", 1)]
    lon_patterns = [("longitude", 3), ("经度", 3), ("lng", 2), ("lon", 2), ("x", 1)]

    def consider(key: str, field_name: str, score: int) -> None:
        if key not in scores or score >= scores[key]:
            scores[key] = score
            mapping[key] = field_name

    for field in fields:
        fname = field.name
        fname_l = fname.lower()
        if any(p in fname_l for p in name_patterns):
            mapping["name"] = field.name
        elif any(p in fname_l for p in address_patterns):
            mapping["address"] = field.name
        else:
            lat_score = _best_coord_match(fname, lat_patterns)
            lon_score = _best_coord_match(fname, lon_patterns)
            if lat_score is not None and (lon_score is None or lat_score >= lon_score):
                consider("lat", fname, lat_score)
            elif lon_score is not None:
                consider("lon", fname, lon_score)

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
    missing_refs: List[str] = []
    errors: List[Dict[str, Any]] = []

    for result in fetch_results:
        ref_id = result.get("ref_id")
        stored = load_ref(ref_id) if load_ref and ref_id else None

        if not stored:
            logger.warning(f"[Explorer:{task_id}] Ref {ref_id} not found")
            missing_refs.append(ref_id or "<none>")
            continue

        # Per-source isolation: a corrupt payload (bad base64/hex) or a parser
        # failure must skip just this source, matching fetch_stage's error
        # isolation. Previously an unguarded decode / parse could abort the
        # whole stage.
        try:
            raw = RawContent(
                data=decode_fetch_payload(stored),
                content_type=stored["content_type"],
                encoding=stored["encoding"],
            )
            structured = await data_adapter.parse(raw)
        except Exception as e:
            logger.warning(f"[Explorer:{task_id}] Parse failed for ref {ref_id}: {e}")
            errors.append({"source_id": result.get("source_id"), "ref_id": ref_id, "error": str(e)})
            continue

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

    # Fail-fast: if there were fetch results to parse but every ref was missing
    # (cross-worker handoff break / store down), return failure rather than
    # handing an empty parsed_results to geocode and reporting success.
    # Partial misses (some refs resolved) stay success — one source failing
    # shouldn't sink the whole pipeline.
    if fetch_results and not parsed_all:
        return StageResult(
            stage="parse",
            data={"task_id": task_id, "parsed_results": [], "missing_refs": missing_refs, "errors": errors},
            success=False,
            message=(
                f"Parse stage produced no output: all {len(missing_refs)} fetch ref(s) "
                f"unresolved (missing={missing_refs}). Likely a session-store handoff failure."
            ),
        )

    return StageResult(
        stage="parse",
        data={
            "task_id": task_id,
            "parsed_results": parsed_all,
            "missing_refs": missing_refs,
            "errors": errors,
        },
        success=True,
    )
