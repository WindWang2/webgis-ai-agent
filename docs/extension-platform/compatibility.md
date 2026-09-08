# Compatibility and Versioning

Source: `app/extensions_platform/api_version.py`. All compatibility
judgments are pure, deterministic functions with no I/O; every failure
produces a typed diagnostic (`API_VERSION_INCOMPATIBLE` or
`CORE_VERSION_INCOMPATIBLE`, both error severity ⇒ state `incompatible`).
There is no "compatible by default" fallback.

## The three version axes

| Axis | Constant / field | Current value | What it covers |
| --- | --- | --- | --- |
| Extension API | `CORE_API_VERSION` (host) vs manifest `api_version` | `1.0.0` | The host's extension-facing interface: `ExtensionContext` methods and the projection registrar shapes |
| Manifest schema | `MANIFEST_SCHEMA_VERSION` (host) vs manifest `schema_version` | `1` | The `manifest.json` document format itself |
| Core release | `CORE_RELEASE_VERSION` (host) vs manifest `minimum_core_version` / `maximum_core_version` | `0.1.3` | The overall host release an extension was written against |

## Rule 1: extension API compatibility

`check_extension_api_compatibility(api_version)`:

- **Major must equal** the host major.
- **Minor must be ≤** the host minor (the host is backward compatible within
  a major; an extension may not require features newer than the host).
- Patch level is ignored.

With host `1.0.0`:

| Manifest `api_version` | Verdict | Reason |
| --- | --- | --- |
| `1.0.0` | compatible | exact match |
| `1.0` | compatible | patch defaults to 0 |
| `1.1.0` | incompatible | minor 1 newer than host minor 0 |
| `2.0.0` | incompatible | major 2 ≠ host major 1 |
| `0.9.0` | incompatible | major 0 ≠ host major 1 |
| `"1.2.3.4"` | incompatible | not parseable semver |

## Rule 2: core version window

`check_core_version_window(minimum_core_version, maximum_core_version)` —
the host release must fall inside the **half-open window**
`[minimum_core_version, maximum_core_version)`:

- lower bound **inclusive** ("written for this release or later");
- upper bound **exclusive** ("before the next presumed-breaking release").

With core release `0.1.3`:

| Window | Verdict | Reason |
| --- | --- | --- |
| `[0.1.0, None)` | compatible | `0.1.3 ≥ 0.1.0`, no upper bound |
| `[0.1.3, None)` | compatible | lower bound inclusive |
| `[0.2.0, None)` | incompatible | core older than the minimum |
| `[0.1.0, 0.2.0)` | compatible | `0.1.3 < 0.2.0` |
| `[0.1.0, 0.1.3)` | incompatible | core `0.1.3` is **not** `< 0.1.3` (exclusive) |

Declaring an explicit `maximum_core_version` is recommended for extensions
that depend on behaviors expected to change; omitting it means "no upper
bound". The manifest itself rejects an inverted window (upper ≤ lower).

## Rule 3: manifest schema version

`schema_version > MANIFEST_SCHEMA_VERSION` ⇒ the manifest is rejected at
parse time (fail closed — an older host never silently ignores newer
fields). `schema_version < 1` is likewise rejected. See
[manifest-reference.md](manifest-reference.md).

## Deprecation policy

- **New fields** in a minor API release must be optional; hosts advertise
  their exact `CORE_API_VERSION` via `inspect` / `status_report`, and SDK
  validators produce typed diagnostics (not silent acceptance) for anything
  they do not recognize. Manifest-level unknown fields are always errors
  (`extra="forbid"`), which is why document-format changes are gated by
  `schema_version`.
- **Removed or redefined fields** (a breaking change to the extension API
  surface) require a **major** `CORE_API_VERSION` bump. On a major bump this
  file gains a migration matrix section; extensions pin their compatible
  range with `api_version` + the core window.

## Feature negotiation via `feature_flags`

Manifests may declare `feature_flags: {"<flag>": <default bool>}`:

- Operators override per extension id through
  `EXTENSION_PERMISSION_GRANTS`-style settings, specifically
  `EXTENSION_FEATURE_FLAGS='{"<ext-id>": {"<flag>": true}}'`. Host overrides
  win over manifest defaults.
- A declared flag with no host override produces a `FEATURE_FLAG_UNRESOLVED`
  **warning** and the manifest default is used (activation is `degraded`).
- Flag-gated dependency edges: a `DependencyDeclaration` with
  `feature_flag` set is skipped when the flag resolves false — it does not
  count for required-dependency checks or cycle detection.
- Flag gating explains "declared but not registered" warnings: an item
  declared in the manifest but registered only under a disabled flag yields
  `DECLARED_BUT_UNREGISTERED` (warning, `degraded` activation), never an
  error.

## Migration notes for future versions

1. Bumping `CORE_API_VERSION` minor: additively only (new optional spec
   fields, new diagnostics codes appended — existing codes are never
   renumbered). Extensions built for `1.0.x` keep validating.
2. Bumping `CORE_API_VERSION` major: the host may drop or reshape
   `ExtensionContext` methods; extensions must re-target and re-declare
   `api_version`. Expect the state machine, ledger semantics, and
   namespace rules to be stable across majors — they are the load-bearing
   invariants pinned by the conformance corpus.
3. Bumping `MANIFEST_SCHEMA_VERSION`: new fields land as optional; hosts
   accept `≤ current`. Extension authors should keep `schema_version` at the
   lowest value that includes the fields they use.
4. Core release drift: prefer an explicit `maximum_core_version` when your
   extension touches fast-moving areas (cartography payload shapes, recipe
   DSL fields); the exclusive upper bound is what protects your users from
   the next breaking release.
