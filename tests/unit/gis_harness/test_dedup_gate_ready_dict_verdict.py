"""R2-P1（qc-loop round 4）回归锁：final_gate 幂等门必须识别 dict 形状裁决。

历史缺陷：``_dedup_gate_blocks`` 裸 ``str(stored.get("product_verdict"))``
比对 READY 词表，而 ``map_product_block`` 持久化的是
``derive_product_verdict`` 的完整 dict —— ``str(dict)`` 恒不在词表内，
READY 会话每轮都全量重跑终验（同文件 ``_product_verdict_token`` 的
docstring 即为此坑而写，此处漏用）。
"""
from app.services.gis_harness.completion.pipeline import (
    _dedup_gate_blocks,
    _rows_fingerprint,
)


def _stored(verdict) -> dict:
    return {
        "status": "complete",
        "product_verdict": verdict,
        "checked_revision": 3,
        "render_observation_seq": 2,
        "rows_fingerprint": _rows_fingerprint({})[:2048],
    }


def test_dict_verdict_ready_sessions_skip_finalization():
    assert _dedup_gate_blocks(
        _stored({"verdict": "READY"}), {}, 3, 2, final_gate=True) is True
    assert _dedup_gate_blocks(
        _stored({"verdict": "READY_WITH_WARNINGS"}), {}, 3, 2,
        final_gate=True) is True


def test_dict_verdict_non_ready_still_reverifies():
    assert _dedup_gate_blocks(
        _stored({"verdict": "NEEDS_REPAIR"}), {}, 3, 2, final_gate=True) is False


def test_string_verdict_compat():
    assert _dedup_gate_blocks(
        _stored("READY"), {}, 3, 2, final_gate=True) is True
