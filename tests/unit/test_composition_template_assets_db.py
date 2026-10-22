"""G04 组合模板资产 DB/服务测试。

覆盖：模型建表与约束、生命周期状态机（create→publish→retire→restore）、
savepoint 版本并发、作用域/IDOR 矩阵（private/org/project/admin/匿名）、
解析两态（for_apply/for_replay）、save-as-my-template 派生、
builtin 资产注册表纪律（DB 资产永不全局注册）。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — 注册全部模型（含本方向新表）
from app.core.database import Base
from app.lib.cartography.template_assets import TemplateAssetPayload
from app.services.templates import composition_assets as svc


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _registry_pristine():
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
        reset_composition_template_registry,
    )
    reset_composition_template_registry()
    reg = get_composition_template_registry()
    yield reg
    reset_composition_template_registry()


def _payload(**kw):
    base = dict(
        name="测试资产",
        base_template_id="composition.density_map",
        slot_overrides={"chart_panel": {"max_count": 2}},
    )
    base.update(kw)
    return TemplateAssetPayload(**base)


def test_create_and_publish_lifecycle(db):
    asset, v1 = svc.create_asset(db, payload=_payload(), creator_id="u1")
    assert asset.id.startswith("asset.")
    assert asset.status == "draft"
    assert v1.revision == 1 and v1.status == "draft"
    assert v1.validation is None  # 合法 payload 校验快照为空
    assert v1.fingerprint.startswith("asset-sha256:")

    published = svc.publish_version(
        db, asset.id, 1, user_id="u1")
    assert published.status == "published"
    asset_row = db.get(type(asset), asset.id)
    assert asset_row.status == "published"
    assert asset_row.current_revision == 1

    resolved = svc.resolve_asset_for(
        db, asset.id, user_id="u1")
    assert resolved.revision == 1
    assert resolved.template.id == asset.id
    assert resolved.contract.template_id == asset.id


def test_publish_gate_blocks_invalid_payload(db):
    bad = _payload(
        slot_overrides={"chart_panel": {"preferred_templates": ["ghost/x"]}})
    asset, v1 = svc.create_asset(db, payload=bad, creator_id="u1")
    assert v1.validation, "未知 preferred template 必须进校验快照"
    with pytest.raises(svc.CompositionAssetError) as ei:
        svc.publish_version(db, asset.id, 1, user_id="u1")
    assert ei.value.reason == "asset_publish_gate_failed"
    # draft 资产不可 apply
    with pytest.raises(svc.CompositionAssetError) as ei2:
        svc.resolve_asset_for(db, asset.id, user_id="u1")
    assert ei2.value.reason == "asset_draft"


def test_retire_blocks_apply_but_allows_pinned_replay(db):
    asset, v1 = svc.create_asset(db, payload=_payload(), creator_id="u1",
                                 publish=True)
    svc.retire_asset(db, asset.id, user_id="u1", note="superseded")
    assert db.get(type(asset), asset.id).status == "retired"
    # 新 apply 拒绝
    with pytest.raises(svc.CompositionAssetError) as ei:
        svc.resolve_asset_for(db, asset.id, None, user_id="u1")
    assert ei.value.reason == "asset_retired"
    # 钉 revision 重放只读放行（旧图打开/导出）
    replay = svc.resolve_asset_for(
        db, asset.id, 1, mode="for_replay", user_id="u1")
    assert replay.revision == 1
    assert replay.record.status == "retired"


def test_restore_creates_new_revision_and_republishes(db):
    asset, _ = svc.create_asset(db, payload=_payload(), creator_id="u1",
                                publish=True)
    v2 = svc.new_version(
        db, asset.id, _payload(slot_overrides={"chart_panel": {"max_count": 1}}),
        user_id="u1", publish=True)
    assert v2.revision == 2
    v3 = svc.restore_revision(db, asset.id, 1, user_id="u1")
    assert v3.revision == 3  # 不重写历史：新 revision
    assert v3.status == "published"
    assert v3.fingerprint  # 指纹存在（内容 = r1 的 payload）
    # current 前移
    assert db.get(type(asset), asset.id).current_revision == 3


def test_restore_after_retire_revives_asset(db):
    asset, _ = svc.create_asset(db, payload=_payload(), creator_id="u1",
                                publish=True)
    svc.retire_asset(db, asset.id, user_id="u1")
    v2 = svc.restore_revision(db, asset.id, 1, user_id="u1")
    assert v2.status == "published"
    assert db.get(type(asset), asset.id).status == "published"


def test_revision_monotonic_and_unique_guard(db):
    """版本号 max+1 单调；唯一约束拦截重复 revision（并发语义的守卫面）。"""
    from sqlalchemy.exc import IntegrityError

    from app.models.composition_template_asset import (
        CompositionTemplateAssetVersion as V,
    )
    asset, _ = svc.create_asset(db, payload=_payload(), creator_id="u1")
    v2 = svc.new_version(db, asset.id, _payload(), user_id="u1")
    v3 = svc.new_version(db, asset.id, _payload(), user_id="u1")
    assert (v2.revision, v3.revision) == (2, 3)
    dup = V(asset_id=asset.id, revision=3,
            payload=_payload().model_dump(), status="draft")
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_scope_visibility_matrix(db):
    a1, _ = svc.create_asset(
        db, payload=_payload(), creator_id="u1", org_id=1, publish=True)
    a2, _ = svc.create_asset(
        db, payload=_payload(), creator_id="u2", org_id=2, visibility="org",
        publish=True)
    # project 资产：先建项目行
    from app.models.project import Project
    db.add(Project(id="proj-1", org_id=2, owner_id="u2", name="P"))
    db.commit()
    a3, _ = svc.create_asset(
        db, payload=_payload(), creator_id="u2", org_id=2,
        visibility="project", project_id="proj-1", publish=True)

    # creator 总可见
    assert svc.asset_visible(a1, "u1", None) is True
    # 同 org 可见（org 资产与任意资产同 org 即可见）
    assert svc.asset_visible(a2, "u9", 2) is True
    # 跨 org 不可见
    assert svc.asset_visible(a1, "u2", 2) is False
    # 匿名不可见
    assert svc.asset_visible(a1, None, None) is False
    # 项目资产：项目属主可见，非属主同 org 也不可见（除非 org 可见性）
    assert svc.asset_visible(a3, "u2", 2) is True
    assert svc.asset_visible(a3, "u9", 2, project_ids=["proj-1"]) is True
    assert svc.asset_visible(a3, "u9", 2, project_ids=[]) is False
    # admin 全见
    assert svc.asset_visible(a1, "uX", 99, role="admin") is True


def test_idor_not_found_not_403(db):
    """不可见资产 = 404 语义（不泄露存在性）；可见但非 creator = 403。"""
    a1, _ = svc.create_asset(db, payload=_payload(), creator_id="u1",
                             publish=True)
    with pytest.raises(svc.CompositionAssetNotFoundError):
        svc.resolve_asset_for(db, a1.id, user_id="u2", org_id=2)
    with pytest.raises(svc.CompositionAssetNotFoundError):
        svc.publish_version(db, a1.id, None, user_id="u2", org_id=2)
    # org 共享资产：同 org 可见（resolve 通过）但非 creator 不可写（403）
    a2, _ = svc.create_asset(
        db, payload=_payload(), creator_id="u1", org_id=1,
        visibility="org", publish=True)
    resolved = svc.resolve_asset_for(db, a2.id, user_id="u2", org_id=1)
    assert resolved.asset_id == a2.id
    with pytest.raises(svc.CompositionAssetForbiddenError):
        svc.retire_asset(db, a2.id, user_id="u2", org_id=1)
    # private 资产：同 org 也不可见（private 语义强于样式模板隐式 org 子句）
    with pytest.raises(svc.CompositionAssetNotFoundError):
        svc.resolve_asset_for(db, a1.id, user_id="u2", org_id=None)
    a3, _ = svc.create_asset(
        db, payload=_payload(), creator_id="u1", org_id=1, publish=True)
    with pytest.raises(svc.CompositionAssetNotFoundError):
        svc.resolve_asset_for(db, a3.id, user_id="u2", org_id=1)


def test_visibility_validation(db):
    with pytest.raises(svc.CompositionAssetError) as ei:
        svc.create_asset(db, payload=_payload(), creator_id="u1",
                         visibility="public")
    assert ei.value.reason == "asset_invalid_visibility"
    with pytest.raises(svc.CompositionAssetError) as ei2:
        svc.create_asset(db, payload=_payload(), creator_id="u1",
                         visibility="project")  # 缺 project_id
    assert ei2.value.reason == "asset_invalid_visibility"
    with pytest.raises(svc.CompositionAssetError):
        svc.create_asset(db, payload=_payload(), creator_id="u1",
                         visibility="project", project_id="ghost-proj")
    with pytest.raises(svc.CompositionAssetForbiddenError):
        svc.create_asset(db, payload=_payload(), creator_id=None)


def test_payload_mode_fail_closed(db):
    with pytest.raises(svc.CompositionAssetError) as ei:
        svc.create_asset(
            db, payload=TemplateAssetPayload(name="x"), creator_id="u1")
    assert ei.value.reason == "asset_payload_error"


def test_db_assets_never_register_globally(db, monkeypatch):
    """注册表纪律：DB 资产解析不触碰全局组合注册表（跨租户纪律）。"""
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    asset, _ = svc.create_asset(db, payload=_payload(), creator_id="u1",
                                publish=True)
    reg = get_composition_template_registry()
    calls = []
    original = reg.upsert_asset

    def _spy(tpl, *, revision):
        calls.append((tpl.id, revision))
        return original(tpl, revision=revision)

    monkeypatch.setattr(reg, "upsert_asset", _spy)
    resolved = svc.resolve_asset_for(db, asset.id, user_id="u1")
    assert resolved.template.id == asset.id
    assert calls == []  # DB 资产：零注册
    assert reg.get(asset.id) is None


def test_builtin_assets_register_globally(db):
    """builtin 资产（公共命名空间）for_apply 解析后进注册表（单调）。"""
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    reg = get_composition_template_registry()
    resolved = svc.resolve_asset_for(db, "asset.builtin.heat_stats_portfolio")
    assert resolved.revision == 1
    assert reg.get("asset.builtin.heat_stats_portfolio") is not None
    assert reg.asset_revision_of("asset.builtin.heat_stats_portfolio") == 1
    # builtin 钉不存在 revision → 拒绝
    with pytest.raises(svc.CompositionAssetError) as ei:
        svc.resolve_asset_for(
            db, "asset.builtin.heat_stats_portfolio", 99)
    assert ei.value.reason == "asset_revision_not_found"


def test_list_assets_scoped(db):
    svc.create_asset(db, payload=_payload(), creator_id="u1", org_id=1,
                     publish=True)
    svc.create_asset(db, payload=_payload(), creator_id="u2", org_id=2,
                     publish=True)
    rows_u1 = svc.list_assets(db, user_id="u1", org_id=1)
    assert len(rows_u1) == 1 and rows_u1[0]["asset_id"].startswith("asset.")
    rows_admin = svc.list_assets(db, user_id="admin", org_id=None, role="admin")
    assert len(rows_admin) == 2
    rows_anon = svc.list_assets(db, user_id=None, org_id=None)
    assert rows_anon == []


def test_list_versions_and_fingerprint_stability(db):
    asset, _ = svc.create_asset(db, payload=_payload(), creator_id="u1",
                                publish=True)
    svc.new_version(db, asset.id, _payload(
        slot_overrides={"chart_panel": {"max_count": 1}}),
        user_id="u1", publish=True)
    versions = svc.list_versions(db, asset.id, user_id="u1", org_id=None)
    assert [v["revision"] for v in versions] == [2, 1]
    assert versions[0]["fingerprint"] != versions[1]["fingerprint"]


def test_derive_payload_from_spec_variant_diff():
    """save-as-my-template：系统基底上 → 只存差异覆写（参数绑定复用）。"""
    spec = {
        "layout": {
            "components": [
                {"id": "title", "type": "title", "enabled": True,
                 "position": "top-center"},
                {"id": "legend", "type": "legend", "enabled": True,
                 "position": "bottom-left", "variant": "academic"},
                {"id": "scale_bar", "type": "scale_bar", "enabled": True,
                 "position": "bottom-right"},
                {"id": "statistics_panel", "type": "statistics_panel",
                 "enabled": True, "position": "top-left"},
            ],
            "composition": {
                "template_id": "composition.standard_analysis",
                "template_version": "1.0.0",
                "contract_id": "",
            },
        },
    }
    payload = svc.derive_payload_from_spec(spec)
    assert payload.base_template_id == "composition.standard_analysis"
    assert payload.inline_template is None
    # 已与 base 一致的槽位不进覆写（只存差异）
    assert "scale_bar" not in payload.slot_overrides
    # legend variant=academic → preferred_templates 绑定
    legend_ov = payload.slot_overrides.get("legend", {})
    assert legend_ov.get("preferred_templates") == ["legend/academic"]


def test_derive_payload_from_spec_inline_when_no_system_base():
    spec = {
        "layout": {
            "components": [
                {"id": "title", "type": "title", "enabled": True,
                 "position": "top-center"},
                {"id": "chart-panel-1", "type": "chart_panel",
                 "enabled": False, "position": "top-left"},
            ],
            "composition": {"template_id": "asset.builtin.ghost",
                            "template_version": "asset-r1"},
        },
    }
    payload = svc.derive_payload_from_spec(spec)
    assert payload.inline_template is not None
    slots = {s["id"]: s for s in payload.inline_template["component_slots"]}
    assert slots["title"]["cardinality"] == "required"
    assert slots["chart-panel-1"]["cardinality"] == "optional"
    # 无基底时 base_template_id 为空
    assert payload.base_template_id == ""


def test_resolved_asset_bounded_dict(db):
    asset, _ = svc.create_asset(db, payload=_payload(), creator_id="u1",
                                publish=True)
    resolved = svc.resolve_asset_for(db, asset.id, user_id="u1")
    d = resolved.to_bounded_dict()
    assert d["asset_id"] == asset.id
    assert d["contract_id"].startswith(f"contract.asset.{asset.id}@r")
    assert d["template_version"] == "asset-r1"


def test_fingerprint_recompute_matches_stored(db):
    from app.lib.cartography.template_assets import asset_fingerprint
    asset, _ = svc.create_asset(db, payload=_payload(), creator_id="u1",
                                publish=True)
    version = svc.list_versions(
        db, asset.id, user_id="u1", org_id=None)[0]
    resolved = svc.resolve_asset_for(db, asset.id, user_id="u1")
    assert asset_fingerprint(resolved.record) == version["fingerprint"]


# ── review 修复回归（P1-2 / P2-1 / P2-2 / P2-3）──────────────────────────


def test_derive_payload_preserves_all_edits_after_real_apply():
    """review P1-2：真实 apply 后派生必须覆盖全部组件（实例 id ≠ 槽位 id）。"""
    from app.lib.cartography.composition_contract import (
        SEED_CONTRACTS,
        apply_contract,
    )

    seed = next(c for c in SEED_CONTRACTS
                if c.contract_id == "contract.core.basic_thematic")
    spec, _report = apply_contract({"version": "1.6", "layout": {}}, seed)
    payload = svc.derive_payload_from_spec(spec)
    assert payload.base_template_id == "composition.standard_analysis"
    assert payload.inline_template is None
    # apply 物化的每个组件都必须在 overrides 或「与 base 无差异」集中
    # —— 统计面板/图例/指北针/比例尺/归属等实例 id（legend-main 等）必须
    # 解析回 base 槽位，不允许静默丢弃。
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    base = get_composition_template_registry().get("composition.standard_analysis")
    base_slots = {s.id: s.model_dump() for s in base.component_slots}
    # 关键：派生 slot id 集合必须 ⊆ base 槽位集合（解析回槽位，不是实例 id）
    for slot_id in payload.slot_overrides:
        assert slot_id in base_slots, f"{slot_id} 不是 base 槽位 id"
    # 每个在场组件类型都至少被一个 base 槽位覆盖
    present_types = {c["type"] for c in spec["layout"]["components"]}
    for ctype in present_types:
        assert any(
            ctype in s["allowed_component_types"] for s in base_slots.values()
        ), f"{ctype} 无对应 base 槽位（派生必丢编辑）"


def test_derive_payload_falls_back_inline_for_extra_components():
    """review P1-2：用户新增组件不在基底 → 诚实降级 inline（不吞编辑）。"""
    from app.lib.cartography.composition_contract import (
        SEED_CONTRACTS,
        apply_contract,
    )
    seed = next(c for c in SEED_CONTRACTS
                if c.contract_id == "contract.core.basic_thematic")
    spec, _ = apply_contract({"version": "1.6", "layout": {}}, seed)
    spec["layout"]["components"].append({
        "id": "uncertainty-panel", "type": "uncertainty_panel",
        "enabled": True, "position": "bottom-left"})
    payload = svc.derive_payload_from_spec(spec)
    assert payload.inline_template is not None, "新增组件必须保真（inline）"
    ids = {s["id"] for s in payload.inline_template["component_slots"]}
    assert "uncertainty-panel" in ids


def test_pinned_draft_revision_apply_rejected(db):
    """review P2-1：钉版本应用不得绕过发布门（draft 拒绝）。"""
    bad = _payload(
        slot_overrides={"chart_panel": {"preferred_templates": ["ghost/x"]}})
    asset, v1 = svc.create_asset(db, payload=bad, creator_id="u1")
    assert v1.validation  # draft 且校验未过
    with pytest.raises(svc.CompositionAssetError) as ei:
        svc.resolve_asset_for(db, asset.id, 1, user_id="u1")
    assert ei.value.reason == "asset_version_not_published"


def test_project_asset_foreign_project_rejected(db):
    """review P2-2：project 资产不可挂到无归属关系的外国项目。"""
    from app.models.project import Project
    db.add(Project(id="proj-foreign", org_id=99, owner_id="other",
                   name="Foreign"))
    db.commit()
    with pytest.raises(svc.CompositionAssetForbiddenError):
        svc.create_asset(
            db, payload=_payload(), creator_id="u1", org_id=1,
            visibility="project", project_id="proj-foreign")
    # 同 org 项目可挂
    db.add(Project(id="proj-same-org", org_id=1, owner_id="colleague",
                   name="Shared"))
    db.commit()
    asset, _ = svc.create_asset(
        db, payload=_payload(), creator_id="u1", org_id=1,
        visibility="project", project_id="proj-same-org")
    assert asset.project_id == "proj-same-org"
