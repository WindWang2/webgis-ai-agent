"""数据准备域服务层 —— inspect / 绑定写回（API 与工具共用的业务面，M5）。

路由与工具都不得绕过本层自拼逻辑（单一业务真相）：
- ``inspect_source``：ref → 收据 + 提案 + 有界样本（一次检查，两处消费）；
- ``bind_field_roles``：用户确认角色 → descriptor 版本化写回（缺 descriptor
  时先从画像铸造 —— 与 ingest 同链，不建第二身份）。

安全/有界纪律：
- 输入是会话内 ref（ref 即会话域内能力），会话归属校验由调用方完成；
- 样本 ≤50 行 × ≤64 字段、坐标只披露首个位置点 —— 绝不搬运数据本体；
- 全部异常 typed 化（InspectError，code 词表），不向调用方泄漏堆栈。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.data.profile import DatasetProfileV3, FieldProfile, profile_features
from app.lib.data.quality import QualityReport, run_quality_checks
from app.services.data_preparation.field_proposal import (
    FieldProposal,
    propose_field_roles,
)
from app.services.data_preparation.spatial_receipt import (
    SpatialInspectionReceipt,
    build_spatial_receipt,
)
from app.services.data_preparation.vocabulary import to_semantic_roles

logger = logging.getLogger(__name__)

MAX_PREVIEW_ROWS = 50
MAX_PREVIEW_FIELDS = 64

INSPECT_ERROR_CODES = (
    "REF_MISSING", "PAYLOAD_UNSUPPORTED", "PROFILE_FAILED",
)


class InspectError(Exception):
    """typed 检查失败。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code if code in INSPECT_ERROR_CODES else "PAYLOAD_UNSUPPORTED"
        self.detail = str(detail)[:200]


