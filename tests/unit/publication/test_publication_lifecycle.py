"""WP2 生命周期与读者授权集成测试。

旅程骨架（必过旅程 1/2 的后端半边）：
- 作者 draft → publish（executor 物化 CAS）→ link → 读者访问；
- 撤销/过期/未发布/错 code/跨 owner 一律 404/403；
- 字段白名单纵深防御（物化文件不含白名单外字段；查询面 field_not_allowed）；
- 版本不可变：发布后编辑原图，旧版本快照不变；新版本显式发布。
"""
from __future__ import annotations

import asyncio

import pytest

from tests.unit.publication.conftest import (
    DISTRICTS_FC,
    OWNER_TOKEN_A,
    OWNER_TOKEN_B,
    SCHOOL_MAPSPEC,
    SCHOOLS_FC,
    SESSION_A,
    create_published_publication,
)

pytestmark = pytest.mark.asyncio


async def test_draft_create_and_read_world(world):
    from app.services.publication.service import create_draft

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "区县学校分布专题图", "license": "CC0-SIMULATED"},
        )
        assert row.status == "draft"
        assert row.access_policy == "private"
        assert row.share_code is None
        snapshot = row.snapshot
        assert snapshot["spec_version"] == "publication_snapshot.v1"
        assert snapshot["meta"]["license"] == "CC0-SIMULATED"
        layer_ids = [lyr["layer_id"] for lyr in snapshot["layers"]]
        assert layer_ids == ["lyr-districts", "lyr-schools"]
        # 字段白名单来自真实数据采样（内部预算字段在候选里，但发布选择可控）
        schools = snapshot["layers"][1]
        assert "school_name" in schools["field_whitelist"]
        assert snapshot["accessibility"]["altText"]
        assert snapshot["qa"]["spec_validity"] == "schema_valid"


async def test_publish_flow_and_reader_journey(world, monkeypatch):
    """必过旅程 1 后端半边：draft→publish→link→读者读图/读数/统计。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user_optional
    from tests.unit.publication.conftest import make_app

    published = await create_published_publication(world["db_factory"])
    assert published["executor_result"]["status"] == "published"
    assert published["executor_result"]["layers"]["lyr-schools"] > 0
    code = published["share_code"]

    app = make_app(world["db_factory"])

    async def _anon():
        return {"user_id": None, "org_id": None}

    app.dependency_overrides[get_current_user_optional] = _anon
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 读者元数据（等价信息投影）
        resp = await client.get(f"/api/v1/publications/shared/{code}")
        assert resp.status_code == 200
        doc = resp.json()["data"]
        assert doc["meta"]["title"] == "区县学校分布专题图"
        assert doc["scope"]["crs"] == "EPSG:3857"
        assert any(lyr["layer_id"] == "lyr-schools" for lyr in doc["layers"])
        assert doc["accessibility"]["altText"]

        # 读者 map spec：源已重写为读者端点；无 ref:/ref_id 会话载体残留
        resp = await client.get(f"/api/v1/publications/shared/{code}/map")
        assert resp.status_code == 200
        reader_spec = resp.json()["data"]["spec"]
        assert reader_spec["sources"]["schools"]["url"].endswith(
            f"/shared/{code}/layers/lyr-schools/data"
        )
        assert "ref_id" not in reader_spec["sources"]["schools"]

        # 层数据：物化 GeoJSON 不含白名单外字段（写时投影）
        resp = await client.get(f"/api/v1/publications/shared/{code}/layers/lyr-schools/data")
        assert resp.status_code == 200
        fc = resp.json()
        props = fc["features"][0]["properties"]
        assert "school_name" in props
        assert "internal_budget" not in props
        assert resp.headers.get("x-content-type-options") == "nosniff"

        # 要素分页 + 白名单筛选 + 显式分母
        resp = await client.get(
            f"/api/v1/publications/shared/{code}/layers/lyr-schools/features"
            "?limit=5&where=" + '%7B%22field%22%3A%22students%22%2C%22op%22%3A%22gte%22%2C%22value%22%3A420%7D'
        )
        assert resp.status_code == 200
        page = resp.json()["data"]
        assert page["denominator"]["total_features"] == 12
        assert page["denominator"]["matching_features"] is not None

        # summary 统计（显式分母）
        resp = await client.get(
            f"/api/v1/publications/shared/{code}/summary?layer_id=lyr-schools&field=students"
        )
        assert resp.status_code == 200
        summary = resp.json()["data"]
        assert summary["stats"]["count"] == 12
        assert summary["denominator"]["total_features"] == 12
        assert summary["denominator"]["matching_features"] == 12


async def test_reader_negatives_all_404(world):
    """未发布/撤销/错 code → 统一 404（不泄露状态差异）。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user_optional
    from app.services.publication.service import create_draft, revoke_publication
    from app.services.publication.store import get_publication
    from tests.unit.publication.conftest import make_app

    async with world["db_factory"]() as db:
        draft = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "草稿图", "license": "CC0-SIMULATED"},
        )
        draft_id = draft.id

    published = await create_published_publication(world["db_factory"])
    good_code = published["share_code"]

    # 撤销 good 链接
    async with world["db_factory"]() as db:
        row = await get_publication(db, published["row_id"])
        assert await revoke_publication(db, row) is True

    app = make_app(world["db_factory"])

    async def _anon():
        return {"user_id": None, "org_id": None}

    app.dependency_overrides[get_current_user_optional] = _anon
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in (
            f"/api/v1/publications/shared/{good_code}",  # 已撤销
            "/api/v1/publications/shared/nonexistent-code",
            "/api/v1/publications/shared/short",
        ):
            resp = await client.get(path)
            assert resp.status_code == 404, path

        # draft（未发布）无 link → 读者面不可达；直接以 draft 的 id 走作者面
        # 校验 status=draft 未被读者端点泄露
        async with world["db_factory"]() as db:
            row = await get_publication(db, draft_id)
            assert row.status == "draft"
            assert row.share_code is None


