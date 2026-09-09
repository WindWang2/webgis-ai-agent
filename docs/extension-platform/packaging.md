# Packaging an Extension

Reference example:
[`extensions/examples/extdemo-pack/`](../../extensions/examples/extdemo-pack/).
Generate a starter with
`python -m app.extensions_platform scaffold <namespace> <name> --dir OUT_DIR`.

## Directory layout

```
<root>/                      # a directory listed in EXTENSIONS_DIRS (or --root)
  <any-dir-name>/            # the extension pack (dir name is irrelevant)
    manifest.json            # the only entry point discovery looks for
    main.py                  # entry_point module: must define activate(ctx)
    health.py                # optional: health check (diagnostics_entry)
    tile_catalog.py          # optional: sibling modules (adapters, helpers)
    catalog.json             # optional: data files (hashed into the fingerprint)
```

Discovery is exactly one level deep: `<root>/<ext>/manifest.json`. A
directory without a `manifest.json` is ignored. Packs are discovered in
sorted order; a duplicate extension id quarantines the later copy.

### Sibling modules: the pack is NOT on `sys.path`

The entry module is loaded by the host under a fingerprinted module name
(`webgis_ext_<ns>_<name>_<fp12>`) **by file path** — the pack directory is
never added to `sys.path`. Plain `import tile_catalog` will fail (or worse,
collide with an unrelated cached module). Load siblings by explicit path and
mount them under your own module namespace, exactly as the example does:

```python
import importlib.util
import sys
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parent

def _load_sibling(module_name: str):
    """Load a sibling module by path (the pack dir is not on sys.path)."""
    path = _PACK_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"{__name__}_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load extension sibling module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # under webgis_ext_* -> purged on unload
    spec.loader.exec_module(module)
    return module

tile_catalog = _load_sibling("tile_catalog")
health = _load_sibling("health")
```

Mounting under `__name__` (which is the `webgis_ext_*` name the host chose)
matters: `unload()` purges every module sharing the `webgis_ext_<ns>_<name>`
prefix, so no stale handles survive a reload.

## Discovery bounds (bounded discovery)

| Bound | Value | On violation |
| --- | --- | --- |
| Extensions per scan | 64 (`DEFAULT_MAX_EXTENSIONS`) | `DISCOVERY_LIMIT_EXCEEDED` error; remaining dirs ignored |
| `manifest.json` size | 256 KiB | treated as parse failure |
| Manifest declared items | 128 | parse error |
| Fingerprint input | ≤ 512 files, ≤ 8 MiB total | fingerprint refused (`FINGERPRINT_CHANGED`) |

A broken pack fails alone (per-extension fail closed); the rest of the scan
continues. A root that is not a directory yields a warning and is skipped.

## Fingerprint semantics

`compute_fingerprint(ext_dir)` hashes, in sorted relative-path order and
domain-separated (`webgis-extension-fingerprint-v1`), every file in the
pack.

- **Excluded**: `__pycache__/` directories and `*.pyc` files — interpreter
  byproducts, not content. Without this exclusion the first activation (which
  writes bytecode) would change every fingerprint.
- **Included**: everything else, including data files like `catalog.json` —
  content change ⇒ fingerprint change ⇒ the host can detect tampering and
  the module namespace changes (anti-staleness; see
  [architecture.md](architecture.md)).
- The fingerprint participates in the entry module name and is re-checked at
  activation (`FINGERPRINT_CHANGED` warning if content drifted between
  discovery and activation).

## Settings reference (`EXTENSIONS_*`)

Defined in `app/core/config.py`; resolved into a `HostPolicy` by
`settings_bridge.host_policy_from_settings()` (fail closed: malformed JSON
or an unknown permission word aborts policy construction).

