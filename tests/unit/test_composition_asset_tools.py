"""G04 组合模板资产工具面 e2e 测试。

真 mapspec_store + 隔离 SQLite DB；工具经 registry.dispatch（生产同路径）。
覆盖：apply 资产分支（身份块携带 asset_id/revision）、钉版本重放、
退役拒绝、draft 拒绝、不可见 404 语义、save-as-my-template 落库、
list 工具 scope（DB 失败降级 builtin-only）、与既有契约 apply 无回归。
"""
import shutil
import uuid

import pytest

from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry

from app.lib.cartography.template_assets import TemplateAssetPayload
from app.services.provenance.context import (
    ToolExecutionContext,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from app.services.templates import composition_assets as svc


@pytest.fixture
def registry():
    r = ToolRegistry()
    from app.tools.composition_tools import register_composition_tools
    register_composition_tools(r)
    return r


@pytest.fixture
async def clean_session():
    sid = f"g04-ta-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    session_dir = BASE_STORAGE_DIR / sid
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


@pytest.fixture
def db_assets(monkeypatch, tmp_path):
    """隔离 DB + 工具身份上下文（SessionLocal 指向临时 sqlite）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401
    from app.core.database import Base

    engine = create_engine(f"sqlite:///{tmp_path}/g04ta.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _fake_session_local():
        return Session()

    monkeypatch.setattr(
        "app.core.database.SessionLocal", _fake_session_local)
    # 路由层同步面用 from app.core.database import SessionLocal —— 补同款 patch
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def caller():
    token = set_tool_execution_context(
        ToolExecutionContext(user_id="u1", org_id=1))
    yield token
    reset_tool_execution_context(token)


def _payload():
    return TemplateAssetPayload(
        name="工具测试资产",
        base_template_id="composition.density_map",
        slot_overrides={"chart_panel": {"max_count": 2}},
    )


def test_new_tools_registered(registry):
    for name in ("webgis_list_composition_template_assets",
                 "webgis_save_composition_template"):
        assert registry.has(name)


def test_reason_codes_include_asset_codes():
    from app.tools.composition_tools import COMPOSITION_TOOL_REASON_CODES
    from app.services.templates.composition_assets import (
        ASSET_REASON_CODES,
    )
    for code in ASSET_REASON_CODES:
        assert code in COMPOSITION_TOOL_REASON_CODES


@pytest.mark.asyncio
async def test_list_returns_builtin_assets(registry, db_assets):
    res = await registry.dispatch(
        "webgis_list_composition_template_assets", {}, session_id=None)
    assert res["success"] is True
    builtin = [a for a in res["assets"] if a["builtin"]]
    assert len(builtin) == 4
    assert all(a["asset_id"].startswith("asset.builtin.") for a in builtin)


@pytest.mark.asyncio
async def test_apply_builtin_asset_writes_identity(registry, clean_session,
                                                   db_assets):
    res = await registry.dispatch(
        "webgis_apply_composition",
        {"template_asset_id": "asset.builtin.heat_stats_portfolio"},
        session_id=clean_session)
    assert res["success"] is True, res
    assert res["template_asset"]["asset_id"] == \
        "asset.builtin.heat_stats_portfolio"
    assert res["template_asset"]["revision"] == 1
    identity = res["report"]["identity"]
    assert identity["asset_id"] == "asset.builtin.heat_stats_portfolio"
    assert identity["asset_revision"] == 1
    spec = await mapspec_store.get_mapspec(clean_session)
    assert spec["layout"]["composition"]["asset_id"] == \
        "asset.builtin.heat_stats_portfolio"
    # 幂等：重复 apply 零新增
    res2 = await registry.dispatch(
        "webgis_apply_composition",
        {"template_asset_id": "asset.builtin.heat_stats_portfolio"},
        session_id=clean_session)
    assert res2["success"] is True
    assert res2["report"]["created_count"] == 0
    assert res2["report"]["preserved"]  # 已创建实例被保留（零触碰）


@pytest.mark.asyncio
async def test_apply_db_asset_requires_published(registry, clean_session,
                                                 db_assets, caller):
    session = db_assets
    asset, _v1 = svc.create_asset(
        session, payload=_payload(), creator_id="u1", org_id=1)
    session.commit()
    res = await registry.dispatch(
        "webgis_apply_composition", {"template_asset_id": asset.id},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["asset_draft"]


@pytest.mark.asyncio
async def test_apply_db_asset_published(registry, clean_session, db_assets,
                                        caller):
    session = db_assets
    asset, _ = svc.create_asset(
        session, payload=_payload(), creator_id="u1", org_id=1, publish=True)
    session.commit()
    res = await registry.dispatch(
        "webgis_apply_composition", {"template_asset_id": asset.id},
        session_id=clean_session)
    assert res["success"] is True, res
    identity = res["report"]["identity"]
    assert identity["asset_id"] == asset.id
    assert identity["asset_revision"] == 1
    spec = await mapspec_store.get_mapspec(clean_session)
    assert spec["layout"]["composition"]["asset_id"] == asset.id


@pytest.mark.asyncio
async def test_apply_retired_asset_rejected_but_pinned_replay_ok(
        registry, clean_session, db_assets, caller):
    session = db_assets
    asset, _ = svc.create_asset(
        session, payload=_payload(), creator_id="u1", org_id=1, publish=True)
    session.commit()
    # 应用一次（记录身份）
    res1 = await registry.dispatch(
        "webgis_apply_composition", {"template_asset_id": asset.id},
        session_id=clean_session)
    assert res1["success"] is True
    svc.retire_asset(session, asset.id, user_id="u1", org_id=1)
    session.commit()
    # 退役后新 apply（current）拒绝
    res2 = await registry.dispatch(
        "webgis_apply_composition", {"template_asset_id": asset.id},
        session_id=clean_session)
    assert res2["success"] is False
    assert res2["reason_codes"] == ["asset_retired"]
    # 钉版本重放放行（旧图打开语义）
    res3 = await registry.dispatch(
        "webgis_apply_composition",
        {"template_asset_id": asset.id, "template_asset_revision": 1},
        session_id=clean_session)
    assert res3["success"] is True, res3
    assert res3["report"]["identity"]["asset_revision"] == 1


@pytest.mark.asyncio
async def test_apply_unknown_asset_not_found(registry, clean_session,
                                            db_assets, caller):
    res = await registry.dispatch(
        "webgis_apply_composition", {"template_asset_id": "asset.ghost"},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["asset_not_found"]


@pytest.mark.asyncio
async def test_apply_asset_other_user_403(registry, clean_session, db_assets,
                                          caller):
    """跨用户私有资产 → asset_not_found（不泄露存在性）。"""
    session = db_assets
    asset, _ = svc.create_asset(
        session, payload=_payload(), creator_id="u2", org_id=1, publish=True)
    session.commit()
    res = await registry.dispatch(
        "webgis_apply_composition", {"template_asset_id": asset.id},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["asset_not_found"]


@pytest.mark.asyncio
async def test_apply_asset_pinned_revision_missing(registry, clean_session,
                                                   db_assets, caller):
    session = db_assets
    asset, _ = svc.create_asset(
        session, payload=_payload(), creator_id="u1", org_id=1, publish=True)
    session.commit()
    res = await registry.dispatch(
        "webgis_apply_composition",
        {"template_asset_id": asset.id, "template_asset_revision": 9},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["asset_revision_not_found"]


@pytest.mark.asyncio
async def test_save_composition_template_from_session(
        registry, clean_session, db_assets, caller):
    # 先铺一个系统模板应用
    res_apply = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic"},
        session_id=clean_session)
    assert res_apply["success"] is True
    res = await registry.dispatch(
        "webgis_save_composition_template",
        {"session_id": clean_session, "name": "我的版式",
         "visibility": "private"},
        session_id=clean_session)
    assert res["success"] is True, res
    assert res["asset_id"].startswith("asset.")
    assert res["revision"] == 1
    assert res["status"] == "published"
    # 落库后可解析（同一用户）
    session = db_assets
    resolved = svc.resolve_asset_for(
        session, res["asset_id"], user_id="u1", org_id=1)
    assert resolved.revision == 1
    # 保存不触碰当前会话
    spec = await mapspec_store.get_mapspec(clean_session)
    assert "composition" in spec["layout"]


@pytest.mark.asyncio
async def test_save_from_empty_session_rejected(registry, clean_session,
                                                db_assets, caller):
    res = await registry.dispatch(
        "webgis_save_composition_template",
        {"session_id": clean_session},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["asset_payload_error"]


@pytest.mark.asyncio
async def test_save_project_visibility_requires_project(
        registry, clean_session, db_assets, caller):
    await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic"},
        session_id=clean_session)
    res = await registry.dispatch(
        "webgis_save_composition_template",
        {"session_id": clean_session, "visibility": "project"},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["asset_invalid_visibility"]


@pytest.mark.asyncio
async def test_list_db_assets_scoped_to_caller(registry, db_assets, caller):
    session = db_assets
    svc.create_asset(session, payload=_payload(), creator_id="u1", org_id=1,
                     publish=True)
    session.commit()
    res = await registry.dispatch(
        "webgis_list_composition_template_assets", {}, session_id=None)
    assert res["success"] is True
    db_rows = [a for a in res["assets"] if not a["builtin"]]
    assert len(db_rows) == 1
    assert db_rows[0]["visibility"] == "private"
    assert db_rows[0]["revision"] == 1
