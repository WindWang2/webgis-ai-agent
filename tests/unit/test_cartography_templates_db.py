"""
Unit tests for CartographyTemplate SQLAlchemy model, Pydantic schemas, and seed data.
"""
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import CartographyTemplate, Layer, Organization, User
from app.schemas.template_schema import (
    CartographyTemplateCreate,
    CartographyTemplateResponse,
    BasemapPayload,
    SymbologyPayload,
    LayoutTemplatePayload,
    ThematicPresetPayload,
    SEED_TEMPLATES,
)


@pytest.fixture
def db_session():
    """Create in-memory SQLite database session for unit tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_cartography_template_model_crud(db_session):
    """Test creating, reading, and querying CartographyTemplate via SQLAlchemy."""
    template_id = f"tmpl_{uuid.uuid4().hex[:12]}"
    tmpl = CartographyTemplate(
        id=template_id,
        kind="basemap",
        name="Academic Light",
        category="basemap",
        keywords=["academic", "light", "positron"],
        description="Carto Positron vector basemap template",
        payload={
            "providerId": "carto-positron",
            "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
        },
        is_builtin=True,
        version=1,
    )
    db_session.add(tmpl)
    db_session.commit()

    fetched = db_session.query(CartographyTemplate).filter_by(id=template_id).first()
    assert fetched is not None
    assert fetched.name == "Academic Light"
    assert fetched.kind == "basemap"
    assert fetched.is_builtin is True
    assert fetched.keywords == ["academic", "light", "positron"]
    assert fetched.payload["providerId"] == "carto-positron"


def test_cartography_template_filter_by_kind(db_session):
    """Test filtering CartographyTemplates by kind."""
    tmpl1 = CartographyTemplate(
        id="tmpl_b1", kind="basemap", name="Basemap 1", payload={"providerId": "osm"}, is_builtin=True
    )
    tmpl2 = CartographyTemplate(
        id="tmpl_s1", kind="symbology", name="Symbology 1", payload={"mode": "single", "geometry": "Polygon", "style": {"color": "#ff0000"}}, is_builtin=True
    )
    tmpl3 = CartographyTemplate(
        id="tmpl_l1", kind="layout", name="Layout 1", payload={"paperSize": "A4", "orientation": "landscape"}, is_builtin=True
    )
    tmpl4 = CartographyTemplate(
        id="tmpl_t1", kind="thematic", name="Thematic 1", payload={"variant": "choropleth", "method": "quantiles", "k": 5, "palette": "YlOrRd"}, is_builtin=True
    )
    db_session.add_all([tmpl1, tmpl2, tmpl3, tmpl4])
    db_session.commit()

    basemaps = db_session.query(CartographyTemplate).filter_by(kind="basemap").all()
    assert len(basemaps) == 1
    assert basemaps[0].name == "Basemap 1"

    symbology_tmpls = db_session.query(CartographyTemplate).filter_by(kind="symbology").all()
    assert len(symbology_tmpls) == 1

    thematics = db_session.query(CartographyTemplate).filter_by(kind="thematic").all()
    assert len(thematics) == 1


def test_cartography_template_multi_tenancy(db_session):
    """Test template association with organization and user."""
    org = Organization(name="Test Org", slug="test-org")
    user = User(id="user_123", username="testuser", email="test@example.com")
    db_session.add_all([org, user])
    db_session.commit()

    tmpl = CartographyTemplate(
        id="tmpl_custom1",
        org_id=org.id,
        creator_id=user.id,
        kind="symbology",
        name="Custom User Symbology",
        payload={"mode": "single", "geometry": "Point", "style": {"color": "#00ff00"}},
        is_builtin=False,
    )
    db_session.add(tmpl)
    db_session.commit()

    fetched = db_session.query(CartographyTemplate).filter_by(id="tmpl_custom1").first()
    assert fetched.org_id == org.id
    assert fetched.creator_id == user.id
    assert fetched.is_builtin is False


def test_seed_templates_count_and_kinds():
    """Test that SEED_TEMPLATES contains ~18 templates across all 4 kinds."""
    assert len(SEED_TEMPLATES) >= 16
    kinds = {tmpl["kind"] for tmpl in SEED_TEMPLATES}
    assert kinds == {"basemap", "symbology", "layout", "thematic"}

    for tmpl in SEED_TEMPLATES:
        assert tmpl["is_builtin"] is True
        assert tmpl["id"].startswith("tmpl_")
        assert "name" in tmpl
        assert "payload" in tmpl


from pydantic import TypeAdapter
from app.schemas.template_schema import (
    SymbologySinglePayload,
    SymbologyCategoricalPayload,
    ThematicChoroplethPayload,
    ThematicHeatmapPayload,
)


def test_pydantic_payload_validation():
    """Test Pydantic schemas for the 4 discriminated payload shapes."""
    bm = BasemapPayload(
        providerId="carto-positron",
        vectorStyleUrl="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
    )
    assert bm.providerId == "carto-positron"

    sym_adapter = TypeAdapter(SymbologyPayload)

    sym_single = sym_adapter.validate_python({
        "mode": "single",
        "geometry": "Polygon",
        "style": {"fillColor": "#3b82f6", "fillOpacity": 0.7}
    })
    assert isinstance(sym_single, SymbologySinglePayload)
    assert sym_single.mode == "single"

    sym_cat = sym_adapter.validate_python({
        "mode": "categorical",
        "geometry": "Polygon",
        "field": "landuse",
        "colorMap": {"residential": "#ff0000", "commercial": "#00ff00"}
    })
    assert isinstance(sym_cat, SymbologyCategoricalPayload)
    assert sym_cat.mode == "categorical"

    layout = LayoutTemplatePayload(
        paperSize="A4",
        orientation="landscape",
        title="Test Layout",
        style={"titleColor": "#1e293b", "fontFamily": "Inter"}
    )
    assert layout.paperSize == "A4"
    assert layout.style.titleColor == "#1e293b"

    them_adapter = TypeAdapter(ThematicPresetPayload)

    them_choro = them_adapter.validate_python({
        "variant": "choropleth",
        "method": "quantiles",
        "k": 5,
        "palette": "YlOrRd"
    })
    assert isinstance(them_choro, ThematicChoroplethPayload)
    assert them_choro.variant == "choropleth"

    them_heat = them_adapter.validate_python({
        "variant": "heatmap",
        "intensity": 0.8,
        "radius": 25,
        "heatPalette": ["#0000ff", "#00ff00", "#ffff00", "#ff0000"]
    })
    assert isinstance(them_heat, ThematicHeatmapPayload)
    assert them_heat.variant == "heatmap"


def test_alembic_seed_data_in_db(db_session):
    """Test inserting SEED_TEMPLATES into DB and querying them by kind and is_builtin."""
    for tmpl_data in SEED_TEMPLATES:
        tmpl = CartographyTemplate(
            id=tmpl_data["id"],
            kind=tmpl_data["kind"],
            name=tmpl_data["name"],
            category=tmpl_data.get("category"),
            keywords=tmpl_data.get("keywords", []),
            description=tmpl_data.get("description"),
            payload=tmpl_data["payload"],
            is_builtin=tmpl_data["is_builtin"],
            version=tmpl_data.get("version", 1),
        )
        db_session.add(tmpl)
    db_session.commit()

    builtins = db_session.query(CartographyTemplate).filter_by(is_builtin=True).all()
    assert len(builtins) == len(SEED_TEMPLATES)

    # Verify counts per kind
    basemaps = db_session.query(CartographyTemplate).filter_by(kind="basemap", is_builtin=True).all()
    assert len(basemaps) == 4

    symbology_tmpls = db_session.query(CartographyTemplate).filter_by(kind="symbology", is_builtin=True).all()
    assert len(symbology_tmpls) == 5

    layouts = db_session.query(CartographyTemplate).filter_by(kind="layout", is_builtin=True).all()
    assert len(layouts) == 4

    thematics = db_session.query(CartographyTemplate).filter_by(kind="thematic", is_builtin=True).all()
    assert len(thematics) == 5

