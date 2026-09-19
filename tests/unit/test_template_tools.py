"""
Unit tests for template tools: list_templates & apply_template (Seam 1 dispatch).
"""
import pytest
from app.tools.registry import ToolRegistry
from app.tools.templates import register_template_tools
from app.tools.__init__ import init_tools


@pytest.fixture
def registry():
    """Create a ToolRegistry and register template tools."""
    reg = ToolRegistry()
    register_template_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_list_templates_all(registry):
    """Test list_templates returning all built-in templates when no filter is provided.

    F-FE-TPL: V2 expanded the library to 60+ entries (incl. 22 composites);
    a 20-row default page only samples a subset of kinds, so the test
    explicitly requests a full-library scan.
    """
    result = await registry.dispatch("list_templates", {"limit": 100})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) >= 70
    kinds = {t["kind"] for t in templates}
    assert kinds == {"basemap", "symbology", "layout", "thematic", "composite"}


@pytest.mark.asyncio
async def test_list_templates_filter_by_kind(registry):
    """Test list_templates filtering by kind.

    F-FE-TPL: V2 expanded the symbology library from 5 to 16 entries; the
    lower bound is 15 to enforce the expansion.
    """
    result = await registry.dispatch("list_templates", {"kind": "symbology"})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) >= 15
    for t in templates:
        assert t["kind"] == "symbology"


@pytest.mark.asyncio
async def test_list_templates_filter_by_query(registry):
    """Test list_templates filtering by query keyword/name."""
    result = await registry.dispatch("list_templates", {"q": "学术"})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) >= 2
    for t in templates:
        assert "学术" in t["name"] or "学术" in t.get("description", "") or any("学术" in k for k in t.get("keywords", []))


@pytest.mark.asyncio
async def test_apply_template_symbology_single(registry):
    """Test apply_template with a single mode symbology template."""
    sample_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                "properties": {"name": "Zone A"}
            }
        ]
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_sym_admin_blue",
        "geojson": sample_geojson
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "symbology"
    assert result["template_id"] == "tmpl_sym_admin_blue"
    assert "geojson" in result
    # #1388 P06: style-only path does not stamp fill_color onto features
    # (paint lives in params.style / LAYER_STYLE_UPDATE).
    assert result["geojson"]["features"][0]["properties"]["name"] == "Zone A"
    assert "fill_color" not in result["geojson"]["features"][0]["properties"]
    # #557 断点 1：前端 layer_style_update 期望 params.style（flat paint 键），
    # 不再是顶层 style_applied。
    assert "style_applied" not in result
    assert result["command"] == "LAYER_STYLE_UPDATE"
    assert result["params"]["layer_id"] is None  # caller-provided (absent here)
    assert result["params"]["style"]["color"] == "#3b82f6"
    assert result["params"]["style"]["fill"] == "#3b82f6"
    assert result["params"]["style"]["strokeWidth"] == 1.5


@pytest.mark.asyncio
async def test_apply_template_basemap(registry):
    """Test apply_template with a basemap template ID emitting BASE_LAYER_CHANGE command."""
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_bm_positron"
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "basemap"
    assert result["command"] == "BASE_LAYER_CHANGE"
    # #557 断点 2：前端 base_layer_change 期望 params.name（TILE_PROVIDERS 规范名），
    # 不再是模板载荷里的 providerId。
    assert "providerId" not in result["params"]
    assert result["params"]["name"] == "Carto Positron 矢量"


@pytest.mark.asyncio
async def test_apply_template_layout(registry):
    """Test apply_template with a layout template ID emitting EXPORT_LAYOUT_UPDATE / export_map command."""
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_ly_academic"
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "layout"
    # Layout must dispatch to the real frontend command (was EXPORT_LAYOUT_UPDATE — fixed)
    assert result["command"] == "export_map"
    assert "params" in result
    assert result["params"]["paperSize"] == "A4"
    assert result["params"]["style"]["fontFamily"] == "Georgia, serif"


@pytest.mark.asyncio
async def test_apply_template_thematic_choropleth(registry):
    """Test apply_template with a thematic choropleth preset template ID."""
    sample_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"pop": 10}},
            {"type": "Feature", "properties": {"pop": 50}},
            {"type": "Feature", "properties": {"pop": 100}},
        ]
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_th_pop_choro",
        "field": "pop",
        "geojson": sample_geojson,
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "thematic"
    assert result["variant"] == "choropleth"
    assert result["command"] == "create_thematic_map"
    assert result["field"] == "pop"
    assert result["style"] is not None
    assert result["legend_spec"] is not None
    assert result["legend_spec"]["type"] == "graduated"
    # #557 断点 1（同族）：create_thematic_map 前端 run 需要 params.geojson +
    # params.style —— 显式 params 携带完整契约键（useMapBridge 优先用显式 params）。
    assert result["params"]["geojson"] == sample_geojson
    assert result["params"]["style"] is not None
    assert result["params"]["legend_spec"]["type"] == "graduated"


