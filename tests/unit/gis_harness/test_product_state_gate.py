"""F14 — finalizer 幂等门第四键：product_state_fingerprint 契约。

ADR-0211 follow-up：READY 后新 export receipt 落章、或 semantic-only 产品
编辑（改 product_spec.digest，不推 cartographic revision）必须打破旧
READY gate —— 此前三把钥匙（checked_revision / render_observation_seq /
rows_fingerprint）对产品语义侧状态全部失明。

失败方向保守不变：第四键只会**打开**门（多重验），绝不把未验证状态挡在
门外；旧块无键 → 一次性重验自愈补齐。
"""
import pytest

from app.services.gis_harness.completion.pipeline import _dedup_gate_blocks
from app.services.gis_harness.workflow_instance import (
    product_state_fingerprint,
    product_state_payload,
    rows_fingerprint,
)


def _ready_stored(chapter=None, **overrides):
    stored = {
        "status": "complete",
        "checked_revision": 7,
        "render_observation_seq": 3,
        "rows_fingerprint": rows_fingerprint(chapter or {}),
        "product_state_fingerprint": product_state_fingerprint(chapter or {}),
    }
    stored.update(overrides)
    return stored


# ── 指纹确定性 / 敏感性 ─────────────────────────────────────────────────


def test_fingerprint_deterministic_same_input():
    ch = {"export_receipts": [{"format": "pdf", "revision": "3",
                               "created_at": 1.0, "filename": "a.pdf"}]}
    assert product_state_fingerprint(ch) == product_state_fingerprint(dict(ch))


def test_fingerprint_ignores_receipt_list_order():
    ch1 = {"export_receipts": [
        {"format": "pdf", "revision": "1", "created_at": 1.0, "filename": "a.pdf"},
        {"format": "png", "revision": "2", "created_at": 2.0, "filename": "b.png"},
    ]}
    ch2 = {"export_receipts": list(reversed(ch1["export_receipts"]))}
    assert product_state_fingerprint(ch1) == product_state_fingerprint(ch2)


def test_new_receipt_breaks_fingerprint():
    ch1 = {"export_receipts": []}
    ch2 = {"export_receipts": [
        {"format": "pdf", "revision": "7", "created_at": 99.0,
         "filename": "map_export_1_x.pdf"}]}
    assert product_state_fingerprint(ch1) != product_state_fingerprint(ch2)


def test_receipt_field_edit_breaks_fingerprint():
    base = {"format": "pdf", "revision": "1", "created_at": 1.0,
            "filename": "a.pdf"}
    ch1 = {"export_receipts": [dict(base)]}
    ch2 = {"export_receipts": [dict(base, revision="2")]}
    assert product_state_fingerprint(ch1) != product_state_fingerprint(ch2)


def test_product_spec_digest_edit_breaks_fingerprint():
    ch1 = {"product_spec": {"digest": "aaaa", "rows": [1, 2]}}
    ch2 = {"product_spec": {"digest": "bbbb", "rows": [1, 2]}}
    assert product_state_fingerprint(ch1) != product_state_fingerprint(ch2)
    # digest 缺席（旧章节形状）= 空串，不炸
    assert product_state_fingerprint({"product_spec": {}}) == (
        product_state_fingerprint({}))


def test_fingerprint_blind_to_non_product_fields():
    """行表/警告等非产品语义字段不影响第四键（由既有三把钥匙覆盖）。"""
    ch1 = {"data_requirements": [{"capability": "buffer", "status": "done"}],
           "methodology_warnings": [{"code": "x"}]}
    ch2 = {"data_requirements": [{"capability": "buffer", "status": "failed"}],
           "methodology_warnings": []}
    assert product_state_fingerprint(ch1) == product_state_fingerprint(ch2)
    assert rows_fingerprint(ch1) != rows_fingerprint(ch2)


def test_payload_bounded_and_serializable():
    ch = {"export_receipts": [
        {"format": f"f{i}", "revision": str(i), "created_at": float(i),
         "filename": f"x{i}.png", "extra": "dropped"}
        for i in range(20)
    ]}
    payload = product_state_payload(ch)
    import json

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert len(canonical) < 8192  # 20 条回执的规范化载荷有界
    assert all("extra" not in r for r in payload["export_receipts"])


# ── 门行为 ───────────────────────────────────────────────────────────────


def test_gate_blocks_when_product_state_unchanged():
    ch = {"export_receipts": [{"format": "pdf", "revision": "3",
                               "created_at": 1.0, "filename": "a.pdf"}]}
    assert _dedup_gate_blocks(_ready_stored(ch), ch, 7, 3) is True


def test_gate_opens_on_new_export_receipt():
    """READY 后新 receipt 落章 → 门打开（重验），不被旧 READY 幂等吞掉。"""
    ch_old = {"export_receipts": []}
    stored = _ready_stored(ch_old)
    ch_new = {"export_receipts": [
        {"format": "pdf", "revision": "7", "created_at": 2.0,
         "filename": "map_export_9_x.pdf"}]}
    assert _dedup_gate_blocks(stored, ch_new, 7, 3) is False


def test_gate_opens_on_semantic_only_product_edit():
    """semantic-only 产品编辑（digest 变、revision/seq/rows 不变）→ 门打开。"""
    ch_old = {"product_spec": {"digest": "aaa"}}
    stored = _ready_stored(ch_old)
    ch_new = {"product_spec": {"digest": "bbb"}}
    assert _dedup_gate_blocks(stored, ch_new, 7, 3) is False


def test_gate_opens_once_for_legacy_block_without_key():
    """旧块无 product_state_fingerprint 键 → 首次打开重验（自愈补齐）。"""
    ch = {}
    stored = _ready_stored(ch)
    del stored["product_state_fingerprint"]
    assert _dedup_gate_blocks(stored, ch, 7, 3) is False
    # 补齐后恢复幂等
    stored2 = _ready_stored(ch)
    assert _dedup_gate_blocks(stored2, ch, 7, 3) is True


def test_gate_still_opens_on_revision_or_rows_change():
    """第四键是合取项 —— 既有三把钥匙的打破语义不变。"""
    ch = {"data_requirements": [{"capability": "buffer", "status": "done"}]}
    stored = _ready_stored(ch)
    assert _dedup_gate_blocks(stored, ch, 8, 3) is False  # revision 前进
    ch2 = {"data_requirements": [{"capability": "buffer", "status": "failed"}]}
    assert _dedup_gate_blocks(stored, ch2, 7, 3) is False  # 行漂移


def test_gate_non_terminal_and_force_semantics_unchanged():
    ch = {}
    assert _dedup_gate_blocks({"status": "pending"}, ch, 1, 1) is False
    assert _dedup_gate_blocks(_ready_stored(ch), ch, 1, 1, force=True) is False
    assert _dedup_gate_blocks(None, ch, 1, 1) is False