| Setting | Type / default | Meaning |
| --- | --- | --- |
| `EXTENSIONS_ENABLED` | bool, `False` | Master switch. `False` ⇒ the lifespan never builds a host; startup is identical to pre-platform behavior |
| `EXTENSIONS_DIRS` | str, `""` | Colon-separated (`:`) list of discovery roots (`<root>/<ext>/manifest.json`) |
| `EXTENSIONS_ALLOW` | str, `""` | Comma-separated extension ids force-granted `trusted_extension` |
| `EXTENSIONS_BLOCK` | str, `""` | Comma-separated extension ids forced to `blocked` (wins over allow) |
| `EXTENSIONS_BUILTIN_IDS` | str, `""` | Comma-separated extension ids treated as `trusted_builtin` (repo-shipped packs) |
| `EXTENSIONS_ACTIVATE_UNTRUSTED` | bool, `False` | Execution gate for `local_untrusted` extensions: `False` (default) = allowlist-only activation |
| `EXTENSION_PERMISSION_GRANTS` | str, `""` | Grant table `id:perm1,perm2;id2:perm3`; see [permissions-and-trust.md](permissions-and-trust.md) |
| `EXTENSION_FEATURE_FLAGS` | str, `"{}"` | JSON object `{ext_id: {flag: bool}}`; host overrides for manifest flags |
| `EXTENSION_SETTINGS_JSON` | str, `"{}"` | JSON object `{ext_id: {...}}`; delivered to `activate(ctx)` as `ctx.extension_settings` (typically `settings_schema` instance values) |
| `EXTENSION_SECRETS_JSON` | str, `"{}"` | V2: JSON object `{ext_id: {ref: value}}`; provisioning-as-authorization for `ctx.get_secret` / broker `secret_get`. Values never appear in status, logs, or audit |
| `EXTENSION_NETWORK_ALLOW` | str, `""` | V2: worker broker egress allowlist `"id:host1,host2;id2:*"` (hostname-level, per extension id; `"*"` still passes the SSRF gate, not around it) |
| `EXTENSION_ARTIFACT_ROOTS` | str, `""` | V2: `os.pathsep`-separated roots confining worker broker `artifact_read`/`artifact_write`; **empty = all artifact ops denied** |
| `EXTENSION_TRUSTED_PUBLISHERS` | str, `""` | V2: publisher key table `"key_id:keyfile,..."` (file content = HMAC key bytes); used by `verify` and host-side signature verdicts |
| `EXTENSIONS_TRUST_SIGNED` | bool, `False` | V2: a verified signature from a trusted publisher elevates `local_untrusted` → `trusted_extension` (never lowers an existing level) |
| `EXTENSIONS_ALLOW_UNSIGNED_DEV` | bool, `False` | V2: explicit unsigned-dev mode — loud warning diagnostic, no permission semantics change |
| `EXTENSIONS_MAX_WORKER_CRASHES` | int, `2` | V2: consecutive worker crashes (range 1–10) before the extension is quarantined |

The STAC-related `STAC_API_URL` setting (default
`https://earth-search.aws.element84.com/v1`) is core data-fabric
configuration, not extension config — see [ogc-stac.md](ogc-stac.md).

## Enabling in dev vs prod

Dev (per-shell env):

```bash
export EXTENSIONS_ENABLED=true
export EXTENSIONS_DIRS="extensions/examples"          # root that CONTAINS pack dirs
export EXTENSIONS_BUILTIN_IDS="extdemo.pack"          # trust the example pack
export EXTENSION_PERMISSION_GRANTS="extdemo.pack:network"
python -m uvicorn app.main:app                        # lifespan activates
```

Or without touching the server, use the read-only CLI against any root:

```bash
python -m app.extensions_platform list --root extensions/examples
python -m app.extensions_platform validate extdemo.pack --root extensions/examples
```

Prod checklist:

1. Ship packs to a dedicated root outside the code tree; keep the root in
   your secret/config management (`EXTENSIONS_DIRS`).
2. `EXTENSIONS_ENABLED=true` plus explicit trust: ids in
   `EXTENSIONS_BUILTIN_IDS` (repo-shipped) or `EXTENSIONS_ALLOW`
   (operator-vetted). Everything else lands `local_untrusted`.
3. Grant permissions explicitly per id; the default is none. Review any
   `local_untrusted` pack declaring `filesystem_write` /
   `external_process` / `destructive_action` before granting (the host
   emits a `TRUST_BLOCKED` warning for these).
4. Remember the trusted-code boundary: activation runs the pack's code in
   this process — see [permissions-and-trust.md](permissions-and-trust.md).
5. Pre-flight: `python -m app.extensions_platform doctor` (read-only;
   settings summary, per-extension state, common-problem hints).

## Signing a pack (V2)

`signing.py` adds content signing to the packaging flow. The signature
covers the same content the fingerprint covers — `signature.json` itself
is excluded from the fingerprint (no circularity), everything else
including data files is included.

`signature.json` (written into the pack dir by `package`):

```json
{
  "algorithm": "hmac-sha256",
  "key_id": "acme-release",
  "fingerprint": "<sha256 content fingerprint>",
  "signature": "<hex hmac>",
  "signed_at": "2026-09-09T00:00:00+00:00"
}
```

