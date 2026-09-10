"""Test GIS-10: Ensure matplotlib figure is closed in isoline model.

Verifies that:
1. Figure created in generate_contour_features_from_grid is closed on success.
2. Figure is closed even when an exception is raised during contour generation,
   preventing figure leaks in matplotlib global state.
"""
from unittest.mock import patch
import matplotlib.pyplot as plt
import numpy as np
import pytest

from app.lib.cartography.isoline_model import (
    IsolineContourSpec,
    generate_contour_features_from_grid,
)


def _sample_grid():
    x = np.linspace(104.0, 104.1, 20)
    y = np.linspace(30.6, 30.7, 20)
    X, Y = np.meshgrid(x, y)
    Z = 100.0 * np.exp(-((X - 104.05)**2 + (Y - 30.65)**2) / 0.002)
    return X, Y, Z


def test_isoline_figure_closed_on_success():
    """Matplotlib figure is closed after successful contour extraction."""
    plt.close("all")
    initial_figs = len(plt.get_fignums())

    X, Y, Z = _sample_grid()
    spec = IsolineContourSpec(mode="both", levels=[10.0, 30.0, 50.0])

    fc = generate_contour_features_from_grid(X, Y, Z, spec)
    assert fc["type"] == "FeatureCollection"

    # All figures should be closed
    assert len(plt.get_fignums()) == initial_figs


def test_isoline_figure_closed_on_exception():
    """Figure must be closed via finally block even if contour generation raises."""
    plt.close("all")
    initial_figs = len(plt.get_fignums())

    X, Y, Z = _sample_grid()
    spec = IsolineContourSpec(mode="lines", levels=[10.0, 30.0])

    # Patch Axes.contour to raise an unhandled exception
    with patch("matplotlib.axes.Axes.contour", side_effect=RuntimeError("Topology crash")):
        with pytest.raises(RuntimeError) as exc_info:
            generate_contour_features_from_grid(X, Y, Z, spec)

        assert "Topology crash" in str(exc_info.value)

    # Figure must NOT be leaked in matplotlib's global registry
    assert len(plt.get_fignums()) == initial_figs
