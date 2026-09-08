# Diagnostics and the Developer CLI

Entry point: `python -m app.extensions_platform <command>` (thin
`__main__.py` forwarding to `cli.py`).

Hard contract of the CLI:

- **Read-only + scaffold + supply-chain commands.** No command ever
  activates an extension — lifecycle commands stop at discover /
  `validate_extension`. The host is built on a fresh throwaway
  `ToolRegistry()`, so the live server's registries are never touched.
  The V2 commands write only where documented: `package` writes
  `signature.json` into the given pack directory (that is its purpose);
  `verify` / `sbom` are read-only. `certify` **executes extension code**
  (its lifecycle smoke performs a real activate → health → deactivate, and
  worker packs spawn a real subprocess) — only run it on packs you trust.
  No command prints key or secret material.
- Default output is human-readable text; `--json` makes stdout **pure
  JSON** (trust-boundary notices become JSON fields), errors go to stderr.
- Exit codes: `0` success (findings reported by `list` / `doctor` /
  `catalog` are not failures; `verify` counts `verified`/`missing` as
  success; `certify` counts a `certified` report as success), `1`
  validation failure / unknown id / settings parse failure, `2` usage
  error (bad scaffold arguments, target exists).
- Every lifecycle command accepts `--root PATH` (repeatable; overrides
  `EXTENSIONS_DIRS` entirely) and `--json`. `package` / `verify` operate
  directly on a pack directory and take no `--root`.

## Commands

| Command | What it does |
| --- | --- |
| `list` | Discover and list id / version / state / trust / declared types / entry point, plus a `failures` section for directories whose manifest did not parse |
| `inspect <ext-id>` | Full manifest (pretty JSON), path, state, trust, content fingerprint, dependency edges (required/optional with feature flags), and diagnostics |
| `validate <ext-id>` | Run `validate_extension` compatibility judgment; exit `0` compatible / `1` not (unknown ids also exit `1` — a broken manifest shows up as a discovery failure, so run `list`) |
| `doctor` | Settings summary, parsed grants table, roots in use, per-extension state + diagnostics, `problems` and `hints` sections (common-problem advice keyed by stable diagnostic code); pure read-only |
| `scaffold <ns> <name> --dir OUT_DIR` | Generate a starter pack (`manifest.json`, `main.py`, `health.py`, `test_<name>.py`) that passes `validate` immediately; refuses reserved namespaces, bad tokens, and existing targets (exit `2`). The manifest is checked through the real parser before anything is written |
| `catalog` | Declaration catalog grouped by namespace (markdown by default; `--json` machine-readable), including projected names and honesty metadata |
| `package <pack-dir> --key-id ID --key-file PATH` | V2: content-sign the pack (writes `signature.json`, HMAC-SHA256; key material never printed). See [packaging.md](packaging.md) |
| `verify <pack-dir> [--publisher KEY_ID:PATH ...]` | V2: deterministic signature verdict (`signed_verified` / `signed_untrusted` / `invalid` / `tampered` / `missing`); exit 0 for verified/missing, 1 otherwise |
| `sbom <ext-id>` | V2: deterministic SBOM — file inventory (path/bytes/sha256), python imports, dependency mirror incl. version constraints, secret-shape scan (`--json` for the full document) |
| `certify <ext-id>` | V2: certification suite — manifest contract, api compat, dependency constraints, signature status, SBOM secret scan, execution mode, real lifecycle smoke (activate → health → deactivate; ACTIVE records get health-only). exit 0 iff `certified` |

Typical loop:

```bash
python -m app.extensions_platform scaffold acme tools --dir /tmp/exts
python -m app.extensions_platform validate acme.tools --root /tmp/exts
python -m app.extensions_platform inspect acme.tools --root /tmp/exts --json
python -m app.extensions_platform doctor
python -m app.extensions_platform catalog --root extensions/examples
```

## Diagnostic codes

All failures in the platform produce a typed `ExtensionDiagnostic`
(`code`, `severity` ∈ info/warning/error, `message`, `extension_id`,
`context`). Codes live in `app/extensions_platform/diagnostics.py`; new
codes may only be appended, never renumbered. Error severity blocks
activation; warnings degrade it.

