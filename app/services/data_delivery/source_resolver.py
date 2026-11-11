"""Data Delivery Source Resolver — 授权快照读取适配（G18 WP03）。

四类源锚 → 统一 ``SourceSnapshot``：

- **session_ref**：session FC ref（Redis/内存 LRU；descriptor 携带
  content_revision 与截断/总数证据）；
- **fabric_parquet**：``ref:fabric-parquet/<id>`` 磁盘工件（路径派生单点
  ``fabric_parquet_path``；pyarrow 分批读，geo metadata 还 CRS）；
- **project_artifact**：DB ``Artifact`` + 不可变 ``ArtifactRevision``
  （缺省 head / 显式 revision_no；经项目权限 + durable blob 读回并校验
  内容摘要 —— 原 session 过期后仍可交付）；
- **table_rows**：session 内 rows 型 payload（表格交付，无几何要求）。

纪律（任务书不变量 1/2/3/4）：
- 授权先行：session 守卫 / 项目成员校验在读取之前；未知 kind 失败关闭；
  ref 文本绝不拼接成本地路径（经 charset 白名单派生函数）。
- 快照一致：身份 = 内容指纹 + revision 锚；执行前复核，漂移即拒绝
  （SOURCE_REVISION_CHANGED），绝不混读两个版本。
- 只能读取失效指针或无 payload metadata → 明确不可交付（typed 错误），
  不伪造空文件。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.schemas.data_delivery_schema import DeliverySourceRef
from app.services.data_delivery import contracts as dl_contracts

logger = logging.getLogger(__name__)


class SourceResolutionError(Exception):
    """typed 源解析失败（error_code + 用户可读 reason）。"""

    def __init__(self, error_code: str, reason: str):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass
class DeliveryActor:
    """调用方身份（路由 Depends / 工具上下文归一）。"""

    user_id: Optional[str] = None
    org_id: Optional[int] = None
    owner_token: Optional[str] = None

    @classmethod
    def from_user_dict(cls, user: Optional[Dict[str, Any]]) -> "DeliveryActor":
        user = user or {}
        uid = user.get("user_id") or user.get("id")
        if uid in (None, "", "anonymous"):
            uid = None
        return cls(user_id=str(uid) if uid else None,
                   org_id=user.get("org_id"), owner_token=None)


async def verify_session_access(actor: DeliveryActor, session_id: str) -> None:
    """会话归属守卫 —— 复用任务中心同款 verify_session_owner（404 语义不
    泄漏存在性；未认证调用方在路由层已被拦截，这里兜底拒绝）。"""
    from fastapi import HTTPException

    from app.core.database import async_db_session
    from app.services.auth_history_bridge import verify_session_owner

    if not session_id:
        raise SourceResolutionError(dl_contracts.ERR_INVALID_SPEC, "缺少 session_id")
    if actor.user_id is None and not actor.owner_token:
        raise SourceResolutionError(
            dl_contracts.ERR_UNAUTHORIZED, "需要登录会话或 owner token")
    try:
        async with async_db_session() as db:
            await verify_session_owner(
                db, session_id, user_id=actor.user_id, owner_token=actor.owner_token)
    except HTTPException as e:
        raise SourceResolutionError(
            dl_contracts.ERR_UNAUTHORIZED, "无权访问该会话的数据引用") from e


def _verify_project_access(actor: DeliveryActor, project_id: str) -> None:
    """项目成员校验（同步 DB；失败 → 404 语义不泄漏存在性）。"""
    from fastapi import HTTPException

    from app.core.database import SessionLocal
    from app.services.project_service import ProjectService

    with SessionLocal() as db:
        project = ProjectService.get_project_with_auth(
            db=db, project_id=project_id, user_id=actor.user_id, org_id=actor.org_id
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json_sha256(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


# ── payload 形态识别 ──────────────────────────────────────────────────────

def _as_fc_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """FC 形态：{type: FeatureCollection, features: [...]}（宽容 bbox/properties）。"""
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection" \
            and isinstance(payload.get("features"), list):
        return payload
    return None


def _as_rows_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """rows 形态：{rows: [dict,...]}（feature_aggregate 等标准表格产物）。"""
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list) \
            and all(isinstance(r, dict) for r in payload["rows"][:8]):
        return payload
    return None


# ── 字段/几何画像（O(N) 全扫 —— payload 已驻内存，如实计数）──────────────

_TYPE_ORDER = {"unknown": 0, "bool": 1, "int": 2, "float": 3, "str": 4,
               "complex": 5}


def _infer_type(value: Any, current: str) -> str:
    if value is None:
        return current
    if isinstance(value, bool):
        t = "bool"
    elif isinstance(value, int):
        t = "int"
    elif isinstance(value, float):
        t = "float"
    elif isinstance(value, str):
        t = "str"
    else:
        t = "complex"
    if current == "unknown":
        return t
    if current == "int" and t == "float":
        return "float"
    if _TYPE_ORDER.get(t, 5) > _TYPE_ORDER.get(current, 0) and not (
        current == "float" and t == "int"
    ):
        return t if t != current else current
    return current


def _profile_fc(features: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """全扫 FC：字段类型画像 + 几何摘要 + properties 层证据。"""
    fields: Dict[str, Dict[str, Any]] = {}
    geo_types: Dict[str, int] = {}
    null_geom = empty_geom = 0
    has_z = False
    props_evidence: Dict[str, Any] = {}

    def _first_coord(g: Any) -> Optional[list]:
        node = g
        for _ in range(12):
            if isinstance(node, list) and node and isinstance(node[0], (int, float)):
                return node
            if isinstance(node, list) and node:
                node = node[0]
            else:
                return None
        return None

    for feat in features:
        props = feat.get("properties") or {}
        for k, v in props.items():
            ent = fields.setdefault(k, {"name": k, "inferred_type": "unknown",
                                        "sample_values": []})
            ent["inferred_type"] = _infer_type(v, ent["inferred_type"])
            if len(ent["sample_values"]) < 3 and v is not None:
                ent["sample_values"].append(repr(v)[:64])
        g = feat.get("geometry")
        if g is None:
            null_geom += 1
            continue
        gtype = g.get("type") if isinstance(g, dict) else None
        if not gtype:
            empty_geom += 1
            continue
        geo_types[gtype] = geo_types.get(gtype, 0) + 1
        coords = g.get("coordinates")
        if coords is None or coords == [] or coords == {}:
            empty_geom += 1
            continue
        if not has_z:
            first = _first_coord(coords)
            if first and len(first) >= 3:
                has_z = True
    ordered = [fields[k] for k in sorted(fields)]
    geometry = {
        "types": sorted(geo_types),
        "has_z": has_z,
        "null_or_empty_count": null_geom + empty_geom,
        "null_geometry": null_geom,
        "empty_geometry": empty_geom,
        "sampled_rows": len(features),
    }
    return ordered, geometry, props_evidence


def _profile_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fields: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        for k, v in row.items():
            ent = fields.setdefault(k, {"name": k, "inferred_type": "unknown",
                                        "sample_values": []})
            ent["inferred_type"] = _infer_type(v, ent["inferred_type"])
            if len(ent["sample_values"]) < 3 and v is not None:
                ent["sample_values"].append(repr(v)[:64])
    return [fields[k] for k in sorted(fields)]


def _completeness_from_fc_props(props: Dict[str, Any]) -> Dict[str, Any]:
    """materialize 落在 FC properties 的完整性证据（诚实转发，缺省空）。"""
    out: Dict[str, Any] = {}
    for key in ("total_count", "truncated", "total_matching"):
        if key in props:
            out[key] = props[key]
    schema_info = props.get("schema_info")
    if isinstance(schema_info, dict):
        if "crs" in schema_info:
            out["crs_hint"] = schema_info["crs"]
    return out


@dataclass
class SourceSnapshot:
    """解析后的源快照 —— 预览与执行共用的只读视图。"""

    kind: str
    label: str
    is_table: bool
    row_count: int
    content_fingerprint: str
    content_revision: Optional[int] = None
    artifact_revision_no: Optional[int] = None
    crs: Optional[str] = None
    completeness: Dict[str, Any] = field(default_factory=dict)
    fields: List[Dict[str, Any]] = field(default_factory=list)
    geometry: Optional[Dict[str, Any]] = None
    # 载体（二选一）：
    payload: Optional[Dict[str, Any]] = None       # FC 或 rows（已驻内存）
    parquet_path: Optional[Path] = None            # fabric-parquet 磁盘工件

    # ── 批式迭代（读取与变换有界；调用方逐批检查取消/预算）───────────────

    def iter_feature_batches(self, batch_rows: int) -> Iterator[List[Dict[str, Any]]]:
        """产出 Feature 批次（表格源产出行 dict 批次 —— 消费方按 is_table 分派）。"""
        if self.parquet_path is not None:
            import pyarrow.parquet as pq

            from app.services.data_fabric.vector_carrier import arrow_to_features

            pf = pq.ParquetFile(str(self.parquet_path))
            for table in pf.iter_batches(batch_size=batch_rows):
                yield arrow_to_features(table)
            return
        if self.payload is not None:
            if self.is_table:
                rows = self.payload["rows"]
                for start in range(0, len(rows), batch_rows):
                    yield rows[start:start + batch_rows]
                return
            features = self.payload.get("features") or []
            for start in range(0, len(features), batch_rows):
                yield features[start:start + batch_rows]
            return
        raise SourceResolutionError(
            dl_contracts.ERR_SOURCE_EXPIRED, "源快照无可用载体")

    def features(self) -> List[Dict[str, Any]]:
        """整表读取（表格行同构返回）—— 仅用于已在内存的 payload 或受限格式。"""
        if self.payload is not None:
            if self.is_table:
                return list(self.payload["rows"])
            return list(self.payload.get("features") or [])
        if self.parquet_path is not None:
            batches = list(self.iter_feature_batches(dl_contracts.READ_BATCH_ROWS))
            out: List[Dict[str, Any]] = []
            for b in batches:
                out.extend(b)
            return out
        raise SourceResolutionError(
            dl_contracts.ERR_SOURCE_EXPIRED, "源快照无可用载体")

    async def verify_unchanged(self, session_id: Optional[str]) -> None:
        """执行前复核：内容指纹漂移 → typed 拒绝（不混读两版本）。"""
        if self.kind == "session_ref" and session_id:
            from app.services.session_data import session_data_manager

            payload = await session_data_manager.get(session_id, self._src_ref or "")
            if payload is None:
                raise SourceResolutionError(
                    dl_contracts.ERR_SOURCE_EXPIRED, "源会话引用已过期或被清除")
            fresh = _snapshot_from_payload(self.label, payload, self._src_ref)
            if fresh.content_fingerprint != self.content_fingerprint:
                raise SourceResolutionError(
                    dl_contracts.ERR_SOURCE_REVISION_CHANGED,
                    "源会话数据在预览后发生变化，请重新预览后提交")
            return
        if self.parquet_path is not None:
            if not self.parquet_path.is_file():
                raise SourceResolutionError(
                    dl_contracts.ERR_SOURCE_EXPIRED, "源磁盘工件已不存在")
            if _file_sha256(self.parquet_path) != self.content_fingerprint:
                raise SourceResolutionError(
                    dl_contracts.ERR_SOURCE_REVISION_CHANGED,
                    "源 GeoParquet 工件在预览后发生变化，请重新预览后提交")
            return
        # project_artifact 修订：内容不可变，读回时已校验摘要 —— 恒通过。

    # 内部：session ref 原文（verify_unchanged 复读用）
    _src_ref: Optional[str] = None


def _snapshot_from_payload(
    label: str, payload: Any, src_ref: str
) -> SourceSnapshot:
    """payload（FC / rows）→ 快照（形态未知 → typed 失败关闭）。"""
    fc = _as_fc_payload(payload)
    if fc is not None:
        features = fc["features"]
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fingerprint = dataset_fingerprint_service.calculate_data_fingerprint(features)
        fields, geometry, _ = _profile_fc(features)
        props = fc.get("properties") or {}
        completeness = _completeness_from_fc_props(props)
        crs = completeness.pop("crs_hint", None)
        snap = SourceSnapshot(
            kind="session_ref", label=label, is_table=False, row_count=len(features),
            content_fingerprint=fingerprint, crs=crs, completeness=completeness,
            fields=fields, geometry=geometry, payload=fc,
        )
        snap._src_ref = src_ref
        return snap
    rows = _as_rows_payload(payload)
    if rows is not None:
        row_list = rows["rows"]
        snap = SourceSnapshot(
            kind="session_ref", label=label, is_table=True, row_count=len(row_list),
            content_fingerprint=_canonical_json_sha256(row_list),
            completeness={k: rows[k] for k in ("is_complete", "truncated",
                                               "total_matching", "group_count")
                          if k in rows},
            fields=_profile_rows(row_list), payload=rows,
        )
        snap._src_ref = src_ref
        return snap
    raise SourceResolutionError(
        dl_contracts.ERR_SOURCE_SHAPE_UNSUPPORTED,
        "源 payload 既不是 FeatureCollection 也不是 rows 表格 —— 无法交付")


def _normalize_crs(raw: Any) -> Optional[str]:
    """CRS 归一：接受 EPSG:xxxx / WKT 前缀 / pyproj CRS dict → 字符串或 None。"""
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        # pyproj CRS.to_dict() 形态（如 {'init': 'epsg:4326'}）
        init = raw.get("init")
        if isinstance(init, str):
            return init.upper()
    return None


async def resolve_source(
    source: DeliverySourceRef, actor: DeliveryActor
) -> SourceSnapshot:
    """源锚 + 调用方 → 授权后的 SourceSnapshot（失败 → typed 错误）。"""
    kind = source.kind

    if kind == "session_ref":
        session_id, ref = source.session_id, source.ref
        if not session_id or not ref:
            raise SourceResolutionError(
                dl_contracts.ERR_INVALID_SPEC, "session_ref 需要 session_id 与 ref")
        await verify_session_access(actor, session_id)
        from app.services.session_data import session_data_manager

        descriptor = await session_data_manager.get_ref_descriptor(session_id, ref)
        payload = await session_data_manager.get(session_id, ref)
        if payload is None:
            raise SourceResolutionError(
                dl_contracts.ERR_SOURCE_EXPIRED,
                "会话数据引用已过期（无持久副本）—— 无法交付；请先物化或晋升为项目产物")
        label = ref
        if descriptor:
            label = str(descriptor.get("alias") or descriptor.get("title") or ref)
        snap = _snapshot_from_payload(label, payload, ref)
        snap.kind = "session_ref"
        if descriptor and isinstance(descriptor.get("content_revision"), int):
            snap.content_revision = descriptor["content_revision"]
        return snap

    if kind == "fabric_parquet":
        session_id, ref = source.session_id, source.ref
        if not session_id or not ref:
            raise SourceResolutionError(
                dl_contracts.ERR_INVALID_SPEC, "fabric_parquet 需要 session_id 与 ref")
        from app.services.artifact_registry import (
            fabric_parquet_path,
            is_fabric_parquet_ref,
        )

        if not is_fabric_parquet_ref(ref):
            raise SourceResolutionError(
                dl_contracts.ERR_INVALID_SPEC, f"不是 fabric-parquet ref: {ref[:64]}")
        await verify_session_access(actor, session_id)
        path = fabric_parquet_path(session_id, ref)
        if path is None or not path.is_file():
            raise SourceResolutionError(
                dl_contracts.ERR_SOURCE_NOT_FOUND,
                f"磁盘工件不存在: {ref}（可能已被 GC 或会话清理）")
        return _snapshot_from_parquet(ref, path)

    if kind == "project_artifact":
        project_id, artifact_id = source.project_id, source.artifact_id
        if not project_id or not artifact_id:
            raise SourceResolutionError(
                dl_contracts.ERR_INVALID_SPEC, "project_artifact 需要 project_id 与 artifact_id")
        from fastapi import HTTPException
        from fastapi.concurrency import run_in_threadpool

        try:
            await run_in_threadpool(_verify_project_access, actor, project_id)
        except HTTPException as e:
            raise SourceResolutionError(
                dl_contracts.ERR_UNAUTHORIZED,
                "无权访问该项目（或项目不存在）") from e
        return await run_in_threadpool(
            _resolve_project_artifact, project_id, artifact_id, source.revision_no
        )

    if kind == "table_rows":
        session_id, ref = source.session_id, (source.table_ref or source.ref)
        if not session_id or not ref:
            raise SourceResolutionError(
                dl_contracts.ERR_INVALID_SPEC, "table_rows 需要 session_id 与 table_ref")
        await verify_session_access(actor, session_id)
        from app.services.session_data import session_data_manager

        payload = await session_data_manager.get(session_id, ref)
        if payload is None:
            raise SourceResolutionError(
                dl_contracts.ERR_SOURCE_EXPIRED, "表格引用已过期（无持久副本）")
        rows = _as_rows_payload(payload)
        if rows is None:
            raise SourceResolutionError(
                dl_contracts.ERR_SOURCE_SHAPE_UNSUPPORTED, "引用不是 rows 表格形态")
        snap = SourceSnapshot(
            kind="table_rows", label=ref, is_table=True,
            row_count=len(rows["rows"]),
            content_fingerprint=_canonical_json_sha256(rows["rows"]),
            completeness={k: rows[k] for k in ("is_complete", "truncated",
                                               "total_matching", "group_count")
                          if k in rows},
            fields=_profile_rows(rows["rows"]), payload=rows,
        )
        snap._src_ref = ref
        return snap

    raise SourceResolutionError(
        dl_contracts.ERR_SOURCE_KIND_UNKNOWN, f"未知源类型: {kind}")


def _snapshot_from_parquet(ref: str, path: Path) -> SourceSnapshot:
    """GeoParquet 工件 → 快照（geo metadata 单点解析；无 geo → CRS 未知）。"""
    try:
        import pyarrow.parquet as pq
    except Exception as e:  # pragma: no cover
        raise SourceResolutionError(
            dl_contracts.ERR_SOURCE_SHAPE_UNSUPPORTED,
            f"pyarrow 不可用，无法读取源工件: {e}") from e
    try:
        meta = pq.read_metadata(str(path))
        schema = pq.read_schema(str(path))
    except Exception as e:
        raise SourceResolutionError(
            dl_contracts.ERR_SOURCE_SHAPE_UNSUPPORTED,
            f"源 GeoParquet 元数据不可读: {e}") from e

    crs: Optional[str] = None
    geometry_column: Optional[str] = None
    schema_meta = dict(schema.metadata or {})
    geo_raw = schema_meta.get(b"geo")
    if geo_raw:
        try:
            geo = json.loads(geo_raw.decode("utf-8", "replace"))
            col = geo.get("primary_column") or next(iter(geo.get("columns", {})), None)
            if col:
                geometry_column = col
                crs = _normalize_crs((geo.get("columns") or {}).get(col, {}).get("crs"))
        except Exception:  # noqa: BLE001 — geo meta 损坏按未知 CRS 处理
            crs = None
    fields: List[Dict[str, Any]] = []
    for f in schema:
        if f.name == geometry_column:
            continue
        t = str(f.type)
        if t.startswith("large_"):
            t = t[len("large_"):]
        fields.append({"name": f.name, "inferred_type": t, "sample_values": None})
    snap = SourceSnapshot(
        kind="fabric_parquet", label=ref, is_table=(geometry_column is None),
        row_count=int(meta.num_rows), content_fingerprint=_file_sha256(path),
        crs=crs,
        completeness={"source": "fabric_geoparquet_artifact"},
        fields=fields, parquet_path=path,
    )
    if geometry_column:
        snap.geometry = {"types": [], "has_z": False, "null_or_empty_count": -1,
                         "sampled_rows": 0,
                         "note": "几何类型在预览采样阶段推断"}
    return snap


def sample_parquet_geometry(snap: SourceSnapshot, limit: int = 2000) -> None:
    """首批采样补齐 parquet 几何摘要（types/has_z/null 计数 = 采样证据）。"""
    if snap.parquet_path is None or snap.geometry is None:
        return
    feats: List[Dict[str, Any]] = []
    for batch in snap.iter_feature_batches(limit):
        feats.extend(batch)
        if len(feats) >= limit:
            break
    _, geometry, _ = _profile_fc(feats)
    geometry["sampled_rows"] = len(feats)
    geometry["sampled"] = True
    snap.geometry = geometry
    snap.geometry["null_or_empty_count"] = geometry["null_or_empty_count"]


def _resolve_project_artifact(
    project_id: str, artifact_id: str, revision_no: Optional[int]
) -> SourceSnapshot:
    """项目产物修订 → 快照（durable 读回 + 摘要校验；session 过期无关）。"""
    from sqlalchemy import select as sa_select

    from app.core.database import SessionLocal
    from app.models.project import Artifact, ArtifactRevision
    from app.services.project_artifact_promotion import read_content

    with SessionLocal() as db:
        artifact = db.execute(
            sa_select(Artifact).where(Artifact.id == artifact_id)
        ).scalar_one_or_none()
        if artifact is None or artifact.project_id != project_id:
            raise SourceResolutionError(
                dl_contracts.ERR_SOURCE_NOT_FOUND, "项目产物不存在")
        stmt = sa_select(ArtifactRevision).where(
            ArtifactRevision.artifact_id == artifact_id)
        if revision_no is not None:
            stmt = stmt.where(ArtifactRevision.revision_no == revision_no)
        revision = db.execute(
            stmt.order_by(ArtifactRevision.revision_no.desc())
        ).scalars().first()
        if revision is None:
            raise SourceResolutionError(
                dl_contracts.ERR_SOURCE_EXPIRED,
                "项目产物没有可物化修订（未晋升或内容已过期）")
        content = read_content(revision.content_location, revision.content_sha256)
        if content is None:
            raise SourceResolutionError(
                dl_contracts.ERR_SOURCE_EXPIRED,
                "项目产物内容读取失败或摘要不匹配（durable 存储异常）")

    fc = _as_fc_payload(content)
    label = artifact.name or artifact_id
    if fc is not None:
        features = fc["features"]
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        snap = SourceSnapshot(
            kind="project_artifact", label=label, is_table=False,
            row_count=len(features),
            content_fingerprint=dataset_fingerprint_service.calculate_data_fingerprint(
                features),
            artifact_revision_no=revision.revision_no,
            crs=_normalize_crs(artifact.crs),
            completeness={"artifact_revision": revision.revision_no,
                          "content_sha256": revision.content_sha256,
                          "is_complete": True},
            payload=fc,
        )
        snap.fields, snap.geometry, _ = _profile_fc(features)
        return snap
    rows = _as_rows_payload(content)
    if rows is not None:
        snap = SourceSnapshot(
            kind="project_artifact", label=label, is_table=True,
            row_count=len(rows["rows"]),
            content_fingerprint=_canonical_json_sha256(rows["rows"]),
            artifact_revision_no=revision.revision_no,
            crs=_normalize_crs(artifact.crs),
            completeness={"artifact_revision": revision.revision_no,
                          "content_sha256": revision.content_sha256,
                          "is_complete": True},
            payload=rows, fields=_profile_rows(rows["rows"]),
        )
        return snap
    raise SourceResolutionError(
        dl_contracts.ERR_SOURCE_SHAPE_UNSUPPORTED,
        f"项目产物 payload 形态不支持交付 (artifact_type={artifact.artifact_type})")
