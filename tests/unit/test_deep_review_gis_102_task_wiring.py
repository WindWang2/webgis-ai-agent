"""Verify-pass regression: ``analyze_vegetation_index`` positional task args.

The tool contract (mirroring ``_run_ndvi_legacy``) is
``(raster_path, nir, red, session, index_type, green, blue, swir1, swir2)``
with ``job_id`` injected as a keyword by ``submit_durable_job`` / retry.
A pre-existing shift (``job_id`` sat at position 5 of
``run_ndvi_analysis``) made every worker invocation raise
``TypeError: got multiple values for argument 'job_id'`` — including the
GIS-102 NBR/B12 path. Binding the recorded args to the real signature is
the regression oracle.
"""
import asyncio
import inspect

from app.services.spatial_tasks import run_ndvi_analysis
from app.tools.nature_resources import register_nature_resource_tools
from app.tools.registry import ToolRegistry


def test_tool_task_args_bind_to_task_signature(monkeypatch):
    captured = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return {"status": "analysis_task_started", "job_id": "1"}

    monkeypatch.setattr(
        "app.tools.nature_resources.submit_durable_job", fake_submit
    )
    reg = ToolRegistry()
    register_nature_resource_tools(reg)
    asyncio.run(reg.dispatch(
        "analyze_vegetation_index",
        {
            "raster_path": "x.tif",
            "index_type": "nbr",
            "nir_band": 8,
            "swir2_band": 12,
        },
        session_id="s",
    ))

    bound = inspect.signature(run_ndvi_analysis).bind(
        *captured["task_args"],
        **{**(captured.get("task_kwargs") or {}), "job_id": 7},
    )
    assert bound.arguments["index_type"] == "nbr"
    assert bound.arguments["nir_band"] == 8
    assert bound.arguments["swir2_band"] == 12
    assert bound.arguments["job_id"] == 7
    assert captured["params"]["swir2_band"] == 12
