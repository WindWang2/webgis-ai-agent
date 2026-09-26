"""Composition Contract v1 契约测试（ADR-0214 D2/D3/D4）。

覆盖：seed 契约 fail-closed 校验、canonical 指纹、有界 diff、lock-aware
apply（幂等/保留用户编辑/锁零触碰/链接幂等）、身份块入指纹、锁纯函数。
"""
import copy

import pytest

from app.lib.cartography.composition_contract import (
    ContractApplyError,
    CompositionContractV1,
    ContractSlot,
    LOCK_REASON_USER_WINS,
    SEED_CONTRACTS,
    apply_contract,
    contract_fingerprint,
    diff_contracts,
    get_contract_registry,
    locked_component_ids_of,
    read_composition_identity,
    reset_contract_registry,
)
from app.lib.cartography.quality_loop import cartographic_fingerprint


@pytest.fixture()
def contracts():
    """独立注册表实例（含 seeds）：负例 register 不污染共享单例
    （单例污染会让其他文件的 pristine-validate 测试吃到脏状态）。"""
    from app.lib.cartography.composition_contract import ContractRegistry
    fresh = ContractRegistry()
    fresh.load_builtins()
    return fresh


@pytest.fixture()
def base_contract():
    return next(
        c for c in SEED_CONTRACTS
        if c.contract_id == "contract.core.basic_thematic")


# ── 注册表与 fail-closed 校验 ─────────────────────────────────────────────


def test_seed_contracts_validate_clean(contracts):
    assert contracts.validate() == []


def test_seed_contracts_cover_three_purposes(contracts):
    purposes = {c.purpose for c in contracts.all_contracts()}
    assert {"basic_thematic", "heat_distribution_stats", "change_comparison"} <= purposes


def test_unknown_template_reference_fails_closed(contracts, base_contract):
    bad = base_contract.model_copy(
        update={"contract_id": "contract.test.bad_tpl", "template_id": "composition.ghost"})
    contracts.register(bad)
    issues = contracts.validate()
    assert any("contract.test.bad_tpl: template composition.ghost 未注册" in i
               for i in issues)


def test_slot_not_in_template_fails_closed(contracts, base_contract):
    bad = base_contract.model_copy(update={
        "contract_id": "contract.test.bad_slot",
        "slots": (ContractSlot(slot_id="nonexistent_slot"),)})
    contracts.register(bad)
    assert any("slot nonexistent_slot 不在模板中" in i
               for i in contracts.validate())


def test_bad_link_type_fails_closed(contracts, base_contract):
    from app.lib.cartography.composition_contract import ContractLink
    bad = base_contract.model_copy(update={
        "contract_id": "contract.test.bad_link",
        "links": (ContractLink(src_slot="title", dst_slot="legend", type="teleports"),)})
    contracts.register(bad)
    assert any("link 未知类型 teleports" in i for i in contracts.validate())


def test_min_abi_above_current_fails_closed(contracts, base_contract):
    bad = base_contract.model_copy(
        update={"contract_id": "contract.test.abi", "min_abi_version": 99})
    contracts.register(bad)
    assert any("min_abi 99" in i for i in contracts.validate())


# ── 指纹与 diff ───────────────────────────────────────────────────────────


def test_fingerprint_stable(base_contract):
    assert contract_fingerprint(base_contract) == contract_fingerprint(base_contract)
    assert contract_fingerprint(base_contract).startswith("contract-sha256:")


def test_fingerprint_diff_sensitive(base_contract):
    bumped = base_contract.model_copy(update={"contract_version": "1.1.0"})
    assert contract_fingerprint(base_contract) != contract_fingerprint(bumped)


def test_diff_bounded_and_deterministic(base_contract):
    newer = base_contract.model_copy(update={
        "contract_version": "2.0.0",
        "slots": tuple(base_contract.slots) + (ContractSlot(slot_id="inset_map"),),
    })
    d1 = diff_contracts(base_contract, newer)
    d2 = diff_contracts(base_contract, newer)
    assert d1 == d2
    assert d1["slots_added"] == ["inset_map"]
    assert any(s.startswith("version_changed:1.0.0->2.0.0") for s in d1["disclosures"])


def test_diff_symmetry_slots(base_contract):
    newer = base_contract.model_copy(update={
        "slots": tuple(s for s in base_contract.slots if s.slot_id != "title")})
    d = diff_contracts(base_contract, newer)
    assert d["slots_removed"] == ["title"]


# ── apply：空 spec 物化 ───────────────────────────────────────────────────


