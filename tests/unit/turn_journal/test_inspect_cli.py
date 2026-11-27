"""CLI --stats-limit 回归（review P3）：--stats 汇总不得静默截断。

>50 turn 的 session 此前固定只显示 50 条，且表头 ``turn summaries (50)``
易被误读成全量；现在上限可由 ``--stats-limit`` 调大，并显式打进表头。
"""
from __future__ import annotations

import asyncio
import sys

import scripts.turn_journal_inspect as cli
from app.services.turn_journal.contracts import TurnEventRecord

_SESSION = "s-inspect-cli"


def _seed_turns(ledger, turns: int) -> None:
    for i in range(turns):
        turn_id = f"t{i:03d}"
        asyncio.run(ledger.append(TurnEventRecord(
            session_id=_SESSION, kind="tool_started", turn_id=turn_id,
            event_id=f"{turn_id}:start", causal_id=f"c{i}",
            detail={"tool": "query"})))
        asyncio.run(ledger.append(TurnEventRecord(
            session_id=_SESSION, kind="turn_ended", turn_id=turn_id,
            event_id=f"{turn_id}:end", detail={"status": "completed"})))


def test_stats_limit_is_explicit_and_raisable(ledger, monkeypatch, capsys):
    monkeypatch.setattr(cli, "TurnEventLedger", lambda: ledger)
    _seed_turns(ledger, 60)

    monkeypatch.setattr(sys, "argv",
                        ["turn_journal_inspect.py", _SESSION, "--stats"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    # 生效上限必须显式出现在表头，不能被误读成全量（review P3）。
    assert "turn summaries (50, limit=50):" in out
    # 截断保留最近的 turn：最老 10 个 turn 不在表内。
    assert "t059: events=2" in out
    assert "t009: events=2" not in out

    monkeypatch.setattr(
        sys, "argv",
        ["turn_journal_inspect.py", _SESSION, "--stats", "--stats-limit", "60"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "turn summaries (60, limit=60):" in out
    assert "t009: events=2" in out
