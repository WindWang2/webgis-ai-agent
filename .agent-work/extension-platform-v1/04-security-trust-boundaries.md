# 04 — Security & Trust Boundaries

## 1. Trust boundary statement (normative)

**The trust boundary is the Python in-process import.** An extension is code imported into the API process:
it runs with full process privileges — filesystem, DB credentials, env, network. The platform provides
**lifecycle, namespacing, validation, and blast-radius control (per-pack rollback), not isolation**.
No sandbox claims of any kind are made: there is no WASM/seccomp/VM layer, and the existing "AST/builtins
sandbox" for dynamic skills (`app/tools/skills.py:12-100,377-409`) is a deny-list heuristic for a tier-3
meta-tool, not a security boundary (raw 10 §5). Extension distribution therefore equals software
installation: signed/reviewed sources only; V1 ships first-party packs from the `extensions/` repo dir.

## 2. Tier-3 chokepoint (defense in depth, must remain intact)

- Deepest gate: registry `_dispatch_impl` refuses tier>=3 unless `confirm_tier3()` ContextVar is set
  (`app/tools/registry.py:81-88,116-127,1213-1218`); tier>=3 forces `side_effect=destructive` (:629-631);
  PLANNED tools unexecutable (:1201-1206).
- Bridge gate: Pi-side tier>=3 hard reject (`agent_pi_bridge.py:495-506`).
- Surface gate: spawn superset excludes tier>=3 / security_tier>=3 (`pi_native_surface.py:256-273`);
  per-turn selector drops tier>=3 (`tool_surface_v3.py:174-186`); final tier double-check
  (`pi_native_surface.py:390-398`); `list_available_tools` hides tier-3 (raw 10 §4).
- Platform rule: extension tools default to `tier=2, side_effect=read_only, security_tier=1`; anything
  higher requires explicit manifest declaration and is validated (an undeclared destructive tool fails load).

## 3. Bridge secret / HMAC turn capability

- `/pi-tools/execute` requires `X-Pi-Bridge-Secret` (constant-time compare, `pi_tools.py:36-48`) + HMAC
  turn token verified with the same secret (:63-68) + live-turn check → 409 on superseded turns (:74-89).
- Secret source: env `WEBGIS_BRIDGE_SECRET` or `DATA_DIR/.pi_bridge_secret` created atomically
  (mkstemp + chmod 0600 + flock), fail-fast on write error — no per-worker divergent secrets
  (`app/core/bridge_secret.py:22-66`).
- Every callback carries a signed, live turn capability; never route by mutable "current session"
  (raw 50 §5.6). The active-tools marker is an out-of-band control plane: user text is neutralized against
  marker forgery (`pi_turn_context.py:97-107`); extension enforces `MAX_ACTIVE=48` + superset membership.
- Any new host integration must replicate this plumbing (raw 10 §6.9); the platform ships it as shared
  helpers, not per-extension copies.

## 4. SSRF layers — current split and required unification

| Stack | Protections | Gaps |
|---|---|---|
| `data_fabric/security.py` (requests; all fabric adapters) | scheme allowlist (:117), hostname/IP blocklist (:153-159), all `getaddrinfo` records vs `BLOCKED_NETWORKS` (:55-65), metadata IPs (:40-43), IPv4-mapped unwrap (:87-89); redirect re-validation `SSRFSafeHTTPAdapter` (:316-331); `bounded_get` byte cap floor 16 MiB (:347); defusedxml (:277-293); same-origin cursors (:464-489); local path allowlist (:399-447) | unresolvable host allowed with warning (:172-183); connect-time IP pinning is a stated follow-up (:309-313) |
| `core/network.py` (aiohttp; osm/geocoding/chinese_maps/provider_health) | certifi SSL, shared pool, 10 s timeout (:166-200); rate limit + breaker (`provider_health.py:225-286`) | **no SSRF check, no redirect re-validation, no body cap** (`resp.json()` unbounded :271); targets are operator constants today (:105,123) |
| GDAL `/vsicurl` + `services/rs/` | timeout/retry knobs (`lib/geo_raster/env.py:39-44`), byte+count budget, host breaker (`remote.py:63-104`) | **no SSRF validation anywhere** — `stac_client.py:212 RasterReader.open(href)` opens any STAC asset href (grep-confirmed, raw 60 §3c) |
| stray | — | `tools/local_admin.py:179` raw `httpx.get` bypassing tracker/shared client |

**Platform requirement:** one `SafeHttp` facade (sync + async + GDAL href pre-validation via
`validate_url`) that all extension egress must use; extension tools declaring `network: true`
(`descriptor.py:197`) are bound to it at projection time. V1 core additive fix: route STAC/GDAL hrefs
through `validate_url` before `/vsicurl` open.

## 5. Secrets exposure paths (existing)

- Fabric credentials persisted **plaintext** in `DataSource.connection_profile` JSON (`models/data_fabric.py:22`,
  deliberate per `manager.py:153-159`); adapters inject `options["headers"]` verbatim
  (`ogc_api_adapter.py:53-54`, `wfs_adapter.py:114-115`).
- Redaction: `sanitize_profile_dict`/`redact_url` on REST + tool args (`security.py:242-274,:218-239`;
  `tools/data_fabric_tools.py:139`); decision log redacts arg keys only (`decision_log.py:55-66`) — **no
  generic redaction of tool results** (raw 60 §3).
- LLM key: server-side only, per-request header (`llm_client.py:122-126`), pooled clients carry no auth
  header (:165-175), Pi gets `$OPENAI_API_KEY` env indirection (`pi_rpc_client.py:227-234`).
- Platform rules: manifests never contain secrets — only **secret references** (env var name / settings key);
  the permission layer injects values at call time; extension diagnostics must pass through
  `sanitize_profile_dict` before display.

## 6. Permission model requirements

- Manifest declares required permissions: `network` (egress, SafeHttp-bound), `filesystem` (scoped roots
  under `DATA_FABRIC_LOCAL_FILE_ROOTS` semantics, `security.py:399-447`), `secrets` (named references),
  `db_write`, `pi_surface` (native-surface visibility), `tier3_offload` (never granted in V1).
- Enforcement at **projection time**: SDK builders refuse to register a tool whose descriptor
  (`network`, `requires_credentials`, `security_tier`, `required_permission`, `descriptor.py:183-220`)
  exceeds the manifest grant. Runtime enforcement stays in the existing tier/side-effect pipeline
  (registry.py:1213-1218) — the platform adds no second dispatch gate.
- `allow_private` is never an LLM/tool parameter; server-side only (raw 60 §3b).

## 7. Quarantine policy

- A pack whose validation/activation fails ≥N times, or whose fingerprint changed without version bump,
  enters `DISABLED` (quarantine): recorded, not loaded, surfaced in `python -m app.extensions_platform doctor`.
- Quarantined packs' journal entries are rolled back at next startup; re-enablement is an explicit operator
  command (never automatic), consistent with the fail-fast Philosophy of strict manifest validation
  (`main.py:75-86`) versus the *current* silent warning-drop (`app/tools/__init__.py:70-71`) which the
  platform replaces with loud, stateful failure.
