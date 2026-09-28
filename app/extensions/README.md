# app/extensions/ — Pi agent extension artifact (not a Python package)

This directory intentionally contains **no Python code**. It is not part of the
Python extension platform and must not be confused with it:

- `webgis-tools/index.mjs` — the WebGIS extension entry file loaded by the
  bundled vendor/pi coding agent. `app/main.py` builds this exact path and
  passes it to `get_pi_bridge(extension_paths=[...])` at startup; Node's
  extension loader executes it as ESM.
- The Python GIS extension platform (SDK, manifests, host, worker,
  marketplace, certification) lives in **`app/extensions_platform/`** — see
  `docs/extension-platform/architecture.md`.
- Repo-root `extensions/` holds example packs *for* that platform
  (`extensions/examples/extdemo-*-pack`) — also unrelated to this directory.

Contract tests read `index.mjs` from this path; do not move, rename, or add
sibling files (e.g. a dead `index.ts`) without updating `app/main.py` and:

- `tests/test_tool_meta_contract.py`
- `tests/unit/test_pi_bridge_compat.py`
- `tests/unit/test_pi_extension_hardening.py`
- `tests/unit/test_pi_extension_turn_token.py`
