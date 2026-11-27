"""P2 修复回归：app/tools/print_layout_tools.py（review-swarm P2-00）。

- export 声明 expected_revision 却从不核验：调用方以为有 CAS 基线保护，
  实际按当前修订导出。提交前按 service 同一事实源核验，stale → SUPERSEDED。
- ``_bounded`` 逐键补回都可 <2000 字节，多键同时偏大时回执可远超 6000
  字节上限；补回时必须把累计体积压回 limit。
"""
from __future__ import annotations

import json

import pytest

from app.tools.registry import ToolRegistry


@pytest.fixture()
def export_tool():
    from app.tools.print_layout_tools import register_print_layout_tools

    reg = ToolRegistry()
    register_print_layout_tools(reg)
    return reg._tools["webgis_print_layout_export"]


async def test_export_expected_revision_mismatch_is_superseded(
        export_tool, monkeypatch):
    from app.services.print_layout_authoring import service

    submitted = []

    async def _load_mapspec(session_id):
        return {"layout": {}}

    async def _spec_revision(session_id, mapspec):
        return 7

    async def _submit_export(*args, **kwargs):
        submitted.append((args, kwargs))
        return {"success": True, "status": "exported"}

    monkeypatch.setattr(service, "load_mapspec", _load_mapspec)
    monkeypatch.setattr(service, "_spec_revision", _spec_revision)
    monkeypatch.setattr(service, "submit_export", _submit_export)

    out = await export_tool(
        format="pdf", expected_revision=3, session_id="s-cas")

    assert out["success"] is False
    assert out["code"] == "SUPERSEDED"
    assert out["committed"] is False
    assert submitted == []


async def test_export_expected_revision_match_still_submits(
        export_tool, monkeypatch):
    from app.services.print_layout_authoring import service

    submitted = []

    async def _load_mapspec(session_id):
        return {"layout": {}}

    async def _spec_revision(session_id, mapspec):
        return 7

    async def _submit_export(*args, **kwargs):
        submitted.append((args, kwargs))
        return {"success": True, "status": "exported",
                "download_url": "/f/out.pdf"}

    monkeypatch.setattr(service, "load_mapspec", _load_mapspec)
    monkeypatch.setattr(service, "_spec_revision", _spec_revision)
    monkeypatch.setattr(service, "submit_export", _submit_export)

    out = await export_tool(
        format="pdf", expected_revision=7, session_id="s-cas")

    assert out["success"] is True
    assert submitted
    assert submitted[0][0][0] == "s-cas"


def test_bounded_receipt_stays_within_limit_with_many_midsized_keys():
    from app.tools.print_layout_tools import _bounded

    payload = {
        "success": False, "status": "blocked", "code": "BLOCKED",
        "message": "blocked",
        "receipt": "r" * 1800,
        "changes": ["c" * 90] * 18,
        "issues": [{"code": "I", "message": "m" * 100}] * 12,
        "errors": ["e" * 90] * 18,
    }

    out = _bounded(payload)

    assert out["truncated"] is True
    size = len(json.dumps(out, ensure_ascii=False, default=str).encode("utf-8"))
    assert size <= 6000
