"""Regression tests for #584: generate_analysis_report must receive session_id
via the registry's trusted injection channel.

Pre-fix defect: the tool's parameter was named ``_session_id`` while the
registry injection probe matches the exact name ``session_id``
(``if "session_id" in sig.parameters``) — injection never happened, so the
agent path always failed with "无法确定当前会话 ID". The only working channel
was the LLM fabricating a ``session_id`` kwarg — the exact untrusted input the
session-injection design exists to block: a cross-session read face that could
read and download any other session's conversation messages as a report.

The fix: the parameter is named ``session_id`` (kept out of the LLM schema by
the explicit ``parameters=`` registration), the registry injects the trusted
id before execution (overwriting any LLM-supplied value), and the tool no
longer reads kwargs — forging is closed.
"""
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.db_model import Conversation
from app.tools.registry import ToolRegistry
from app.tools.report import register_report_tools


def _msg():
    return SimpleNamespace(
        role="user", content="你好", tool_calls=None, tool_result=None
    )


def _install_tool(monkeypatch, tmp_path, conversations):
    """Register generate_analysis_report with all externals faked so the tool
    can run to completion without DB / WeasyPrint / Redis."""
    from app.tools import report as tool_mod

    reg = ToolRegistry()
    register_report_tools(reg)

    report_rows: dict = {}

    class _FakeSyncDB:
        def get(self, model, pk):
            if model is Conversation:
                return conversations.get(pk)
            if model is tool_mod.Report:
                return report_rows.get(pk)
            return None

        def query(self, model):
            q = MagicMock()
            q.filter.return_value.order_by.return_value.all.return_value = [_msg()]
            return q

        def add(self, row):
            report_rows[row.id] = row

        def commit(self):
            pass

    @contextmanager
    def fake_db_session():
        yield _FakeSyncDB()

    monkeypatch.setattr(tool_mod, "db_session", fake_db_session)
    monkeypatch.setattr(tool_mod, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        tool_mod, "mapspec_store", MagicMock(get_mapspec=AsyncMock(return_value=None))
    )

    async def fake_render(**kwargs):
        os.makedirs(os.path.dirname(kwargs["output_path"]), exist_ok=True)
        with open(kwargs["output_path"], "w", encoding="utf-8") as f:
            f.write("# report\n")
        return True

    monkeypatch.setattr(tool_mod.spatial_report_engine, "generate_report", fake_render)
    return reg


@pytest.mark.asyncio
async def test_dispatch_injects_session_id_and_generates_report(monkeypatch, tmp_path):
    """registry.dispatch with session_id must reach the tool — the report is
    generated for that session. Pre-fix: the param-name mismatch made this
    return "无法确定当前会话 ID" every time."""
    conversations = {"sess-trust-a": Conversation(id="sess-trust-a", title="t")}
    reg = _install_tool(monkeypatch, tmp_path, conversations)

    result = await reg.dispatch(
        "generate_analysis_report", {"format": "markdown"}, session_id="sess-trust-a"
    )
    assert result.get("type") == "report_generated", result


@pytest.mark.asyncio
async def test_llm_forged_session_id_is_overridden(monkeypatch, tmp_path):
    """Even if the LLM smuggles a session_id argument in its tool call, the
    registry's trusted injection overwrites it — the tool runs against the
    authorized session only (no cross-session read)."""
    conversations = {"sess-trust-b": Conversation(id="sess-trust-b", title="t")}
    reg = _install_tool(monkeypatch, tmp_path, conversations)

    # Only sess-trust-b exists. If the forged id were honored, the tool would
    # read sess-evil's messages and fabricate that session's report file.
    result = await reg.dispatch(
        "generate_analysis_report",
        {"format": "markdown", "session_id": "sess-evil"},
        session_id="sess-trust-b",
    )
    assert result.get("type") == "report_generated", (
        f"trusted session must win over the forged id, got {result}"
    )


@pytest.mark.asyncio
async def test_dispatch_without_session_id_errors_honestly(monkeypatch, tmp_path):
    """Without a session the tool reports the missing context honestly instead
    of guessing."""
    conversations = {"sess-trust-c": Conversation(id="sess-trust-c", title="t")}
    reg = _install_tool(monkeypatch, tmp_path, conversations)

    result = await reg.dispatch("generate_analysis_report", {"format": "markdown"})
    assert isinstance(result, dict) and "error" in result
    assert "会话" in result["error"]