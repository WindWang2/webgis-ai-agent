"""Budget gate CLI (quality-e2e-v9, ADR-0146 P5).

Runs the four first-wave budget measurements, compares against
perf/budgets.json with a noise tolerance, and fails (exit 1) on any breach —
the CI gate. Budget changes are explicit file edits (PR-reviewable); there is
no env knob to relax the gate.

Usage:
  python scripts/perf/run_budget.py [--iterations N] [--budgets PATH]
                                    [--report OUT.json] [--only ID]

Self-proof (acceptance: "故意超线的合成用例能红"):
  python scripts/perf/run_budget.py --self-test   # exits 0 iff an injected
  over-budget measurement makes the gate RED.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.perf.measurements import MEASUREMENTS, SkipMeasurement  # noqa: E402

DEFAULT_BUDGETS = REPO / "perf" / "budgets.json"


def load_budgets(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "quality-e2e-v9/ADR-0146 budget gate":
        raise SystemExit(f"unknown budgets schema in {path}")
    return data


def evaluate(budget_entry: Dict[str, Any], measured_ms: float,
             tolerance_pct: float) -> Dict[str, Any]:
    target = float(budget_entry["target"])
    ceiling = target * (1 + tolerance_pct / 100)
    return {
        "id": budget_entry["id"],
        "target_ms": target,
        "tolerance_pct": tolerance_pct,
        "ceiling_ms": round(ceiling, 3),
        "measured_ms": round(measured_ms, 3),
        "ok": measured_ms <= ceiling,
    }


def run_gate(iterations: int | None = None, budgets_path: Path = DEFAULT_BUDGETS,
             only: str | None = None, overrides: Dict[str, float] | None = None,
             skip_missing: bool = False) -> tuple[List[Dict[str, Any]], bool]:
    cfg = load_budgets(budgets_path)
    tolerance = float(cfg.get("tolerance_pct", 10))
    iters = iterations or int(cfg.get("iterations_ci", 20))
    results: List[Dict[str, Any]] = []
    all_ok = True
    for entry in cfg["budgets"]:
        bid = entry["id"]
        if only and bid != only:
            continue
        if overrides and bid in overrides:
            measured = overrides[bid]
        else:
            fn = MEASUREMENTS.get(bid)
            if fn is None:
                results.append({"id": bid, "ok": False,
                                "error": "no measurement registered"})
                all_ok = False
                continue
            start = time.perf_counter()
            try:
                measured = float(fn(iterations=iters))
            except SkipMeasurement as skip:
                results.append({"id": bid, "ok": True, "skipped": True,
                                "reason": str(skip)})
                continue
            except Exception as exc:  # noqa: BLE001 — measurement failure = red
                results.append({"id": bid, "ok": False,
                                "error": f"{type(exc).__name__}: {exc}"})
                all_ok = False
                continue
            _ = time.perf_counter() - start
        results.append(evaluate(entry, measured, tolerance))
        if not results[-1]["ok"]:
            all_ok = False
    return results, all_ok


def render_report(results: List[Dict[str, Any]], all_ok: bool) -> str:
    lines = ["budget gate: " + ("PASS" if all_ok else "FAIL")]
    for r in results:
        if r.get("skipped"):
            lines.append(f"  SKIP {r['id']}: {r['reason']}")
        elif "error" in r:
            lines.append(f"  ERROR {r['id']}: {r['error']}")
        else:
            head = "  ok  " if r["ok"] else "  RED  "
            lines.append(
                f"{head}{r['id']}: measured {r['measured_ms']}ms "
                f"vs target {r['target_ms']}ms (+{r['tolerance_pct']}% → ceiling "
                f"{r['ceiling_ms']}ms)")
    return "\n".join(lines)


def self_test(budgets_path: Path = DEFAULT_BUDGETS) -> int:
    """Inject a synthetic over-budget measurement; the gate must go RED."""
    cfg = load_budgets(budgets_path)
    first = cfg["budgets"][0]
    results, ok = run_gate(overrides={first["id"]: float(first["target"]) * 10},
                           budgets_path=budgets_path, only=first["id"])
    red = not ok and results and results[0]["ok"] is False
    print(render_report(results, ok))
    print("self-test:", "gate goes RED on synthetic breach — OK" if red
          else "gate did NOT go red — BROKEN")
    return 0 if red else 1


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.budgets)

    results, ok = run_gate(iterations=args.iterations, budgets_path=args.budgets,
                           only=args.only)
    report = render_report(results, ok)
    print(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(
            {"ok": ok, "results": results, "at": time.time()},
            indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
