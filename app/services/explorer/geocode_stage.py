"""Geocode stage — the geocoding algorithm of the Explorer pipeline, as a pure
async function.

Extracted from the Celery task body (architecture-review C2) so the algorithm
has a callable name and is testable through its interface rather than via
``explorer_geocode_task.run()`` patched at module symbols. The Celery task in
``app/tasks/explorer/task_chain.py`` is now a thin adapter that injects the
three external touchpoints (session-store round-trip, the async geocoder, and
a progress callback) and translates the result into the next stage's handoff dict.

Design contract (the seam this module exposes):

- **Pure about the algorithm.** No Celery, no module globals. Index mapping,
  the 30%-failure provider-rotation threshold, coordinate extraction, and
  per-row status mutation live here.
- **Dependencies injected, not imported.** ``load_ref`` / ``store_ref`` cross
  the session-store seam; ``batch_geocode`` is the async geocoder
  (``batch_geocode_cn``); ``on_progress`` reports a 0-100 int. Callers (prod
  and tests) cross the same seam.
- **Returns rows + summary, not a ref_id.** Minting the ref_id is the Celery
  task's concern (it owns the handoff dict); this function stays pure about
  the store by accepting ``store_ref`` only when the caller wants rows stored.
- **dict I/O.** Rows are plain dicts carrying the ``_lat`` / ``_lon`` /
  ``_geocode_status`` / ``_geocode_provider`` / ``_geocode_error`` magic keys,
  preserving the existing row contract the validate stage and frontend read.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

from app.services.geocode_strategy import (
    DEFAULT_PROVIDERS,
    PROVIDER_FAILURE_THRESHOLD,
    BATCH_SIZE,
    GeocodeProviderStrategy,
)


@dataclass
class GeocodeSummary:
    """Aggregate outcome of a geocode run over all parsed sources."""

    total: int = 0
    success: int = 0
    failed: int = 0
    predefined: int = 0
    success_rate: float = 0.0
    multi_provider: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "success": self.success,
            "failed": self.failed,
            "predefined": self.predefined,
            "success_rate": self.success_rate,
            "multi_provider": self.multi_provider,
        }


@dataclass
class GeocodeStageResult:
    """Return type of :func:`geocode_stage`.

    ``rows`` carries every input row annotated with geocode status keys; the
    Celery task passes ``rows`` + ``summary`` to ``store_ref`` to mint the
    ``geocoded_ref_id`` the next stage reads.
    """

    rows: list[dict] = field(default_factory=list)
    summary: GeocodeSummary = field(default_factory=GeocodeSummary)


# Type aliases for the injected seams — kept loose (dict I/O) per the interface
# decision: the algorithm reasons over the existing row/ref dicts, not typed
# models. Tightening to pydantic is a separate, deliberately out-of-scope pass.
LoadRef = Callable[[str], dict | None]
StoreRef = Callable[[dict], str]
BatchGeocode = Callable[..., Awaitable[dict]]
OnProgress = Callable[[int], None]


async def geocode_stage(
    parsed_sources: list[dict],
    *,
    load_ref: LoadRef,
    batch_geocode: BatchGeocode,
    store_ref: StoreRef | None = None,
    on_progress: OnProgress | None = None,
    providers: list[str] | None = None,
) -> GeocodeStageResult:
    """Geocode every row across ``parsed_sources`` via a provider fallback chain.

    For each parsed source: rows already carrying lat/lon are marked
    ``predefined`` and skipped; the rest are geocoded in batches of
    :data:`BATCH_SIZE`, rotating to the next provider when a batch's failure
    rate exceeds :data:`PROVIDER_FAILURE_THRESHOLD`.

    Args:
        parsed_sources: list of ``{ref_id, row_count, mapping}`` dicts (the
            ``parsed_results`` the parse stage hands off).
        load_ref: resolves a ``ref_id`` to its stored ``{rows, mapping}`` dict
            (or ``None`` if absent).
        batch_geocode: async geocoder, signature-compatible with
            ``batch_geocode_cn(addresses, provider=..., max_concurrency=...)``.
        store_ref: if given, called with ``{rows, summary}`` per the prior
            contract; the returned ref_id is the caller's to surface (the
            Celery task puts it in the handoff dict as ``geocoded_ref_id``).
            May be ``None`` for tests that only inspect the returned rows.
        on_progress: optional ``0-100`` int callback (the Celery task wires it
            to ``self.update_state``).
        providers: override the fallback order; defaults to
            :data:`DEFAULT_PROVIDERS`.

    Returns:
        rows annotated with ``_lat``/``_lon``/``_geocode_status``/
        ``_geocode_provider``/``_geocode_error`` plus an aggregate summary.
    """
    providers = providers if providers is not None else DEFAULT_PROVIDERS
    total_rows = sum(s.get("row_count", 0) for s in parsed_sources)

    if total_rows == 0:
        result = GeocodeStageResult()
        if store_ref is not None:
            store_ref({"rows": [], "summary": result.summary.as_dict()})
        return result

    all_geocoded: list[dict] = []
    processed = 0
    # One flag across all chunks, mirroring the original single-boolean
    # semantics: set whenever any batch rotated past the first provider.
    multi_provider = _MultiProviderFlag()

    def _report_progress() -> None:
        if on_progress is not None:
            on_progress(int(processed / total_rows * 100))

    _report_progress()

    for parsed in parsed_sources:
        data = load_ref(parsed["ref_id"])
        row_count = parsed.get("row_count", 0)
        if not data:
            processed += row_count
            continue

        rows = data["rows"]
        mapping = data.get("mapping", {})
        address_field = mapping.get("address", "address")
        lat_field = mapping.get("lat")
        lon_field = mapping.get("lon")

        # Partition rows: predefined (already have coords) vs to-geocode vs
        # missing-address failures. Predefined rows are carried verbatim with
        # their existing values; only the geocode-status keys are added.
        chunk: list[tuple[int, str]] = []  # (row_index, address_string)
        for idx, row in enumerate(rows):
            has_lat = lat_field is not None and row.get(lat_field) is not None
            has_lon = lon_field is not None and row.get(lon_field) is not None
            if has_lat and has_lon:
                row["_lat"] = row[lat_field]
                row["_lon"] = row[lon_field]
                row["_geocode_status"] = "predefined"
                row["_geocode_provider"] = None
                row["_geocode_error"] = None
            else:
                address = row.get(address_field)
                if address:
                    chunk.append((idx, str(address)))
                else:
                    row["_lat"] = None
                    row["_lon"] = None
                    row["_geocode_status"] = "failed"
                    row["_geocode_provider"] = None
                    row["_geocode_error"] = "missing address"
            all_geocoded.append(row)

        await _geocode_chunk(
            rows,
            chunk,
            batch_geocode=batch_geocode,
            providers=providers,
            flag=multi_provider,
        )

        processed += len(rows)
        _report_progress()

    summary = _summarize(all_geocoded)
    summary.multi_provider = multi_provider.hit

    if store_ref is not None:
        store_ref({"rows": all_geocoded, "summary": summary.as_dict()})

    return GeocodeStageResult(rows=all_geocoded, summary=summary)


# Internal flag object so _geocode_chunk (which rotates providers) can signal
# multi-provider usage back to the caller without a return-value dance.
@dataclass
class _MultiProviderFlag:
    hit: bool = False


async def _geocode_chunk(
    rows: list[dict],
    chunk: list[tuple[int, str]],
    *,
    batch_geocode: BatchGeocode,
    providers: list[str],
    flag: _MultiProviderFlag,
) -> None:
    """Geocode one source's ``chunk`` of (row_idx, address) pairs in batches.

    Mutates ``rows`` in place, writing the ``_lat``/``_lon``/``_geocode_*``
    status keys. Implements the per-batch provider fallback: if a batch's
    failure rate exceeds the threshold and another provider remains, the
    failed addresses are retried against the next provider, and ``flag`` is
    set to signal that a rotation occurred.
    """
    addresses = [address for _, address in chunk]
    strategy = GeocodeProviderStrategy()
    results, hit = await strategy.geocode_addresses(
        addresses,
        batch_geocode=batch_geocode,
        providers=providers,
        failure_threshold=PROVIDER_FAILURE_THRESHOLD,
        batch_size=BATCH_SIZE
    )
    if hit:
        flag.hit = True
        
    for (row_idx, _), res in zip(chunk, results):
        row = rows[row_idx]
        row["_lat"] = res.lat
        row["_lon"] = res.lon
        row["_geocode_status"] = res.status
        row["_geocode_provider"] = res.provider
        row["_geocode_error"] = res.error


def _summarize(all_geocoded: list[dict]) -> GeocodeSummary:
    """Tally row statuses into a :class:`GeocodeSummary`."""
    total = len(all_geocoded)
    success = sum(1 for r in all_geocoded if r.get("_geocode_status") == "ok")
    failed = sum(1 for r in all_geocoded if r.get("_geocode_status") == "failed")
    predefined = sum(
        1 for r in all_geocoded if r.get("_geocode_status") == "predefined"
    )
    to_geocode = total - predefined
    success_rate = round(success / to_geocode, 4) if to_geocode > 0 else 0.0
    return GeocodeSummary(
        total=total,
        success=success,
        failed=failed,
        predefined=predefined,
        success_rate=success_rate,
    )
