"""G04 W3 —— 4 类典型制图成果端到端语义一致性 + 失败/恢复/并发矩阵。

按 DoD 要求：每类成果经「真实 composition apply → 用户编辑（user-wins）
→ 钉版本 restore/重放 → export manifest 语义一致性」验证。

4 类：① 点分布+热力+柱状统计（heat_stats_portfolio）；② 分级设色+图例
统计（choropleth_graduated_report）；③ 时序对比双图（temporal_change_pair）；
④ 遥感分类/变化（rs_classification_change）。

另含：锁零触碰、CAS superseded、身份块指纹链变化、manifest 显式资产
模板注入（DB 资产）、退役资产旧图再渲染诚实降级。
"""
import shutil
import uuid

import pytest

from app.lib.cartography.composition_contract import (
    apply_contract,
    read_composition_identity,
)
from app.lib.cartography.product_manifest import (
    build_product_manifest,
    verify_export_semantic_consistency,
)
from app.lib.cartography.template_assets import (
    BUILTIN_TEMPLATE_ASSETS,
    materialize_template,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.services.templates import composition_assets as svc
from app.tools.registry import ToolRegistry

ASSET_IDS = [r.asset_id for r in BUILTIN_TEMPLATE_ASSETS]
FAMILY_NAMES = (
    "asset.builtin.heat_stats_portfolio",
    "asset.builtin.choropleth_graduated_report",
    "asset.builtin.temporal_change_pair",
    "asset.builtin.rs_classification_change",
)


@pytest.fixture
def registry():
    r = ToolRegistry()
    from app.tools.composition_tools import register_composition_tools
    register_composition_tools(r)
    return r


@pytest.fixture
async def clean_session():
    sid = f"g04-w3-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    session_dir = BASE_STORAGE_DIR / sid
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


def _minimal_spec() -> dict:
    return {
        "version": "1.6",
        "layers": [{
            "id": "layer-thematic",
            "visible": True,
            "type": "fill",
            "paint": {"color": {"method": "choropleth", "field": "value"}},
        }],
        "legend": {"field": "value", "palette_id": "YlOrRd",
                   "colors": ["#fef0d9", "#fdcc8a"]},
        "layout": {"components": [], "component_links": []},
    }


async def _apply(registry, session_id: str, asset_id: str) -> dict:
    res = await registry.dispatch(
        "webgis_apply_composition",
        {"template_asset_id": asset_id}, session_id=session_id)
    assert res["success"] is True, res
    return res


# ── 4 族成果：apply → user edit → restore → export manifest ──────────────


@pytest.mark.parametrize("asset_id", FAMILY_NAMES)
@pytest.mark.asyncio
async def test_family_apply_user_edit_restore_manifest(registry,
                                                       clean_session,
                                                       asset_id):
    session_id = clean_session
    # 1) 真实 apply
    res = await _apply(registry, session_id, asset_id)
    rev_after_apply = res["mutation_revision"]
    assert rev_after_apply >= 1
    spec = await mapspec_store.get_mapspec(session_id)
    identity = read_composition_identity(spec)
    assert identity is not None
    assert identity.asset_id == asset_id
    assert identity.asset_revision == 1

    # 2) 用户编辑（user-wins）：改标题 options —— apply 幂等不得重置
    layout = spec["layout"]
    title = next(
        (c for c in layout["components"] if c["type"] == "title"), None)
    if title is not None:
        title["options"] = {"text": "用户自己的标题", "font_size": 42}
        title["style"] = {"color": "#112233"}
        edit_res = await mapspec_store.layout_set(
            session_id, components=layout["components"],
            component_links=layout.get("component_links") or [],
            origin="user", actor="user_edit",
            expected_revision=rev_after_apply)
        # 用户编辑必须落盘（CAS 推进）；拒绝 = 测试布置失败，不吞错
        assert edit_res.get("success") is True, edit_res.get("message")
        assert edit_res["mutation_revision"] > rev_after_apply

    # 3) 同资产幂等重放：用户编辑逐位保留（按实例 id 比对，不按类型
    # —— 多个同型实例在场时取首个会张冠李戴）
    res2 = await _apply(registry, session_id, asset_id)
    assert res2["report"]["created_count"] == 0
    spec_replayed = await mapspec_store.get_mapspec(session_id)
    if title is not None:
        title_now = next(
            (c for c in spec_replayed["layout"]["components"]
             if c.get("id") == title.get("id")), None)
        assert title_now is not None, "用户编辑的实例必须保留"
        assert title_now.get("options", {}).get("text") == "用户自己的标题"
        assert title_now.get("style", {}).get("color") == "#112233"

    # 4) export manifest：组合身份携带资产引用 + requiredness 可评估
    manifest = build_product_manifest(
        spec_replayed, export_format="png",
        diagnostics=[{"code": "placeholder_hidden"}])
    comp = manifest["composition"]
    assert comp["template_id"] == asset_id
    assert comp["asset_id"] == asset_id
    assert comp["asset_revision"] == 1
    report = verify_export_semantic_consistency(
        spec_replayed, export_format="png",
        diagnostics=[{"code": "placeholder_hidden"}])
    aspects = {c["aspect"]: c["status"] for c in report["checks"]}
    # 4 族模板 required 槽均已被 apply 物化 → requiredness pass
    assert aspects["component_requiredness"] == "pass"


# ── 锁：user-wins 零触碰 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_locked_component_zero_touch_on_apply(registry, clean_session):
    session_id = clean_session
    await _apply(registry, session_id, "asset.builtin.heat_stats_portfolio")
    spec = await mapspec_store.get_mapspec(session_id)
    # 用户锁定 title（W15 单一事实：workbench.lockedComponentIds，经
    # save_mapspec 全量落盘——layout_set 不携带 workbench 分支）
    spec.setdefault("workbench", {})["lockedComponentIds"] = ["title"]
    title = next(c for c in spec["layout"]["components"]
                 if c["type"] == "title")
    title["options"] = {"text": "锁定后用户改"}
    await mapspec_store.save_mapspec(session_id, spec)
    # 新 apply：预检见锁 → component_locked:user_wins 拒绝落盘
    res = await registry.dispatch(
        "webgis_apply_composition",
        {"template_asset_id": "asset.builtin.heat_stats_portfolio"},
        session_id=session_id)
    assert res["success"] is False
    assert res["reason_codes"] == ["component_locked:user_wins"]
    assert "title" in res.get("locked_component_ids", [])
    # 会话状态未被触碰（用户编辑保留）
    spec_after = await mapspec_store.get_mapspec(session_id)
    title_after = next(c for c in spec_after["layout"]["components"]
                       if c["type"] == "title")
    assert title_after["options"]["text"] == "锁定后用户改"


# ── 钉版本重放 + CAS ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pinned_revision_and_cas_superseded(registry, clean_session,
                                                  db_assets, caller):
    session_id = clean_session
    session = db_assets
    from app.lib.cartography.template_assets import TemplateAssetPayload
    asset, _ = svc.create_asset(
        session, payload=TemplateAssetPayload(
            name="重放资产", base_template_id="composition.density_map",
            slot_overrides={"chart_panel": {"max_count": 2}}),
        creator_id="u1", org_id=1, publish=True)
    session.commit()
    # r2：收紧 chart_panel 为 forbidden
    svc.new_version(
        session, asset.id, TemplateAssetPayload(
            name="重放资产", base_template_id="composition.density_map",
            slot_overrides={"chart_panel": {"max_count": 0,
                                            "cardinality": "forbidden"}}),
        user_id="u1", org_id=1, publish=True)
    session.commit()

    # 钉 r1 应用 → chart 物化（重放语义）
    res_r1 = await registry.dispatch(
        "webgis_apply_composition",
        {"template_asset_id": asset.id, "template_asset_revision": 1},
        session_id=session_id)
    assert res_r1["success"] is True, res_r1
    assert res_r1["report"]["identity"]["asset_revision"] == 1
    spec = await mapspec_store.get_mapspec(session_id)
    assert any(c["type"] == "chart_panel" for c in spec["layout"]["components"])

    # CAS：并发突变后过期 expected_revision → superseded（reason code）
    stale = max(0, int(res_r1.get("mutation_revision") or 0) - 1)
    await mapspec_store.layout_set(
        session_id, components=spec["layout"]["components"],
        origin="user", actor="cas_setup")
    res_cas = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic",
         "expected_revision": stale},
        session_id=session_id)
    assert res_cas["success"] is False
    assert res_cas["reason_codes"] == ["apply_superseded"]


