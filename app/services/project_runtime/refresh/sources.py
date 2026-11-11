"""G10 UPD-01：项目输入版本清单（真实生产读取路径）。

权威绑定：
- 逻辑身份 = ``dataset:<ProjectDataset.id>``（稳定行身份，**不是文件名**）；
- 内容版本 = ``RefDescriptor.content_hash``（≤1MB payload 的 sha256）或
  ``ProjectDataset.version_fingerprint``；``content_revision`` 只是计数器，
  只能给 REVISION_TOKEN 证据级别，不能升级成精确内容结论；
- 基线 = 本任务账本 ``baselines.json`` 里的上次采纳/首次建立的 SourceSnapshot，
  并与既有 runtime snapshot 的 ``DatasetDescriptor.version_fingerprint`` 交叉校验；
- payload = ``session_data_manager.get_shared`` 的真实会话 ref，
  大表按有界上限截断并标 ``truncated``，绝不把全量 FeatureCollection
  送进模型上下文。

权限：跨 owner / 撤权由路由层 ``_auth_project`` + ``_verify_session_access``
拒绝（404 不泄露存在性）；本模块只接受已经过鉴权的 db + project_id，
且差异摘要只在鉴权通过后构造（失权时根本不调用本模块）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Protocol, Tuple

from sqlalchemy.orm import Session

from app.lib.data.fingerprints import canonical_fingerprint
from app.models.project import ProjectDataset

from .contracts import (
    MAX_SOURCES,
    EvidenceLevel,
    FeatureIdKind,
    SourceInventory,
    SourceInventoryItem,
    SourceSnapshot,
    SourceState,
)

MAX_FEATURES_IN_SNAPSHOT = 20_000
STABLE_ID_KEYS = ("id", "feature_id", "fid", "object_id", "uuid", "gid")


class SourcePayloadProvider(Protocol):
    """来源 payload 读取端口（生产实现 / 合成 fixture 共用同一判定代码）。"""

    async def read(self, *, session_id: str, source_ref: str) -> Dict[str, Any]: ...


class SessionRefPayloadProvider:
    """生产实现：真实调用既有 session_data_manager。

    只读，无副作用；读取失败返回空 dict（由调用方降级为 UNKNOWN 证据，
    绝不编造精确差异，也绝不发起任意远程 URL 抓取 —— 远程再取数必须走
    受控 data-fabric adapter 与 SSRF/DNS/预算规则）。
    """

    async def read(self, *, session_id: str, source_ref: str) -> Dict[str, Any]:
        if not session_id or not source_ref:
            return {}
        from app.services.session_data import session_data_manager

        descriptor: Dict[str, Any] = {}
        payload: Any = None
        try:
            descriptor = await session_data_manager.get_ref_descriptor(
                session_id, source_ref
            ) or {}
        except Exception:  # noqa: BLE001 — 读取失败 → 证据缺失，诚实降级
            descriptor = {}
        try:
            payload = await session_data_manager.get_shared(session_id, source_ref)
        except Exception:  # noqa: BLE001
            payload = None
        return {"descriptor": descriptor, "payload": payload}


class StaticPayloadProvider:
    """本地合成数据端口（无付费/远程凭证时走真实生产判定代码路径）。"""

    def __init__(self, mapping: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.mapping = mapping or {}

    async def read(self, *, session_id: str, source_ref: str) -> Dict[str, Any]:
        return dict(self.mapping.get(source_ref) or {})


def _extract_features(payload: Any) -> Tuple[Dict[str, Dict[str, Any]], FeatureIdKind, bool]:
    """FeatureCollection → 稳定 ID 索引；无稳定 ID 一律拒绝按位置当身份。"""
    if isinstance(payload, dict):
        rows = payload.get("features")
    elif isinstance(payload, list):
        rows = payload
    else:
        return {}, FeatureIdKind.NONE, False
    if not isinstance(rows, list):
        return {}, FeatureIdKind.NONE, False

    indexed: Dict[str, Dict[str, Any]] = {}
    kind = FeatureIdKind.STABLE
    truncated = len(rows) > MAX_FEATURES_IN_SNAPSHOT
    for row in rows[:MAX_FEATURES_IN_SNAPSHOT]:
        if not isinstance(row, dict):
            continue
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        fid: Any = row.get("id")
        if fid in (None, ""):
            for key in STABLE_ID_KEYS:
                if props.get(key) not in (None, ""):
                    fid = props[key]
                    break
        if fid in (None, ""):
            kind = FeatureIdKind.NONE
            continue  # 不把行位置当身份
        indexed[str(fid)] = {
            "properties": dict(props),
            "geometry": row.get("geometry"),
        }
    if kind is FeatureIdKind.NONE and indexed:
        # 混合（部分有 ID）：整体不可逐要素对齐，不伪造精确差异
        return {}, FeatureIdKind.NONE, True
    return indexed, kind, truncated


def _schema_fields(descriptor: Dict[str, Any]) -> Dict[str, str]:
    raw = descriptor.get("field_schema")
    out: Dict[str, str] = {}
    if isinstance(raw, dict):
        for name, info in raw.items():
            if isinstance(info, dict):
                out[str(name)] = f"{info.get('type') or ''}|{info.get('unit') or ''}"
            else:
                out[str(name)] = f"{info}|"
    if not out and isinstance(descriptor.get("filterable_fields"), list):
        out = {str(f): "|" for f in descriptor["filterable_fields"][:100]}
    return out


def build_snapshot(
    *,
    logical_id: str,
    row: Any,
    descriptor: Dict[str, Any],
    payload: Any,
    version_token: str = "",
    owner_id: str = "",
    denominator_nodes: Optional[List[str]] = None,
    raster_dependencies: Optional[List[str]] = None,
    analysis_roles: Optional[List[str]] = None,
) -> SourceSnapshot:
    """由真实 DB 行 + ref descriptor + payload 构造可比较快照。"""
    features, id_kind, payload_truncated = _extract_features(payload)
    fields = _schema_fields(descriptor)
    content_hash = descriptor.get("content_hash")
    revision = descriptor.get("content_revision")
    db_fingerprint = str(getattr(row, "version_fingerprint", "") or "")
    token = version_token or (str(content_hash) if content_hash else
                              (f"rev:{revision}" if revision else db_fingerprint))

    if content_hash:
        evidence = EvidenceLevel.CONTENT_EXACT
        state = SourceState.REFRESHABLE
    elif revision:
        evidence = EvidenceLevel.REVISION_TOKEN
        state = SourceState.REFRESHABLE
    elif db_fingerprint:
        evidence = EvidenceLevel.REVISION_TOKEN
        state = SourceState.REFRESHABLE
    else:
        evidence = EvidenceLevel.NONE
        state = SourceState.VERSION_UNKNOWN

    crs = str(descriptor.get("crs") or getattr(row, "crs", "") or "")
    bbox = descriptor.get("bbox") if isinstance(descriptor.get("bbox"), list) else None
    if bbox is not None and len(bbox) != 4:
        bbox = None
    schema_truncated = descriptor.get("field_schema_complete") is False
    estimated = int(descriptor.get("estimated_bytes") or 0)

    tiles: Dict[str, str] = {}
    raw_tiles = descriptor.get("tiles") or descriptor.get("blocks")
    if isinstance(raw_tiles, dict):
        tiles = {str(k)[:64]: str(v)[:128] for k, v in list(raw_tiles.items())[:256]}
    # 方法依赖（全局分类断点 / 核密度带宽 / stencil / 重采样 / 网络拓扑）
    # 由 descriptor 或读侧显式声明；命中即必须扩大依赖域，不能按块局部更新。
    declared_deps = (raster_dependencies or []) + list(
        descriptor.get("global_method_dependencies")
        or descriptor.get("raster_dependencies") or []
    )
    declared_deps = [str(d)[:64] for d in declared_deps[:16]]
    # 数据集名是项目内既有的分析角色语义（如 "schools" → data:schools），
    # 比从 logical_id 末段推导更贴近真实分析图；读侧可显式覆盖。
    roles = [str(r)[:64] for r in (analysis_roles or []) if r]
    if not roles:
        name = str(getattr(row, "name", "") or "")
        if name:
            roles = [f"data:{name.split('.')[0][:56]}"]

    return SourceSnapshot(
        logical_id=logical_id,
        display_name=str(getattr(row, "name", "") or ""),
        kind=("raster" if descriptor.get("raster_capable") else
              ("table" if not features else "vector")),
        state=state,
        evidence=evidence,
        version_token=token[:512],
        fingerprints={
            "content": str(content_hash) if content_hash else None,
            "schema": canonical_fingerprint(fields) if fields else None,
            "metadata": canonical_fingerprint({
                "name": str(getattr(row, "name", "") or ""),
                "source_type": str(getattr(row, "source_type", "") or ""),
                "quality": str(getattr(row, "quality_status", "") or ""),
            }),
            "crs": canonical_fingerprint(crs) if crs else None,
        },
        schema_fields=fields,
        crs=crs[:512],
        bbox=bbox,
        features=features,
        feature_id_kind=id_kind,
        raster_tiles=tiles,
        raster_dependencies=declared_deps,
        analysis_roles=roles,
        denominator_nodes=list(denominator_nodes or ["analysis:normalized"]),
        owner_id=owner_id,
        sampled=bool(descriptor.get("sampled") is True),
        truncated=bool(payload_truncated or schema_truncated),
        available_bytes=estimated,
    )


async def collect_project_sources(
    db: Session,
    *,
    project_id: str,
    session_id: str = "",
    user_id: str = "",
    payload_provider: Optional[SourcePayloadProvider] = None,
    baselines: Optional[Dict[str, Dict[str, Any]]] = None,
    runtime_descriptor_versions: Optional[Dict[str, str]] = None,
    establish_baseline: bool = False,
) -> Tuple[SourceInventory, Dict[str, SourceSnapshot], Dict[str, SourceSnapshot], Dict[str, Any]]:
    """收集项目来源清单 + 基线/当前快照 + 是否新建立基线。

    返回 ``(inventory, baseline_snapshots, current_snapshots, meta)``；
    ``meta["baseline_established"]`` 为 True 时表示本次是首次建立基线，
    **不能**声称任何数据变化。
    """
    provider = payload_provider or SessionRefPayloadProvider()
    rows = await asyncio.to_thread(
        lambda: db.query(ProjectDataset)
        .filter(ProjectDataset.project_id == project_id,
                ProjectDataset.detached_at.is_(None))
        .order_by(ProjectDataset.created_at)
        .limit(MAX_SOURCES + 1)
        .all()
    )
    truncated = len(rows) > MAX_SOURCES
    rows = rows[:MAX_SOURCES]

    baseline_snapshots: Dict[str, SourceSnapshot] = {}
    current_snapshots: Dict[str, SourceSnapshot] = {}
    items: List[SourceInventoryItem] = []
    warnings: List[str] = []
    source_versions: Dict[str, str] = {}
    baseline_payload: Dict[str, Any] = dict(baselines or {})

    for row in rows:
        logical_id = f"dataset:{row.id}"
        source_ref = str(getattr(row, "source_ref", "") or "")
        data = await provider.read(session_id=session_id, source_ref=source_ref)
        descriptor = data.get("descriptor") if isinstance(data.get("descriptor"), dict) else {}
        snapshot = build_snapshot(
            logical_id=logical_id, row=row, descriptor=descriptor,
            payload=data.get("payload"), owner_id=user_id,
        )
        current_snapshots[logical_id] = snapshot
        source_versions[logical_id] = snapshot.version_token

        raw_base = (baselines or {}).get(logical_id)
        if isinstance(raw_base, dict):
            try:
                base = SourceSnapshot.model_validate(raw_base)
                # 与既有 runtime snapshot 的 DatasetDescriptor 交叉校验
                pinned = (runtime_descriptor_versions or {}).get(str(row.id))
                if pinned and pinned != base.version_token:
                    warnings.append(
                        f"{logical_id}: runtime-snapshot descriptor version differs from "
                        "refresh baseline; using explicit refresh baseline"
                    )
                baseline_snapshots[logical_id] = base
                verdict = ("unchanged" if base.version_token and
                           base.version_token == snapshot.version_token else "changed")
            except Exception:  # noqa: BLE001
                warnings.append(f"{logical_id}: stored baseline invalid; treated as unverifiable")
                verdict = "unverifiable"
        else:
            verdict = "unverifiable"

        items.append(SourceInventoryItem(
            logical_id=logical_id,
            display_name=snapshot.display_name,
            kind=snapshot.kind,
            state=snapshot.state,
            evidence=snapshot.evidence,
            baseline_version=str((raw_base or {}).get("version_token") or "")[:512],
            current_version=snapshot.version_token,
            owner_id=user_id,
            crs=snapshot.crs,
            bbox=snapshot.bbox,
            verdict=verdict,
            note=("first baseline; no comparable prior version" if verdict == "unverifiable"
                  else ""),
        ))

    if establish_baseline and not (baselines or {}):
        baseline_payload = {
            logical_id: snap.model_dump(mode="json")
            for logical_id, snap in current_snapshots.items()
        }
        warnings.append(
            "baseline established on first check: no data-change claim is possible yet"
        )

    if truncated:
        warnings.append(f"source list truncated at {MAX_SOURCES} datasets")

    inventory = SourceInventory(
        items=items,
        truncated=truncated,
        warnings=warnings[:16],
        fingerprint=canonical_fingerprint({
            "items": [i.model_dump(mode="json") for i in items],
            "versions": dict(sorted(source_versions.items())),
        }),
    )
    meta = {
        "baseline_established": bool(establish_baseline and not (baselines or {})),
        "baseline_payload": baseline_payload,
        "checked_at": time.time(),
        "source_versions": source_versions,
    }
    return inventory, baseline_snapshots, current_snapshots, meta


__all__ = [
    "SourcePayloadProvider", "SessionRefPayloadProvider", "StaticPayloadProvider",
    "build_snapshot", "collect_project_sources", "MAX_FEATURES_IN_SNAPSHOT",
]
