"""#1113 P3 static-audit miscellaneous fixes."""
import logging

import pytest
from pydantic import BaseModel, Field
from typing import Any, Literal


def test_layer_data_header_is_nosniff():
    """P3-4: X-Content-Type-Options must be the full token nosniff."""
    from pathlib import Path
    src = Path("app/api/routes/layer.py").read_text(encoding="utf-8")
    assert '"X-Content-Type-Options": "nosniff"' in src
    assert '"X-Content-Type-Options": "nosn"' not in src


@pytest.mark.asyncio
async def test_memory_overwrite_pops_descriptor():
    """P3-5: overwrite must invalidate _descriptors like Redis D-4."""
    from app.services.session_data import MemorySessionStore

    store = MemorySessionStore(capacity=10)
    sid = "sess-1113"
    ref = await store.store(sid, {"type": "FeatureCollection", "features": []}, prefix="t")
    assert await store.get_ref_descriptor(sid, ref) is not None
    ok = await store.overwrite(sid, ref, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}
    ]})
    assert ok is True
    assert await store.get_ref_descriptor(sid, ref) is None


def test_gdf_from_features_invalid_crs_warns(caplog):
    """P3-7: invalid declared CRS must warn and log the declared value."""
    from app.lib.geo_processor.core import gdf_from_features

    fc = {
        "type": "FeatureCollection",
        "crs": "EPSG:99999",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [500000.0, 4000000.0]},
                "properties": {},
            }
        ],
    }
    with caplog.at_level(logging.WARNING):
        gdf = gdf_from_features(fc, context="issue-1113")
    assert gdf is not None and len(gdf) == 1
    joined = " ".join(r.message for r in caplog.records)
    assert "EPSG:99999" in joined
    assert "issue-1113" in joined or "falling back" in joined.lower() or "invalid" in joined.lower()


@pytest.mark.asyncio
async def test_pydantic_bypass_still_validates_non_any_fields(monkeypatch):
    """P3-3: oversized bypass must still reject out-of-range non-Any scalars."""
    from app.tools.registry import ToolRegistry, _annotation_is_any
    import app.tools.registry as reg

    assert _annotation_is_any(Any) is True
    assert _annotation_is_any(int) is False

    class Params(BaseModel):
        payload: Any = None
        radius: float = Field(ge=0, le=100)
        mode: Literal["a", "b"] = "a"

    registry = ToolRegistry()

    async def _tool(payload=None, radius: float = 1.0, mode: str = "a"):
        return {"ok": True, "radius": radius, "mode": mode}

    registry.register(
        name="issue_1113_bypass_tool",
        description="test",
        func=_tool,
        tier=1,
        domains=["test"],
        args_model=Params,
    )

    monkeypatch.setattr(reg, "_is_args_oversized", lambda arguments: True)

    # Out-of-range radius must be VALIDATION_ERROR even on oversized bypass.
    result = await registry.dispatch(
        "issue_1113_bypass_tool",
        {"payload": {"huge": "x" * 10}, "radius": 999.0, "mode": "a"},
    )
    assert isinstance(result, dict)
    assert result.get("success") is False or result.get("code") == "VALIDATION_ERROR" or "校验失败" in str(result)
    # Literal violation
    result2 = await registry.dispatch(
        "issue_1113_bypass_tool",
        {"payload": {"huge": "x"}, "radius": 1.0, "mode": "nope"},
    )
    assert isinstance(result2, dict)
    assert result2.get("success") is False or result2.get("code") == "VALIDATION_ERROR" or "校验失败" in str(result2)
    # Valid scalars + oversized payload pass
    result3 = await registry.dispatch(
        "issue_1113_bypass_tool",
        {"payload": {"huge": "x"}, "radius": 10.0, "mode": "b"},
    )
    assert result3.get("ok") is True or result3.get("success") is True or (
        isinstance(result3, dict) and result3.get("radius") == 10.0
    )
