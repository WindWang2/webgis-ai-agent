"""R1 审查修复回归 —— P2-1~4 与可行动 P3 的关闭证据测试。

对应 REVIEW_R1.md 发现清单；每条测试名标注关闭的发现编号。
"""
from __future__ import annotations

import asyncio

import pytest

from tests.unit.publication.conftest import (
    DISTRICTS_FC,
    OWNER_TOKEN_A,
    OWNER_TOKEN_B,
    SCHOOLS_FC,
    SCHOOL_MAPSPEC,
    SESSION_A,
    create_published_publication,
)  # noqa: F401 — SCHOOL_MAPSPEC/SCHOOLS_FC 供漂移用例

pytestmark = pytest.mark.asyncio


async def test_r1_p2_1_cross_owner_base_publication_denied(world):
    """[P2-1] 跨 owner 谱系挂靠 → 403（不继承 meta、不追加版本）。"""
    from app.services.publication.service import PublicationRequestError, create_draft

    base = await create_published_publication(world["db_factory"])

    # 换作者 B 的 token 挂靠 A 的 base（他人凭证）
    from app.services.publication.store import get_publication

    async with world["db_factory"]() as db:
        with pytest.raises(PublicationRequestError) as exc_info:
            await create_draft(
                db,
                session_id=SESSION_A,
                owner_token=OWNER_TOKEN_B,  # 他人凭证
                creator_id=None,
                org_id=None,
                meta_params={},
                base_publication_id=base["row_id"],
            )
        assert exc_info.value.status_code == 403
        # 产品谱系未被追加版本（仍只有 v1）
        row = await get_publication(db, base["row_id"])
        assert row.version == 1


async def test_r1_p2_4_shared_source_per_layer_rewrite(world, monkeypatch):
    """[P2-4] 共享源多层 → 每层独立 reader source（不再错配）。"""
    from app.services.publication.contract import PublicationSnapshotDocument
    from app.services.publication.reader import build_reader_map_spec
    from app.services.publication.service import create_draft

    spec = {
        **SCHOOL_MAPSPEC,
        "sources": {
            "schools": {**SCHOOL_MAPSPEC["sources"]["schools"]},
        },
        "layers": [
            {"id": "lyr-a", "source": "schools", "type": "circle",
             "paint": {"circle-color": "#111"}, "legend": {"title": "A 层"}},
            {"id": "lyr-b", "source": "schools", "type": "circle",
             "paint": {"circle-color": "#222"}, "legend": {"title": "B 层"}},
        ],
    }
    from tests.unit.publication.conftest import _install_session_world

    _install_session_world(monkeypatch, spec=spec, refs={"ref:schools-1": SCHOOLS_FC})
    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "共享源图", "license": "CC0-SIMULATED"},
            selected_layer_ids=["lyr-a", "lyr-b"],
            field_whitelists={
                "lyr-a": ["school_name"],
                "lyr-b": ["school_name", "students"],
            },
        )
        doc = PublicationSnapshotDocument(**row.snapshot)
        reader_spec, degradations = build_reader_map_spec(doc, share_code="c-x")
        sources = reader_spec["sources"]
        # 每层独立 source，指向各自产物
        assert sources["publication:lyr-a"]["url"].endswith("/layers/lyr-a/data")
        assert sources["publication:lyr-b"]["url"].endswith("/layers/lyr-b/data")
        lyr_by_id = {lyr["id"]: lyr for lyr in reader_spec["layers"]}
        assert lyr_by_id["lyr-a"]["source"] == "publication:lyr-a"
        assert lyr_by_id["lyr-b"]["source"] == "publication:lyr-b"
        assert not any(d["kind"] == "layer_dropped" for d in degradations)


async def test_r1_p2_3_executor_gate_blocks_license(world):
    """[P2-3] 发布门在 executor CAS 单点：无 license 的草稿即使进入 job 也被阻断。"""
    from app.services.publication import executor
    from app.services.publication.service import create_draft

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "无许可直发图", "license": ""},
        )
    with pytest.raises(executor.PublicationMaterializeError) as exc_info:
        await asyncio.to_thread(executor.materialize_publication, row.id)
    assert exc_info.value.reason == "license_unknown"


async def test_r1_p3_10_snapshot_data_drift_typed_fail(world, monkeypatch):
    """[P3-10] 冻结后数据漂移 → 物化 typed 失败（分母不失真）。"""
    from app.services.publication import executor
    from app.services.publication.service import create_draft
    from tests.unit.publication.conftest import _install_session_world

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "漂移图", "license": "CC0-SIMULATED"},
        )
        draft_id = row.id
    # 草稿后编辑 ref 数据（追加要素）
    drifted_fc = {
        **SCHOOLS_FC,
        "features": [*SCHOOLS_FC["features"], dict(SCHOOLS_FC["features"][0])],
    }
    _install_session_world(monkeypatch, spec=SCHOOL_MAPSPEC, refs={
        "ref:schools-1": drifted_fc,
        "ref:districts-1": DISTRICTS_FC,
    })
    with pytest.raises(executor.PublicationMaterializeError) as exc_info:
        await asyncio.to_thread(executor.materialize_publication, draft_id)
    assert exc_info.value.reason == "snapshot_data_drift"


async def test_r1_p3_5_revoke_publish_race_no_resurrection(world, monkeypatch):
    """[P3-5] 并发 revoke × in-flight publish：DB 级 CAS 消除复活窗口。

    executor 读 draft → revoke 提交（revoked+清 code）→ executor CAS 写 0 行 →
    终态保持 revoked。
    """
    from app.services.publication.service import create_draft, revoke_publication
    from app.services.publication.store import get_publication

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "竞态图", "license": "CC0-SIMULATED"},
        )
        draft_id = row.id

    # 作者撤销（先于 executor 的 CAS 提交）
    async with world["db_factory"]() as db:
        row = await get_publication(db, draft_id)
        assert await revoke_publication(db, row) is True
    # executor 阶段：CAS 条件 UPDATE 只认 draft → 0 行 → typed 拒绝（不复活）
    from app.services.publication import executor

    with pytest.raises(executor.PublicationMaterializeError) as exc_info:
        await asyncio.to_thread(executor.materialize_publication, draft_id)
    assert exc_info.value.reason == "not_draft"


async def test_r1_p3_7_summary_stats_honesty(world):
    """[P3-7] 非数值计数 + NaN 不进统计（summary 诚实分母）。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user_optional
    from tests.unit.publication.conftest import make_app

    published = await create_published_publication(world["db_factory"])
    code = published["share_code"]
    app = make_app(world["db_factory"])

    async def _anon():
        return {"user_id": None, "org_id": None}

    app.dependency_overrides[get_current_user_optional] = _anon
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # level 是字符串字段 → 全部 non_numeric
        resp = await client.get(
            f"/api/v1/publications/shared/{code}/summary?layer_id=lyr-schools&field=level"
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["stats"]["count"] == 0
        assert body["denominator"]["non_numeric_count"] == 12
        assert body["denominator"]["numeric_count"] == 0


async def test_r1_p3_16_revoke_removes_artifacts(world):
    """[P3-16] 撤销终态清理产物目录。"""
    import os

    from app.services.publication.service import revoke_publication
    from app.services.publication.store import get_publication, publication_dir

    published = await create_published_publication(world["db_factory"])
    pid = published["row_id"]
    assert publication_dir(pid).is_dir()
    async with world["db_factory"]() as db:
        row = await get_publication(db, pid)
        assert await revoke_publication(db, row) is True
    assert not publication_dir(pid).is_dir()
    _ = os
