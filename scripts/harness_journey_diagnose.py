#!/usr/bin/env python3
"""Render a bounded, session-scoped diagnostic from a ReplayTrace JSON file."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.lib.harness.replay.journey import render_journey_diagnostic

_MAX_INPUT_BYTES = 2 * 1024 * 1024
_MODES = (
    "offline_replay", "live_dispatch_stub_llm", "browser_renderer",
    "live_services_opt_in",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="ReplayTrace JSON path (max 2 MiB)")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--mode", choices=_MODES, default="offline_replay")
    parser.add_argument("--max-bytes", type=int, default=32 * 1024)
    parser.add_argument("--output", default="-", help="Output JSON path, or - for stdout")
    return parser


def _error(reason_code: str) -> str:
    return json.dumps({
        "schema_version": "gis_journey_diagnostic.v1",
        "overall": "blocked",
        "reason_code": reason_code,
        "truncated": False,
    }, separators=(",", ":"), sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        source = Path(args.input)
        if source.stat().st_size > _MAX_INPUT_BYTES:
            raise ValueError("input too large")
        trace = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(trace, dict):
            raise TypeError("trace root must be an object")
        payload = render_journey_diagnostic(
            trace,
            session_id=args.session_id,
            turn_id=args.turn_id,
            mode=args.mode,
            max_bytes=args.max_bytes,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print(_error("invalid_input"))
        return 2

    if args.output == "-":
        print(payload)
    else:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8")
    report = json.loads(payload)
    return 2 if report.get("reason_code") == "permission_denied" else 0


if __name__ == "__main__":
    raise SystemExit(main())
