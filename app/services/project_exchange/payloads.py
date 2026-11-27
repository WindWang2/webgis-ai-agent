"""载荷解析（WP2）——导出时把 embeddable 依赖解析为真实字节。

三条权威通路（不摸 Redis key、不走任意磁盘路径）：
1. upload 数据集 → UploadRecord 持久文件（DATA_DIR/uploads/<id>/original.*）；
2. 会话 ref 数据集 → session_data_manager.get_shared（ref:raster/* 走
   artifact_registry 磁盘真相单点）；会话过期 → missing（诚实披露）；
3. 项目产物 → ArtifactRevision head 修订（json 走 read_content digest 校验；
   binary 走 BlobStore 直读 + sha256 复核）。

任何解析失败都是分类事实（missing + reason），绝不伪造载荷。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.services.project_exchange import contracts as pc
from app.services.project_exchange.closure import ArtifactNode, DatasetNode

logger = logging.getLogger(__name__)

#: 会话 ref 单载荷字节预算（导出内嵌；超出 → 诚实 missing/披露）。
SESSION_PAYLOAD_BUDGET_BYTES = 256 * 1024 * 1024

#: 会话 ref 并发解析上限（共机资源纪律）。
_RESOLVE_SEMAPHORE = asyncio.Semaphore(2)


@dataclass
class ResolvedPayload:
    """解析后的载荷（materialize 阶段的输入）。"""

    kind: str                      # featurecollection | rows | binary | file
    data: Any = None               # dict（fc/rows）或 bytes（binary/file）
    path: Optional[Path] = None    # file 形态的磁盘真身（流式打包用）
    sha256: str = ""
    byte_size: int = 0
    feature_count: Optional[int] = None
    geometry_type: str = ""
    bbox: Optional[list] = None
    source_path: str = ""          # 溯源（不上 manifest；回执用）


@dataclass
class PayloadOutcome:
    payload: Optional[ResolvedPayload] = None
    classification: str = pc.CLASS_MISSING
    reason: str = ""


def _file_sha256(path: Path) -> str:
    return pc.sha256_file(path)


def _fc_meta(payload: Dict[str, Any]) -> Tuple[Optional[int], str, Optional[list]]:
    """FeatureCollection 摘要（canonical walker；空数据不虚构）。"""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        return None, "", None
    from app.utils.geojson import geojson_bbox

    bbox = geojson_bbox(payload)
    geom_types: Dict[str, int] = {}
    for f in features[:5000]:
        geom = f.get("geometry") if isinstance(f, dict) else None
        if isinstance(geom, dict) and geom.get("type"):
            geom_types[geom["type"]] = geom_types.get(geom["type"], 0) + 1
    geometry_type = ""
    if geom_types:
        geometry_type = max(geom_types.items(), key=lambda kv: kv[1])[0]
        if len(geom_types) > 1:
            geometry_type = "Mixed"
    return len(features), geometry_type, bbox


def resolve_upload_payload(node: DatasetNode) -> PayloadOutcome:
    """upload 数据集 → 持久文件（file 形态；流式打包，不整读）。"""
    if not node.upload_path:
        return PayloadOutcome(reason=node.reason or "上传引用不可解析")
    path = Path(node.upload_path)
    if not path.is_file():
        return PayloadOutcome(
            classification=pc.CLASS_MISSING,
            reason=f"上传文件缺失: uploads/{node.upload_id}")
    try:
        size = path.stat().st_size
    except OSError as e:
        return PayloadOutcome(reason=f"上传文件不可读: {e}")
    if size > pc.MAX_ENTRY_BYTES:
        return PayloadOutcome(
            classification=pc.CLASS_UNSUPPORTED,
            reason=f"上传文件 {size}B 超过单条目上限 {pc.MAX_ENTRY_BYTES}B")
    kind = "file"
    ext = path.suffix.lower()
    if ext == ".tif" or ext == ".tiff":
        kind = "raster_file"
    elif ext in (".csv", ".parquet"):
        kind = "table_file"
    return PayloadOutcome(payload=ResolvedPayload(
        kind=kind, path=path, sha256=_file_sha256(path), byte_size=size,
        feature_count=node.feature_count, geometry_type=node.geometry_type,
        bbox=node.bbox, source_path=str(path),
    ), classification=pc.CLASS_EMBEDDED)


async def resolve_session_payload(session_id: Optional[str],
                                  node: DatasetNode) -> PayloadOutcome:
    """会话 ref 数据集 → 真实会话载荷（raster ref 走磁盘真相单点）。"""
    ref = node.source_ref
    if not session_id:
        return PayloadOutcome(
            reason="无会话上下文：会话引用载荷无法解析（可先物化为项目产物再导出）")
    if ref.startswith("ref:raster/"):
        return await asyncio.to_thread(_resolve_raster_ref, session_id, ref)
    try:
        from app.services.session_data import session_data_manager

        async with _RESOLVE_SEMAPHORE:
            payload = await session_data_manager.get_shared(session_id, ref)
    except Exception as e:  # noqa: BLE001 — store 故障按载荷缺失（诚实）
        logger.warning("[project-exchange] session ref read failed %s: %s", ref, e)
        return PayloadOutcome(reason=f"会话引用读取失败: {type(e).__name__}")
    if payload is None:
        return PayloadOutcome(
            reason="会话引用已过期（无持久副本）—— 建议先物化为项目产物")
    return _payload_from_dict(payload, source=ref)


def _resolve_raster_ref(session_id: str, ref: str) -> PayloadOutcome:
    from app.services.artifact_registry import is_raster_ref, raster_png_path

    if not is_raster_ref(ref):
        return PayloadOutcome(reason="非 raster ref")
    path = raster_png_path(session_id, ref)
    if path is None or not path.is_file():
        return PayloadOutcome(reason="会话栅格文件缺失")
    try:
        size = path.stat().st_size
    except OSError as e:
        return PayloadOutcome(reason=f"会话栅格不可读: {e}")
    if size > pc.MAX_ENTRY_BYTES:
        return PayloadOutcome(
            classification=pc.CLASS_UNSUPPORTED,
            reason=f"会话栅格 {size}B 超过单条目上限")
    return PayloadOutcome(payload=ResolvedPayload(
        kind="raster_png", path=path, sha256=_file_sha256(path),
        byte_size=size, source_path=str(path),
    ), classification=pc.CLASS_EMBEDDED)


def _payload_from_dict(payload: Any, source: str) -> PayloadOutcome:
    """dict 载荷 → fc/rows 形态判定 + 摘要（canonical bytes 在打包时算）。"""
    import json

    if isinstance(payload, dict) and isinstance(payload.get("features"), list):
        count, geom, bbox = _fc_meta(payload)
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                          default=str).encode("utf-8")
        if len(blob) > SESSION_PAYLOAD_BUDGET_BYTES:
            return PayloadOutcome(
                classification=pc.CLASS_UNSUPPORTED,
                reason=f"会话载荷 {len(blob)}B 超过导出预算")
        return PayloadOutcome(payload=ResolvedPayload(
            kind="featurecollection", data=payload,
            byte_size=len(blob), feature_count=count, geometry_type=geom,
            bbox=bbox, source_path=source,
        ), classification=pc.CLASS_EMBEDDED)
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                          default=str).encode("utf-8")
        if len(blob) > SESSION_PAYLOAD_BUDGET_BYTES:
            return PayloadOutcome(
                classification=pc.CLASS_UNSUPPORTED,
                reason=f"会话表格 {len(blob)}B 超过导出预算")
        return PayloadOutcome(payload=ResolvedPayload(
            kind="rows", data=payload, byte_size=len(blob),
            feature_count=len(payload["rows"]), source_path=source,
        ), classification=pc.CLASS_EMBEDDED)
    return PayloadOutcome(
        classification=pc.CLASS_UNSUPPORTED,
        reason="会话载荷形态不支持（非 FeatureCollection/rows）")


def resolve_artifact_payload(node: ArtifactNode) -> PayloadOutcome:
    """项目产物 → head 修订内容（json digest 校验读；binary 直读+复核）。"""
    if not node.content_location:
        return PayloadOutcome(
            reason="产物无可物化修订（未晋升或内容已过期）")
    if node.content_type == "binary":
        return _resolve_binary_artifact(node)
    from app.services.project_artifact_promotion import read_content

    content = read_content(node.content_location, node.content_sha256 or "")
    if content is None:
        return PayloadOutcome(reason="产物内容读取失败或摘要不匹配")
    return _payload_from_dict(content, source=node.content_location)


def _resolve_binary_artifact(node: ArtifactNode) -> PayloadOutcome:
    """binary 修订（如晋升栅格 PNG）→ BlobStore 直读 + sha256 复核。"""
    from app.services.project_artifact_promotion import content_store_root

    root = content_store_root()
    try:
        path = (root / node.content_location).resolve()
        path.relative_to(root.resolve())
    except (ValueError, OSError):
        return PayloadOutcome(reason="产物内容位置越界（durable 存储异常）")
    if not path.is_file():
        return PayloadOutcome(reason="产物内容缺失")
    digest = _file_sha256(path)
    if node.content_sha256 and digest != node.content_sha256:
        return PayloadOutcome(reason="产物内容摘要不匹配（存储被篡改或损坏）")
    if path.stat().st_size > pc.MAX_ENTRY_BYTES:
        return PayloadOutcome(
            classification=pc.CLASS_UNSUPPORTED,
            reason="产物内容超过单条目上限")
    return PayloadOutcome(payload=ResolvedPayload(
        kind="binary", path=path, sha256=digest,
        byte_size=path.stat().st_size, source_path=str(path),
    ), classification=pc.CLASS_EMBEDDED)
