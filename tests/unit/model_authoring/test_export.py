"""模型导出/导入对偶单测（export_document / parse_import_document）。

覆盖面：export→parse 往返相等（layout 显式缺席）、导出载荷零实例态/
零 UI 坐标、未知 format / 坏 version / 坏 document 的 typed 拒绝、
错误摘要有界。
"""
from __future__ import annotations

from typing import Dict

import pytest

from app.schemas.model_authoring_schema import (
    ModelDocument,
    ModelEdge,
    ModelLayoutPos,
    ModelNode,
    ModelSource,
)
from app.services.model_authoring.export import (
    MODEL_EXPORT_FORMAT,
    MODEL_EXPORT_FORMAT_VERSION,
    export_document,
    parse_import_document,
)


def _doc_with_layout() -> ModelDocument:
    return ModelDocument(
        name="Facility Aggregation",
        description="示例模型",
        source=ModelSource(kind="manual", ref="draft-1"),
        nodes=[
            ModelNode(node_id="facilities", kind="data_input", role="facility"),
            ModelNode(node_id="aggregate", kind="analysis",
                      capability="admin_aggregation"),
            ModelNode(node_id="model_output", kind="output"),
        ],
        edges=[
            ModelEdge(from_node="facilities", from_port="facility",
                      to_node="aggregate", to_port="in"),
            ModelEdge(from_node="aggregate", from_port="out",
                      to_node="model_output", to_port="in"),
        ],
        output_node_ids=["model_output"],
        layout={"aggregate": ModelLayoutPos(x=120, y=80)},
    )


def test_export_shape_and_layout_excluded() -> None:
    """导出载荷：format 门 + document；layout 键不存在（可移植科学面）。"""
    doc = _doc_with_layout()
    exported = export_document(doc)
    assert exported["format"] == MODEL_EXPORT_FORMAT == "webgis-model"
    assert exported["format_version"] == MODEL_EXPORT_FORMAT_VERSION == "1"
    document = exported["document"]
    assert "layout" not in document
    assert document["name"] == "Facility Aggregation"
    assert document["source"] == {"kind": "manual", "ref": "draft-1"}
    assert len(document["nodes"]) == 3
    # 导出里全量搜索不到 layout 相关键（节点坐标不入载荷）。
    assert "layout" not in exported


def test_export_parse_roundtrip_preserves_science_face() -> None:
    """export→parse 往返：除 layout 外与原文档逐键相等（对偶性）。"""
    doc = _doc_with_layout()
    parsed = parse_import_document(export_document(doc))
    expected = doc.model_dump(mode="json")
    expected.pop("layout", None)
    actual = parsed.model_dump(mode="json")
    actual.pop("layout", None)  # 解析产物 layout 回到默认空（有损且诚实）
    assert actual == expected
    # layout 是编辑器局部状态：往返后为空。
    assert parsed.layout == {}


def test_unknown_format_rejected() -> None:
    """未知 format → ValueError("UNSUPPORTED_FORMAT: ...")。"""
    payload: Dict = {"format": "other-model", "format_version": "1",
                     "document": {}}
    with pytest.raises(ValueError) as excinfo:
        parse_import_document(payload)
    assert "UNSUPPORTED_FORMAT" in str(excinfo.value)
    # 非 dict 载荷同门。
    with pytest.raises(ValueError) as excinfo:
        parse_import_document(["not", "an", "object"])  # type: ignore[arg-type]
    assert "UNSUPPORTED_FORMAT" in str(excinfo.value)


def test_unsupported_version_rejected() -> None:
    """不支持的 format_version → ValueError("UNSUPPORTED_FORMAT_VERSION: ...")。"""
    payload = {"format": "webgis-model", "format_version": "2",
               "document": {}}
    with pytest.raises(ValueError) as excinfo:
        parse_import_document(payload)
    assert "UNSUPPORTED_FORMAT_VERSION" in str(excinfo.value)


def test_missing_document_object_rejected() -> None:
    """缺 document 载荷 → ValueError("INVALID_DOCUMENT: ...")。"""
    payload = {"format": "webgis-model", "format_version": "1"}
    with pytest.raises(ValueError) as excinfo:
        parse_import_document(payload)
    assert "INVALID_DOCUMENT" in str(excinfo.value)


def test_invalid_document_rejected_with_bounded_summary() -> None:
    """document 载荷违反 schema → ValueError，带 pydantic 错误摘要（≤300 字）。"""
    bad_document = {
        "schema_version": "1",
        "name": "broken",
        "source": {"kind": "manual"},
        "nodes": [ModelNode(node_id="ok", kind="output").model_dump()],
        "edges": [{"from_node": "ok", "from_port": "out",
                   "to_node": "ghost", "to_port": "in"}],  # 引用未知节点
    }
    payload = {"format": "webgis-model", "format_version": "1",
               "document": bad_document}
    with pytest.raises(ValueError) as excinfo:
        parse_import_document(payload)
    message = str(excinfo.value)
    assert "INVALID_DOCUMENT" in message
    detail = message.split("INVALID_DOCUMENT: ", 1)[1]
    assert len(detail) <= 300


def test_parse_is_draft_only_contract() -> None:
    """导入只产草稿文档：产物是 ModelDocument，无任何执行副作用入口。

    （docstring 契约锁定：导入绝不自动执行 —— 函数签名不返回包/实例/
    执行句柄，唯一产物是 pydantic 草稿。）
    """
    doc = _doc_with_layout()
    parsed = parse_import_document(export_document(doc))
    assert isinstance(parsed, ModelDocument)
    assert parsed.model_config.get("extra") == "forbid"  # schema 同一契约
