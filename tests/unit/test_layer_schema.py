"""
Schema guard: Layer model must declare `style_config` and NOT `properties_def`.

Context: the cartography work removed `properties_def` (dormant) and repurposed
`style_config` as the current-template pointer. Migration d3e4f5a6b7c8 drops the
orphaned column from existing DBs. This test guards against accidental reintroduction
of `properties_def` in the ORM model.
"""
from app.models.db_model import Layer


def test_layer_model_has_style_config():
    column_names = {c.name for c in Layer.__table__.columns}
    assert "style_config" in column_names, "Layer model must declare style_config (template pointer)"


def test_layer_model_does_not_have_properties_def():
    column_names = {c.name for c in Layer.__table__.columns}
    assert "properties_def" not in column_names, (
        "Layer model must NOT declare properties_def — it was dropped (dormant) and "
        "migration d3e4f5a6b7c8 removes the column from existing DBs."
    )
