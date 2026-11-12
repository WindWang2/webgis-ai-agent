"""G24 P0 域内防线测试：重复自然键 / 大整数 id / _wfid 链一致性。"""
from __future__ import annotations

import pytest

from app.services.feature_edit.contracts import FINDING_CODES
from app.services.feature_edit.identity import find_ambiguous_ids, resolve_feature_id
from app.services.feature_edit.plan import build_plan
from app.services.mvt import _chain_stable_id, _encode_value, _varint
from app.services.feature_pages import resolve_feature_stable_id


def test_finding_code_registered():
    assert "ambiguous_target" in FINDING_CODES


def test_find_ambiguous_ids_flags_duplicates_not_wfids():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": 1, "geometry": None, "properties": {"OBJECTID": 5}},
            {"type": "Feature", "id": 2, "geometry": None, "properties": {"OBJECTID": 5}},
            {"type": "Feature", "id": 3, "geometry": None, "properties": {"_wfid": "wf1-z", "OBJECTID": 9}},
        ],
    }
    amb = find_ambiguous_ids(fc, ["5", "wf1-z", "9"])
    # OBJECTID=5 重复 → 歧义；wf1-z 唯一 wfid → 不歧义
    assert amb == {"5": 2}


@pytest.mark.asyncio
async def test_duplicate_natural_key_edit_refused_typed():
    """A07：重复 id 编辑 → ambiguous_target（not committable），不 first-wins。"""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": 1,
                "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                "properties": {"OBJECTID": 5, "name": "twin-a"},
            },
            {
                "type": "Feature",
                "id": 2,
                "geometry": {"type": "Point", "coordinates": [116.1, 39.1]},
                "properties": {"OBJECTID": 5, "name": "twin-b"},
            },
        ],
    }
    envelope = {"geojson": fc}
    plan, findings = build_plan(
        ref_id="ref:dup-test-0001",
        payload=envelope,
        ops=[{"op": "delete_features"}],
        requested_ids=["5"],
        selection=None,
        dataset_fingerprint="fp-dup",
        content_revision=1,
    )
    codes = {f.get("code") for f in findings}
    assert "ambiguous_target" in codes, findings
    assert plan is None or not plan.get("committable", False)


def test_wfid_first_chain_unified():
    feat = {"type": "Feature", "id": 7, "properties": {"_wfid": "wf1-q", "OBJECTID": 7}}
    assert _chain_stable_id(feat) == "wf1-q"
    assert resolve_feature_stable_id(feat) == "wf1-q"
    # 物化前身份（feature_edit）保持顶层 id 优先 —— resolve_feature_ids 的
    # WFID 显式分支覆盖 wfid 寻址，语义不回归
    assert resolve_feature_id(feat) == "7"


def test_mvt_int_beyond_2p53_downgrades_to_string():
    """A06：2^53 ≤ |v| < 2^63 的整数以字符串下行（JS Number 丢精度防线）。"""
    big = 2**53 + 1  # 9007199254740993 —— JS Number 无法精确表示
    encoded = _encode_value(big)
    # string 值：field 1 (string) + 长度 + utf8 数字原文
    assert encoded[0] >> 3 == 1  # field 1, wire 2 (bytes)
    assert encoded[2:] == str(big).encode()

    small = 42
    enc_small = _encode_value(small)
    assert enc_small[0] >> 3 == 4  # int_value 字段

    neg_big = -(2**53)
    assert _encode_value(neg_big)[0] >> 3 == 1  # 负大数同样字符串化

    # 2^53 - 1 仍是合法 int64 下行（JS 可精确表示）
    assert _encode_value(2**53 - 1)[0] >> 3 == 4