| Code | Severity | Meaning |
| --- | --- | --- |
| `manifest_schema_unsupported` | error | Manifest `schema_version` is newer than the host supports; upgrade the host or downgrade the manifest (parse-time rejections surface as `manifest_invalid`; `doctor` attaches this hint by message) |
| `manifest_invalid` | error | Manifest failed validation: unknown field (`extra="forbid"`), bad enum value, inverted core window, duplicate ids, size limits |
| `manifest_parse_failed` | error | `manifest.json` unreadable, oversized (> 256 KiB), or not valid JSON |
| `namespace_invalid` | error | Namespace does not match `^[a-z][a-z0-9_]{1,31}$` |
| `namespace_reserved` | error | Namespace is in the reserved list (`core`, `webgis`, `app`, `pi`, …) |
| `id_collision` | error | The same extension id was discovered under multiple roots; the later-sorted copy is quarantined |
| `core_version_incompatible` | error | Core release outside the extension's `[minimum_core_version, maximum_core_version)` window |
| `api_version_incompatible` | error | Manifest `api_version` major ≠ host major, or minor newer than the host's |
| `extension_type_unsupported` | error | Manifest declares a reserved future extension type (`marketplace`, `wallet`, `theme_engine`, `secret_store`) — not supported in API 1.x. (`model_provider` is supported since API 1.1.0; declaring it below the V2 floor surfaces as `manifest_invalid` with the api-floor rule in the message) |
| `dependency_missing` | error | A required dependency was not discovered (or is quarantined), or is not active at activation time |
| `dependency_cycle` | error | A required-dependency cycle across manifests (three-color DFS over the discovered set) |
| `optional_dependency_absent` | warning | An optional dependency is absent; activation proceeds as `degraded` |
| `trust_blocked` | error / warning | error: extension blocked by operator policy (`EXTENSIONS_BLOCK`) ⇒ quarantined, never imported. warning: a `local_untrusted` extension declares high-risk permissions (`filesystem_write`, `external_process`, `destructive_action`) — grants still required, no automatic elevation |
| `permission_not_granted` | error | Runtime denial: the permission is not in `EXTENSION_PERMISSION_GRANTS` for this extension id. Declaration ≠ authorization. Surfaces at dispatch as `TOOL_ERROR` / `ExtensionPermissionDenied` |
| `permission_declaration_invalid` | error / warning | error: manifest or spec declares a permission outside the fixed 9-word vocabulary, or a tool requires permissions the manifest does not declare, or a network-flagging tool/provider lacks the `network` permission. warning: duplicate permission declaration |
| `entry_point_missing` | error | `entry_point` does not resolve to `<pack>/<entry>.py` (or `<pack>/<entry>/__init__.py`), or the loaded module does not define `activate(ctx)` |
| `entry_point_failed` | error | The entry module could not be imported, or `activate(ctx)` raised (any extension exception is contained and typed), or the optional `deactivate()` hook raised (warning) |
| `registry_projection_failed` | error | The authoritative registry rejected the registration (e.g. a descriptor failed its pydantic validation) |
| `registry_projection_collision` | error | The projected id already exists — core entries can never be shadowed; includes tool/algorithm/source-type/recipe/component collisions and unknown `capabilities`/`algorithms` references |
| `registry_rollback_incomplete` | error | An undo closure failed during ledger rollback (best-effort cleanup continues; each failure is reported) |
| `undeclared_registration` | error | `activate(ctx)` registered an item the manifest does not declare ⇒ activation fails and rolls back (fail closed) |
| `declared_but_unregistered` | warning | A declared tool/algorithm/provider was not registered (typically feature-flag-gated) ⇒ `degraded` activation |
| `health_check_failed` | warning | The post-activation (or probed) health check reported `degraded`, or the diagnostics entry was unresolvable / raised |
| `health_unhealthy` | error | Post-activation health check returned `unhealthy` ⇒ activation is rolled back |
| `discovery_limit_exceeded` | error | More than 64 extension directories under the roots; remaining directories ignored |
| `fingerprint_changed` | error / warning | error: directory exceeds fingerprint bounds (> 512 files or > 8 MiB). warning: content changed since discovery/last load (anti-tamper signal; the new fingerprint is adopted) |
| `feature_flag_unresolved` | warning | A declared feature flag has no host override; the manifest default is used |
| `extension_disabled` | error | Activation attempted on an operator-disabled extension; enable it first |
| `worker_mode_invalid` | error | A worker-mode rule was violated: manifest structure (forbidden section / permission / streaming capability, or api below the V2 floor), a class-instance projection API called inside a worker, `args_model` instead of explicit `parameters`, or `stream=True` on a worker model provider |
| `worker_protocol_mismatch` | error | RPC protocol version or frame shape mismatch between host and worker |
| `worker_startup_timeout` | error | The worker missed the `execution.startup_timeout_s` handshake budget; killed |
| `worker_call_timeout` | error | A worker call exceeded `execution.call_timeout_s`; process group killed, projections roll back |
| `worker_crashed` | error | The worker process died (EOF / exit / protocol failure); stderr tail attached; projections roll back |
| `worker_restart_quarantined` | error | `EXTENSIONS_MAX_WORKER_CRASHES` consecutive crashes reached; the extension is quarantined until re-discovered |
| `worker_result_invalid` | error | Reserved code for a worker result frame failing host-side validation (defined in the append-only vocabulary; no current emission path — a malformed result surfaces as `worker_protocol_mismatch` or `worker_crashed`) |
| `broker_denied` | error | The capability broker refused an op (no grant, allowlist miss, path outside artifact roots, unprovisioned secret ref, unknown op, SSRF gate) |
| `output_limit_exceeded` | error | A serialized tool result exceeded `execution.max_output_bytes` (typed error result; the worker survives). Also broker artifact writes over 32 MiB |
| `resource_limit_unavailable` | warning | POSIX rlimits could not be applied for this worker (non-POSIX or `setrlimit` failure); wall-clock kill remains. Honest degradation — no sandbox claims |
| `signature_invalid` | error / warning | error: `signature.json` malformed, wrong algorithm, HMAC mismatch, key unreadable, or unsigned pack under `EXTENSIONS_TRUST_SIGNED` / `EXTENSIONS_ALLOW_UNSIGNED_DEV` (warning variants). error ⇒ quarantined |
| `publisher_untrusted` | warning | Signature present but its `key_id` is not in `EXTENSION_TRUSTED_PUBLISHERS`; operator trust config decides |
| `package_tampered` | error | Pack content changed after signing (fingerprint ≠ signed fingerprint) or between discovery and worker handshake ⇒ quarantined even if allowlisted |
| `signature_verified` | info | Signature verified and publisher trusted; recorded when `EXTENSIONS_TRUST_SIGNED` elevates trust |
| `dependency_constraint_invalid` | error | A `dependencies[].version` constraint string is malformed (also a parse error at manifest level) |
| `dependency_conflict` | error | Upgrade preflight: the new version violates a dependent's version constraint; upgrade refused, old version keeps running |
| `operation_in_flight` | error | Deactivate (or a second call) attempted while a worker call is in flight; retry after completion |

## Health check contract

Declare `diagnostics_entry` in the manifest as `"module:function"` (attribute
path off the entry module) or a bare `"function"` (on the entry module). The
function takes no arguments and returns the host's report shape:

```python
def check() -> dict:
    return {"status": "healthy", "messages": []}
```

- `status` ∈ `healthy | degraded | unhealthy`; `messages` is a list of
  strings.
- No `diagnostics_entry` at all ⇒ implicitly `healthy`.
- An unresolvable entry, or a raising check, ⇒ `degraded` (a broken probe
  must not fail the host).
- **Post-activation gate:** immediately after `activate(ctx)` the host runs
  the check once. `unhealthy` ⇒ ledger rollback, activation `failed`
  (`HEALTH_UNHEALTHY`). `degraded` ⇒ warning (`HEALTH_CHECK_FAILED`),
  extension state `degraded`.
- `host.health(extension_id)` re-runs the check on demand and attaches
  `"state"`; for non-active extensions it reports `unhealthy` with the
  current state as the message.

The example pack's `health.py` (`diagnostics_entry: "health:check"`) is
statically healthy; the scaffold's default is `"check_health"` forwarding to
a `health.py` sibling. Keep checks cheap and side-effect free — they run at
every activation and probe.
