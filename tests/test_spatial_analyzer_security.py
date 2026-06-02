"""Security tests for SpatialAnalyzer.attribute_filter — gdf.query() injection.

The function must validate query strings BEFORE passing them to gdf.query().
Relying on pandas exceptions is a blacklist approach; we need a whitelist.
"""
import pytest
from app.services.spatial_analyzer import SpatialAnalyzer

FEATURES = [
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "properties": {"name": "A", "value": 10},
    },
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [1, 1]},
        "properties": {"name": "B", "value": 20},
    },
]


class TestAttributeFilterQueryValidation:
    """Query validation must reject dangerous patterns BEFORE gdf.query()."""

    # --- Safe queries should work ---

    def test_safe_numeric_comparison(self):
        result = SpatialAnalyzer.attribute_filter(FEATURES, "value > 15")
        assert result.success
        assert len(result.data["features"]) == 1
        assert result.data["features"][0]["properties"]["name"] == "B"

    def test_safe_string_equality(self):
        result = SpatialAnalyzer.attribute_filter(FEATURES, "name == 'A'")
        assert result.success
        assert len(result.data["features"]) == 1

    def test_safe_and_expression(self):
        result = SpatialAnalyzer.attribute_filter(FEATURES, "value >= 10 and value <= 20")
        assert result.success
        assert len(result.data["features"]) == 2

    def test_safe_chained_comparison(self):
        result = SpatialAnalyzer.attribute_filter(FEATURES, "10 <= value <= 20")
        assert result.success
        assert len(result.data["features"]) == 2

    def test_safe_in_list(self):
        result = SpatialAnalyzer.attribute_filter(FEATURES, "value in [10, 30]")
        assert result.success
        assert len(result.data["features"]) == 1

    def test_safe_or_expression(self):
        result = SpatialAnalyzer.attribute_filter(FEATURES, "value < 15 or value > 25")
        assert result.success
        assert len(result.data["features"]) == 1

    # --- Dangerous queries must be rejected by our validator, not by pandas ---

    @pytest.mark.parametrize(
        "query",
        [
            "__import__('os').system('id')",
            "exec('raise RuntimeError')",
            "eval('__import__(\"os\").popen(\"id\")')",
            "__import__('builtins').open('/etc/passwd').read()",
            "open('/etc/passwd').read()",
            "getattr(__builtins__, 'exec')('')",
            "globals()",
            "locals()",
            "compile('1','','exec')",
            "breakpoint()",
        ],
    )
    def test_rejects_dangerous_query(self, query):
        result = SpatialAnalyzer.attribute_filter(FEATURES, query)
        assert not result.success
        # Must be rejected by our validator, not by a pandas exception
        assert "unsafe" in result.summary.lower() or "disallowed" in result.summary.lower()

    # --- Dunder attribute bypass must also be rejected ---

    @pytest.mark.parametrize(
        "query",
        [
            '"col".__class__.__bases__',
            '().__class__.__subclasses__()',
            '"col".__class__.__mro__',
            '"col".__init__()',
            '"col".__init__.__globals__',
            '"x".__class__.__bases__[0].__subclasses__()',
        ],
    )
    def test_rejects_dunder_attribute_bypass(self, query):
        """MRO chain and dunder attributes must be blocked."""
        result = SpatialAnalyzer.attribute_filter(FEATURES, query)
        assert not result.success
        assert "unsafe" in result.summary.lower() or "disallowed" in result.summary.lower()
