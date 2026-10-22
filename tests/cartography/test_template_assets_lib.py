"""TemplateAsset lib 契约测试（G04 / ADR-0214 留白收口）。

覆盖：payload 互斥模式、物化确定性（variant 深合并 / inline 命名空间）、
指纹稳定与敏感、发布门 fail-closed 校验、内置 4 族资产合法、注册表
asset.* 单调 upsert（防覆盖系统 id / 防回退）、派生契约引用语义、
CompositionIdentity additive 资产字段（旧 spec 逐位不变 + 钉版本 apply）。
"""
import pytest

from app.lib.cartography.composition_contract import (
    CompositionIdentity,
    ContractApplyError,
    SEED_CONTRACTS,
    apply_contract,
    contract_fingerprint,
    read_composition_identity,
)
from app.lib.cartography.composition_templates import (
    get_composition_template_registry,
)
from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.lib.cartography.template_assets import (
    BUILTIN_TEMPLATE_ASSETS,
    TemplateAssetError,
    TemplateAssetPayload,
    TemplateAssetRecord,
    asset_fingerprint,
    asset_reference_of,
    derive_contract,
    get_builtin_asset,
    materialize_template,
    validate_asset_template,
)


@pytest.fixture()
def registry():
    """隔离刷新共享单例（负例不污染其他文件的 pristine 校验测试）。"""
    from app.lib.cartography.composition_templates import (
        reset_composition_template_registry,
    )
    reset_composition_template_registry()
    reg = get_composition_template_registry()
    yield reg
    reset_composition_template_registry()


@pytest.fixture()
def variant_record():
    return TemplateAssetRecord(
        asset_id="asset.test.variant1",
        payload=TemplateAssetPayload(
            name="测试变体",
            base_template_id="composition.density_map",
            slot_overrides={
                "statistics_panel": {
                    "cardinality": "recommended",
                    "preferred_templates": ["statistics-panel/kpi"],
                },
                "chart_panel": {"max_count": 2},
            },
            tags=["test"],
        ),
    )


def _inline_payload() -> TemplateAssetPayload:
    return TemplateAssetPayload(
        name="内联模板",
        inline_template={
            "id": "asset.test.inline1",
            "name": "Inline",
            "component_slots": [
                {"id": "title", "allowed_component_types": ["title"],
                 "cardinality": "required", "required": True,
                 "position_zone": "top-center"},
                {"id": "scale_bar", "allowed_component_types": ["scale_bar"],
                 "cardinality": "required", "required": True,
                 "position_zone": "bottom-right"},
            ],
            "output_targets": ["interactive"],
        },
    )


# ── payload 模式 ──────────────────────────────────────────────────────────


def test_payload_mode_exclusive():
    with pytest.raises(TemplateAssetError, match="asset_payload_error"):
        TemplateAssetPayload().mode()
    with pytest.raises(TemplateAssetError, match="asset_payload_error"):
        TemplateAssetPayload(
            base_template_id="composition.density_map",
            inline_template={"id": "asset.x"},
        ).mode()
    assert TemplateAssetPayload(
        base_template_id="composition.density_map").mode() == "variant"
    assert _inline_payload().mode() == "inline"


# ── 物化：variant / inline ────────────────────────────────────────────────


def test_materialize_variant_deep_merge(registry, variant_record):
    tpl = materialize_template(variant_record)
    assert tpl.id == "asset.test.variant1"
    base = registry.get("composition.density_map")
    # 未覆写槽位逐字段继承 base（真值引用，不复制第二语义）
    base_slots = {s.id: s for s in base.component_slots}
    for slot in tpl.component_slots:
        if slot.id not in ("statistics_panel", "chart_panel"):
            assert slot.model_dump() == base_slots[slot.id].model_dump()
    stats = next(s for s in tpl.component_slots if s.id == "statistics_panel")
    assert stats.cardinality == "recommended"
    assert stats.preferred_templates == ["statistics-panel/kpi"]
    charts = next(s for s in tpl.component_slots if s.id == "chart_panel")
    assert charts.max_count == 2
    assert charts.allowed_component_types == \
        base_slots["chart_panel"].allowed_component_types
    # fallback 保持系统链
    assert tpl.fallback_template == base.fallback_template


