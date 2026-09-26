"""Component ABI v1 契约测试（ADR-0214 D1）。

覆盖：投影完整性、fail-closed 交叉审计、props 前置校验正/负例、
版本投影、slots 投影确定性。
"""
import pytest

from app.lib.cartography.component_abi import (
    COMPONENT_ABI_META,
    COMPONENT_ABI_VERSION,
    abi_record_for,
    component_version,
    validate_props,
    versions_projection,
)
from app.lib.cartography.component_registry import (
    get_component_registry,
    reset_component_registry,
)
from app.lib.cartography.component_templates import SEED_COMPONENT_TEMPLATES


@pytest.fixture()
def registry():
    reset_component_registry()
    return get_component_registry()


def test_abi_version_is_int_one():
    assert COMPONENT_ABI_VERSION == 1


def test_projection_covers_all_native_types(registry):
    """每个注册 native 类型都能投影出完整 ABI 记录（DoD：元数据可验证）。"""
    for desc in registry.native_descriptors():
        record = abi_record_for(desc)
        assert record.type == desc.type
        assert record.version
        assert record.abi_version == COMPONENT_ABI_VERSION
        assert record.category == desc.category
        assert record.semantic_role == desc.semantic_role
        assert set(record.renderer_support) == set(desc.renderer_support)
        assert set(record.exporter_support) == set(desc.exporter_support)


def test_projection_is_deterministic(registry):
    """同输入两次投影逐位一致（纯函数纪律）。"""
    desc = registry.get_by_type("legend")
    a = abi_record_for(desc).model_dump()
    b = abi_record_for(desc).model_dump()
    assert a == b


def test_slots_projection_from_composition_templates(registry):
    """legend 可担任的 slot 来自 composition 模板 allowed_component_types。"""
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    expected = set()
    for tpl in get_composition_template_registry().all_templates():
        for slot in tpl.component_slots:
            if "legend" in slot.allowed_component_types:
                expected.add(slot.id)
    record = abi_record_for(registry.get_by_type("legend"))
    assert set(record.slots) == expected
    assert "legend" in record.slots  # 核心槽位确实在列


def test_default_props_projected_from_seed_templates(registry):
    """north_arrow 的 default props 投影出唯一 variant 默认值。"""
    record = abi_record_for(registry.get_by_type("north_arrow"))
    assert record.default_props.get("variant") == "compass_minimal_black"


def test_registry_validate_clean_with_abi_audit(registry):
    """健康注册表：含 ABI 审计在内零 issue（failclosed 契约延续）。"""
    assert registry.validate() == []


def test_abi_audit_detects_missing_meta(registry, monkeypatch):
    """抽走一个类型的 ABI 条目 → validate 报 abi_meta_missing（fail-closed）。"""
    ctype = "scale_bar"
    monkeypatch.delitem(COMPONENT_ABI_META, ctype)
    issues = registry.validate()
    assert any(f"abi_meta_missing: {ctype}" in i for i in issues)


def test_abi_audit_detects_orphan_meta(registry, monkeypatch):
    """注册表没有的 ABI 条目 → abi_meta_orphan（占位类型不得混入）。"""
    from app.lib.cartography.component_abi import ComponentABIMeta
    monkeypatch.setitem(COMPONENT_ABI_META, "ghost_widget", ComponentABIMeta())
    issues = registry.validate()
    assert any("abi_meta_orphan: ghost_widget" in i for i in issues)


def test_abi_audit_detects_ungrounded_props(registry, monkeypatch):
    """props schema 声明写入面不存在的键 → abi_props_ungrounded（防空头契约）。"""
    from app.lib.cartography.component_abi import ComponentABIMeta, PropsFieldSpec
    ghost = ComponentABIMeta(props_schema={
        "nonexistent_key": PropsFieldSpec(type="str")})
    monkeypatch.setitem(COMPONENT_ABI_META, "title", ghost)
    issues = registry.validate()
    assert any("abi_props_ungrounded: title.nonexistent_key" in i for i in issues)


def test_validate_props_accepts_declared_valid():
    issues = validate_props("scale_bar", {
        "orientation": "horizontal", "unit": "metric", "style": "boxed"})
    assert issues == []


def test_validate_props_rejects_enum_violation():
    issues = validate_props("scale_bar", {"unit": "nautical"})
    assert issues == ["props_invalid:unit:not_in_enum"]


