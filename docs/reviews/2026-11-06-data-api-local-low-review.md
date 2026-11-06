# Data API / local sources Low verification — 2026-11-06

Delegated scope: #1860 S1-023/S1-024/S1-033 service portion, #1862 S3b-016..020. Shared branch `codex/all-issues-deep-review`; no commits or GitHub writes.

| Issue | Finding | Status | Implementation |
|---|---|---|---|
| #1860 | S1-023 | fixed / verified | Catalog q≤128 characters; SQL LIKE literal escaping `%_\` with explicit escape; closed case-normalized geometry enum and exact type match (Point does not match MultiPoint). Existing public/live-owner tenant guards retained. |
| #1860 | S1-024 | fixed / verified | Default5k/max50k rows; bounded process-local user2/source4/global16 concurrent stream leases, user12/source60 starts/min; user admitted before prep, source after actual catalog authorization and before adapter resolution. Rejected source never creates a pump. Generator/response failure/disconnect closes leases idempotently. Registry≤4096 keys. Multi-worker windows remain independent (same documented boundary as existing tile limiter), no distributed-limit claim. |
| #1860 | S1-033 | fixed / verified | Publish/revoke service uses ProjectService.get_project_for_write with live User active/role/org ownership; actor None/anonymous denied before IO. Session-owner and object-manifest owner gates unchanged. API project-read/live-org helper remains core-owned. |
| #1862 | S3b-016 | fixed / verified | Local POI candidates undergo actual administrative/request polygon covers or WGS84 Geod.inv radius test, before final response limit. Admin polygon unavailable→local miss, not bbox-as-boundary. Candidate scans bounded5k; raw total_match is never mislabeled as filtered count, truncation/window basis disclosed. |
| #1862 | S3b-017 | fixed / verified | Generated osmconf atomically refreshes when template differs; CONFIG_FILE is passed as a per-dataset OSM driver open option, no os.environ write. Real tiny OSM import verifies option and unchanged unrelated global environment. |
| #1862 | S3b-018 | fixed / verified | Long/short rows normalized to header width at import and central chunk seam; township rows filter finite in-range coordinates before conversion; real GPKG import proves long extra columns and invalid town coordinates cannot abort import. |
| #1862 | S3b-019 | fixed / verified | District cache512 entries LRU, max128char keys, thread lock, copies on hit/insert so caller mutations cannot poison cached codes. |
| #1862 | S3b-020 | fixed / verified | Literal escaped LIKE for township/fullname/province/county, blank/invalid year rows skipped rather than int(None); actual panel SQLite import/search verifies wildcard literals. |

Validation checkpoints: `/tmp/data-api-local78.log`40passed (existing stream cancellation/lifetime, local routing, local stats import, lakehouse publishing). `/tmp/data-api-low79.log`8passed new adversarial tests. `/tmp/data-api-local80.log`29passed/1 new fixture failure (required User.email omitted; corrected); no production authorization relaxation. Ruff passes; `/tmp/data-api-local-debt81.log`debt ratchet passes without baseline change. Final expanded rerun pending.

[GDAL OSM driver documentation](https://gdal.org/en/stable/drivers/vector/osm.html#open-options) specifies `CONFIG_FILE` as a dataset open option; this preserves per-open isolation while selecting the generated template.

## Final verification

`/tmp/data-api-local82.log`: **70 passed** — all8 finding regressions plus full existing stream route/disconnect tests, local-first routing, real local stats import, lakehouse publishing, Data Fabric REST and local-first off-loop contracts. The new User fixture now includes required email; live owner/editor/viewer/deactivated identity cases pass. Ruff and owned-path diff whitespace checks pass; no production auth guard was mocked away in the live permission regression.

Rate admission evidence includes user concurrency, aggregate source concurrency across distinct users, global process capacity, user rate, source-denied requests before adapter resolution, successful response release and >50k validation. Registry keys are hashed; errors expose no source/user identity. Existing pump cancellation tests pass with leases installed.

New artifacts to include in root integration: `app/services/data_fabric/stream_limits.py`, `tests/unit/test_data_api_local_low_20261106.py`, this ledger. No commits or external writes.

Final gate `/tmp/data-api-local-debt83.log`: debt ratchet OK (except_silent392/491, unannotated1762/1788, dict_str_any7079/7086, god_modules17/19). Baseline unchanged. All8 scoped rows fixed/verified; source frozen for root integration.

Final spatial follow-through `/tmp/data-spatial-local84.log`:41passed after conservative WGS84 candidate-radius bbox and full unsimplified administrative geometry bounds. A true1000m geodesic cardinal boundary point remains within the candidate envelope before exact radius filtering; no corner false positives leak through.