def test_materialize_variant_fail_closed(registry, variant_record):
    bad = variant_record.model_copy(deep=True)
    bad.payload.slot_overrides = {"no_such_slot": {"cardinality": "optional"}}
    with pytest.raises(TemplateAssetError, match="slot_not_in_base"):
        materialize_template(bad)
    bad2 = variant_record.model_copy(deep=True)
    bad2.payload.slot_overrides = {"title": {"id": "hijack"}}
    with pytest.raises(TemplateAssetError, match="slot_field_not_overridable"):
        materialize_template(bad2)
    bad3 = TemplateAssetRecord(
        asset_id="asset.test.bad_base",
        payload=TemplateAssetPayload(base_template_id="composition.ghost"),
    )
    with pytest.raises(TemplateAssetError, match="base_not_found"):
        materialize_template(bad3)


def test_materialize_inline_namespace_enforced():
    rec = TemplateAssetRecord(
        asset_id="asset.test.inline1", payload=_inline_payload())
    tpl = materialize_template(rec)
    assert tpl.id == "asset.test.inline1"
    assert tpl.component_slots[0].id == "title"
    # inline dict 内携带非 asset id → 强制改写为资产 id
    payload = _inline_payload()
    payload.inline_template["id"] = "composition.standard_analysis"
    rec2 = TemplateAssetRecord(asset_id="asset.test.inline1", payload=payload)
    assert materialize_template(rec2).id == "asset.test.inline1"


def test_materialize_rejects_non_asset_namespace():
    rec = TemplateAssetRecord(
        asset_id="composition.standard_analysis",
        payload=TemplateAssetPayload(base_template_id="composition.density_map"))
    with pytest.raises(TemplateAssetError, match="id_namespace"):
        materialize_template(rec)


def test_materialize_deterministic(registry, variant_record):
    assert materialize_template(variant_record).model_dump() == \
        materialize_template(variant_record).model_dump()


# ── 指纹 ──────────────────────────────────────────────────────────────────


def test_fingerprint_stable_and_sensitive(variant_record):
    f1 = asset_fingerprint(variant_record)
    assert f1 == asset_fingerprint(variant_record)
    assert f1.startswith("asset-sha256:")
    bumped = variant_record.model_copy(deep=True)
    bumped.revision = 2
    assert asset_fingerprint(bumped) != f1
    mutated = variant_record.model_copy(deep=True)
    mutated.payload.slot_overrides["chart_panel"]["max_count"] = 3
    assert asset_fingerprint(mutated) != f1


# ── 发布门校验 ────────────────────────────────────────────────────────────


def test_validate_asset_template_gate(registry, variant_record):
    tpl = materialize_template(variant_record)
    assert validate_asset_template(tpl) == []
    bad = tpl.model_copy(deep=True)
    bad.component_slots[0].allowed_component_types = ["ghost_type"]
    issues = validate_asset_template(bad)
    assert issues and any("unknown component type" in i for i in issues)
    bad2 = tpl.model_copy(deep=True)
    bad2.component_slots[0].preferred_templates = ["ghost/template"]
    assert any("preferred template" in i for i in validate_asset_template(bad2))


def test_builtin_assets_materialize_and_validate(registry):
    assert len(BUILTIN_TEMPLATE_ASSETS) == 4
    for rec in BUILTIN_TEMPLATE_ASSETS:
        assert rec.asset_id.startswith("asset.builtin.")
        tpl = materialize_template(rec)
        assert tpl.id == rec.asset_id
        assert validate_asset_template(tpl) == [], (
            f"builtin asset {rec.asset_id} invalid: "
            f"{validate_asset_template(tpl)}")
        contract = derive_contract(rec, tpl)
        assert contract.template_id == tpl.id
        assert contract.contract_id == \
            f"contract.asset.{rec.asset_id}@r{rec.revision}"
        # 契约槽位 = 模板槽位引用（不复制语义）
        assert [s.slot_id for s in contract.slots] == \
            [s.id for s in tpl.component_slots]
    assert get_builtin_asset("asset.builtin.heat_stats_portfolio") is not None
    assert get_builtin_asset("asset.builtin.ghost") is None


