"""Topology and async CPU boundaries from #1862 S3c-019 / S3b-025."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest


def test_multipolygon_keeps_disjoint_exteriors_and_holes(monkeypatch):
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.path import Path
    from app.services.workflow_runtime.adapters_cartography import _render_features_png

    captured = []
    original = plt.subplots

    def subplots(*args, **kwargs):
        figure, axes = original(*args, **kwargs)
        captured.append((figure, axes))
        return figure, axes

    monkeypatch.setattr(plt, "subplots", subplots)
    # Deliberately use equal winding for exterior and hole: GeoJSON producers
    # do not always normalize it, but the rendered hole must remain empty.
    feature = {"properties": {"class": "land"}, "geometry": {
        "type": "MultiPolygon", "coordinates": [
            [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
             [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]],
            [[[6, 0], [8, 0], [8, 4], [6, 4], [6, 0]]],
        ],
    }}
    png = _render_features_png([feature], title="Topology", field="class")
    assert png.startswith(b"\x89PNG")
    figure, axes = captured[0]
    assert len(axes.patches) == 2
    assert [np.count_nonzero(patch.get_path().codes == Path.MOVETO)
            for patch in axes.patches] == [2, 1]
    axes.grid(False)
    FigureCanvasAgg(figure)
    figure.canvas.draw()
    pixels = np.asarray(figure.canvas.buffer_rgba())

    def pixel(x, y):
        px, py = axes.transData.transform((x, y))
        return pixels[pixels.shape[0] - 1 - round(py), round(px), :3]

    assert np.all(pixel(1.5, 1.7) > 245), "hole must remain unfilled"
    assert np.all(pixel(5.1, 0.7) > 245), "islands must not be bridged"
    assert pixel(0.5, 0.7)[0] < 220
    assert pixel(6.5, 0.7)[0] < 220


@pytest.mark.asyncio
async def test_validate_mapspec_offloads_validation_grammar_and_review(monkeypatch):
    from app.services.mapspec_store import MapSpecStore
    from app.services.mapspec import coordinator
    from app.lib.cartography import quality_loop, grammar_propagation

    main_thread = threading.get_ident()
    calls = []
    snapshot = {"version": "1", "layers": []}

    def validate(spec):
        calls.append(("validate", threading.get_ident()))
        assert spec is snapshot
        return {"success": True, "warnings": ["preserved"]}

    def grammar(spec):
        calls.append(("grammar", threading.get_ident()))
        return "grammar-evidence"

    def review(spec, *, grammar_decision):
        calls.append(("review", threading.get_ident()))
        assert grammar_decision == "grammar-evidence"
        return SimpleNamespace(to_dict=lambda: {"final_fingerprint": "fingerprint"})

    store = MapSpecStore()
    monkeypatch.setattr(store, "get_mapspec", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(coordinator, "validate", validate)
    monkeypatch.setattr(grammar_propagation, "grammar_auditor_for_mapspec", grammar)
    monkeypatch.setattr(quality_loop, "review_cartography", review)
    result = await asyncio.wait_for(store.validate_mapspec("offload"), 5)
    assert result == {"success": True, "warnings": ["preserved"],
                      "mapspec_fingerprint": "fingerprint",
                      "cartographic_review": {"final_fingerprint": "fingerprint"}}
    assert [name for name, _ in calls] == ["validate", "grammar", "review"]
    assert all(thread != main_thread for _, thread in calls)
    assert len({thread for _, thread in calls}) == 1
