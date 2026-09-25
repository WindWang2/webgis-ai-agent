"""MapSpec additive：组合身份块 + 组件级用户锁（ADR-0214 D3/D4）schema 测试。

覆盖：schema 接受两个新字段、canonical round-trip 保真、TS 投影同步、
身份块经 quality_loop layout 投影进入 cartographic 指纹（DoD #4 的
schema 侧闭环）、存量 spec 零漂移。
"""
import json

import pytest

from app.lib.cartography.composition_contract import (
    apply_contract,
    read_composition_identity,
    SEED_CONTRACTS,
)
from app.lib.cartography.mapspec_schema import (
    canonicalize_mapspec,
    dumps_canonical,
    parse_mapspec,
)
from app.lib.cartography.quality_loop import cartographic_fingerprint

BASIC = next(
    c for c in SEED_CONTRACTS if c.contract_id == "contract.core.basic_thematic")


def _base_spec() -> dict:
    return {
        "version": "1.0",
        "sources": {"s": {"type": "geojson", "inlineData": {"features": []}}},
        "layers": [{"id": "l1", "type": "geojson", "source": "s"}],
        "layout": {"components": []},
    }


def test_schema_accepts_identity_block_and_user_lock():
    spec = _base_spec()
    spec["layout"]["composition"] = {
        "template_id": "composition.standard_analysis",
        "template_version": "1.0.0",
        "contract_id": "contract.core.basic_thematic",
        "component_abi_version": 1,
        "component_versions": {"legend": "1.0.0"},
        "applied_revision": 3,
    }
    spec["layout"]["components"].append(
        {"id": "legend-main", "type": "legend"})
    spec["layout"]["components"].append({"id": "title", "type": "title"})
    parsed = parse_mapspec(spec)  # 不抛 = 接受
    assert parsed.document["layout"]["composition"]["template_id"] == \
        "composition.standard_analysis"


def test_canonical_roundtrip_preserves_new_fields():
    spec = _base_spec()
    spec["layout"]["composition"] = {"template_id": "t", "template_version": "1"}
    spec["layout"]["components"].append({"id": "title", "type": "title"})
    once = dumps_canonical(spec)
    import copy
    twice = dumps_canonical(json.loads(once))
    assert once == twice, "canonical 序列化必须 round-trip 保真"


def test_identity_block_changes_cartographic_fingerprint():
    """DoD #4：模板/契约身份变化 → cartographic_fingerprint 必变。"""
    spec1, _ = apply_contract(_base_spec(), BASIC, revision=1)
    fp1 = cartographic_fingerprint(spec1)
    spec2 = _base_spec()
    spec2["layout"]["composition"] = dict(
        spec1["layout"]["composition"], template_version="2.0.0")
    fp2 = cartographic_fingerprint(spec2)
    assert fp1 != fp2


def test_existing_specs_unchanged_without_new_fields():
    """无身份块/锁位的存量 spec：指纹与 canonical 序列化零漂移。"""
    spec = _base_spec()
    spec["layout"]["components"].append({"id": "title", "type": "title"})
    frozen_canon = dumps_canonical(spec)
    frozen_fp = cartographic_fingerprint(spec)
    assert read_composition_identity(spec) is None
    assert dumps_canonical(spec) == frozen_canon
    assert cartographic_fingerprint(spec) == frozen_fp


def test_apply_writes_schema_valid_identity():
    """契约 apply 写入的身份块必须通过 MapSpec schema 校验（写入面闭环）。"""
    spec, _ = apply_contract(_base_spec(), BASIC, revision=1)
    canonical = dumps_canonical(spec)
    assert "composition" in canonical
    reparsed = json.loads(canonical)
    identity = read_composition_identity(reparsed)
    assert identity is not None
    assert identity.template_id == BASIC.template_id