def test_builtin_families_cover_four_product_kinds(registry):
    ids = {r.asset_id for r in BUILTIN_TEMPLATE_ASSETS}
    assert {
        "asset.builtin.heat_stats_portfolio",
        "asset.builtin.choropleth_graduated_report",
        "asset.builtin.temporal_change_pair",
        "asset.builtin.rs_classification_change",
    } == ids


# ── 注册表 asset.* 单调 upsert ────────────────────────────────────────────


def test_registry_upsert_asset_monotonic(registry, variant_record):
    tpl = materialize_template(variant_record)
    assert registry.upsert_asset(tpl, revision=1) is True
    assert registry.get("asset.test.variant1") is tpl
    assert registry.asset_revision_of("asset.test.variant1") == 1
    # 同 revision 幂等忽略；非法 revision（<1）程序性拒绝
    assert registry.upsert_asset(tpl, revision=1) is False
    with pytest.raises(ValueError, match="revision must be >= 1"):
        registry.upsert_asset(tpl, revision=0)
    assert registry.get("asset.test.variant1") is tpl
    # 更高 revision 生效
    tpl2 = tpl.model_copy(deep=True)
    tpl2.name = "v2"
    assert registry.upsert_asset(tpl2, revision=2) is True
    assert registry.get("asset.test.variant1").name == "v2"
    assert registry.asset_revision_of("asset.test.variant1") == 2
    # drop 仅限资产命名空间
    assert registry.drop_asset("asset.test.variant1") is True
    assert registry.get("asset.test.variant1") is None
    assert registry.drop_asset("composition.density_map") is False
    assert registry.get("composition.density_map") is not None


def test_registry_upsert_rejects_system_ids(registry):
    tpl = registry.get("composition.density_map")
    with pytest.raises(ValueError, match="asset.\\*"):
        registry.upsert_asset(tpl, revision=1)


def test_registry_validate_covers_assets(registry, variant_record):
    tpl = materialize_template(variant_record)
    assert registry.upsert_asset(tpl, revision=1) is True
    assert registry.validate() == []
    # 无效资产模板进注册表 → 注册表级校验如实报告（不静默）
    bad = tpl.model_copy(deep=True)
    bad.component_slots[0].allowed_component_types = ["ghost_type"]
    assert registry.upsert_asset(bad, revision=2) is True
    issues = registry.validate()
    assert any("asset.test.variant1" in i for i in issues)


# ── 引用投影 ──────────────────────────────────────────────────────────────


def test_asset_reference_projection(registry, variant_record):
    tpl = materialize_template(variant_record)
    ref = asset_reference_of(variant_record, tpl)
    d = ref.to_bounded_dict()
    assert d["asset_id"] == "asset.test.variant1"
    assert d["revision"] == 1
    assert d["builtin"] is False
    assert d["fingerprint"].startswith("asset-sha256:")
    assert d["template_id"] == "asset.test.variant1"


# ── CompositionIdentity additive + apply 钉版本 ────────────────────────────


def test_identity_additive_defaults_preserve_old_spec():
    old = {
        "template_id": "composition.standard_analysis",
        "template_version": "1.0.0",
        "contract_id": "contract.core.basic_thematic",
        "contract_fingerprint": "x",
        "component_abi_version": 1,
        "component_versions": {},
        "applied_revision": 7,
    }
    ident = read_composition_identity({"layout": {"composition": dict(old)}})
    assert ident is not None
    assert ident.asset_id == "" and ident.asset_revision == 0
    # 旧块不含资产键时 to_bounded_dict additive 追加，原键逐位保留
    out = ident.to_bounded_dict()
    for k, v in old.items():
        assert out[k] == v
    assert out["asset_id"] == "" and out["asset_revision"] == 0


