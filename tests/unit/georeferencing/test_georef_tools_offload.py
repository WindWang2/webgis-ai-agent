"""Regression: webgis_georef_plan must not run sync plan service work on the loop.

``service.create_plan`` / ``service.revise_draft`` persist drafts and solve the
affine fit synchronously. Called directly from the async tool they block the
event loop; they must be offloaded via ``asyncio.to_thread`` like the other
service calls in ``app/tools/georeferencing_tools.py``.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import app.tools.georeferencing_tools as georef_tools
from app.tools.registry import ToolRegistry


def _plan_stub(**overrides):
    base = {
        "plan_id": "gplan_probe",
        "revision": 2,
        "source_id": "src_probe",
        "target_crs": "EPSG:3857",
        "resampling": "bilinear",
        "gcps": [],
        "preview_stale": True,
        "fit_metrics": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_georef_plan_create_offloads_create_plan(monkeypatch):
    reg = ToolRegistry()
    georef_tools.register_georeferencing_tools(reg)
    loop_thread = threading.get_ident()
    seen = {}

    def fake_create_plan(**kwargs):
        seen["thread"] = threading.get_ident()
        seen["kwargs"] = kwargs
        return _plan_stub()

    monkeypatch.setattr(georef_tools.service, "create_plan", fake_create_plan)

    out = await reg.dispatch(
        "webgis_georef_plan",
        {"action": "create", "source_id": "src_probe"},
        session_id="sess_probe",
    )

    assert out["success"] is True
    assert seen["kwargs"]["session_id"] == "sess_probe"
    assert seen["thread"] != loop_thread


async def test_georef_plan_update_offloads_revise_draft(monkeypatch):
    reg = ToolRegistry()
    georef_tools.register_georeferencing_tools(reg)
    loop_thread = threading.get_ident()
    seen = {}

    def fake_revise_draft(**kwargs):
        seen["thread"] = threading.get_ident()
        seen["kwargs"] = kwargs
        return _plan_stub(revision=kwargs["expected_revision"] + 1)

    monkeypatch.setattr(georef_tools.service, "revise_draft", fake_revise_draft)

    out = await reg.dispatch(
        "webgis_georef_plan",
        {"action": "update", "plan_id": "gplan_probe", "expected_revision": 3},
        session_id="sess_probe",
    )

    assert out["success"] is True
    assert seen["kwargs"]["plan_id"] == "gplan_probe"
    assert seen["thread"] != loop_thread
