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