async def test_cross_owner_author_ops_denied(world):
    """作者面跨 owner 负例：B 的 token 操作 A 的发布 → 403。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user_optional, get_owner_token
    from tests.unit.publication.conftest import make_app

    published = await create_published_publication(world["db_factory"])

    app = make_app(world["db_factory"])

    async def _user_b():
        return {"user_id": None, "org_id": None}

    async def _token_b():
        return OWNER_TOKEN_B

    app.dependency_overrides[get_current_user_optional] = _user_b
    app.dependency_overrides[get_owner_token] = _token_b
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/publications/{published['row_id']}/revoke", json={}
        )
        assert resp.status_code == 403
        resp = await client.post(
            f"/api/v1/publications/{published['row_id']}/link", json={"ttl_days": 3}
        )
        assert resp.status_code == 403
        resp = await client.get(f"/api/v1/publications/{published['row_id']}")
        assert resp.status_code == 403
        # 撤销未被执行（负例后状态不变）
        from app.services.publication.store import get_publication

        async with world["db_factory"]() as db:
            row = await get_publication(db, published["row_id"])
            assert row.status == "published"


async def test_publish_requires_explicit_confirm(world, monkeypatch):
    """publish 端点：confirm 缺失 → typed 400；不许静默发布。"""

    from app.services.publication.service import create_draft

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "确认门图", "license": "CC0-SIMULATED"},
        )
        draft_id = row.id

    from tests.unit.publication.author_client import author_request

    status, body = await author_request(
        world, "POST", f"/api/v1/publications/{draft_id}/publish", json={"confirm": False}
    )
    assert status == 400
    assert body["detail"]["reason"] == "confirmation_required"


async def test_publish_blocked_without_license(world):
    """版权不明（license 空）→ 发布 422 typed 阻断。"""

    from app.services.publication.service import create_draft

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "无许可图", "license": ""},
        )
        draft_id = row.id

    from tests.unit.publication.author_client import author_request

    status, body = await author_request(
        world, "POST", f"/api/v1/publications/{draft_id}/publish", json={"confirm": True}
    )
    assert status == 422
    assert body["detail"]["reason"] == "license_unknown"


async def test_executor_rejects_non_draft(world):
    """幂等/防回退：published 行再次物化 → typed 拒绝（不重放 CAS）。"""
    from app.services.publication import executor

    published = await create_published_publication(world["db_factory"])
    with pytest.raises(executor.PublicationMaterializeError) as exc_info:
        await asyncio.to_thread(executor.materialize_publication, published["row_id"])
    assert exc_info.value.reason == "not_draft"


async def test_version_immutability_and_explicit_v2(world, monkeypatch):
    """必过旅程 2 前半：编辑原图后旧版本不变；新版本显式创建。"""
    from app.services.publication.service import create_draft
    from app.services.publication.store import get_publication

    v1 = await create_published_publication(world["db_factory"])

    async with world["db_factory"]() as db:
        row_v1 = await get_publication(db, v1["row_id"])
        fp_v1 = row_v1.spec_fingerprint
        snap_v1 = row_v1.snapshot

    # 作者编辑原图（spec 变更）
    from tests.unit.publication.conftest import _install_session_world

    edited = {**SCHOOL_MAPSPEC, "view": {**SCHOOL_MAPSPEC["view"], "zoom": 12}}
    _install_session_world(monkeypatch, spec=edited, refs={
        "ref:schools-1": SCHOOLS_FC,
        "ref:districts-1": DISTRICTS_FC,
    })

    async with world["db_factory"]() as db:
        # v1 不可变
        again = await get_publication(db, v1["row_id"])
        assert again.spec_fingerprint == fp_v1
        assert again.snapshot == snap_v1

        # 显式新版本（base_publication_id）
        v2 = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={},
            base_publication_id=v1["row_id"],
        )
        assert v2.product_id == row_v1.product_id
        assert v2.version == 2
        assert v2.spec_fingerprint != fp_v1
        # meta 继承
        assert v2.snapshot["meta"]["license"] == "CC0-SIMULATED"
        assert v2.status == "draft"
        # 旧版本读者链接仍指向 v1（不可变语义）
        assert v1["share_code"]


async def test_link_expiry_blocks_reader(world, monkeypatch):
    """过期链接 → 读者 404（fail-closed）。"""
    from datetime import timedelta

    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user_optional
    from app.models.map_publication import utcnow_naive
    from app.services.publication.store import get_publication
    from tests.unit.publication.conftest import make_app

    published = await create_published_publication(world["db_factory"])

    async with world["db_factory"]() as db:
        row = await get_publication(db, published["row_id"])
        row.share_expires_at = utcnow_naive() - timedelta(hours=1)
        await db.commit()

    app = make_app(world["db_factory"])

    async def _anon():
        return {"user_id": None, "org_id": None}

    app.dependency_overrides[get_current_user_optional] = _anon
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v1/publications/shared/{published['share_code']}")
        assert resp.status_code == 404


async def test_field_boundary_negatives(world):
    """查询面字段边界：fields/where/order 引用白名单外字段 → 400 field_not_allowed。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user_optional
    from tests.unit.publication.conftest import make_app

    published = await create_published_publication(world["db_factory"])
    code = published["share_code"]
    base = f"/api/v1/publications/shared/{code}/layers/lyr-schools/features"

    app = make_app(world["db_factory"])

    async def _anon():
        return {"user_id": None, "org_id": None}

    app.dependency_overrides[get_current_user_optional] = _anon
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # fields 白名单外
        resp = await client.get(f"{base}?fields=internal_budget")
        assert resp.status_code == 400
        assert resp.json()["detail"]["reason"] == "field_not_allowed"
        # where 白名单外（布尔推断通道）
        resp = await client.get(
            f"{base}?where=%7B%22field%22%3A%22internal_budget%22%2C%22op%22%3A%22eq%22%2C%22value%22%3A%22x%22%7D"
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["reason"] == "field_not_allowed"
        # order 白名单外
        resp = await client.get(f"{base}?order=-internal_budget")
        assert resp.status_code == 400
        assert resp.json()["detail"]["reason"] == "field_not_allowed"
        # summary 字段白名单外
        resp = await client.get(
            f"/api/v1/publications/shared/{code}/summary?layer_id=lyr-schools&field=internal_budget"
        )
        assert resp.status_code == 400


async def test_unsupported_source_blocker(world, monkeypatch):
    """瓦片/栅格源 → typed 阻断并从读者面剔除（诚实降级）。"""
    from app.services.publication.service import create_draft

    spec = {
        **SCHOOL_MAPSPEC,
        "sources": {
            **SCHOOL_MAPSPEC["sources"],
            "hillshade": {
                "type": "raster",
                "imageRef": "ref:raster/hillshade",
                "bounds": [116.0, 39.5, 117.0, 40.5],
            },
        },
        "layers": [
            *SCHOOL_MAPSPEC["layers"],
            {"id": "lyr-hillshade", "source": "hillshade", "type": "raster"},
        ],
    }
    from tests.unit.publication.conftest import _install_session_world, SCHOOLS_FC, DISTRICTS_FC

    _install_session_world(monkeypatch, spec=spec, refs={
        "ref:schools-1": SCHOOLS_FC,
        "ref:districts-1": DISTRICTS_FC,
    })
    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "含栅格源图", "license": "CC0-SIMULATED"},
        )
        blockers = row.snapshot["blockers"]
        assert any(b["layer_id"] == "lyr-hillshade" for b in blockers)
        assert any(b["reason"] == "source_not_authorizable" for b in blockers)
        # 读者 map spec 不含栅格层
        from app.services.publication.contract import PublicationSnapshotDocument
        from app.services.publication.reader import build_reader_map_spec

        doc = PublicationSnapshotDocument(**row.snapshot)
        reader_spec, degradations = build_reader_map_spec(doc, share_code="whatever")
        layer_ids = [lyr["id"] for lyr in reader_spec["layers"]]
        assert "lyr-hillshade" not in layer_ids
        assert any(d["kind"] == "layer_dropped" and d["layer_id"] == "lyr-hillshade" for d in degradations)
        assert "hillshade" not in reader_spec["sources"]