def test_apply_on_empty_spec_materializes_slots(base_contract):
    spec, report = apply_contract({}, base_contract, revision=7)
    types = [c["type"] for c in spec["layout"]["components"]]
    assert "title" in types and "legend" in types and "scale_bar" in types
    assert report.created, "空 spec 必然物化实例"
    assert report.locked_skipped == []
    # 实例 id 惯例与 composer 单一真值一致
    ids = [c["id"] for c in spec["layout"]["components"]]
    assert "legend-main" in ids and "north-arrow" in ids
    # provenance 落盘（origin=template）
    legend = next(c for c in spec["layout"]["components"] if c["id"] == "legend-main")
    assert legend["provenance"]["origin"] == "template"
    assert legend["provenance"]["source_template"] == "composition.standard_analysis"
    # 身份块落盘
    identity = read_composition_identity(spec)
    assert identity is not None
    assert identity.template_id == "composition.standard_analysis"
    assert identity.applied_revision == 7
    assert identity.component_abi_version == 1
    assert "legend" in identity.component_versions
    # 有界报告可序列化
    assert report.to_bounded_dict()["created_count"] == len(report.created)


def test_apply_is_idempotent(base_contract):
    spec1, report1 = apply_contract({}, base_contract, revision=1)
    spec2, report2 = apply_contract(spec1, base_contract, revision=1)
    assert report2.created == []
    assert report2.links_added == 0
    assert spec1 == spec2, "重复 apply 必须逐位一致"


def test_apply_input_not_mutated(base_contract):
    spec = {"layout": {"components": []}}
    frozen = copy.deepcopy(spec)
    apply_contract(spec, base_contract)
    assert spec == frozen


def test_apply_writes_subtitle_annotates_title_link(base_contract):
    spec, report = apply_contract({}, base_contract)
    links = spec["layout"]["component_links"]
    pair = [(e["src"], e["dst"], e["type"]) for e in links]
    assert ("subtitle", "title", "annotates") in pair
    assert report.links_added >= 1


def test_apply_preserves_user_style_edits(base_contract):
    spec = {"layout": {"components": [
        {"id": "legend-main", "type": "legend", "enabled": True,
         "style": {"fontSize": "99px"}, "options": {"style": "compact"}},
    ]}}
    new_spec, report = apply_contract(spec, base_contract)
    legend = next(c for c in new_spec["layout"]["components"]
                  if c["id"] == "legend-main")
    assert legend["style"] == {"fontSize": "99px"}, "用户样式编辑必须保留"
    assert legend["options"]["style"] == "compact"
    assert "legend-main" in report.preserved
    assert "legend-main" not in report.created


def test_apply_preserves_disabled_instance_as_user_intent(base_contract):
    spec = {"layout": {"components": [
        {"id": "north-arrow", "type": "north_arrow", "enabled": False},
    ]}}
    new_spec, report = apply_contract(spec, base_contract)
    arrow = next(c for c in new_spec["layout"]["components"]
                 if c["id"] == "north-arrow")
    assert arrow["enabled"] is False, "用户显式关闭 = user-wins，不得复活"
    assert "north-arrow" not in report.created


def test_apply_respects_workbench_lock_zero_touch(base_contract):
    """W15 workbench 锁集命中 → 槽位级零触碰（含 provenance 不写入）。"""
    spec = {"workbench": {"lockedComponentIds": ["title"]},
            "layout": {"components": [
        {"id": "title", "type": "title", "style": {"fontWeight": "900"}},
    ]}}
    new_spec, report = apply_contract(spec, base_contract)
    title = next(c for c in new_spec["layout"]["components"] if c["id"] == "title")
    assert title["style"] == {"fontWeight": "900"}
    assert "provenance" not in title, "锁实例零触碰（含 provenance）"
    assert "title" in report.locked_skipped
    assert "title" in report.preserved
    assert any(LOCK_REASON_USER_WINS in d for d in report.disclosures)


def test_apply_links_idempotent(base_contract):
    spec = {"layout": {
        "components": [
            {"id": "subtitle", "type": "subtitle"}, {"id": "title", "type": "title"}],
        "component_links": [
            {"src": "subtitle", "dst": "title", "type": "annotates", "dst_kind": "component"}],
    }}
    new_spec, report = apply_contract(spec, base_contract)
    assert report.links_added == 0, "已存在的同端点同型链接不得重复"


def test_apply_preferred_template_override(contracts, base_contract):
    """契约覆写 preferred_template 必须生效（且校验期锁定类型合法）。"""
    overridden = base_contract.model_copy(update={
        "contract_id": "contract.test.title_override",
        "slots": tuple(
            ContractSlot(slot_id="title", preferred_template="title/academic")
            if s.slot_id == "title" else s
            for s in base_contract.slots)})
    contracts.register(overridden)
    spec, _ = apply_contract({}, overridden)
    title = next(c for c in spec["layout"]["components"] if c["type"] == "title")
    assert title.get("variant") == "academic"
    assert title["style"].get("fontWeight") == "600"


