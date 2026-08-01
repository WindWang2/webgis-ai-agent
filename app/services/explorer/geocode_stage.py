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

# Default provider fallback order, mirroring the prior inline literal. Kept as
# a module constant so the task and tests can reference/override one source of
# truth rather than re-hardcoding the list.
DEFAULT_PROVIDERS: list[str] = ["amap", "baidu", "tianditu"]

# Rotate to the next provider when the per-batch failure rate exceeds this.
PROVIDER_FAILURE_THRESHOLD = 0.30

# Rows are geocoded in batches of this size (matches the prior inline literal).
BATCH_SIZE = 100


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
    for batch_start in range(0, len(chunk), BATCH_SIZE):
        batch = chunk[batch_start:batch_start + BATCH_SIZE]
        pending = list(range(len(batch)))  # indices into batch
        provider_idx = 0

        while pending and provider_idx < len(providers):
            provider = providers[provider_idx]
            addresses = [batch[i][1] for i in pending]

            result = await batch_geocode(
                addresses, provider=provider, max_concurrency=3
            )

            # Complete provider failure (no results, no per-row errors): rotate.
            if "error" in result and not result.get("results") and not result.get("errors"):
                provider_idx += 1
                flag.hit = True
                continue

            # Map results/errors back by index within the current addresses list.
            # result["index"] is position in `addresses`; translate to batch idx.
            success_by_idx: dict[int, dict] = {}
            for r in result.get("results", []):
                batch_idx = pending[r["index"]]
                success_by_idx[batch_idx] = r
            error_by_idx: dict[int, dict] = {}
            for e in result.get("errors", []):
                batch_idx = pending[e["index"]]
                error_by_idx[batch_idx] = e

            failed_this_attempt: list[int] = []
            for p_idx in pending:
                if p_idx in success_by_idx:
                    r = success_by_idx[p_idx]
                    row_idx = batch[p_idx][0]
                    row = rows[row_idx]

                    # Extract coordinates: prefer result["results"][0]["location"]
                    # (a [lon, lat] pair); fall back to top-level lat/lon.
                    lat: float | None = None
                    lon: float | None = None
                    results_list = r.get("results")
                    if results_list and len(results_list) > 0:
                        loc = results_list[0].get("location")
                        if loc and len(loc) == 2:
                            lon, lat = loc[0], loc[1]
                    if lat is None:
                        lat = r.get("lat")
                    if lon is None:
                        lon = r.get("lon")

                    if lat is not None and lon is not None:
                        row["_lat"] = lat
                        row["_lon"] = lon
                        row["_geocode_status"] = "ok"
                        row["_geocode_provider"] = provider
                        row["_geocode_error"] = None
                    else:
                        failed_this_attempt.append(p_idx)
                else:
                    failed_this_attempt.append(p_idx)

            failure_rate = (
                len(failed_this_attempt) / len(pending) if pending else 0
            )
            if (
                failure_rate > PROVIDER_FAILURE_THRESHOLD
                and failed_this_attempt
                and provider_idx < len(providers) - 1
            ):
                flag.hit = True
                pending = failed_this_attempt
                provider_idx += 1
            else:
                # Mark remaining as failed.
                for p_idx in failed_this_attempt:
                    row_idx = batch[p_idx][0]
                    row = rows[row_idx]
                    row["_lat"] = None
                    row["_lon"] = None
                    row["_geocode_status"] = "failed"
                    row["_geocode_provider"] = provider
                    if provider_idx == len(providers) - 1:
                        row["_geocode_error"] = "all_providers_failed"
                    elif p_idx in error_by_idx:
                        row["_geocode_error"] = error_by_idx[p_idx].get(
                            "error", "unknown error"
                        )
                    else:
                        row["_geocode_error"] = "no response"
                pending = []

        # Exhausted all providers with rows still pending: mark all failed.
        for p_idx in pending:
            row_idx = batch[p_idx][0]
            row = rows[row_idx]
            row["_lat"] = None
            row["_lon"] = None
            row["_geocode_status"] = "failed"
            row["_geocode_provider"] = providers[-1] if providers else None
            row["_geocode_error"] = "all_providers_failed"


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
