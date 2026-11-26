"""Unit tests for PiRpcClient._read_stderr diagnostic-line elevation (v1.6.0).

vendor/packages/coding-agent/src/modes/diagnostics-stderr.ts (v1.6.0) writes
runtime diagnostics to stderr as ``Error: {message}`` / ``Warning: {message}``
after RPC-mode rebind. The host must surface those lines at WARNING (model
fallback / settings alerts) while ordinary stderr noise stays DEBUG.

Tests the pure predicate (``is_stderr_diagnostic_line``) and the reader loop
routing with a fake proc — no subprocesses.
"""
import logging
from unittest.mock import MagicMock

import pytest

from app.services.chat.pi_rpc_client import (
    PiRpcClient,
    is_stderr_diagnostic_line,
)


class _FakeStderr:
    """readline() sequence yielding pre-encoded lines, then EOF (b"")."""

    def __init__(self, lines: list[str]):
        self._lines = [line.encode() + b"\n" for line in lines]

    def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


def _make_client(tmp_path) -> PiRpcClient:
    return PiRpcClient(pi_rpc_entry=tmp_path / "rpc-entry.js")


# ── pure predicate: vendor format matching ──────────────────────────────────

@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # vendor diagnostics-stderr.ts exact output
        ("Error: model fallback engaged (anthropic -> webgis/openai)", True),
        ("Warning: unknown setting 'pi.webgisMode' in settings.json", True),
        # chalk-wrapped (FORCE_COLOR) — ANSI stripped before matching
        ("\x1b[31mError: quota exhausted\x1b[39m", True),
        ("\x1b[33mWarning: stale extension cache\x1b[39m", True),
        # indented continuation / stack lines are NOT diagnostics
        ("    at writeDiagnosticsToStderr (rpc-mode.ts:321)", False),
        # bare info lines carry no prefix in the vendor format → DEBUG
        ("listening on rpc", False),
        # node-style crash output that doesn't match the diagnostic shape
        ("throw new Error('boom')", False),
        ("", False),
    ],
)
def test_is_stderr_diagnostic_line(line, expected):
    assert is_stderr_diagnostic_line(line) is expected


# ── reader loop routing ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_stderr_routes_diagnostics_to_warning(tmp_path, caplog):
    """Diagnostic lines → WARNING; noise stays DEBUG; EOF exits cleanly."""
    proc = MagicMock()
    proc.stderr = _FakeStderr([
        "Error: model fallback engaged (anthropic -> webgis/openai)",
        "listening on rpc",
        "Warning: unknown setting 'pi.webgisMode'",
    ])
    client = _make_client(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="app.services.chat.pi_rpc_client"):
        await client._read_stderr(proc)
    levels = {r.getMessage(): r.levelno for r in caplog.records if "PiStderr" in r.getMessage()}
    fallback_msg = next(m for m in levels if "model fallback" in m)
    noise_msg = next(m for m in levels if "listening on rpc" in m)
    settings_msg = next(m for m in levels if "unknown setting" in m)
    assert levels[fallback_msg] == logging.WARNING
    assert levels[settings_msg] == logging.WARNING
    assert levels[noise_msg] == logging.DEBUG


@pytest.mark.asyncio
async def test_read_stderr_diagnostic_payload_is_bounded(tmp_path, caplog):
    """WARNING 载荷有界（~300 字符）—— 超长诊断不给日志面塞无界负载。"""
    proc = MagicMock()
    proc.stderr = _FakeStderr(["Error: " + "D" * 1000])
    client = _make_client(tmp_path)
    with caplog.at_level(logging.WARNING, logger="app.services.chat.pi_rpc_client"):
        await client._read_stderr(proc)
    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_msgs, "diagnostic line must surface at WARNING"
    payload = warning_msgs[0]
    # 300 上界含 ``Error: `` 前缀（7 字符）→ 293 个 D 存活
    assert "D" * 293 in payload
    assert "D" * 294 not in payload
