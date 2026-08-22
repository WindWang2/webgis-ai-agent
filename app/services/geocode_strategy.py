import logging
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

DEFAULT_PROVIDERS: list[str] = ["amap", "baidu", "tianditu"]
PROVIDER_FAILURE_THRESHOLD = 0.30
BATCH_SIZE = 100

@dataclass
class GeocodeAddressResult:
    lat: Optional[float]
    lon: Optional[float]
    status: str
    provider: Optional[str]
    error: Optional[str]
    # #772: provider-reported precision level (amap/tianditu "level", baidu
    # "precision_level") and a per-row fallback marker (the row was answered by
    # a different provider than the one requested — with_fallback switched).
    precision: Optional[str] = None
    provider_switched: bool = False


def _row_provider(r: dict, requested: str) -> str:
    """#772: the provider that ACTUALLY answered the row.

    ``batch_geocode_cn`` wraps each address in ``with_fallback``, which may
    silently retry an amap failure on baidu/tianditu; the row dict carries the
    real provider (``r["provider"]`` / ``r["provenance"]["source"]``). Falling
    back to ``requested`` only when the payload carries no provider field.
    """
    provider = r.get("provider")
    if not provider:
        provenance = r.get("provenance")
        if isinstance(provenance, dict):
            provider = provenance.get("source")
    return str(provider) if provider else requested


def _row_precision(r: dict) -> Optional[str]:
    """#772: extract the provider's precision level from the nested result
    (baidu ``precision_level`` / amap+tianditu ``level``), if reported."""
    results_list = r.get("results")
    if isinstance(results_list, list) and results_list and isinstance(results_list[0], dict):
        first = results_list[0]
        for key in ("precision_level", "level", "precision"):
            val = first.get(key)
            if val:
                return str(val)
    return None

def extract_lat_lon(item: dict) -> tuple[Optional[float], Optional[float]]:
    """Extract coordinates: prefer item["results"][0]["location"]
    (a [lon, lat] pair); fall back to top-level lat/lon, lat/lng or location dict.
    """
    results_list = item.get("results")
    if results_list and isinstance(results_list, list) and len(results_list) > 0:
        loc = results_list[0].get("location")
        if loc and isinstance(loc, list) and len(loc) == 2:
            try:
                return float(loc[0]), float(loc[1])
            except (ValueError, TypeError):
                pass
            
    # Location dict
    loc_dict = item.get("location")
    if loc_dict and isinstance(loc_dict, dict):
        lat = loc_dict.get("lat")
        lon = loc_dict.get("lon") or loc_dict.get("lng")
        if lat is not None and lon is not None:
            try:
                return float(lon), float(lat)
            except (ValueError, TypeError):
                pass
            
    # Top level lat / lon / lng
    lat = item.get("lat")
    lon = item.get("lon") or item.get("lng")
    if lat is not None and lon is not None:
        try:
            return float(lon), float(lat)
        except (ValueError, TypeError):
            pass
        
    # direct loc array
    loc = item.get("loc")
    if loc and isinstance(loc, list) and len(loc) == 2:
        try:
            return float(loc[0]), float(loc[1])
        except (ValueError, TypeError):
            pass

    return None, None

class GeocodeProviderStrategy:
    async def geocode_addresses(
        self,
        addresses: list[str],
        batch_geocode: Callable[..., Awaitable[dict]],
        providers: list[str] | None = None,
        failure_threshold: float = PROVIDER_FAILURE_THRESHOLD,
        batch_size: int = BATCH_SIZE
    ) -> tuple[list[GeocodeAddressResult], bool]:
        providers = providers if providers is not None else DEFAULT_PROVIDERS
        results_out = [
            GeocodeAddressResult(None, None, "failed", None, "no response")
            for _ in range(len(addresses))
        ]
        multi_provider_hit = False

        for batch_start in range(0, len(addresses), batch_size):
            batch_addresses = addresses[batch_start:batch_start + batch_size]
            pending = list(range(len(batch_addresses)))
            provider_idx = 0

            while pending and provider_idx < len(providers):
                provider = providers[provider_idx]
                current_addresses = [batch_addresses[i] for i in pending]
                
                result = await batch_geocode(current_addresses, provider=provider, max_concurrency=3)
                
                if "error" in result and not result.get("results") and not result.get("errors"):
                    provider_idx += 1
                    multi_provider_hit = True
                    continue

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
                    overall_idx = batch_start + p_idx
                    if p_idx in success_by_idx:
                        r = success_by_idx[p_idx]
                        lon, lat = extract_lat_lon(r)
                        # #771: (0, 0) is Null Island — providers (tianditu/
                        # baidu) default a MISSING location to 0, so a (0,0)
                        # row is an unresolved address, never a success.
                        if lat is not None and lon is not None and not (lat == 0 and lon == 0):
                            # #772: attribute the row to the provider that
                            # actually answered (with_fallback may have
                            # switched) and carry its precision level; mark the
                            # switch so summary.multi_provider is truthful.
                            actual_provider = _row_provider(r, provider)
                            switched = bool(actual_provider != provider)
                            if switched:
                                multi_provider_hit = True
                            results_out[overall_idx] = GeocodeAddressResult(
                                lat, lon, "ok", actual_provider, None,
                                precision=_row_precision(r),
                                provider_switched=switched,
                            )
                        else:
                            failed_this_attempt.append(p_idx)
                    else:
                        failed_this_attempt.append(p_idx)

                failure_rate = len(failed_this_attempt) / len(pending) if pending else 0.0
                if failure_rate > failure_threshold and failed_this_attempt and provider_idx < len(providers) - 1:
                    multi_provider_hit = True
                    pending = failed_this_attempt
                    provider_idx += 1
                else:
                    for p_idx in failed_this_attempt:
                        overall_idx = batch_start + p_idx
                        error_msg = "no response"
                        if provider_idx == len(providers) - 1:
                            error_msg = "all_providers_failed"
                        elif p_idx in error_by_idx:
                            error_msg = error_by_idx[p_idx].get("error", "unknown error")
                        
                        results_out[overall_idx] = GeocodeAddressResult(None, None, "failed", provider, error_msg)
                    pending = []

            for p_idx in pending:
                overall_idx = batch_start + p_idx
                provider = providers[-1] if providers else None
                results_out[overall_idx] = GeocodeAddressResult(None, None, "failed", provider, "all_providers_failed")

        return results_out, multi_provider_hit
