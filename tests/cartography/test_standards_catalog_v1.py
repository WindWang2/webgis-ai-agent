"""Standards catalog drift gate (ADR-0200).

The committed catalog must equal the registry projection — the generator's
--check, executed as a test so CI cannot skip it.
"""
from __future__ import annotations

import json

from app.lib.cartography.standards.catalog import (
    MD_PATH,
    JSON_PATH,
    generate_catalog_json,
    generate_catalog_markdown,
)
from app.lib.cartography.standards.packs import get_core_pack


def test_catalog_markdown_matches_registry():
    assert MD_PATH.read_text(encoding="utf-8") == generate_catalog_markdown()


def test_catalog_json_matches_registry():
    committed = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert committed == generate_catalog_json()


def test_catalog_lists_every_core_rule():
    data = generate_catalog_json()
    pack = next(p for p in data["packs"] if p["pack_id"] == "core")
    assert {r["rule_id"] for r in pack["rules"]} == {
        r.rule_id for r in get_core_pack().rules
    }
    markdown = generate_catalog_markdown()
    for rule_id in (r.rule_id for r in get_core_pack().rules):
        assert rule_id in markdown
