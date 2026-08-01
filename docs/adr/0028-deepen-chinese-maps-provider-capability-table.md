# Deepen Chinese-maps provider behind one capability table

**Status:** accepted

Record architecture-review **Batch 6 Candidate F1** as implemented. This ADR
records the decision so future reviews do not re-suggest the shallow-free-function
layout it replaced.

## Context

The Chinese-maps tool cluster (`app/tools/chinese_maps/`) had three pain points
surveyed in the Batch 6 architecture review (`/tmp/architecture-review-20260801-222351.html`):

1. **27× postlude duplication.** The `build params → _<provider>_get →
   if "error" in data → data.get(unpack_key)` sequence was copy-pasted across
   the three provider files. 60–70% of each provider file was this boilerplate.
2. **9× dispatch scaffold.** `__init__.py`'s `register_chinese_map_tools`
   rewrote the same 8-line `for p in _fallback_order(...): try/except`
   fallback loop for each of the 9 multi-provider tools.
3. **Implicit capability matrix.** Which provider supports which capability
   was smeared across scattered `exclude=` sets and `if provider == "amap"`
   branches, with no single declarative source of truth.

A verified latent bug made this urgent: `amap.py` never imported `asyncio` or
`aiohttp`, yet `_distance_matrix_amap`, `_get_route_distance_amap`, and
`_isochrone_analysis` used `asyncio.Semaphore`/`asyncio.gather` and caught
`aiohttp.ClientError` — those branches raised `NameError` on execution.
`sibling baidu.py` / `tianditu.py` were clean, itself proof the cluster had no
shared import contract. The bug survived because none of the 27 provider impls
had behavioral test coverage (no fake-GET seam existed).

## Decision

Deepen the three providers behind one capability table, in five commits
(`d49366c` → `80b5a25`).

### Module shape: Protocol + provider classes

A `@runtime_checkable ChineseMapsProvider` Protocol (`protocol.py`) declares
the **9 shared capabilities** with identical signatures across providers.
`AmapProvider`, `BaiduProvider`, `TiandituProvider` (in their respective
modules) implement it. Each class encapsulates endpoint paths, request-param
building, response-unwrap keys, and **both sides of CRS** (input WGS84→provider
CRS via a private `_to_src`; output normalization via provider-private
`_shape_*` helpers using one `transform_geojson` pass). POI outputs still route
through the existing `_shaping.py` (unchanged — it stays focused on POI).

The three Amap-only features (`isochrone`, `transit`, `traffic`) are
**non-Protocol methods** on `AmapProvider`; tool wrappers call them directly
(no fallback — Amap-only by design). The Protocol is the capstone: its
docstring documents the authoritative ✓/✗ capability matrix.

### Dispatch collapse: `with_fallback`

The 9× fallback scaffold collapsed into a shared
`with_fallback(preferred, call, exclude, no_key_msg, tool_name)` helper in
`http.py`. Per-tool LLM-facing input validation (radius bounds, list lengths,
minutes range, Chinese error strings returned to the LLM) **stays in the
`@tool` bodies** — it is per-capability, called once, and returns to the LLM.
Per-tool failure messages are preserved via `no_key_msg`.

### Test seam: injected `get` callable

Each provider's `__init__` takes a `get: Callable[[str, dict], Awaitable[dict]]`
defaulting to the real `_amap_get`/`_baidu_get`/`_tianditu_get` (which delegate
to `tracked_provider_get` — the Batch 5 / E4 transport seam). Tests construct
`AmapProvider(get=FakeGet({...}))` with canned JSON; provider methods call
`self._get(endpoint, params)`. This is the deepening payoff: 27 provider impls
gained behavioral coverage for the first time.

### Backward compatibility: clean deletion

All 26 free functions and the `__init__.py` re-export block (L39–56) are
deleted. They existed solely to feed the now-deleted `_dispatch` dicts. The
single external consumer (`test_chinese_maps_split.py:114`) was rewired to the
new seam. Three singletons (`_AMAP`/`_BAIDU`/`_TIANDITU`) plus a `_PROVIDERS`
map replace them.

## Consequences

- **depth**: one narrow Protocol interface over deep per-provider implementations.
- **locality**: the capability matrix lives in Protocol membership + three
  singleton declarations, not 9 scattered `exclude=` sets.
- **leverage**: `with_fallback` collapses 9× scaffold; the provider class
  collapses 27× postlude.
- **Bug fixed**: the `asyncio`/`aiohttp` `NameError` is fixed structurally
  (module-level imports); three regression tests lock the concurrency and
  except branches.
- **Second latent bug fixed**: `transform_geojson` silently no-op'd on tuple
  coordinates (shapely `__geo_interface__` format) because `_walk_coords` only
  handled lists. Now normalizes tuples to lists. Would have leaked
  untransformed GCJ-02/BD-09 coords from any caller feeding shapely geometries.
- **Test coverage**: `_shaping.py`'s documented-but-missing `test_shaping` is
  added (9 tests); 21 provider-behavior tests via the fake-GET seam; 5
  Protocol-parity tests.

## What we are not doing

- **No pagination primitive** — verified no provider paginates today (every
  search caps via a single request's size param). A primitive with no consumer
  would be speculative.
- **No expansion of `_shaping.py` to non-POI outputs** — non-POI outputs
  (route polylines, district polygons) use provider-private `_shape_*` helpers
  instead, keeping `_shaping.py` focused on POI collections.
- **No `_get_route_distance_amap` public surface** — stays a private helper on
  `AmapProvider` (used by isochrone & distance_matrix riding fallback only).

## Related

- Batch 5 / E4 (ADR-0027): `tracked_provider_get` — the transport the
  providers' default `get` delegates to. F1 sits one layer above it.
- Batch 3 / C3: `shape_poi_collection` — the POI output module F1 builds on.
- The `CONTEXT.md` "Chinese Maps Provider (capability matrix)" entry documents
  the resulting module.
