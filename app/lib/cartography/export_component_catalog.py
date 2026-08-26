#!/usr/bin/env python3
"""Export the backend component catalog as a frontend contract artifact.

Writes ``frontend/lib/map-components/component-catalog.generated.json`` —
the single machine-readable truth consumed by:

- the frontend renderer parity test (every renderer_required type must have
  a registered renderer);
- the backend contract test (tests/unit/test_component_catalog_parity.py)
  which regenerates the payload and fails on drift.

Run from repo root:  python -m app.lib.cartography.export_component_catalog
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from app.lib.cartography.component_registry import get_component_registry
from app.services.gis_harness.components import ComponentType

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT = REPO_ROOT / "frontend" / "lib" / "map-components" / "component-catalog.generated.json"

# ComponentTypes that must have a live interactive renderer whenever a spec
# enables them (chrome/panel family). Export/planned types are exempt.
RENDERER_EXEMPT = {
    "basemap",          # type-only union member (style, not chrome)
    "export_layout",    # exporter-side only
    "graticule",        # export-oriented overlay (renderer optional)
    "map_border",       # export frame (renderer optional)
    "inset_map",        # runtime_status=planned (not in ComponentType union yet)
}


def build_catalog() -> dict:
    registry = get_component_registry()
    types = sorted(get_args(ComponentType))
    components = []
    for t in types:
        desc = registry.get_by_type(t)
        entry = {
            "type": t,
            "variants": list(desc.variants) if desc else [],
            "defaultVariant": desc.default_variant if desc else "default",
            "defaultPosition": desc.default_position if desc else "none",
            "category": desc.category if desc else "",
            "runtimeStatus": desc.runtime_status if desc else "native",
            "rendererRequired": t not in RENDERER_EXEMPT,
        }
        components.append(entry)
    return {
        "schemaVersion": 1,
        "exportedFrom": "app/lib/cartography/component_registry.py",
        "componentTypes": components,
    }


def main() -> int:
    catalog = build_catalog()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(catalog['componentTypes'])} component types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