def test_apply_preferred_template_planned_discloses(contracts, base_contract, monkeypatch):
    """preferred 模板非 native → 退化为 descriptor 默认并如实披露。"""
    from app.lib.cartography.component_templates import ComponentTemplate
    from app.lib.cartography.component_registry import get_component_registry
    desc = get_component_registry().get_by_type("title")
    planned = ComponentTemplate(
        id="title/ghost-planned", component_type="title",
        category=desc.category, variant="ghost", runtime_status="planned")
    from app.lib.cartography import component_templates as ct_mod
    ct_mod.reset_component_template_registry()
    try:
        monkeypatch.setattr(
            ct_mod, "SEED_COMPONENT_TEMPLATES",
            list(ct_mod.SEED_COMPONENT_TEMPLATES) + [planned])
        overridden = base_contract.model_copy(update={
            "contract_id": "contract.test.title_planned",
            "slots": tuple(
                ContractSlot(slot_id="title", preferred_template="title/ghost-planned")
                if s.slot_id == "title" else s
                for s in base_contract.slots)})
        spec, report = apply_contract({}, overridden)
    finally:
        ct_mod.reset_component_template_registry()
    title = next(c for c in spec["layout"]["components"] if c["type"] == "title")
    assert title.get("variant") in ("", None) or title.get("variant") != "ghost"
    assert any("preferred_template_not_native" in d for d in report.disclosures)


def test_apply_unknown_template_raises(base_contract):
    ghost = base_contract.model_copy(update={
        "contract_id": "contract.test.ghost", "template_id": "composition.ghost"})
    with pytest.raises(ContractApplyError):
        apply_contract({}, ghost)


def test_apply_slot_not_in_template_raises(base_contract):
    ghost = base_contract.model_copy(update={
        "contract_id": "contract.test.ghost_slot",
        "slots": (ContractSlot(slot_id="nope"),)})
    with pytest.raises(ContractApplyError):
        apply_contract({}, ghost)


# ── 身份块 → 指纹捕获（DoD #4）───────────────────────────────────────────


def test_template_version_change_captured_by_fingerprint(base_contract):
    spec, _ = apply_contract({}, base_contract, revision=1)
    fp_before = cartographic_fingerprint(spec)
    bumped = base_contract.model_copy(update={"contract_version": "9.9.9"})
    spec2, _ = apply_contract(spec, bumped, revision=1)
    fp_after = cartographic_fingerprint(spec2)
    assert fp_before != fp_after, "契约版本变化必须被 plan/product 指纹捕获"


def test_spec_without_identity_fingerprint_untouched():
    spec = {"layout": {"components": [{"id": "title", "type": "title"}]}}
    fp1 = cartographic_fingerprint(spec)
    # 仅 import 本模块（无写入）不得改变指纹语义
    assert fp1 == cartographic_fingerprint(spec)


# ── 锁集读取（W15 单一事实）─────────────────────────────────────────────


def test_locked_component_ids_reader_semantics():
    assert locked_component_ids_of({}) == []
    assert locked_component_ids_of({"workbench": {}}) == []
    assert locked_component_ids_of(
        {"workbench": {"lockedComponentIds": ["a", 1, "", "b"]}}) == ["a", "b"]
    assert locked_component_ids_of(
        {"workbench": {"lockedComponentIds": "not-a-list"}}) == []


def test_bounded_identity_and_report(base_contract):
    from app.lib.cartography.component_abi import COMPONENT_ABI_VERSION as V
    spec, report = apply_contract({}, base_contract, revision=3)
    ident = read_composition_identity(spec)
    assert ident.to_bounded_dict()["component_abi_version"] == V
    bounded = report.to_bounded_dict()
    assert set(bounded) == {
        "contract_id", "created", "created_count", "preserved",
        "locked_skipped", "links_added", "disclosures", "identity"}


def test_contract_roundtrip_serialization(base_contract):
    """model_dump → 重建 → 指纹一致（canonical serialize 契约）。"""
    dumped = base_contract.model_dump()
    rebuilt = CompositionContractV1(**dumped)
    assert contract_fingerprint(rebuilt) == contract_fingerprint(base_contract)


# ── review 修复回归（P2-2 / P2-7）─────────────────────────────────────────


def test_apply_skips_materialization_when_id_locked(base_contract):
    """review P2-2：空槽物化分配到的实例 id 命中锁集 → 跳过 + 披露
    （库级直调同样受锁保护，不依赖引擎守卫兜底）。"""
    spec = {
        "workbench": {"lockedComponentIds": ["title"]},
        "layout": {"components": [
            {"id": "legend-main", "type": "legend"},
            {"id": "north-arrow", "type": "north_arrow"},
            {"id": "scale-bar", "type": "scale_bar"},
            {"id": "attribution", "type": "attribution"},
        ]},
    }
    new_spec, report = apply_contract(spec, base_contract)
    types = [c["type"] for c in new_spec["layout"]["components"]]
    assert "title" not in types, "锁 id 不得被物化"
    assert "title" in report.locked_skipped
    assert any(LOCK_REASON_USER_WINS in d for d in report.disclosures)


def test_cyclic_slot_links_fail_closed(contracts, base_contract):
    """review P2-7：slot 链接环在创作期拒绝（防自锁会话）。"""
    from app.lib.cartography.composition_contract import ContractLink
    cyclic = base_contract.model_copy(update={
        "contract_id": "contract.test.cycle",
        "links": (
            ContractLink(src_slot="title", dst_slot="legend", type="under"),
            ContractLink(src_slot="legend", dst_slot="title", type="under"),
        )})
    contracts.register(cyclic)
    issues = contracts.validate()
    assert any("slot link 成环" in i for i in issues)
