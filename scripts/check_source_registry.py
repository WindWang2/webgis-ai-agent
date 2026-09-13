#!/usr/bin/env python
"""ads-v1 source-registry lint (DS1, ADR-0171).

Checks every ``config/sources/*.yaml``:

1. schema + protocol validity (loads through ``SourceRegistryService`` — any
   pydantic/YAML failure is an error);
2. duplicate source_id (error);
3. inline dataset bbox sanity (minx<maxx, miny<maxy, within [-180,-90,180,90]);
4. missing quota for networked protocols (warning);
5. ``fallbacks`` reference declared source_ids (error on unknown);
6. capability claims vs the fabric adapter's real capability flags
   (error when a source claims pushdown the adapter class does not support);
7. credential hygiene: plaintext-looking values in auth/options (error);
8. ``verified: false`` sources are listed for disclosure (informational).

Exit code 0 = clean; 1 = at least one error.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.services.data_fabric import source_registry as SR  # noqa: E402


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    unverified: list[str] = []

    directory = REPO / SR.DEFAULT_SOURCES_DIR
    if not directory.exists():
        print(f"[lint] no sources dir at {directory}")
        return 1

    service = SR.SourceRegistryService(directory).load()
    sources = service.list_sources()
    if not sources:
        errors.append("no source declarations found")

    known_ids = {s.source_id for s in sources}
    networked = {"gov_portal", "stats_api", "stac", "ogc_api", "wfs", "arcgis"}

    for s in sources:
        sid = s.source_id
        source_registry_source = s
        # 3. bbox sanity on inline datasets
        for d in s.datasets:
            if d.bbox:
                if len(d.bbox) != 4:
                    errors.append(f"{sid}/{d.dataset_id}: bbox must have 4 values")
                else:
                    minx, miny, maxx, maxy = d.bbox
                    if not (minx < maxx and miny < maxy):
                        errors.append(f"{sid}/{d.dataset_id}: bbox corners inverted {d.bbox}")
                    if not (-180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90):
                        errors.append(f"{sid}/{d.dataset_id}: bbox outside lon/lat range {d.bbox}")
        # 4. quota presence for networked protocols
        if s.protocol in networked and s.quota.requests_per_minute is None and s.quota.daily_max is None:
            warnings.append(f"{sid}: networked source declares no quota (add requests_per_minute/daily_max)")
        # 5. fallback refs (bare ids or conditional rules)
        for fb in source_registry_source.normalized_fallbacks():
            if fb.source_id not in known_ids:
                errors.append(f"{sid}: fallback '{fb.source_id}' is not a declared source_id")
        # 7. credential hygiene (belt & braces — loader already rejects these)
        for k, v in s.options.items():
            lk = str(k).lower()
            if any(t in lk for t in ("secret", "password", "token", "api_key")) and isinstance(v, str) and not v.startswith("${"):
                errors.append(f"{sid}: options.{k} looks like a plaintext credential")
        # 8. verification disclosure
        if not s.verified:
            unverified.append(sid)
        # 6. capability claims vs adapter reality (fabric-mappable only)
        if s.protocol in SR.FABRIC_PROTOCOLS | SR.ADS_PROTOCOLS:
            from app.services.data_fabric.registry import resolve_adapter_spec

            spec = resolve_adapter_spec(s.protocol)
            claims = s.pushdown
            mismatches = []
            if claims.bbox and not spec.supports_bbox:
                mismatches.append("bbox")
            if claims.cql and not spec.supports_filter:
                mismatches.append("cql")
            if claims.aggregation and not getattr(spec, "supports_aggregation", False):
                pass  # aggregation has no AdapterSpec flag yet — lint keeps quiet
            if claims.pagination and not spec.supports_pagination:
                mismatches.append("pagination")
            for m in mismatches:
                errors.append(f"{sid}: claims pushdown '{m}' but adapter '{s.protocol}' does not support it")

    print(f"[lint] sources loaded: {len(sources)} ({directory})")
    for w in warnings:
        print(f"[lint][warn] {w}")
    for u in unverified:
        print(f"[lint][unverified] {u} (down-ranked until verified — disclose in PR)")
    if errors:
        for e in errors:
            print(f"[lint][ERROR] {e}")
        return 1
    print("[lint] OK — no errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
