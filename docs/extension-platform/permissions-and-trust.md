# Permissions and Trust

Sources: `app/extensions_platform/permissions.py` and `trust.py`.

## The permission vocabulary (fixed at 9)

```python
network, filesystem_read, filesystem_write,
project_artifact_read, project_artifact_write,
external_process, database, model_provider, destructive_action
```

The vocabulary is closed in V1 **and stays frozen in V2**. Adding a word
requires raising the extension API major version. Unknown words are
rejected (fail closed), never ignored. V2's new capabilities deliberately
did *not* grow the vocabulary: worker secrets are provisioning-gated (see
below), not a `secret` permission word — so no major version bump was
needed. `filesystem_*` and `external_process` are high-risk words with an
honest caveat — see [the boundary statement](#the-trusted-code-boundary-honest-statement).
Note: worker-mode manifests cannot declare `external_process` at all
(isolated workers have no subprocess surface; parse-time rejection).

## Declaration ≠ grant

- **Declaration** is the manifest's `permissions` list — an *intent*. It is
  validated against the vocabulary (`PERMISSION_DECLARATION_INVALID` error
  for unknown words, warning for duplicates) and bounds what the extension's
  tools may require (`required_permissions ⊆ manifest permissions`).
- **Grant** is the operator's decision via `EXTENSION_PERMISSION_GRANTS`.
  The default grant set is **empty**: nothing is authorized until an
  operator writes the grant line.

### Grants config format

```
EXTENSION_PERMISSION_GRANTS="extdemo.pack:network,filesystem_read;acme.gis:network"
```

- Entries separated by `;`, each `extension_id:perm1,perm2`.
- Whitespace is ignored; repeated ids for the same extension are merged
  (union).
- Malformed entries (missing id) or unknown permission words raise at
  policy-build time (`parse_grants_config` — fail closed; `python -m
  app.extensions_platform doctor` reports them as problems).

## Typed denial path

For a tool declaring `required_permissions`, the SDK wraps the function at
projection time. Before every call, each required permission is checked
against the grant set; the first missing one raises
`ExtensionPermissionDenied` — the function body never runs. The exception is
classified by the standard `ToolRegistry` error surface, so the LLM sees:

```json
{
  "code": "TOOL_ERROR",
  "error_type": "ExtensionPermissionDenied",
  "error": "tool 'synth_fetch' of 'acme.pack' requires permission 'network' which was not granted"
}
```

No silent degradation, no stringly-typed failures; the same typed exception
is directly usable in tests (`PermissionGrantSet.require`). A missing grant
does **not** fail activation — activation and grants are orthogonal; denial
happens at call time.

## Narrowing-only composition

Sub-agents and nested contexts can only **shrink** permissions:

```python
child = parent_grants.intersect(other_grants)   # granted = parent ∩ other
```

There is no union-of-grants path; no code path can widen a grant set beyond
what the operator granted.

## Trust levels

`TrustLevel` (from `trust.py`):

| Level | Meaning |
| --- | --- |
| `core` | In-repo core code (not managed by the extension platform); not declarable in a manifest |
| `trusted_builtin` | Ships with the repository, hosted by the platform (e.g. `extensions/examples/extdemo-pack`); code-reviewed into master; activatable by default |
| `trusted_extension` | Third-party extension explicitly named by the operator in `EXTENSIONS_ALLOW` |
| `local_untrusted` | Discovered locally but not named; can be inspected/validated; **activation requires an explicit operator opt-in** — the id must be in `EXTENSIONS_ALLOW`, or `EXTENSIONS_ACTIVATE_UNTRUSTED=true` must be set (production default: `false`) |
| `blocked` | Explicitly banned via `EXTENSIONS_BLOCK`; never imported |

### Resolution order

```python
resolve_trust(extension_id, allowlist, blocklist, builtin_ids)
# blocklist > allowlist > builtin > default local_untrusted
```

The manifest `trust` field is a **self-nomination with no authority** — the
host recomputes trust from operator policy at discovery. A manifest may only
declare `trusted_builtin`, `trusted_extension`, or `local_untrusted`
(`DECLARABLE_TRUST_LEVELS`); declaring `core` or `blocked` is a parse error.

`blocked` resolves at discovery to state `QUARANTINED` with a
`TRUST_BLOCKED` error diagnostic; quarantined extensions are **never
imported** (pinned by `test_quarantined_extension_never_imports`). The same
diagnostic code also carries a warning when a `local_untrusted` extension
declares high-risk permissions.

### Signature-based trust elevation (V2)

Discovery verifies pack signatures against the operator's publisher table
`EXTENSION_TRUSTED_PUBLISHERS` (`"key_id:keyfile,..."`) and applies the
verdict deterministically:

- `tampered` or `invalid` ⇒ **QUARANTINED even if the id is allowlisted**.
  A broken or forged signature is a supply-chain failure, not a trust
  footnote; the pack must be re-signed and re-discovered.
- `signed_verified` with `EXTENSIONS_TRUST_SIGNED=true` ⇒ a
  `local_untrusted` extension elevates to `trusted_extension`
  (`signature_verified`, info). Elevation is never a downgrade of an
  existing level.
- `signed_untrusted` ⇒ warning (`publisher_untrusted`) under the
  trust-signed policy; without the policy the operator's allow/builtin
  config decides exactly as in V1.
- `missing` ⇒ default policy is silent (V1 behavior preserved);
  `EXTENSIONS_ALLOW_UNSIGNED_DEV=true` turns it into a loud warning
  without changing any permission semantics.

Honest reading: HMAC signatures authenticate *content* ("the operator
approved exactly these bytes"), not publisher identity — publisher ≈
operator. See [security-boundary.md](security-boundary.md).

## Secrets: provisioning as authorization (V2)

Extension secrets are not a permission — they are an **operator-provisioned
resource**:

```json
// EXTENSION_SECRETS_JSON = {extension_id: {ref: value}}
{"acme.pack": {"demo_key": "sk-..."}}
```

- `activate(ctx)` injects the per-id map; `ctx.get_secret(ref)` (in-process)
  or `ctx.broker.get_secret(ref)` (worker, via the broker) returns the
  value only for refs the operator provisioned for *this* extension id.
  An unprovisioned ref is a typed denial — there is no wildcard and no
  fallback.
- The permission vocabulary stays frozen at 9 words precisely because
  provisioning *is* the authorization decision.
- Values never appear in audit records, status reports, logs, or tool
  results (pinned by `test_broker.py::test_audit_never_contains_secret_value`
  and `test_model_provider.py::test_secret_provisioned_and_never_echoed`).
- This is config-based provisioning, not a vault: no rotation, leases, or
  audit sink. Documented as a V2 limitation in
  [limitations.md](limitations.md).

## The capability broker (worker extensions only)

Extensions running with `execution.mode=worker` have no direct host
capability access: network requests, artifact reads/writes, and secret
lookups all cross an RPC into the host-side **default-deny capability
broker**, which re-checks grants plus per-op gates (network allowlist +
authoritative SSRF gate; artifact-root confinement; provisioning) and
audits every attempt into a bounded ring.

The full op → permission → gates → limits matrix lives in
[security-boundary.md](security-boundary.md). Two honest notes:

- **In-process extensions do not pass through the broker** — their
  boundary is the trusted-code statement below, unchanged from V1.
- The broker gates the SDK/broker channels of a worker; it does not
  firewall the process (see the threat model for what that means).

### Activation gate for `local_untrusted`

Trust is an **execution gate**, not just a label: `blocked` extensions are never imported, and `local_untrusted` extensions activate only when `EXTENSIONS_ACTIVATE_UNTRUSTED=true` or the id is allowlisted. In-process tests construct `HostPolicy(..., allow_local_untrusted_activation=True)` explicitly (local-development semantics).

### High-risk permissions under `local_untrusted`

`HIGH_RISK_PERMISSIONS = {filesystem_write, external_process,
destructive_action}`. A `local_untrusted` extension declaring any of them
gets a warning at validation: grants are still required at runtime and there
is no automatic elevation — but the operator is expected to look before
granting.

## The trusted-code boundary (honest statement)

Read this before relying on permissions for containment:

- Extensions are loaded **in-process** via `importlib` and execute with the
  same interpreter, memory, and OS-level filesystem/process permissions as
  the core. **This is not a sandbox**, and the platform does not claim
  otherwise anywhere (the CLI prints the same notice on every relevant
  command).
- What permissions **can** enforce: gates on the channels the SDK and
  platform provide — the tool-call wrapper (every dispatch), and helper
  channels such as provider HTTP routed through the platform's security
  layer. A permission check is deterministic and typed.
- What permissions **cannot** enforce: arbitrary Python code inside an
  activated extension is not constrained by `filesystem_*` or
  `external_process` grants — a malicious extension could open files or
  spawn processes directly. `EXTENSIONS_BLOCK` (never import the code at
  all) is the only hard containment control in V1.
- Trust levels make the *decision* "which code runs, which permissions are
  granted" explicit and auditable; they do not isolate code.

Operational consequence: only activate extensions you trust at the same
level as the core application, and use `local_untrusted` + explicit grants
as a review gate, not as a security boundary.
