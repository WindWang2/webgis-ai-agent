"""SpatialMemory 契约 schema 漂移守护。

`docs/dev/spatial-memory-contracts/gis-spatial-memory.schema.json` 必须与
`contract.SpatialMemoryRecord.to_bounded_dict` 的两个导出形状一致：
- 模型面（include_audit=False）：required 集合 = 模型面键集；
- 审计面（include_audit=True）：properties 全集 = 审计面键集。
改契约忘改 schema（或反过来）→ 本测试红。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "docs/dev/spatial-memory-contracts/gis-spatial-memory.schema.json"
)


def _load_schema() -> dict:
    return json.loads(_SCHEMA.read_text(encoding="utf-8"))


def _model_face_keys() -> set:
    from app.services.gis_memory.contract import SpatialMemoryRecord

    rec = SpatialMemoryRecord(
        id="m", kind="resolved_place", scope="session", scope_id="s",
        org_id="o", subject="x", value={}, confidence=0.9,
    )
    return set(rec.to_bounded_dict(include_audit=False).keys())


def _audit_face_keys() -> set:
    from app.services.gis_memory.contract import SpatialMemoryRecord

    rec = SpatialMemoryRecord(
        id="m", kind="resolved_place", scope="session", scope_id="s",
        org_id="o", subject="x", value={}, confidence=0.9,
    )
    return set(rec.to_bounded_dict(include_audit=True).keys())


def test_model_face_matches_required():
    schema = _load_schema()
    assert set(schema["required"]) == _model_face_keys()


def test_audit_face_matches_properties():
    schema = _load_schema()
    props = set(schema["properties"].keys())
    assert props == _audit_face_keys(), (
        "schema properties 与 contract 导出面漂移："
        f"schema-only={sorted(props - _audit_face_keys())} "
        f"contract-only={sorted(_audit_face_keys() - props)}"
    )


def test_kind_enum_matches_contract():
    schema = _load_schema()
    from app.services.gis_memory.contract import SPATIAL_MEMORY_KINDS

    assert set(schema["properties"]["kind"]["enum"]) == set(SPATIAL_MEMORY_KINDS)


def test_bounded_dict_is_json_round_trippable():
    from app.services.gis_memory.contract import SpatialMemoryRecord

    rec = SpatialMemoryRecord(
        id="m", kind="resolved_place", scope="session", scope_id="s",
        org_id="o", subject="成都市", value={"name": "成都市"},
        refs=["local:admin:city:成都市"], confidence=0.9,
    )
    blob = json.dumps(rec.to_bounded_dict(include_audit=True), ensure_ascii=False)
    assert "成都市" in blob