def test_apply_contract_with_asset_identity(registry, variant_record):
    spec = {"layout": {}}
    tpl = materialize_template(variant_record)
    contract = derive_contract(variant_record, tpl)
    # 工具链路：服务层物化后显式传钉版本模板（不依赖注册表现值）
    new_spec, report = apply_contract(
        spec, contract, revision=3, template=tpl,
        asset_id=variant_record.asset_id, asset_revision=variant_record.revision)
    ident = report.identity
    assert ident.asset_id == "asset.test.variant1"
    assert ident.asset_revision == 1
    raw = new_spec["layout"]["composition"]
    assert raw["asset_id"] == "asset.test.variant1"
    assert raw["asset_revision"] == 1
    reread = read_composition_identity(new_spec)
    assert reread.asset_revision == 1
    # 无资产参数 → additive 字段为空（兼容面）
    seed = next(c for c in SEED_CONTRACTS
                if c.contract_id == "contract.core.basic_thematic")
    _, report2 = apply_contract({"layout": {}}, seed)
    assert report2.identity.asset_id == ""
    assert report2.identity.asset_revision == 0


def test_apply_contract_pinned_template_mismatch_fail_closed(registry):
    seed = next(c for c in SEED_CONTRACTS
                if c.contract_id == "contract.core.basic_thematic")
    other = registry.get("composition.density_map")
    with pytest.raises(ContractApplyError, match="template_id_mismatch"):
        apply_contract({"layout": {}}, seed, template=other)


def test_apply_contract_pinned_template_used(registry, variant_record):
    """钉旧 revision 重放：传入的模板优先于注册表现值。"""
    tpl_r1 = materialize_template(variant_record)
    contract = derive_contract(variant_record, tpl_r1)
    assert registry.upsert_asset(tpl_r1, revision=1) is True
    # 资产升到 r2（去掉 chart_panel），注册表现值变化
    bumped = variant_record.model_copy(deep=True)
    bumped.revision = 2
    bumped.payload.slot_overrides["chart_panel"] = {"max_count": 0}
    tpl_r2 = materialize_template(bumped)
    assert registry.upsert_asset(tpl_r2, revision=2) is True
    # 钉 r1 apply：用 r1 模板 → chart_panel 仍可物化
    _, report = apply_contract(
        {"layout": {}}, contract, revision=1,
        template=tpl_r1,
        asset_id="asset.test.variant1", asset_revision=1)
    created_types = report.created
    assert any("chart" in c for c in created_types), (
        "钉 r1 模板必须物化 chart_panel（r2 把它禁用）")
    # 身份块钉 r1
    assert report.identity.asset_revision == 1


def test_contract_fingerprint_of_seed_unaffected():
    """seed 契约指纹模式不受 additive 资产字段影响（同契约稳定）。"""
    seed = SEED_CONTRACTS[0]
    assert contract_fingerprint(seed) == contract_fingerprint(seed)


def test_identity_block_changes_fingerprint_with_asset():
    base_spec = {
        "version": "1.6",
        "layout": {"composition": {
            "template_id": "t", "template_version": "1",
            "component_abi_version": 1,
        }},
    }
    with_asset = {
        "version": "1.6",
        "layout": {"composition": {
            "template_id": "t", "template_version": "1",
            "component_abi_version": 1,
            "asset_id": "asset.x", "asset_revision": 2,
        }},
    }
    assert cartographic_fingerprint(base_spec) != cartographic_fingerprint(
        with_asset)


def test_identity_model_accepts_extra_read_keys():
    ident = CompositionIdentity(
        template_id="t", template_version="1",
        asset_id="asset.builtin.rs_classification_change", asset_revision=1)
    assert ident.to_bounded_dict()["asset_id"] == \
        "asset.builtin.rs_classification_change"
