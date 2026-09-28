#!/usr/bin/env python
"""Turn journal 只读诊断 CLI（H04 / ADR-0216）。

给 session（可选 turn）输出：因果树、未终局 turn、悬挂步骤恢复分类、
最后一致 mutation revision、账本体量。只读——本脚本绝不写账本、
绝不触发任何副作用重放（恢复建议是给人/agent 看的）。

用法::

    python scripts/turn_journal_inspect.py <session_id> [--turn-id T] \
        [--events] [--stats] [--json]

不走 HTTP/鉴权（运维面直连 DB）；沿用 SessionLocal（PG/SQLite 由配置决定）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.turn_journal.diagnostics import (  # noqa: E402
    build_causal_tree_sync,
    session_journal_report_sync,
)
from app.services.turn_journal.ledger import TurnEventLedger  # noqa: E402


def _print_tree(tree: Dict[str, Any]) -> None:
    for turn in tree.get("turns", []):
        settled = "settled" if turn.get("settled") else "UNSETTLED"
        if turn.get("compacted"):
            settled += "+compacted"
        status = turn.get("terminal_status") or "-"
        print(f"turn {turn['turn_id']}  [{settled} status={status}] "
              f"run={turn.get('run_id') or '-'} events={turn.get('event_count')}")
        for step in turn.get("steps", []):
            result = (step.get("result") or {}).get("kind") or "NO-RESULT"
            print(f"  step {step.get('step_id') or '-'} tool={step.get('tool') or '-'} "
                  f"call={step.get('causal_id')} -> {result}")
        for mut in turn.get("mutations", []):
            print(f"  mutation {mut.get('mutation_id')} rev={mut.get('revision')} "
                  f"call={mut.get('tool_call_id') or '-'}"
                  f"{' late' if mut.get('late') else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("--turn-id", default="", help="因果树只看该 turn")
    parser.add_argument("--events", action="store_true", help="附带原始事件流")
    parser.add_argument("--stats", action="store_true", help="附账本体量统计")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    ledger = TurnEventLedger()
    report: Dict[str, Any] = session_journal_report_sync(
        ledger, args.session_id, turn_id=args.turn_id)

    if args.json:
        if args.events:
            report["events"] = ledger._list_events_sync(  # noqa: SLF001
                args.session_id, turn_id=args.turn_id)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"session {args.session_id}"
          + (f" turn {args.turn_id}" if args.turn_id else ""))
    print(f"last_consistent_revision: "
          f"{report.get('last_consistent_revision')}")
    print(f"unsettled turns: {len(report.get('unsettled_turns') or [])}")
    for suggestion in report.get("recovery_suggestions") or []:
        print(f"  [{suggestion.get('classification', 'replan')}] "
              f"turn={suggestion.get('turn_id')} "
              f"step={suggestion.get('step_id') or '-'} "
              f"-> {suggestion.get('suggestion')} ({suggestion.get('reason')})")
    print("causal tree:")
    tree = report.get("causal_tree") or {}
    if args.turn_id and not tree.get("turns"):
        tree = build_causal_tree_sync(ledger, args.session_id,
                                      turn_id=args.turn_id)
    _print_tree(tree)
    if args.events:
        events = ledger._list_events_sync(  # noqa: SLF001
            args.session_id, turn_id=args.turn_id)
        print(f"events ({len(events)}):")
        for e in events:
            print(f"  #{e['id']} {e['occurred_at']} {e['kind']} "
                  f"turn={e['turn_id']} seq={e['seq']} "
                  f"causal={e['causal_id'] or '-'} {e['note'][:80]}")
    if args.stats:
        stats = ledger._turn_summaries_sync(args.session_id, limit=50)  # noqa: SLF001
        print(f"turn summaries ({len(stats)}):")
        for s in stats:
            print(f"  {s['turn_id']}: events={s['event_count']} "
                  f"terminal={s['terminal']} "
                  f"last_rev={s['last_mutation_revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
