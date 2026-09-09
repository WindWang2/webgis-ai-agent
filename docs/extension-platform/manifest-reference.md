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
| `api_version` | `str` | `"1.0.0"` | Semver; host major must match, minor must be ≤ host minor. **V2 features require ≥ 1.1.0** — see [the V2 floor rule](#the-v2-feature-floor-api_version--110) | `API_VERSION_INCOMPATIBLE` error at validate ⇒ `incompatible`. See [compatibility.md](compatibility.md). |
| `minimum_core_version` | `str` | `"0.1.0"` | Semver; lower bound of the core release window (inclusive) | `CORE_VERSION_INCOMPATIBLE` error at validate |
| `maximum_core_version` | `Optional[str]` | `None` | Semver; **exclusive** upper bound; must exceed `minimum_core_version` | Parse error if window inverted; `CORE_VERSION_INCOMPATIBLE` if release ≥ bound |
| `title` | `str` | `""` | — | — |
| `description` | `str` | `""` | ≤ 2000 chars | Parse error |
| `vendor` | `str` | `""` | — | — |
| `extension_types` | `list[str]` | `[]` | Values from `tools, algorithms, data_providers, cartography, workflow, model_provider` | Unknown value ⇒ parse error. `model_provider` requires `api_version >= 1.1.0`. Types are also inferred from populated declaration sections (`declared_type_set()`). |
| `capabilities` | `list[str]` | `[]` | Informational self-description toward the capability catalog. **Not authorization; not validated against CapabilityRegistry** (unlike the spec-level `capabilities`, which are validated). | — |
| `permissions` | `list[str]` | `[]` | Values from the fixed 9-word vocabulary | Unknown word ⇒ `PERMISSION_DECLARATION_INVALID` error; duplicate ⇒ warning. See [permissions-and-trust.md](permissions-and-trust.md). |
| `trust` | `str` | `"local_untrusted"` | Declarable values only: `trusted_builtin`, `trusted_extension`, `local_untrusted` | `core` / `blocked` / anything else ⇒ parse error. **The declaration has no authoritative effect** — the host recomputes trust from operator policy. |
| `dependencies` | `list[DependencyDeclaration]` | `[]` | Each: `{id, required=True, feature_flag=None, version=None}`; edge is skipped when its `feature_flag` resolves false; `version` is a V2 constraint string (see [below](#dependenciesversion-constraints-v2)) | Missing/cyclic ⇒ `DEPENDENCY_MISSING` / `DEPENDENCY_CYCLE` errors ⇒ `incompatible`; constraint unsatisfied ⇒ `DEPENDENCY_MISSING` error; not active at activate ⇒ `failed` |
| `optional_dependencies` | `list[DependencyDeclaration]` | `[]` | Same shape | Absent or constraint unsatisfied ⇒ `OPTIONAL_DEPENDENCY_ABSENT` warning; activation proceeds as `degraded` |
| `feature_flags` | `dict[str, bool]` | `{}` | Declared flag names; host overrides via `EXTENSION_FEATURE_FLAGS` | Flag with no host override ⇒ `FEATURE_FLAG_UNRESOLVED` warning (manifest default wins) |
| `settings_schema` | `Optional[dict]` | `None` | JSON object; serialized size ≤ 32 KiB; `type` (default `"object"`) must be a string. Values are provided per extension id via `EXTENSION_SETTINGS_JSON`. | Parse error on oversize/malformed |
| `tools` | `list[ToolDeclaration]` | `[]` | See below; names unique per section | Duplicate ⇒ parse error |
| `algorithms` | `list[AlgorithmDeclaration]` | `[]` | See below; ids unique per section | Duplicate ⇒ parse error |
| `data_providers` | `list[DataProviderDeclaration]` | `[]` | See below; source types unique per section | Duplicate ⇒ parse error |
| `cartography_items` | `list[CartographyItemDeclaration]` | `[]` | See below; `(kind, id)` pairs unique | Duplicate ⇒ parse error |
| `workflow_packs` | `list[WorkflowPackDeclaration]` | `[]` | See below; pack ids unique per section | Duplicate ⇒ parse error |
| `model_providers` | `list[ModelProviderDeclaration]` | `[]` | **V2.** See [below](#model_providers-v2); provider ids unique per section. Requires `api_version >= 1.1.0` | Duplicate ⇒ parse error |
| `execution` | `Optional[ExecutionDeclaration]` | `None` | **V2.** See [below](#execution-v2). `None` = `in_process` (V1 semantics). Requires `api_version >= 1.1.0` | Worker-mode structural violations ⇒ parse error |
| `entry_point` | `str` | required | Module name inside the extension dir, without `.py`. Resolves to `<pack>/<entry>.py` or `<pack>/<entry>/__init__.py`; empty or `"__init__"` means `<pack>/__init__.py`. The module must define `activate(ctx)`. | `ENTRY_POINT_MISSING` error (file absent, checked without importing) or `ENTRY_POINT_FAILED` error (import/`activate` missing or raised) |
| `diagnostics_entry` | `Optional[str]` | `None` | `"module:function"` resolved as attributes of the entry module, or a bare `"function"` on the entry module. The function returns `{"status": "healthy\|degraded\|unhealthy", "messages": [...]}`. | Unresolvable or raising ⇒ `degraded` health; unhealthy ⇒ activation rollback. See [diagnostics-cli.md](diagnostics-cli.md). |

### Size guards

- Total declared items across all six sections (including
  `model_providers`) ≤ **128** (`MAX_DECLARED_ITEMS`).
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
  `summary=""`, `tier` (int `1..2`, default 1 — extensions can never declare
  tier 3, the core confirmation chokepoint; tier 2 requires at least one
  `domains` entry, fail closed), `side_effect="unclassified"`.
- `AlgorithmDeclaration`: `id` (required, `_NAME_RE`), `description=""`,
  `scientific_status="EXPERIMENTAL"`.
- `DataProviderDeclaration`: `source_type` (required, `_NAME_RE`),
  `description=""`, `supports_query=True`.
- `CartographyItemDeclaration`: `kind` (required, `component|model|theme`),
  `id` (required), `description=""`, `runtime_status` (`native|planned|
  unavailable`, default `planned`).
- `WorkflowPackDeclaration`: `pack_id` (required), `description=""`,
  `recipe_count` (int `0..128`, default 0).

## The V2 feature floor (`api_version` >= 1.1.0)

V2 features are opt-in and floor-gated. A manifest that declares any of

- an `execution` section,
- a non-empty `model_providers` section (or the `model_provider` type),
- a `version` constraint on any dependency (required or optional),

must declare `api_version >= 1.1.0` (`V2_FEATURE_API_FLOOR` in
`api_version.py`). The check is a cross-field validation at parse time and
fails closed with a message naming the rule — an old-api manifest carrying
V2 fields is structurally rejected, never silently ignored. Manifests
without V2 fields keep `api_version "1.0.0"` and remain valid unchanged.

## `execution` (V2)

```json
"execution": {
  "mode": "worker",
  "startup_timeout_s": 10,
  "call_timeout_s": 30,
  "max_memory_mb": 512,
  "max_cpu_seconds": 60,
  "max_output_bytes": 1048576
}
```

| Field | Type | Default | Bounds | Meaning |
| --- | --- | --- | --- | --- |
| `mode` | `str` | `"in_process"` | `in_process \| worker` | `worker` = the extension runs in an isolated subprocess the host never imports (see [security-boundary.md](security-boundary.md)); `in_process` = V1 semantics |
| `startup_timeout_s` | `float` | `10.0` | `0.5 .. 120` | Handshake + activate budget for the worker process; exceeded ⇒ `worker_startup_timeout` |
| `call_timeout_s` | `float` | `30.0` | `0.1 .. 3600` | Per-call (and health RPC) budget; exceeded ⇒ `worker_call_timeout` + `killpg` |
| `max_memory_mb` | `int` | `512` | `32 .. 8192` | `RLIMIT_AS` applied by the worker itself before loading the pack |
| `max_cpu_seconds` | `int` | `60` | `1 .. 86400` | `RLIMIT_CPU` (soft = hard), same application point |
| `max_output_bytes` | `int` | `1048576` (1 MiB) | `1024 .. 67108864` (64 MiB) | Serialized tool-result cap; exceeded ⇒ `output_limit_exceeded` typed error (the worker is not killed) |

All budget fields are host-enforced upper bounds; the hard caps exist so a
malicious manifest cannot declare pathological budgets. On platforms
without rlimit support the host emits typed
`resource_limit_unavailable` warnings — no false sandbox claims.

### Worker-mode structural constraints (parse-time, fail closed)

A manifest with `execution.mode = "worker"` is **rejected** at parse time if:

- it declares `algorithms`, `data_providers`, `cartography_items`, or
  `workflow_packs` — class-instance projections register objects into
  host registries and cannot cross the process boundary. Only `tools` and
  `model_providers` are supported in worker mode;
- its `permissions` include `external_process` — an isolated worker has no
  subprocess surface;
- any `model_providers[]` entry declares the `streaming` capability — the
  single-frame RPC cannot carry an event stream.

## `model_providers` (V2)

```json
"model_providers": [
  {
    "id": "wordfreq",
    "description": "Deterministic offline word-frequency model.",
    "capabilities": ["streaming", "cancellation"],
    "credentials_ref": "demo_key"
  }
]
```

| Field | Type | Default | Constraints | Meaning |
| --- | --- | --- | --- | --- |
| `id` | `str` | required | `_NAME_RE` (`^[a-z][a-z0-9_]{1,63}$`), unique per section | Projected invoke tool name: `<ns>_<id>_invoke` in the `ToolRegistry` |
| `description` | `str` | `""` | — | Tool description for the projected invoke tool |
| `capabilities` | `list[str]` | `[]` | Subset of `{streaming, cancellation, batch}`; unknown values ⇒ parse error; `streaming` forbidden in worker mode | Declared capability envelope — SDK `ModelProviderSpec.capabilities` must stay within it (exceeding ⇒ error) |
| `credentials_ref` | `Optional[str]` | `None` | `_TOKEN_RE` shape | Credential *name*; the value is operator-provisioned per extension id via `EXTENSION_SECRETS_JSON` (`ctx.get_secret` / broker `secret_get`) and never enters status, logs, or tool results |

Semantics: this declares a **GIS domain inference model** (segmentation /
detection / classification style service), projected as a typed invoke
tool on the real dispatch path plus `host.invoke_model_provider()` (worker
mode: single-frame aggregate only). It is **not** an LLM chat transport —
ADR-0102's `ModelDescriptorRegistry` stays closed to extensions. The
`model_provider` permission word must be declared in `permissions` when
the section is used (checked at projection via the SDK validator).

## `dependencies[].version` constraints (V2)

`DependencyDeclaration` gains an optional `version` constraint string with
a deliberately minimal, zero-dependency grammar (defined in
`version_constraints.py`; unrelated to PEP 440):

```
constraint := spec ("," spec)*        # comma = AND semantics
spec       := op version              # version is X.Y[.Z] semver
op         := ">=" | "<" | "<=" | ">" | "==" | "!="
```

- Examples: `">=1.2"`, `">=1.2,<2.0"`, `"==1.4.0"`, `"!=1.3,<2.0"`.
- Empty/whitespace strings are invalid (declaring the field means
  constraining); at most 8 specs and 64 chars.
- Syntax errors are parse errors (fail closed at manifest level).
- At validate time the constraint is checked against the discovered
  dependency's actual version: unsatisfied required ⇒ `DEPENDENCY_MISSING`
  error (state `incompatible`); unsatisfied optional ⇒
  `OPTIONAL_DEPENDENCY_ABSENT` warning (`degraded`). Upgrades that would
  break any dependent's constraint are refused (`dependency_conflict`);
  the resolver and upgrade preflight live in `resolver.py`.

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