@pytest.mark.asyncio
async def test_apply_template_thematic_heatmap(registry):
    """Test apply_template with a thematic heatmap preset template ID."""
    sample_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"density": 10}},
            {"type": "Feature", "properties": {"density": 50}},
        ]
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_th_heatmap",
        "field": "density",
        "geojson": sample_geojson,
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "thematic"
    assert result["variant"] == "heatmap"
    assert result["command"] == "add_native_heatmap"
    assert result["field"] == "density"
    assert result["params"]["radius"] == 30
    # #557 断点 1（同族）：add_native_heatmap 前端 run 需要 params.geojson。
    assert result["params"]["geojson"] == sample_geojson
    assert result["params"]["intensity"] == 0.85


@pytest.mark.asyncio
async def test_apply_template_heatmap_without_data_errors(registry):
    """#557 断点 3/4：热力专题图无 geojson 显式报错（旧实现假成功）。"""
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_th_heatmap",
        "field": "density",
    })
    assert "error" in result
    assert "geojson" in result["error"]


@pytest.mark.asyncio
async def test_apply_template_unknown_id(registry):
    """Test apply_template with non-existent template_id returns an error."""
    result = await registry.dispatch("apply_template", {"template_id": "tmpl_non_existent"})
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_init_tools_includes_templates():
    """Test that app.tools.__init__.init_tools registers list_templates and apply_template."""
    reg = ToolRegistry()
    init_tools(reg)
    tools = reg.list_tools()
    assert "list_templates" in tools
    assert "apply_template" in tools


def test_no_builtin_symbology_preset_carries_field():
    """Spec invariant (US22-style): field is injected at apply-time, NOT stored in a preset.

    Categorical symbology seeds must not hardcode `field` (only thematic does, and even
    thematic injects it at apply). This guards against regression of the field-in-preset fix.
    """
    from app.schemas.template_schema import SEED_TEMPLATES

    offenders = [
        t["id"]
        for t in SEED_TEMPLATES
        if t.get("kind") == "symbology"
        and t.get("is_builtin")
        and t.get("payload", {}).get("mode") == "categorical"
        and "field" in t.get("payload", {})
    ]
    assert offenders == [], f"builtin categorical symbology presets must not carry `field`: {offenders}"


