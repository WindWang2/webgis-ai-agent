"""工具注册一致性 + REST 路由合同测试（WP5 双入口）。

工具：真实 ToolRegistry + register_survey_sampling_tools（无 mock 被测语义）。
路由：TestClient + require_owned_session override（tests/raster_sampling 同款）。
"""
from __future__ import annotations

import pytest

from app.tools.registry import ToolRegistry
from app.tools.survey_sampling_tools import register_survey_sampling_tools

SURVEY_TOOLS = [
    "survey_frame_preview", "survey_design_preview", "survey_create_design",
    "survey_samples", "survey_record_reference", "survey_progress",
    "survey_estimate", "survey_get_estimate",
]


@pytest.fixture()
def registry():
    r = ToolRegistry()
    register_survey_sampling_tools(r)
    return r


def test_tools_registered_with_contracts(registry):
    for name in SURVEY_TOOLS:
        meta = registry.metadata(name)
        assert meta is not None, name
        schema = next(s for s in registry.get_schemas()
                      if s["function"]["name"] == name)
        props = schema["function"]["parameters"]["properties"]
        assert "session_id" not in props              # 注入参数不出 schema


def test_frame_preview_tool_roundtrip(registry, survey_env):
    """工具真跑：合成栅格 → survey_frame_preview → 框 digest + 层表。"""
    import asyncio

    from .conftest import CLASS_DICT, write_survey_raster
    from app.services.session_data import session_data_manager

    cls_path, _truth = write_survey_raster(survey_env["raster_root"] / "cls.tif")
    ref = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        session_data_manager.store(
            "sess-tool-roundtrip",
            {"kind": "numeric_raster", "file_path": str(cls_path)},
            prefix="raster"))

    # 经 registry.dispatch 真实执行（Pi 面语义：args_model 校验 + dispatch）
    import anyio

    async def _run():
        return await registry.dispatch(
            "survey_frame_preview",
            {"source_ref": str(ref), "band": 1,
             "class_dictionary": {str(k): v for k, v in CLASS_DICT.items()}},
            session_id="sess-tool-roundtrip")

    result = anyio.run(_run)
    # dispatch 直接返回工具载荷（success/summary/frame_digest）
    assert isinstance(result, dict), result
    assert result.get("success") is True, result
    assert result.get("frame_digest"), result
    assert len(result.get("strata", [])) == 3