@pytest.fixture
def db_assets(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401
    from app.core.database import Base

    engine = create_engine(f"sqlite:///{tmp_path}/g04w3.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(
        "app.core.database.SessionLocal", lambda: Session())
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def caller():
    from app.services.provenance.context import (
        ToolExecutionContext,
        reset_tool_execution_context,
        set_tool_execution_context,
    )
    token = set_tool_execution_context(
        ToolExecutionContext(user_id="u1", org_id=1))
    yield token
    reset_tool_execution_context(token)


# ── DB 资产显式模板注入 manifest（不进注册表） ───────────────────────────


def test_manifest_requiredness_with_extra_template():
    """DB 资产模板经 extra_templates 显式注入 → requiredness 可评估。"""
    from app.lib.cartography.template_assets import (
        TemplateAssetPayload,
        TemplateAssetRecord,
    )

    record = TemplateAssetRecord(
        asset_id="asset.manifest_extra",
        payload=TemplateAssetPayload(
            base_template_id="composition.density_map",
            slot_overrides={"colorbar": {"cardinality": "required",
                                         "required": True}}),
    )
    tpl = materialize_template(record)
    # 构造已应用该资产模板的 spec（colorbar required + 在场）
    spec = _minimal_spec()
    spec["layout"] = {
        "components": [
            {"id": "title", "type": "title", "enabled": True},
            {"id": "continuous_colorbar", "type": "continuous_colorbar",
             "enabled": True},
            {"id": "scale_bar", "type": "scale_bar", "enabled": True},
            {"id": "attribution", "type": "attribution", "enabled": True},
            {"id": "north_arrow", "type": "north_arrow", "enabled": True},
        ],
        "component_links": [],
        "composition": {
            "template_id": "asset.manifest_extra",
            "template_version": "asset-r1",
            "asset_id": "asset.manifest_extra",
            "asset_revision": 1,
        },
    }
    report = verify_export_semantic_consistency(
        spec, export_format="png", diagnostics=[],
        extra_templates={"asset.manifest_extra": tpl})
    aspects = {c["aspect"]: c["status"] for c in report["checks"]}
    assert aspects["component_requiredness"] == "pass"
    # 无注入 → not_evaluated（诚实降级，不冒充）
    report2 = verify_export_semantic_consistency(
        spec, export_format="png", diagnostics=[])
    aspects2 = {c["aspect"]: c["status"] for c in report2["checks"]}
    assert aspects2["component_requiredness"] == "not_evaluated"
    # manifest 投影携带资产引用
    manifest = build_product_manifest(spec, export_format="png")
    assert manifest["composition"]["asset_id"] == "asset.manifest_extra"
    assert manifest["composition"]["asset_revision"] == 1


# ── 失败注入：非法载荷/坏身份块/恢复语义 ──────────────────────────────────


@pytest.mark.asyncio
async def test_corrupt_identity_block_tolerated(registry, clean_session):
    """身份块损坏 → 宽容读 None，不阻塞会话。"""
    await _apply(registry, clean_session,
                 "asset.builtin.heat_stats_portfolio")
    spec = await mapspec_store.get_mapspec(clean_session)
    spec["layout"]["composition"] = {"template_id": 12345}  # 坏类型
    await mapspec_store.layout_set(
        clean_session,
        composition=spec["layout"]["composition"],
        components=spec["layout"]["components"],
        origin="user", actor="corrupt")
    reloaded = await mapspec_store.get_mapspec(clean_session)
    # read_composition_identity 宽容处理（值被规范化或 None，不崩）
    assert read_composition_identity(reloaded) is None or isinstance(
        read_composition_identity(reloaded).template_id, str)


@pytest.mark.asyncio
async def test_apply_after_retire_restores_flow(db_assets, caller):
    """退役 → restore（新 revision）→ current 应用恢复。"""
    from app.lib.cartography.template_assets import TemplateAssetPayload
    session = db_assets
    asset, _ = svc.create_asset(
        session, payload=TemplateAssetPayload(
            base_template_id="composition.density_map"),
        creator_id="u1", org_id=1, publish=True)
    session.commit()
    svc.retire_asset(session, asset.id, user_id="u1", org_id=1)
    session.commit()
    with pytest.raises(svc.CompositionAssetError) as ei:
        svc.resolve_asset_for(session, asset.id, user_id="u1", org_id=1)
    assert ei.value.reason == "asset_retired"
    restored = svc.restore_revision(
        session, asset.id, 1, user_id="u1", org_id=1)
    session.commit()
    assert restored.revision == 2
    resolved = svc.resolve_asset_for(
        session, asset.id, user_id="u1", org_id=1)
    assert resolved.revision == 2


def test_payload_variant_diff_minimal_and_fingerprint_sensitive():
    """参数绑定变体：payload 内容变化 → 指纹变化（typed diff 基础）。"""
    from app.lib.cartography.template_assets import (
        TemplateAssetPayload,
        TemplateAssetRecord,
        asset_fingerprint,
        materialize_template,
    )
    base = TemplateAssetRecord(
        asset_id="asset.fp_test",
        payload=TemplateAssetPayload(
            base_template_id="composition.density_map",
            slot_overrides={"chart_panel": {"max_count": 2}}))
    changed = TemplateAssetRecord(
        asset_id="asset.fp_test",
        payload=TemplateAssetPayload(
            base_template_id="composition.density_map",
            slot_overrides={"chart_panel": {"max_count": 3}}))
    assert asset_fingerprint(base) != asset_fingerprint(changed)
    t1 = materialize_template(base)
    t2 = materialize_template(changed)
    s1 = next(s for s in t1.component_slots if s.id == "chart_panel")
    s2 = next(s for s in t2.component_slots if s.id == "chart_panel")
    assert s1.max_count == 2 and s2.max_count == 3


def test_family_templates_satisfy_required_slots_after_apply():
    """4 族模板：apply 到空 spec 后 required 槽全部在场（apply 物化语义）。"""
    for rec in BUILTIN_TEMPLATE_ASSETS:
        tpl = materialize_template(rec)
        from app.lib.cartography.template_assets import derive_contract
        contract = derive_contract(rec, tpl)
        new_spec, report = apply_contract(_minimal_spec(), contract)
        present = {c["type"] for c in new_spec["layout"]["components"]}
        for slot in tpl.component_slots:
            if slot.cardinality == "required":
                assert present & set(slot.allowed_component_types), (
                    f"{rec.asset_id}: required slot {slot.id} 未物化")
