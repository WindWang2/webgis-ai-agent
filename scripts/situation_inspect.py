#!/usr/bin/env python
"""GIS Situation inspector（方向 2 S9，ADR-0180）。

用法：

    ./.venv/Scripts/python scripts/situation_inspect.py <session_id>          # 人类可读
    ./.venv/Scripts/python scripts/situation_inspect.py <session_id> --json   # 机器可读
    ./.venv/Scripts/python scripts/situation_inspect.py --dump-schema         # 契约导出

--json 输出 authoritative facts（value/status/source/revision）、stale、
omitted（有界裁剪证据）、conflicts（consistency 检查）、context projection
的 byte/预算度量。--dump-schema 把 GISSituation v1 的 JSON Schema 刷新到
docs/dev/situation-contracts/（契约测试守护漂移，scripts 是唯一改动口）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.services.gis_situation.compiler import compile_situation  # noqa: E402
from app.services.gis_situation.consistency import check_consistency  # noqa: E402
from app.services.gis_situation.contract import GISSituation  # noqa: E402
from app.services.gis_situation.diff import load_snapshot  # noqa: E402
from app.services.gis_situation.projection import (  # noqa: E402
    SITUATION_BLOCK_MAX_BYTES,
    render_situation_for_context,
)

OUT_DIR = REPO / "docs" / "dev" / "situation-contracts"


def _dump_schema() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    schema = GISSituation.model_json_schema()
    schema["$id"] = "https://webgis-ai-agent.local/schemas/situation-v1/gis-situation.schema.json"
    schema["x-situation-contract-version"] = 1
    path = OUT_DIR / "gis-situation.schema.json"
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


def _fact_row(ctx: str, name: str, fact) -> dict:
    return {
        "context": ctx,
        "fact": name,
        "status": fact.status,
        "source": fact.source,
        "value": fact.value if fact.status == "known" else None,
        "revision": fact.revision,
        "observed_at": fact.observed_at or None,
        "ref": fact.ref,
    }


async def _inspect(session_id: str, as_json: bool) -> int:

    situation = await compile_situation(session_id)
    previous = await load_snapshot(session_id)
    projection = render_situation_for_context(situation)
    conflicts = check_consistency(situation)

    facts = [
        _fact_row(ctx, name, fact)
        for ctx, name, fact in situation.iter_facts()
    ]
    stale = [f for f in facts if f["status"] == "stale"]
    unknown = [f for f in facts if f["status"] == "unknown"]
    report = {
        "session_id": session_id,
        "identity": situation.identity.model_dump(mode="json"),
        "facts": facts,
        "stale": stale,
        "unknown_count": len(unknown),
        "omitted": situation.evidence.omitted,
        "sources_ok": situation.evidence.sources_ok,
        "sources_unavailable": situation.evidence.sources_unavailable,
        "conflicts": [c.to_dict() for c in conflicts],
        "projection": {
            "byte_cost": projection.byte_len,
            "byte_cap": projection.byte_cap,
            "truncated": projection.truncated,
            "budget_constant": SITUATION_BLOCK_MAX_BYTES,
            "text": projection.text,
        },
        "previous_snapshot_revision": (
            previous.identity.revision.model_dump(mode="json")
            if previous is not None else None
        ),
    }
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    rev = situation.identity.revision
    print(f"session={session_id}")
    print(
        f"revision: spec_rev={rev.mutation_revision} "
        f"obs_seq={rev.observation_sequence} int_seq={rev.interaction_sequence}"
    )
    print(f"sources: ok={report['sources_ok']} unavailable={report['sources_unavailable']}")
    print(f"omitted channels: {report['omitted'] or 'none'}")
    print(f"facts: {len(facts)} total, known={len(facts) - len(stale) - len(unknown)}, "
          f"stale={len(stale)}, unknown={len(unknown)}")
    for row in facts:
        mark = {"known": "·", "stale": "!", "unknown": "?", "unavailable": "x"}[row["status"]]
        value = ""
        if row["value"] is not None:
            value = json.dumps(row["value"], ensure_ascii=False)
            if len(value) > 72:
                value = value[:71] + "…"
        print(f"  [{mark}] {row['context']}.{row['fact']} <- {row['source']} {value}")
    if report["conflicts"]:
        print("conflicts:")
        for c in report["conflicts"]:
            print(f"  [{c['severity']}] {c['code']}: {json.dumps(c['detail'], ensure_ascii=False)}")
    p = report["projection"]
    print(f"projection: {p['byte_cost']}/{p['byte_cap']} bytes truncated={p['truncated']}")
    print("-" * 60)
    print(p["text"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GIS Situation inspector")
    parser.add_argument("session_id", nargs="?", help="session to inspect")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dump-schema", action="store_true",
                        help="refresh the committed GISSituation JSON Schema")
    args = parser.parse_args()
    if args.dump_schema:
        return _dump_schema()
    if not args.session_id:
        parser.error("session_id required (or pass --dump-schema)")
    return asyncio.run(_inspect(args.session_id, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
