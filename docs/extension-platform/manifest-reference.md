# Manifest Reference (`manifest.json`)

`GisExtensionManifest` (`app/extensions_platform/manifest.py`) is the only
extension contract. It is a pydantic v2 model with `extra="forbid"` and
`frozen=True`: unknown fields, illegal enum values, and out-of-range sizes
are rejected at parse time (fail closed). A rejected manifest surfaces as a
`MANIFEST_INVALID` (model-level) or `MANIFEST_PARSE_FAILED` (unreadable /
oversized / bad JSON) diagnostic; discovery failures are per-extension and
never take down the scan.

## Top-level fields

| Field | Type | Default | Constraints | Failure mode |
| --- | --- | --- | --- | --- |
| `schema_version` | `int` | `1` | `>= 1`, `<= MANIFEST_SCHEMA_VERSION` (currently 1) | Newer than host ⇒ rejected at parse (`manifest schema_version N newer than supported 1`); upgrade the host or downgrade the manifest. See [schema_version policy](#schema_version-policy). |
| `id` | `str` | required | Must equal `f"{namespace}.{name}"` | Parse error (`manifest id 'x.y' must equal 'x.y'`) |
| `name` | `str` | required | Short name inside the namespace; the tail segment of `id` | Parse error if `id` mismatches |
| `namespace` | `str` | required | Regex `^[a-z][a-z0-9_]{1,31}$`; not in the reserved list | Parse error (`must match …` / `is reserved`) |
| `version` | `str` | required | `X.Y[.Z]` semver (optional prerelease/build tags) | Parse error (`is not X.Y[.Z] semver`) |
| `api_version` | `str` | `"1.0.0"` | Semver; host major must match, minor must be ≤ host minor | `API_VERSION_INCOMPATIBLE` error at validate ⇒ `incompatible`. See [compatibility.md](compatibility.md). |
| `minimum_core_version` | `str` | `"0.1.0"` | Semver; lower bound of the core release window (inclusive) | `CORE_VERSION_INCOMPATIBLE` error at validate |
| `maximum_core_version` | `Optional[str]` | `None` | Semver; **exclusive** upper bound; must exceed `minimum_core_version` | Parse error if window inverted; `CORE_VERSION_INCOMPATIBLE` if release ≥ bound |
| `title` | `str` | `""` | — | — |
| `description` | `str` | `""` | ≤ 2000 chars | Parse error |
| `vendor` | `str` | `""` | — | — |
| `extension_types` | `list[str]` | `[]` | Values from `tools, algorithms, data_providers, cartography, workflow` | `model_provider` ⇒ parse error (`not supported in api_version 1.x`); any other unknown value ⇒ parse error. Types are also inferred from populated declaration sections (`declared_type_set()`). |
| `capabilities` | `list[str]` | `[]` | Informational self-description toward the capability catalog. **Not authorization; not validated against CapabilityRegistry** (unlike the spec-level `capabilities`, which are validated). | — |
| `permissions` | `list[str]` | `[]` | Values from the fixed 9-word vocabulary | Unknown word ⇒ `PERMISSION_DECLARATION_INVALID` error; duplicate ⇒ warning. See [permissions-and-trust.md](permissions-and-trust.md). |
| `trust` | `str` | `"local_untrusted"` | Declarable values only: `trusted_builtin`, `trusted_extension`, `local_untrusted` | `core` / `blocked` / anything else ⇒ parse error. **The declaration has no authoritative effect** — the host recomputes trust from operator policy. |
| `dependencies` | `list[DependencyDeclaration]` | `[]` | Each: `{id, required=True, feature_flag=None}`; edge is skipped when its `feature_flag` resolves false | Missing/cyclic ⇒ `DEPENDENCY_MISSING` / `DEPENDENCY_CYCLE` errors ⇒ `incompatible`; not active at activate ⇒ `failed` |
| `optional_dependencies` | `list[DependencyDeclaration]` | `[]` | Same shape | Absent ⇒ `OPTIONAL_DEPENDENCY_ABSENT` warning; activation proceeds as `degraded` |
| `feature_flags` | `dict[str, bool]` | `{}` | Declared flag names; host overrides via `EXTENSION_FEATURE_FLAGS` | Flag with no host override ⇒ `FEATURE_FLAG_UNRESOLVED` warning (manifest default wins) |
| `settings_schema` | `Optional[dict]` | `None` | JSON object; serialized size ≤ 32 KiB; `type` (default `"object"`) must be a string. Values are provided per extension id via `EXTENSION_SETTINGS_JSON`. | Parse error on oversize/malformed |
| `tools` | `list[ToolDeclaration]` | `[]` | See below; names unique per section | Duplicate ⇒ parse error |
| `algorithms` | `list[AlgorithmDeclaration]` | `[]` | See below; ids unique per section | Duplicate ⇒ parse error |
| `data_providers` | `list[DataProviderDeclaration]` | `[]` | See below; source types unique per section | Duplicate ⇒ parse error |
| `cartography_items` | `list[CartographyItemDeclaration]` | `[]` | See below; `(kind, id)` pairs unique | Duplicate ⇒ parse error |
| `workflow_packs` | `list[WorkflowPackDeclaration]` | `[]` | See below; pack ids unique per section | Duplicate ⇒ parse error |
| `entry_point` | `str` | required | Module name inside the extension dir, without `.py`. Resolves to `<pack>/<entry>.py` or `<pack>/<entry>/__init__.py`; empty or `"__init__"` means `<pack>/__init__.py`. The module must define `activate(ctx)`. | `ENTRY_POINT_MISSING` error (file absent, checked without importing) or `ENTRY_POINT_FAILED` error (import/`activate` missing or raised) |
| `diagnostics_entry` | `Optional[str]` | `None` | `"module:function"` resolved as attributes of the entry module, or a bare `"function"` on the entry module. The function returns `{"status": "healthy\|degraded\|unhealthy", "messages": [...]}`. | Unresolvable or raising ⇒ `degraded` health; unhealthy ⇒ activation rollback. See [diagnostics-cli.md](diagnostics-cli.md). |

### Size guards

- Total declared items across all five sections ≤ **128** (`MAX_DECLARED_ITEMS`).
- `manifest.json` file itself ≤ **256 KiB** (enforced by discovery; oversize
  is treated as a parse failure).

## Naming rules

- `namespace`: `^[a-z][a-z0-9_]{1,31}$`, never in the reserved list
  (`core, webgis, app, pi, builtin, internal, gis, lib, tools, vendor`).
- `name` / tool / algorithm / source-type / cartography-item / pack ids:
  `^[a-z][a-z0-9_]{0,63}$` — a **single character is valid** (e.g.
  `{"namespace": "ns", "name": "a"}`).
- `id` must equal `<namespace>.<name>`.
- `entry_point`: identifier segments separated by `/`
  (`^[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*$`), or `""` for a
  package-root extension (`__init__.py`). `..`, absolute paths and special
  characters are rejected; the host additionally requires the resolved
  entry file to stay inside the extension directory (fingerprint coverage).


```python
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")   # namespace
_NAME_RE   = re.compile(r"^[a-z][a-z0-9_]{1,63}$")  # tool/algorithm/source_type names
```

- Namespace: lowercase snake fragment, 2–32 chars, starts with a letter.
- Reserved namespaces (always rejected):
  `core, webgis, app, pi, builtin, internal, gis, lib, tools, vendor`.
- The global extension id is always `id = f"{namespace}.{name}"`.
- Namespacing of projected ids (enforced by the host, not by the author):

  | Kind | Projected id |
  | --- | --- |
  | tool | `<ns>_<name>` |
  | algorithm | `<ns>.<id>` |
  | data provider source type (and aliases) | `<ns>_<source_type>` |
  | cartography component/model/theme id | `<ns>_<id>` |
  | component payload `type` | forced `<ns>_*` prefix |
  | recipe id inside a workflow pack | must already start with `<ns>_` |

  Core entries are therefore physically impossible to shadow; a projected
  collision is a typed error (`REGISTRY_PROJECTION_COLLISION`).

## Declaration sections

Each declaration describes one future registration. The declarations use the
**un-namespaced** name; the host applies the namespace when reconciling and
projecting.

- `ToolDeclaration`: `name` (required, `_NAME_RE`), `description` (required),
  `summary=""`, `tier` (int `1..2`, default 2 — extensions can never declare
  tier 3, the core confirmation chokepoint), `side_effect="unclassified"`.
- `AlgorithmDeclaration`: `id` (required, `_NAME_RE`), `description=""`,
  `scientific_status="EXPERIMENTAL"`.
- `DataProviderDeclaration`: `source_type` (required, `_NAME_RE`),
  `description=""`, `supports_query=True`.
- `CartographyItemDeclaration`: `kind` (required, `component|model|theme`),
  `id` (required), `description=""`, `runtime_status` (`native|planned|
  unavailable`, default `planned`).
- `WorkflowPackDeclaration`: `pack_id` (required), `description=""`,
  `recipe_count` (int `0..128`, default 0).

## Declaration ↔ registration reconciliation

At activation the host compares what `activate(ctx)` actually registered
against the manifest declarations:

| Direction | Severity | Diagnostic | Effect |
| --- | --- | --- | --- |
| Registered but **not declared** | error | `UNDECLARED_REGISTRATION` | Raised immediately by `ExtensionContext` at registration time; activation fails and the ledger rolls back. |
| Declared but **not registered** | warning | `DECLARED_BUT_UNREGISTERED` | Activation succeeds as `degraded`. Intended for feature-flag-gated items ("(flag-gated?)"). Checked for tools, algorithms, and data providers (post-activation reconciliation). |

Rule of thumb: **you may promise less than you ship per-flag, but you may
never ship more than you promise.**

## `schema_version` policy

`MANIFEST_SCHEMA_VERSION` is currently **1**. The host accepts only
`1 ≤ schema_version ≤ MANIFEST_SCHEMA_VERSION`; anything above is rejected
outright rather than parsed leniently. This is the fail-closed guarantee that
a future manifest field can never be silently ignored by an older host.
New optional fields in schema v2+ must ship together with a host that
understands them; removing or redefining a field requires a schema-version
bump (see [compatibility.md](compatibility.md)).