def _extract_fc(payload: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    """payload → (FC, features, crs)。支持的形状：{"geojson": FC} / FC 本体。"""
    crs = ""
    fc = payload
    if isinstance(payload, dict) and isinstance(payload.get("geojson"), dict):
        fc = payload["geojson"]
        crs = str(payload.get("crs") or "")
    if not isinstance(fc, dict):
        raise InspectError("PAYLOAD_UNSUPPORTED", "ref 载荷不是 FeatureCollection")
    if isinstance(fc.get("crs"), dict):
        crs = str(fc["crs"].get("properties", {}).get("name") or crs)
    features = fc.get("features")
    if not isinstance(features, list):
        raise InspectError("PAYLOAD_UNSUPPORTED", "features 不是列表")
    return fc, features, crs


def _first_position(geometry: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    if not isinstance(geometry, dict):
        return None
    coords: Any = geometry.get("coordinates")

    def _walk(node: Any):
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
            return node
        for child in node or []:
            got = _walk(child)
            if got is not None:
                return got
        return None

    pos = _walk(coords)
    return [round(float(pos[0]), 6), round(float(pos[1]), 6)] if pos and len(pos) >= 2 else None


def bounded_sample(features: Sequence[Dict[str, Any]],
                   *, max_rows: int = MAX_PREVIEW_ROWS) -> Tuple[List[Dict[str, Any]], bool]:
    """有界样本（行截断 + 属性字段截断 + 坐标首点）。"""
    out: List[Dict[str, Any]] = []
    truncated_rows = len(features) > max_rows
    for feat in features[:max_rows]:
        props = feat.get("properties") if isinstance(feat, dict) else None
        bounded_props: Dict[str, Any] = {}
        if isinstance(props, dict):
            for i, (k, v) in enumerate(props.items()):
                if i >= MAX_PREVIEW_FIELDS:
                    break
                if isinstance(v, str):
                    v = v[:200]                 # 字符串截断（防长文本搬运）
                elif v is None or isinstance(v, (int, float, bool)):
                    pass
                else:
                    v = str(v)[:200]
                bounded_props[str(k)[:128]] = v
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        gtype = str(geom.get("type") or "") if isinstance(geom, dict) else ""
        out.append({"properties": bounded_props,
                    "geometry_type": gtype[:32],
                    "first_position": _first_position(geom)})
    return out, truncated_rows


async def inspect_source(
    session_id: str,
    source_ref: str,
    *,
    user_hints: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """一次检查：收据 + 提案 + 有界样本 + 输入指纹（字典形状供 API/工具共消费）。"""
    from app.services.dataset_semantics import get_dataset_semantic_store
    from app.services.session_data import session_data_manager

    payload = await session_data_manager.get(session_id, source_ref)
    if payload is None:
        raise InspectError("REF_MISSING", source_ref)
    _fc, features, crs = _extract_fc(payload)

    input_fingerprint = ""
    try:
        rec = await get_dataset_semantic_store().get(session_id, source_ref)
        if rec.ok and rec.descriptor is not None:
            input_fingerprint = rec.descriptor.descriptor_fingerprint
    except Exception:  # noqa: BLE001 — 指纹是披露面
        input_fingerprint = ""

    try:
        vp, _q = profile_features(features, crs=crs)
        profile = DatasetProfileV3(
            target_ref=source_ref, category="vector", crs=crs,
            extent=vp.extent, vector=vp,
            profile_quality=_q)
    except Exception as exc:  # noqa: BLE001
        raise InspectError("PROFILE_FAILED", str(exc)) from exc
    report: Optional[QualityReport] = None
    try:
        report = run_quality_checks(profile)
    except Exception:  # noqa: BLE001 — 质量面失败按缺席
        report = None

    fields: Dict[str, FieldProfile] = dict(vp.fields)
    row_count = len(features)
    proposals: List[FieldProposal] = propose_field_roles(
        fields, user_hints=user_hints, row_count=row_count)
    # crs_source 优先透传 payload 自带披露（parser 写入 assumed 等，
    # review R1 #7：此前只推导 declared/空，assumed 证据被丢弃）。
    declared_source = ""
    if isinstance(payload, dict):
        declared_source = str(payload.get("crs_source") or "")[:32]
    receipt: SpatialInspectionReceipt = build_spatial_receipt(
        profile, quality_report=report, proposals=proposals,
        crs_source=declared_source or ("declared" if crs else ""))
    sample, truncated = bounded_sample(features)
    return {
        "input_ref": source_ref,
        "input_fingerprint": input_fingerprint[:96],
        "profile": profile,
        "quality_report": report,
        "proposals": proposals,
        "receipt": receipt,
        "sample": sample,
        "sample_truncated": truncated,
    }


BIND_ERROR_CODES = ("REF_MISSING", "ROLE_UNKNOWN", "DESCRIPTOR_WRITE_FAILED")


class BindError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code if code in BIND_ERROR_CODES else "DESCRIPTOR_WRITE_FAILED"
        self.detail = str(detail)[:200]


async def bind_field_roles(
    session_id: str,
    source_ref: str,
    roles: Dict[str, str],
) -> Dict[str, Any]:
    """用户确认角色 → descriptor 版本化写回（M2 契约 §5 /bindings/fields）。

    - 角色码必须是提案词表成员；无 SemanticFieldRole 对应的角色（address）
      跳过并披露（不虚构映射）；
    - descriptor 缺席 → 先从画像铸造（与 ingest 同链），再写回；
    - 写回后 with_fingerprints 重铸 → store.put 新版本（旧版本保留在版本链）。
    """
    from app.lib.gis.dataset_descriptor import SourceRef
    from app.services.dataset_semantics import (
        derive_descriptor_from_v3,
        get_dataset_semantic_store,
    )
    from app.services.session_data import session_data_manager

    payload = await session_data_manager.get(session_id, source_ref)
    if payload is None:
        raise BindError("REF_MISSING", source_ref)
    _fc, features, crs = _extract_fc(payload)

    store = get_dataset_semantic_store()
    rec = await store.get(session_id, source_ref)
    descriptor = rec.descriptor if rec.ok and rec.descriptor is not None else None
    if descriptor is None:
        vp, _q = profile_features(features, crs=crs)
        profile = DatasetProfileV3(target_ref=source_ref, category="vector",
                                   crs=crs, extent=vp.extent, vector=vp)
        descriptor = derive_descriptor_from_v3(
            profile, dataset_key=source_ref, features=features,
            source_refs=[SourceRef(type="ref", ref=source_ref)])

    bound: List[str] = []
    skipped: List[List[str]] = []
    hints = {str(k): str(v) for k, v in (roles or {}).items() if str(k)}
    from app.services.data_preparation.vocabulary import is_proposal_role
    for entry in descriptor.fields:
        role = hints.get(entry.name)
        if role is None:
            continue
        if not is_proposal_role(role):
            skipped.append([entry.name, "ROLE_UNKNOWN"])
            continue
        semantic = list(to_semantic_roles(role))
        if not semantic:
            skipped.append([entry.name, "NO_SEMANTIC_EQUIVALENT"])
            continue
        entry.roles = semantic
        entry.role_confidence = "user_declared"
        bound.append(entry.name)
    descriptor = descriptor.with_fingerprints()
    put = await store.put(session_id, source_ref, descriptor)
    if not put.ok:
        raise BindError("DESCRIPTOR_WRITE_FAILED", str(put.reason_code))
    return {"source_ref": source_ref,
            "descriptor_fingerprint": put.fingerprint[:96],
            "bound_fields": bound,
            "skipped": skipped}
