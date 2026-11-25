"""模型文档导入/导出对偶（可移植科学面）。

导出 = 便携 JSON：format 门（"webgis-model" / "1"）+ document 载荷。
**不含 layout**（纯 UI 坐标不入可移植科学面）、不含任何实例态 / 项目
id / 用户信息。

导入 = 校验 format/format_version 后以 pydantic 还原 ModelDocument
（草稿态）。**导入绝不自动执行** —— 本模块只产出草稿文档，无任何执行
副作用；执行必须走显式 compile → WorkflowPackage → runtime 实例化链路。
"""
from __future__ import annotations

from typing import Dict

from pydantic import ValidationError

from app.schemas.model_authoring_schema import ModelDocument

#: 导出格式门（format / format_version 词表）。
MODEL_EXPORT_FORMAT = "webgis-model"
MODEL_EXPORT_FORMAT_VERSION = "1"

#: 错误摘要帽（pydantic 校验错误截断，响应面有界）。
_ERROR_DETAIL_CAP = 300


def export_document(doc: ModelDocument) -> Dict:
    """ModelDocument → 便携导出 dict（确定性；同 doc 同字节面）。

    layout 显式剔除：坐标是编辑器局部状态，跨环境迁移无科学意义。
    """
    payload = doc.model_dump(mode="json")
    payload.pop("layout", None)
    return {
        "format": MODEL_EXPORT_FORMAT,
        "format_version": MODEL_EXPORT_FORMAT_VERSION,
        "document": payload,
    }


def parse_import_document(payload: Dict) -> ModelDocument:
    """导出 dict → ModelDocument（草稿态；校验失败 → ValueError）。

    - 未知 format → ValueError("UNSUPPORTED_FORMAT: ...")；
    - 不支持的 format_version → ValueError("UNSUPPORTED_FORMAT_VERSION: ...")；
    - document 载荷非法 → ValueError("INVALID_DOCUMENT: <pydantic 摘要>")。

    导入绝不自动执行：只还原草稿文档，无任何执行副作用。
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"UNSUPPORTED_FORMAT: payload must be an object, "
            f"got {type(payload).__name__}")
    fmt = payload.get("format")
    if fmt != MODEL_EXPORT_FORMAT:
        raise ValueError(
            f"UNSUPPORTED_FORMAT: {fmt!r} (expected {MODEL_EXPORT_FORMAT!r})")
    ver = payload.get("format_version")
    if str(ver) != MODEL_EXPORT_FORMAT_VERSION:
        raise ValueError(
            f"UNSUPPORTED_FORMAT_VERSION: {ver!r} "
            f"(expected {MODEL_EXPORT_FORMAT_VERSION!r})")
    document = payload.get("document")
    if not isinstance(document, dict):
        raise ValueError("INVALID_DOCUMENT: missing 'document' object")
    try:
        return ModelDocument.model_validate(document)
    except ValidationError as exc:
        raise ValueError(
            "INVALID_DOCUMENT: " + str(exc)[:_ERROR_DETAIL_CAP]) from None


__all__ = [
    "MODEL_EXPORT_FORMAT",
    "MODEL_EXPORT_FORMAT_VERSION",
    "export_document",
    "parse_import_document",
]