- The HMAC payload is domain-separated:
  `webgis-extension-signature-v1 \n key_id \n fingerprint`. `signed_at` is
  informational only — verification is deterministic and replayable, never
  time-based.
- Re-signing identical content with the same key is idempotent (only
  `signed_at` differs).

Usage:

```bash
# publisher side: write signature.json (key material is never printed)
python -m app.extensions_platform package /path/to/acme-pack \
    --key-id acme-release --key-file /path/to/acme-release.key

# operator side: deterministic verdict (exit 0 = verified/missing,
# 1 = invalid / tampered / signed_untrusted)
python -m app.extensions_platform verify /path/to/acme-pack \
    --publisher acme-release:/path/to/acme-release.key
python -m app.extensions_platform verify /path/to/acme-pack --json
```

Key management: `EXTENSION_TRUSTED_PUBLISHERS="key_id:keyfile,..."` maps
publisher ids to HMAC key files (file *content* is the key). On the host,
discovery runs `verify_pack_signature` against this table and applies the
verdict:

| Verdict | Host action |
| --- | --- |
| `tampered` / `invalid` | **quarantined, even if the id is allowlisted** — re-sign and re-discover (`package_tampered` / `signature_invalid`) |
| `signed_verified` + `EXTENSIONS_TRUST_SIGNED=true` | `local_untrusted` elevates to `trusted_extension` (`signature_verified`, info); existing trust levels are never lowered |
| `signed_untrusted` | warning `publisher_untrusted` when trust-signed policy is on; falls back to operator trust config |
| `missing` | default policy: silent (V1 behavior preserved); under `EXTENSIONS_ALLOW_UNSIGNED_DEV=true`: loud warning only |

Honest positioning: this is **shared-key authentication** (anyone who can
verify holds the signing secret — publisher ≈ operator), not independent
publisher identity. Asymmetric signatures are a follow-up. And signing
complements, never replaces, the trusted-code boundary — see
[security-boundary.md](security-boundary.md).

## SBOM (V2)

`sbom.py` produces a **deterministic** bill of materials: same pack +
same fingerprint ⇒ byte-identical JSON (no timestamps, everything
sorted). Contents:

- `files` — path / bytes / sha256 for every file the fingerprint covers
  (same bounds: 512 files / 8 MiB; exceeded ⇒ typed refusal);
- `python_imports` — top-level module names parsed from the AST of all
  `*.py` files, excluding the standard library, the platform SDK (`app`),
  and relative (sibling) imports;
- `dependencies` — mirror of the manifest dependency declarations,
  including `version` constraint strings and the optional flag;
- `secret_scan` — high-confidence secret *shapes* (AWS access keys,
  private-key blocks, Slack / GitHub / OpenAI-style tokens) reported as
  `{file, kind}` findings; shape hits only, no network verification.
  `signature.json` is scanned too (it can leak just like anything else).

```bash
python -m app.extensions_platform sbom extdemo.pack --root extensions/examples
python -m app.extensions_platform sbom extdemo.pack --json
```

Publishing checklist: run `sbom`, resolve `secret_scan` findings, then
`package` (sign) — a leaked key found by the scan should never make it
into a signed artifact. `certify` runs the scan as part of its suite.

## V3: asymmetric signing and the registry

Signing now supports Ed25519 alongside (and preferred over) the V2
shared-key HMAC. The v2 `signature.json` binds the content fingerprint to
`(publisher, key_id)`; the trust store decides whether that key is active
(elevatable), retired (verifiable but never elevated) or revoked
(quarantine).

```bash
# keys (operator machine; private key never leaves it)
python -m app.extensions_platform keygen --publisher acme --key-id acme-2026 --out-dir keys/

# sign
python -m app.extensions_platform sign ./my-pack --publisher acme \
  --key-id acme-2026 --private-key keys/acme-2026.private.pem

# publish (registry enforces digest, signature, SBOM scan, allowlist)
python -m app.extensions_platform publish ./my-pack \
  --registry-dir /srv/ext-registry --trust-store /srv/ext-trust.json \
  --allow-publisher acme
```

Registry layout, HTTP read API, installation, upgrade/rollback and
revocation propagation are documented in
[marketplace.md](marketplace.md). Worker-capable packs should declare
`api_version >= 1.2.0` and may stream (`execution.max_stream_events`,
`execution.stream_window`) — see
[security-boundary.md](security-boundary.md) for the isolation model.
