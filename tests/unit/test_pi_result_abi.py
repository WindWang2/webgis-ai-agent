"""Pi 结果 ABI v1 envelope 测试（Goal 04, ADR-0224）。

钉住：
- ref-first：geojson/image 只带 ref 串，绝不内联大对象；
- status/refs/error class 在预算内永不丢；预算收缩顺序 warnings→evidence→summary；
- 加性：details 原有字段保留；非 dict details 包 raw 不丢内容；
- kill switch：PI_RESULT_ABI=0 → details 原样。
"""
import json

import pytest

from app.services.chat.pi_result_abi import (
    RESULT_ABI_VERSION,
    attach_result_abi,
    build_result_envelope,
)


class _Result:
    def __init__(self, **kw):
        self.status = kw.get("status", "ok")
        self.llm_payload = kw.get("llm_payload", "done")
        self.slim_event = {}
        self.geojson_ref = kw.get("geojson_ref")
        self.raw_result = kw.get("raw_result", {})
        self.error_msg = kw.get("error_msg")
        self.map_actions = kw.get("map_actions", [])
        self.background_job_ids = kw.get("background_job_ids", [])
        self.capability_evidence = kw.get("capability_evidence")
        self.action_evidence = kw.get("action_evidence")


@pytest.fixture(autouse=True)
def abi_on(monkeypatch):
    monkeypatch.delenv("PI_RESULT_ABI", raising=False)


def test_ok_result_envelope_shape():
    result = _Result(
        geojson_ref="ref://geo/abc",
        raw_result={"result_ref": "ref://r/1", "imageRef": "ref://img/2"},
        map_actions=[{"action_id": "ma-1"}, {"action_id": "ma-2"}],
        background_job_ids=["job-1"],
    )
    env = build_result_envelope(result)
    assert env["abi"] == RESULT_ABI_VERSION
    assert env["status"] == "ok"
    assert env["refs"] == {"geojson": "ref://geo/abc", "result": "ref://r/1", "image": "ref://img/2"}
    assert env["map_action_ids"] == ["ma-1", "ma-2"]
    assert env["background_job_ids"] == ["job-1"]


def test_repeated_result_warns():
    env = build_result_envelope(_Result(status="repeated"))
    assert env["status"] == "repeated"
    assert any("deduplicated" in w for w in env["warnings"])


def test_error_result_class_mapping():
    env = build_result_envelope(_Result(status="error", error_msg="upstream timed out"))
    assert env["error"]["class"] == "timeout"
    env2 = build_result_envelope(_Result(
        status="error", raw_result={"code": "CAPABILITY_INELIGIBLE"},
    ))
    assert env2["error"]["class"] == "capability_ineligible"
    assert env2["error"]["code"] == "CAPABILITY_INELIGIBLE"


def test_cancelled_envelope():
    env = build_result_envelope(None, cancelled=True)
    assert env["status"] == "cancelled"
    assert env["error"]["code"] == "OPERATION_CANCELLED"


def test_envelope_never_inlines_large_payloads():
    big_raw = {"geojson": {"type": "FeatureCollection", "features": [{"i": i} for i in range(5000)]}}
    result = _Result(raw_result=big_raw, geojson_ref="ref://geo/x")
    env = build_result_envelope(result)
    blob = json.dumps(env)
    assert "FeatureCollection" not in blob
    assert "features" not in blob
    assert env["refs"]["geojson"] == "ref://geo/x"


def test_budget_shrinks_warnings_before_status_refs():
    huge = "x" * 5000
    result = _Result(llm_payload=huge)
    env = build_result_envelope(
        result, extra_warnings=[f"warning-{i}-{'y' * 200}" for i in range(20)],
    )
    blob = json.dumps(env, ensure_ascii=False).encode("utf-8")
    assert len(blob) <= 4096
    assert env["status"] == "ok"  # status 永不因预算丢失


def test_attach_is_additive_and_wraps_nondict():
    result = _Result(status="ok", geojson_ref="ref://geo/z")
    details = {"existing": "kept", "summary": "s"}
    out = attach_result_abi(details, result)
    assert out["existing"] == "kept"
    assert out["resultAbi"]["status"] == "ok"
    assert out["resultAbi"]["refs"] == {"geojson": "ref://geo/z"}

    out2 = attach_result_abi("plain-string-details", result)
    assert out2["raw"] == "plain-string-details"
    assert out2["resultAbi"]["status"] == "ok"


def test_attach_kill_switch_returns_unchanged(monkeypatch):
    monkeypatch.setenv("PI_RESULT_ABI", "0")
    result = _Result(status="ok")
    details = {"existing": 1}
    assert attach_result_abi(details, result) is details


def test_attach_failure_keeps_details():
    class _Broken:
        status = "ok"
        llm_payload = None
        # raw_result raises on attribute access
        @property
        def raw_result(self):
            raise RuntimeError("boom")

        geojson_ref = None
        map_actions = []
        background_job_ids = []
        capability_evidence = None
        action_evidence = None
        error_msg = None

    details = {"keep": True}
    out = attach_result_abi(details, _Broken())
    assert out == {"keep": True} or out.get("keep") is True
