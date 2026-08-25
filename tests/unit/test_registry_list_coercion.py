"""列表参数的 JSON 字符串宽容解码（2026-08-25 会话回归）。

webgis_map_product 的 layer_ids/overlay_refs 被 LLM 编码成
``"[\"ref:...\"]"`` —— pydantic 拒以 "Input should be a valid list"，
模型自愈重试不改形（连错 3 轮触发无进展终止）。注册表在 model_validate
前对 list 族字段的 str 实参做 json.loads 解码；其余形态不动。
"""
import asyncio
import json
from typing import List, Optional

from app.tools.registry import (
    ToolRegistry,
    _coerce_json_string_lists,
    _is_list_annotation,
)


def _noop_tool(registry: ToolRegistry):
    """注册一个带 list/Optional[list]/str 参数的无副作用工具。"""
    def product(
        query: str = "",
        layer_ids: List[str] = None,
        overlay_refs: list = None,
        distances: Optional[List[float]] = None,
        note: str = "",
    ) -> dict:
        return {
            "query": query,
            "layer_ids": layer_ids,
            "overlay_refs": overlay_refs,
            "distances": distances,
            "note": note,
        }

    registry.register(
        tier=1, domains=["test"], name="_test_product",
        description="test tool", func=product,
    )
    return product


def test_is_list_annotation():
    assert _is_list_annotation(list)
    assert _is_list_annotation(List)
    assert _is_list_annotation(List[str])
    assert _is_list_annotation(list[str])
    assert _is_list_annotation(Optional[List[str]])
    assert _is_list_annotation(Optional[list])
    assert not _is_list_annotation(str)
    assert not _is_list_annotation(int)
    assert not _is_list_annotation(dict)
    assert not _is_list_annotation(Optional[str])
    assert not _is_list_annotation("not-a-type")


def test_coerce_decodes_json_string_lists():
    reg = ToolRegistry()
    _noop_tool(reg)
    model = reg._models["_test_product"]

    out = _coerce_json_string_lists(
        {
            "query": "成都市小学分析",
            "layer_ids": json.dumps(["result-chatcmpl-tool-x"]),
            "overlay_refs": '["ref:geojson-a", "ref:geojson-b"]',
            "note": "[本应保持原样的普通字符串]",
        },
        model,
    )
    assert out["layer_ids"] == ["result-chatcmpl-tool-x"]
    assert out["overlay_refs"] == ["ref:geojson-a", "ref:geojson-b"]
    # 非 list 注解的 str 不动
    assert out["note"] == "[本应保持原样的普通字符串]"


def test_coerce_leaves_non_list_json_and_bad_json_alone():
    reg = ToolRegistry()
    _noop_tool(reg)
    model = reg._models["_test_product"]

    out = _coerce_json_string_lists(
        {"layer_ids": '"plain string"', "overlay_refs": "{not json"},
        model,
    )
    assert out["layer_ids"] == '"plain string"'
    assert out["overlay_refs"] == "{not json"


def test_dispatch_accepts_json_string_list_args():
    """端到端：dispatch 收到字符串形态的数组参数也能成功执行。"""
    reg = ToolRegistry()
    _noop_tool(reg)
    result = asyncio.run(reg.dispatch("_test_product", {
        "query": "q",
        "layer_ids": json.dumps(["L1", "L2"]),
        "overlay_refs": '["ref:geojson-a"]',
    }))
    assert result["layer_ids"] == ["L1", "L2"]
    assert result["overlay_refs"] == ["ref:geojson-a"]
