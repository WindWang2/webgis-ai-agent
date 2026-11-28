"""WP4 可访问等价信息测试 —— accessibility_manifest 在发布链的诚实投影。

红线：色觉检验结果必须真实（checked=false 如实存在）；清单完整性与
缺失项随读者 doc 下发；alt text 缺省确定性合成（Agent 建议可覆盖）。
"""
from __future__ import annotations

import pytest

from tests.unit.publication.conftest import (
    OWNER_TOKEN_A,
    SESSION_A,
)

pytestmark = pytest.mark.asyncio


async def test_accessibility_manifest_carried_in_snapshot(world):
    from app.services.publication.service import create_draft

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "可访问性图", "license": "CC0-SIMULATED"},
            alt_text="区县学校分布专题图：合成数据演示（作者撰写）",
        )
        a11y = row.snapshot["accessibility"]
        assert a11y["version"] == 1
        # 作者撰写的 alt 优先于确定性合成
        assert a11y["altText"] == "区县学校分布专题图：合成数据演示（作者撰写）"
        # 图层标签来自 legend title（确定性回退链的实证）
        assert "区县" in a11y["layerLabels"]
        assert "学校" in a11y["layerLabels"]
        # 投影随结果
        assert a11y["projection"] == "EPSG:3857"
        # 数据来源随 MapSpec sources.attribution
        assert all("合成" in s["attribution"] for s in a11y["dataSources"])


async def test_colorblind_check_honest(world):
    """色觉声明必须来自 context_matrix 实测；不可得时 checked=False 如实存在。"""
    from app.services.publication.service import create_draft

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "色觉图", "license": "CC0-SIMULATED"},
            palette="viridis",
        )
        colorblind = row.snapshot["accessibility"]["colorblindSafety"]
        assert colorblind["palette"] == "viridis"
        # 无论实测通过与否，declaration 必须非空且 checked 反映真实校验状态
        assert colorblind["declaration"]
        assert isinstance(colorblind["checked"], bool)
        if colorblind["checked"]:
            assert isinstance(colorblind["separable"], bool)


async def test_reader_doc_carries_equivalent_info(world):
    """读者 doc 携带全部等价信息（WP4 投影面）。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user_optional
    from tests.unit.publication.conftest import create_published_publication, make_app

    published = await create_published_publication(world["db_factory"])
    app = make_app(world["db_factory"])

    async def _anon():
        return {"user_id": None, "org_id": None}

    app.dependency_overrides[get_current_user_optional] = _anon
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v1/publications/shared/{published['share_code']}")
        doc = resp.json()["data"]
        # WP4 等价信息面完整
        assert doc["accessibility"]["altText"]
        assert doc["accessibility"]["projection"]
        assert isinstance(doc["accessibility"]["complete"], bool)
        assert doc["meta"]["license"] == "CC0-SIMULATED"
        assert doc["scope"]["geometry_precision"] >= 0
        # QA 摘要含可访问性检查项（不虚报合规）
        checks = dict(doc["qa"]["checks"])
        assert "accessibility_manifest" in checks
        assert checks["accessibility_manifest"] in ("complete", "incomplete")


async def test_alt_text_agent_suggestion_flow(world):
    """alt_text 参数即 Agent 建议入口：缺省走确定性合成。"""
    from app.lib.cartography.accessibility_manifest import build_accessibility_manifest
    from app.services.publication.service import create_draft

    # 缺省合成（确定性；标题族+图层标签）
    manifest = build_accessibility_manifest(
        {
            "layout": {"title": "T"},
            "layers": [{"id": "l1", "legend": {"title": "L"}}],
            "sources": [{"name": "s1", "attribution": "测试署名"}],
            "view": {"projection": "EPSG:3857"},
        }
    )
    assert "T" in manifest["altText"] and "L" in manifest["altText"]
    assert manifest["missing"] == [] and manifest["complete"] is True

    async with world["db_factory"]() as db:
        row = await create_draft(
            db,
            session_id=SESSION_A,
            owner_token=OWNER_TOKEN_A,
            creator_id=None,
            org_id=None,
            meta_params={"title": "合成回退图", "license": "CC0-SIMULATED"},
        )
        # 未给 alt_text → 确定性合成（含源 spec 标题族 + 图层标签）
        alt = row.snapshot["accessibility"]["altText"]
        assert "区县学校分布专题图" in alt  # spec.layout.title
        assert "图层" in alt