def test_validate_props_rejects_type_violation():
    assert validate_props("north_arrow", {"variant": 42}) == [
        "props_invalid:variant:expected_str"]
    assert validate_props("continuous_colorbar", {"ticks": True}) == [
        "props_invalid:ticks:bool_not_int"]


def test_validate_props_bounds():
    assert validate_props("continuous_colorbar", {"ticks": 1}) == [
        "props_invalid:ticks:below_min"]
    assert validate_props("continuous_colorbar", {"ticks": 99}) == [
        "props_invalid:ticks:above_max"]
    assert validate_props("north_arrow", {"variant": "x" * 65}) == [
        "props_invalid:variant:too_long"]


def test_validate_props_ignores_undeclared_keys():
    """未声明字段不否决（options 是 open dict；本函数只守声明契约）。"""
    assert validate_props("title", {"futureKey": "anything"}) == []


def test_validate_props_unknown_type_fail_closed():
    issues = validate_props("ghost_widget", {"a": 1})
    assert issues == ["props_invalid:ghost_widget:abi_meta_missing"]


def test_validate_props_non_object():
    assert validate_props("title", ["not", "a", "dict"]) == [
        "props_invalid:__:not_an_object"]


def test_component_version_and_projection():
    assert component_version("north_arrow") == "1.0.0"
    assert component_version("ghost_widget") == "1.0.0"  # 回退默认，不抛
    proj = versions_projection(["north_arrow", "legend", "north_arrow"])
    assert proj == {"legend": "1.0.0", "north_arrow": "1.0.0"}


def test_props_schemas_grounded_in_seed_templates():
    """声明键 ⊆ seed default_options ∪ renderer 消费键（结构性锁漂移）。"""
    from app.lib.cartography.component_abi import _grounded_option_keys
    grounded = _grounded_option_keys()
    for ctype, meta in COMPONENT_ABI_META.items():
        for key in meta.props_schema:
            assert key in grounded.get(ctype, set()), (
                f"{ctype}.{key} 无写入面/消费面证据")


def test_every_seed_template_type_has_meta_entry():
    types = {t.component_type for t in SEED_COMPONENT_TEMPLATES}
    missing = types - set(COMPONENT_ABI_META)
    assert not missing, f"seed 模板类型缺 ABI 条目: {sorted(missing)}"


# ── review 修复回归（P2-3 / P2-8）─────────────────────────────────────────


def test_validate_props_required_enforced(monkeypatch):
    """声明 required=True 的字段缺失 → missing_required（review P2-3：
    required 此前从未被消费）。"""
    from app.lib.cartography.component_abi import (
        ComponentABIMeta,
        PropsFieldSpec,
    )
    monkeypatch.setitem(COMPONENT_ABI_META, "title", ComponentABIMeta(
        props_schema={"text": PropsFieldSpec(type="str", required=True)}))
    assert validate_props("title", {}) == ["props_invalid:text:missing_required"]
    assert validate_props("title", {"text": "x"}) == []


def test_validate_props_int_enum_branch_reachable(monkeypatch):
    """int 枚举判定可达且优先于范围（review P2-3：原分支死代码）。"""
    from app.lib.cartography.component_abi import (
        ComponentABIMeta,
        PropsFieldSpec,
    )
    monkeypatch.setitem(COMPONENT_ABI_META, "chart_panel", ComponentABIMeta(
        props_schema={"level": PropsFieldSpec(type="int", enum=(1, 2, 3),
                                              min=0, max=99)}))
    assert validate_props("chart_panel", {"level": 5}) == [
        "props_invalid:level:not_in_enum"]
    assert validate_props("chart_panel", {"level": 2}) == []


def test_legacy_id_tables_match_single_truth():
    """review P2-8：遗留手抄 id 表必须与 component_abi 单一真值一致
    （completion contracts + composer legend 族）。漂移即测试失败。"""
    from app.lib.cartography.component_abi import instance_id_for_type
    from app.services.gis_harness.completion.contracts import _COMPONENT_DEFAULT_IDS
    from app.services.gis_harness.component_composer import ComponentComposer
    for ctype, iid in _COMPONENT_DEFAULT_IDS.items():
        assert instance_id_for_type(ctype) == iid, ctype
    for ctype, iid in ComponentComposer._LEGEND_PRIMARY_IDS.items():
        assert instance_id_for_type(ctype) == iid, ctype
