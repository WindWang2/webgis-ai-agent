import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.db_model import CartographyTemplate
from app.core.database import SessionLocal, Base, Engine
from app.tools.registry import ToolRegistry
from app.tools.templates import register_template_tools

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    """Ensure database tables exist and clean up user templates."""
    Base.metadata.create_all(Engine)
    db = SessionLocal()
    try:
        db.query(CartographyTemplate).filter(CartographyTemplate.is_builtin == False).delete()
        db.commit()
    finally:
        db.close()

    yield

    db = SessionLocal()
    try:
        db.query(CartographyTemplate).filter(CartographyTemplate.is_builtin == False).delete()
        db.commit()
    finally:
        db.close()


def test_create_user_template_success():
    """Test saving a new user symbology template via POST /api/v1/templates."""
    req_data = {
        "name": "自定义蓝色行政区",
        "kind": "symbology",
        "description": "用户自定义的单值蓝调填充",
        "keywords": ["用户", "自定义", "蓝色"],
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {
                "fill_color": "#1d4ed8",
                "opacity": 0.85,
                "stroke_color": "#1e3a8a",
                "stroke_width": 2.0,
            },
        },
    }

    response = client.post("/api/v1/templates", json=req_data)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "自定义蓝色行政区"
    assert data["kind"] == "symbology"
    assert data["is_builtin"] is False
    assert data["id"].startswith("tmpl_user_")


def test_create_user_template_invalid_payload():
    """Test creating template with invalid payload for kind fails with 422."""
    req_data = {
        "name": "无效图层",
        "kind": "symbology",
        "payload": {
            "mode": "single",
            # missing required fill_color
            "opacity": 0.5,
        },
    }

    response = client.post("/api/v1/templates", json=req_data)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_template_appears_in_list_templates():
    """Test that created user template immediately appears in list_templates AI tool and GET endpoint."""
    req_data = {
        "name": "我的夜间大屏底图",
        "kind": "basemap",
        "keywords": ["大屏", "夜间"],
        "payload": {
            "providerId": "carto-dark-vec",
            "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        },
    }
    post_res = client.post("/api/v1/templates", json=req_data)
    assert post_res.status_code == 201

    # Check GET endpoint
    get_res = client.get("/api/v1/templates?kind=basemap")
    assert get_res.status_code == 200
    templates_list = get_res.json()
    assert any(t["name"] == "我的夜间大屏底图" for t in templates_list)

    # Check AI tool list_templates
    reg = ToolRegistry()
    register_template_tools(reg)
    tool_res = await reg.dispatch("list_templates", {"kind": "basemap", "q": "夜间"})
    assert tool_res["count"] >= 1
    assert any(t["name"] == "我的夜间大屏底图" for t in tool_res["templates"])


def test_delete_built_in_template_forbidden():
    """Test deleting built-in template returns 403 Forbidden."""
    response = client.delete("/api/v1/templates/tmpl_bm_positron")
    assert response.status_code == 403
    assert "built-in" in response.json()["detail"].lower()


def test_delete_user_template_success():
    """Test deleting user-created template succeeds with 200."""
    req_data = {
        "name": "临时模板",
        "kind": "layout",
        "payload": {
            "paperSize": "A4",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": False,
        },
    }
    post_res = client.post("/api/v1/templates", json=req_data)
    tmpl_id = post_res.json()["id"]

    del_res = client.delete(f"/api/v1/templates/{tmpl_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"
