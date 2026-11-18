"""#2215: _FireProjection.project must emit the setTitle fire projection —
it read the nonexistent ``self.set_title`` and every title SSE event was
silently dropped (AttributeError swallowed upstream)."""
from __future__ import annotations

import time

import pytest

from app.services.chat.pi_runtime_interaction import _FireProjection
from app.services.chat.pi_ui_protocol import parse_ui_request


def _set_title_req(title: str):
    return parse_ui_request({
        "type": "extension_ui_request",
        "id": "ui-1",
        "method": "setTitle",
        "title": title,
    })


def test_set_title_projection_returns_and_updates_snapshot():
    proj = _FireProjection()
    req = _set_title_req("新地图分析")
    out = proj.project(req)
    assert out == {"kind": "title", "title": "新地图分析"}
    assert proj.snapshot()["title"] == "新地图分析"


def test_set_title_projection_survives_repeated_fire():
    proj = _FireProjection()
    first = proj.project(_set_title_req("标题 A"))
    assert first == {"kind": "title", "title": "标题 A"}
    # Within the 1s merge window the second fire is coalesced (dropped),
    # same contract as the other fire-and-forget methods.
    second = proj.project(_set_title_req("标题 B"))
    assert second is None
    assert proj.snapshot()["title"] == "标题 A"


def test_set_title_projection_after_window_emits_latest():
    proj = _FireProjection()
    proj.project(_set_title_req("标题 A"))
    proj._last_emit["setTitle"] = time.monotonic() - 10.0
    out = proj.project(_set_title_req("标题 B"))
    assert out == {"kind": "title", "title": "标题 B"}
