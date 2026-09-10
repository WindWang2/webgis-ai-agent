# Marketplace & Distribution (V3)

Server-side package registry + distribution for GIS extension packs
(ADR-0120). A registry is **storage and policy**, not a commercial store.

## Layout

```
<EXTENSION_REGISTRY_DIR>/
  state.json                 # RegistryState (generation + packages) — the only index
  objects/<digest[:2]>/<digest>   # content-addressed package blobs (.tar.gz)
  .registry.lock             # publish serialization lock (O_EXCL)
```

- Index bounds: ≤ 4096 packages, ≤ 256 versions per package (publish is
  typed-rejected beyond that — documented trade-off).
- Publish is **serialized by the file lock**; POSIX has no atomic
  CAS-rename, so concurrency is handled by exclusion, not rename races.
- Blobs are garbage-collected only under the same lock and only when
  unreferenced **and** older than 7 days.

## Publishing (operator CLI)

```bash
python -m app.extensions_platform keygen --publisher acme --key-id acme-2026 --out-dir keys/
python -m app.extensions_platform sign <pack_dir> --publisher acme --key-id acme-2026 --private-key keys/acme-2026.private.pem
python -m app.extensions_platform publish <pack_dir> \
  --registry-dir /srv/ext-registry --trust-store /srv/ext-trust.json \
  --allow-publisher acme
```

Publish preconditions (all enforced, typed refusal otherwise): blob digest
recompute, Ed25519 signature by an **active** trust-store key (retired keys
are refused at publish time), SBOM secret scan clean, publisher allowlist,
package-id **claim-once** (a second publisher claiming the same id is the
dependency-confusion defense and is rejected).

## Read-only HTTP API

| Route | Meaning |
|---|---|
| `GET /api/v1/extensions/marketplace/packages` | search (`q`/`tag`/`publisher`/`offset`/`limit`) |
| `GET /api/v1/extensions/marketplace/packages/{id}` | package detail |
| `GET /api/v1/extensions/marketplace/packages/{id}/versions/{v}` | version record |
| `GET /api/v1/extensions/marketplace/packages/{id}/versions/{v}/download` | blob stream (`X-Content-Digest`) |

Revoked packages disappear from search and download returns **410**. There
are deliberately **no HTTP write endpoints** — publishing needs the signing
key, which is operator material.

## Installing / upgrading / rolling back (operator CLI)

```bash
python -m app.extensions_platform install acme.demo 1.2.0 \
  --registry-dir /srv/ext-registry --trust-store /srv/ext-trust.json \
  --install-root /srv/ext-install --pin 'acme.demo==1.2.0'
python -m app.extensions_platform rollback acme.demo            # semver-max archived version
python -m app.extensions_platform rollback acme.demo --version 1.1.0
```

One **shared preflight** guards install, upgrade and rollback: digest,
trust-store signature, revocation (a revoked `(id, version)` cannot be
installed, upgraded to, *or rolled back to*), version pin, downgrade gate,
and resolver dependency conflicts.

Swap order is crash-safe: staging (verified unpack) → `rename(active →
versions/<old>)` → `rename(staging → active)`. A crash between the two
renames leaves `active` missing; the **startup recovery routine** (run
before staging sweeps) completes the interrupted install from staging.

## Revocation propagation

`revoke` (CLI) writes the trust store atomically and bumps the registry.
Hosts consume revocations lazily: `ExtensionHost.refresh_revocations()`
re-reads the trust store (mtime fast-path) and deactivates + quarantines
any **active** extension whose `(id, version)` or content fingerprint is
revoked. The worst-case exposure window is documented in
[limitations.md](limitations.md).