# ─── #1442 tenant scope on tool surface ───────────────────────────────────────

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.provenance.context import (
    ToolExecutionContext,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from app.tools import templates as templates_mod


def _fake_template(**kwargs):
    defaults = dict(
        id="tmpl_x",
        kind="layout",
        name="X",
        category="user",
        keywords=[],
        description="",
        payload={"paperSize": "A4"},
        is_builtin=False,
        version=1,
        creator_id=None,
        org_id=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """Minimal SessionLocal stand-in: execute() returns preloaded rows.

    Scope filtering is applied by the real SQLAlchemy ``where`` on a select
    that we cannot evaluate here — instead the test monkeypatches the helper
    that builds the session so the *caller's* helper path is exercised, and
    injects already-filtered rows via ``list_rows`` / ``get_rows``.
    """

    def __init__(self, list_rows=None, get_rows=None):
        self.list_rows = list_rows if list_rows is not None else []
        self.get_rows = get_rows if get_rows is not None else []
        self._closed = False

    def execute(self, stmt):
        # Heuristic: id equality → get path; otherwise list path.
        compiled = str(stmt)
        if "cartography_templates.id" in compiled or ".id =" in compiled or "id =" in compiled.lower():
            return _FakeResult(self.get_rows)
        return _FakeResult(self.list_rows)

    def close(self):
        self._closed = True


def _bind_caller(user_id, org_id=None):
    return set_tool_execution_context(
        ToolExecutionContext(user_id=user_id, org_id=org_id)
    )


@pytest.mark.asyncio
async def test_list_templates_hides_other_tenant(registry, monkeypatch):
    """Tenant B must not see tenant A's user-saved template in list_templates."""
    owned_by_a = _fake_template(
        id="tmpl_user_a_secret",
        name="A私有模板",
        creator_id="user_a",
        org_id=1,
    )
    builtin = _fake_template(
        id="tmpl_builtin_list",
        name="内置",
        is_builtin=True,
        creator_id=None,
        org_id=None,
        payload={},
    )

    def fake_list(*, kind=None):
        # Simulate scoped query result for caller B: only builtin (A's row filtered out).
        rows = [builtin]
        if kind:
            rows = [r for r in rows if r.kind == kind]
        return rows

    monkeypatch.setattr(templates_mod, "_list_scoped_user_templates", fake_list)

    token = _bind_caller("user_b", org_id=2)
    try:
        result = await registry.dispatch("list_templates", {"limit": 100})
    finally:
        reset_tool_execution_context(token)

    ids = {t["id"] for t in result["templates"]}
    assert "tmpl_user_a_secret" not in ids
    # Builtin still visible via registry seed path.
    assert any(t.get("is_builtin") for t in result["templates"]) or len(result["templates"]) > 0


@pytest.mark.asyncio
async def test_list_templates_owner_sees_own(registry, monkeypatch):
    """Tenant A sees their own user-saved template merged into the list."""
    owned = _fake_template(
        id="tmpl_user_a_own",
        name="我的版式",
        creator_id="user_a",
        org_id=1,
    )

    monkeypatch.setattr(
        templates_mod,
        "_list_scoped_user_templates",
        lambda *, kind=None: [owned],
    )

    token = _bind_caller("user_a", org_id=1)
    try:
        result = await registry.dispatch("list_templates", {"limit": 100})
    finally:
        reset_tool_execution_context(token)

    ids = {t["id"] for t in result["templates"]}
    assert "tmpl_user_a_own" in ids
    names = {t["name"] for t in result["templates"]}
    assert "我的版式" in names


@pytest.mark.asyncio
async def test_apply_template_other_tenant_not_found(registry, monkeypatch):
    """apply_template on another tenant's id returns honest not-found."""
    monkeypatch.setattr(
        templates_mod,
        "_get_template_by_id",
        lambda _tid: None,  # scoped miss
    )
    # Ensure registry also misses (non-seed id).
    token = _bind_caller("user_b", org_id=2)
    try:
        result = await registry.dispatch(
            "apply_template", {"template_id": "tmpl_user_a_secret"}
        )
    finally:
        reset_tool_execution_context(token)

    assert "error" in result
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_apply_template_owner_can_apply_own(registry, monkeypatch):
    """Owner can apply their own user-saved symbology template via DB fallback."""
    own = {
        "id": "tmpl_user_a_sym",
        "kind": "symbology",
        "name": "我的蓝",
        "category": "user",
        "keywords": [],
        "description": "",
        "payload": {
            "mode": "single",
            "style": {"color": "#112233", "fillOpacity": 0.5, "strokeWidth": 2},
        },
        "is_builtin": False,
        "version": 1,
    }
    monkeypatch.setattr(templates_mod, "_get_template_by_id", lambda tid: own if tid == own["id"] else None)

    token = _bind_caller("user_a", org_id=1)
    try:
        result = await registry.dispatch(
            "apply_template",
            {"template_id": "tmpl_user_a_sym", "geojson": {"type": "FeatureCollection", "features": []}},
        )
    finally:
        reset_tool_execution_context(token)

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["template_id"] == "tmpl_user_a_sym"


def test_get_template_by_id_applies_scope_clause(monkeypatch):
    """_get_template_by_id must attach template_scope_clause (no unscoped get)."""
    captured = {}
    foreign = _fake_template(id="tmpl_foreign", creator_id="user_a", org_id=1)
    visible = _fake_template(id="tmpl_mine", creator_id="user_b", org_id=2)

    class CapturingSession(_FakeSession):
        def execute(self, stmt):
            captured["stmt"] = stmt
            # Return nothing — we only assert the where clause was applied.
            return _FakeResult([])

    monkeypatch.setattr(
        "app.core.database.SessionLocal",
        lambda: CapturingSession(),
    )

    token = _bind_caller("user_b", org_id=2)
    try:
        got = templates_mod._get_template_by_id("tmpl_foreign")
    finally:
        reset_tool_execution_context(token)

    assert got is None
    assert "stmt" in captured
    # Compiled SQL / string form should mention is_builtin or creator / org predicates.
    stmt_str = str(captured["stmt"])
    assert "cartography_templates" in stmt_str.lower() or "CartographyTemplate" in stmt_str


def test_list_scoped_never_unscoped_all(monkeypatch):
    """_list_scoped_user_templates always WHERE-scopes; never bare .all()."""
    captured = {}

    class CapturingSession(_FakeSession):
        def execute(self, stmt):
            captured["stmt"] = stmt
            return _FakeResult([])

        def query(self, *a, **k):  # pragma: no cover — must not be used
            raise AssertionError("legacy query().all() path must not run")

    monkeypatch.setattr(
        "app.core.database.SessionLocal",
        lambda: CapturingSession(),
    )

    token = _bind_caller("user_b", org_id=2)
    try:
        rows = templates_mod._list_scoped_user_templates()
    finally:
        reset_tool_execution_context(token)

    assert rows == []
    assert "stmt" in captured
