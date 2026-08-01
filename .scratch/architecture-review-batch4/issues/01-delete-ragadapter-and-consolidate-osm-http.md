# 01 — Delete vaporware RAGAdapter and consolidate OSM HTTP fetching

**What to build:**
Delete the orphaned and unimplemented `RAGAdapter` stub file (`app/adapters/rag/rag_adapter.py`) that implies a non-existent vector-store adapter seam. Refactor `app/tools/osm.py` to route all 5 hand-rolled `aiohttp.ClientSession` calls through the shared HTTP client utility (`get_shared_client`), fixing divergent User-Agent headers and ensuring connection pool reuse.

**Blocked by:** None — can start immediately.

**Status:** closed

- [x] Delete `app/adapters/rag/rag_adapter.py` and remove references if any.
- [x] Refactor `app/tools/osm.py` to use `get_shared_client` across all Nominatim and Overpass fetch helpers.
- [x] Align User-Agent headers in `_nominatim_search_poi` with the shared HTTP client header policy.
- [x] Ensure all existing OSM and geocoding tests pass.
