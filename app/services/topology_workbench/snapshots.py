"""Topology Workbench —— 权威快照解析（WP03）。

角色绑定 → 快照（全量要素 + 内容身份 + 完整性），两条真实路径：

- ``session_ref``：会话 ref（session_data_manager 权威载荷 + content_revision）；
- ``project_artifact``：项目持久产物修订（durable BlobStore 内容 + 摘要校验 +
  ``is_complete: True``）—— 复用 G18 data_delivery 的解析语义与鉴权面。

范围（scope）语义：
- checked 类角色可带 scope（bbox / where / ids）—— 解析时在**同一快照**上
  消费实际全集（where 经 feature_pages.evaluate_where；bbox = 粗超集相交并
  持续披露；ids 显式集合）。G24 selection_handle **只在提交时解析**为上述
  scope 形状，句柄本身绝不持久化（进程内 TTL 对象，任务书 WP03）；
- complete 类角色（coverage/target/connectors/exclusions/exit_zones/aoi 数据集）
  携带 scope → typed 拒绝（T12：局部选择 + 全量语义 = 虚假缺口）。

权限：与 data_delivery 同纪律 —— 会话所有权（verify_session_owner）/项目
访问（ProjectService.get_project_with_auth）失败一律拒绝；跨用户不泄露存在性
由路由层 404 处理（T19）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SnapshotError(Exception):
    """typed 快照解析错误：code ∈ ERROR_CODES（source_unresolvable /
    source_unauthorized / scope_not_allowed / invalid_scope / budget_exceeded…）。"""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class RoleSnapshot:
    """一个角色的已解析快照（运行内不可变；身份参与复用键）。"""

    role: str
    kind: str                       # session_ref | project_artifact | bbox
    ref: str                        # ref:… / artifact_id / "bbox"
    features: List[Dict[str, Any]] = field(default_factory=list)
    content_revision: Optional[int] = None
    fingerprint: str = ""           # 内容指纹（canonical sha256 前缀）
    crs: Optional[str] = None
    scope: Optional[Dict[str, Any]] = None      # 规范化 scope（可重放）
    scope_semantics: Optional[str] = None       # bbox → bbox_intersects_coarse
    completeness: str = "full"                  # full | partial
    total_input: int = 0                        # scope 前的全量计数（可证时）
    label: str = ""

    def binding_evidence(self) -> Dict[str, Any]:
        """落库的绑定证据（复用键成分；不含要素载荷）。"""
        return {
            "role": self.role,
            "kind": self.kind,
            "ref": self.ref,
            "content_revision": self.content_revision,
            "fingerprint": self.fingerprint,
            "crs": self.crs,
            "feature_count": len(self.features),
            "completeness": self.completeness,
            "scope": self.scope,
            "scope_semantics": self.scope_semantics,
            "label": self.label,
        }

    def fingerprint_full(self) -> str:
        """完整内容指纹：载荷 canonical sha256（缓存复用键必须覆盖的内容身份）。"""
        canonical = json.dumps(
            {"features": self.features, "scope": self.scope},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fc_features(payload: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """载荷 → (features, crs)。非 FeatureCollection 形状 → SnapshotError。"""
    if not isinstance(payload, dict):
        raise SnapshotError("source_unresolvable", "载荷不是 FeatureCollection 对象")
    crs_decl: Optional[str] = None
    crs_obj = payload.get("crs")
    if isinstance(crs_obj, dict):
        # GeoJSON CRS 对象（旧式）或 {\"code\": \"EPSG:…\"} 简写
        urn = crs_obj.get("code") or ""
        if isinstance(urn, str) and urn:
            crs_decl = urn
    feats = payload.get("features")
    if not isinstance(feats, list):
        raise SnapshotError("source_unresolvable", "载荷缺少 features 数组")
    return feats, crs_decl


def _normalize_crs(crs: Optional[str]) -> Optional[str]:
    if not crs:
        return None
    s = str(crs).strip().upper()
    if s in ("", "UNKNOWN", "NONE"):
        return None
    return s


async def resolve_session_ref(
    *, session_id: str, ref: str
) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[str], str, str]:
    """会话 ref → (features, content_revision, crs, fingerprint, label)。

    鉴权由调用方在提交期完成（verify_session_owner）；worker 侧复用与
    data_quality 相同的会话所有权复核（runner 内）。
    """
    from app.services.session_data import session_data_manager

    payload = await session_data_manager.get(session_id, ref)
    if payload is None:
        raise SnapshotError(
            "source_unresolvable",
            f"会话 ref 不可读（可能已过期或被逐出）: {ref[:64]}",
            ref=ref[:64],
        )
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref) or {}
    features, crs_decl = _fc_features(payload)
    revision = descriptor.get("content_revision")
    fingerprint = payload_fingerprint(features)
    label = str(descriptor.get("alias") or descriptor.get("title") or ref)
    return (
        features,
        (int(revision) if isinstance(revision, int) else None),
        _normalize_crs(crs_decl),
        fingerprint,
        label,
    )


def payload_fingerprint(features: List[Dict[str, Any]]) -> str:
    """要素集合内容指纹（canonical sha256[:32]）。"""
    canonical = json.dumps(
        features, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _resolve_project_artifact_sync(
    project_id: str, artifact_id: str, revision_no: Optional[int]
) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[str], str, str]:
    """项目产物修订 → (features, revision_no, crs, fingerprint, label)。"""
    from sqlalchemy import select as sa_select

    from app.core.database import SessionLocal
    from app.models.project import Artifact, ArtifactRevision
    from app.services.project_artifact_promotion import read_content

    with SessionLocal() as db:
        artifact = db.execute(
            sa_select(Artifact).where(Artifact.id == artifact_id)
        ).scalar_one_or_none()
        if artifact is None or artifact.project_id != project_id:
            raise SnapshotError(
                "source_unresolvable", "项目产物不存在或无权访问",
                artifact_id=artifact_id[:64],
            )
        stmt = sa_select(ArtifactRevision).where(
            ArtifactRevision.artifact_id == artifact_id)
        if revision_no is not None:
            stmt = stmt.where(ArtifactRevision.revision_no == revision_no)
        revision = db.execute(
            stmt.order_by(ArtifactRevision.revision_no.desc())
        ).scalars().first()
        if revision is None:
            raise SnapshotError(
                "source_unresolvable",
                "项目产物没有可物化修订（未晋升或内容已过期）",
                artifact_id=artifact_id[:64],
            )
        content = read_content(revision.content_location, revision.content_sha256)
        if content is None:
            raise SnapshotError(
                "source_unresolvable",
                "项目产物内容读取失败或摘要不匹配（durable 存储异常）",
                artifact_id=artifact_id[:64],
            )
    features, crs_decl = _fc_features(content)
    crs = _normalize_crs(crs_decl) or _normalize_crs(artifact.crs)
    fingerprint = payload_fingerprint(features)
    label = str(artifact.name or artifact_id)
    return features, int(revision.revision_no), crs, fingerprint, label


def apply_scope(
    features: List[Dict[str, Any]],
    scope: Dict[str, Any],
    *,
    id_keys: Tuple[str, ...] = (),
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """在同一快照上消费 scope 的实际全集（T13：不是前 N 个命中）。

    返回 (子集, 消费披露 {examined, matched, semantics, missing_ids?})。
    - where：typed 谓词 AST（feature_pages.evaluate_where 同源校验）；
    - bbox：粗超集相交（保守，语义固定披露 BBOX_SCOPE_SEMANTICS）；
      支持单框（bbox）与 G24 内部形状（boxes，1–2 框，任一相交即命中）；
    - ids：显式身份链解析，缺失 id 披露 missing_ids。
    """
    from app.services.feature_pages import (
        FEATURE_ID_KEYS,
        _feature_intersects_bbox,
        _validate_ast,
        evaluate_where,
    )

    kind = scope.get("kind")
    if kind == "where":
        try:
            validated = _validate_ast(scope.get("where"))
        except Exception as exc:  # noqa: BLE001 — feature_pages 的 typed 错误
            raise SnapshotError("invalid_scope", f"where 谓词非法: {exc}") from exc
        subset = [f for f in features if evaluate_where(validated, f)]
        return subset, {
            "examined": len(features), "matched": len(subset),
            "semantics": "where_typed_ast",
        }
    if kind == "bbox":
        boxes: List[Tuple[float, float, float, float]] = []
        if scope.get("boxes") is not None:
            for raw in scope["boxes"]:
                boxes.append(tuple(float(v) for v in raw))  # type: ignore[misc]
        else:
            sb = scope.get("bbox")
            boxes.append(tuple(float(v) for v in sb))  # type: ignore[misc]
        subset = [
            f for f in features
            if any(_feature_intersects_bbox(f, b) for b in boxes)
        ]
        return subset, {
            "examined": len(features), "matched": len(subset),
            "semantics": "bbox_intersects_coarse",
        }
    if kind == "ids":
        wanted = [str(i) for i in scope.get("ids", [])]
        wanted_set = set(wanted)
        found: Dict[str, Dict[str, Any]] = {}
        for f in features:
            props = (f or {}).get("properties") or {}
            for key in FEATURE_ID_KEYS:
                v = props.get(key)
                if v is None:
                    continue
                sval = str(v)
                if sval in wanted_set and sval not in found:
                    found[sval] = f
                    break
        missing = [i for i in wanted if i not in found]
        subset = [found[i] for i in wanted if i in found]
        return subset, {
            "examined": len(features), "matched": len(subset),
            "semantics": "id_chain_resolution", "missing_ids": missing[:50],
        }
    raise SnapshotError("invalid_scope", f"未知 scope.kind: {kind}")


def bbox_snapshot(role: str, bbox: List[float]) -> RoleSnapshot:
    """显式 bbox AOI → 快照（单矩形 Polygon 要素；语义 = AOI 本身）。"""
    w, s, e, n = (float(v) for v in bbox)
    poly = {
        "type": "Feature",
        "properties": {"_g31_bbox_aoi": f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
        },
    }
    fingerprint = payload_fingerprint([poly])
    return RoleSnapshot(
        role=role, kind="bbox", ref=f"bbox:{fingerprint[:12]}",
        features=[poly], content_revision=None, fingerprint=fingerprint,
        crs="EPSG:4326", label=f"bbox({w:.4f},{s:.4f},{e:.4f},{n:.4f})",
    )


async def resolve_selection_handle_scope(
    session_id: str, handle: str, ref_id: str
) -> Tuple[Dict[str, Any], Optional[int]]:
    """G24 选择句柄 → (规范 scope, handle 绑定 revision)（提交时一次性解析）。

    句柄是进程内 TTL 对象 —— 本函数只在**提交时**消费，返回的 scope 是可
    重放描述（bbox/where/ids）；句柄本身绝不写入持久层（任务书 WP03）。
    三重绑定校验（session/ref/handle）与 TTL 语义由 handle_store 负责；
    消费失败（not_found/stale/expired）→ typed SnapshotError。
    """
    from app.services.selection_handles import SelectionHandleError, handle_store

    try:
        entry = handle_store.get(session_id, ref_id, handle)
    except SelectionHandleError as exc:
        raise SnapshotError(
            "source_unresolvable" if exc.code in ("not_found", "stale", "expired")
            else "invalid_scope",
            f"选择句柄不可用（{exc.code}）: {exc.message}",
            handle=str(handle)[:32],
        ) from exc
    internal = entry.scope  # {kind, boxes|where|ids}（规范化内部形状）
    if internal["kind"] == "bbox":
        scope: Dict[str, Any] = {
            "kind": "bbox",
            "boxes": [[round(v, 7) for v in b] for b in internal["boxes"]],
        }
    elif internal["kind"] == "where":
        scope = {"kind": "where", "where": internal["where"]}
    else:
        scope = {"kind": "ids", "ids": list(internal["ids"])}
    return scope, entry.revision


async def resolve_role_snapshot(
    *,
    role: str,
    binding: Dict[str, Any],
    rule_type: str,
    run_session_id: Optional[str],
    actor_user_id: Optional[str],
    actor_owner_token: Optional[str],
    max_features: int,
    anchor_session_id: Optional[str] = None,
) -> RoleSnapshot:
    """角色绑定 → RoleSnapshot（提交期；鉴权 + scope 语义 + 预算拒绝）。

    complete 角色携带 scope → typed 拒绝（T12）。要素数超预算 → typed 拒绝
    （提交期即可判定；运行期截断属于 partial 语义，在 runner 内处理）。
    """
    from app.services.topology_workbench.contracts import (
        MAX_FEATURES_PER_SNAPSHOT,
        RULE_ROLE_SPECS,
    )

    kind = binding.get("kind")
    scope = binding.get("scope")

    # 选择句柄：提交时解析为 scope（T24/G24 纪律：句柄不持久化）
    if binding.get("selection_handle") is not None and scope is None:
        sid = binding.get("session_id") or run_session_id
        if not sid:
            raise SnapshotError(
                "invalid_binding",
                "selection_handle 解析需要 session_id（绑定级或运行级）",
                role=role,
            )
        if kind != "session_ref":
            raise SnapshotError(
                "invalid_binding",
                "selection_handle 只能与 session_ref 绑定共用",
                role=role,
            )
        scope, _handle_rev = await resolve_selection_handle_scope(
            str(sid), str(binding["selection_handle"]), str(binding.get("ref", ""))
        )

    if rule_type and rule_type in RULE_ROLE_SPECS:
        # complete 角色禁 scope（T12）—— 提交期校验；执行期 rule_type 为空
        # 时跳过（提交期已挡，这里只做解析）。
        complete = [r for r, req in RULE_ROLE_SPECS[rule_type].items()
                    if req.endswith("+complete")]
        if role in complete and scope is not None:
            raise SnapshotError(
                "scope_not_allowed",
                (
                    f"角色 '{role}'（{rule_type}）要求全量快照 —— 覆盖/包含/连接语义下"
                    "局部选择会产生虚假缺口或虚假越界；请改绑完整数据源，或把检查"
                    "对象换到 checked 类角色"
                ),
                role=role, rule_type=rule_type,
            )

    cap = max_features or MAX_FEATURES_PER_SNAPSHOT

    if kind == "bbox":
        return bbox_snapshot(role, binding["bbox"])

    if kind == "session_ref":
        sid = binding.get("session_id") or run_session_id
        if not sid:
            raise SnapshotError(
                "invalid_binding", "session_ref 绑定需要 session_id", role=role,
            )
        if not (anchor_session_id and str(sid) == str(anchor_session_id)):
            # 锚定会话 == 绑定会话时免校验（bridge 的 turn token 已证明该
            # 会话所有权）；其余（含跨会话绑定）必须显式鉴权。
            await _verify_session(sid, actor_user_id, actor_owner_token)
        features, revision, crs, fingerprint, label = await resolve_session_ref(
            session_id=str(sid), ref=binding["ref"]
        )
        # G24 句柄绑定的 revision 前进（handle stale）→ 消费已失败（typed）；
        # 这里在证据里保留 handle 绑定时的 revision 供审计。
        total_input = len(features)
        scope_semantics = None
        if scope is not None:
            features, disclosure = apply_scope(features, scope)
            scope_semantics = disclosure.get("semantics")
        if len(features) > cap:
            raise SnapshotError(
                "budget_exceeded",
                f"角色 '{role}' 要素数 {len(features)} 超出快照预算 {cap}",
                role=role, count=len(features), limit=cap,
            )
        return RoleSnapshot(
            role=role, kind="session_ref", ref=str(binding["ref"]),
            features=features, content_revision=revision, fingerprint=fingerprint,
            crs=crs, scope=scope, scope_semantics=scope_semantics,
            completeness="full", total_input=total_input, label=label,
        )

    if kind == "project_artifact":
        # 项目访问鉴权（404 语义在 ProjectService 内）
        await _verify_project_access(
            binding["project_id"], actor_user_id, actor_owner_token
        )
        from app.core.async_runner import run_sync

        features, revision, crs, fingerprint, label = run_sync(
            _resolve_project_artifact_sync(
                binding["project_id"], binding["artifact_id"],
                binding.get("revision_no"),
            )
        )
        total_input = len(features)
        scope_semantics = None
        if scope is not None:
            features, disclosure = apply_scope(features, scope)
            scope_semantics = disclosure.get("semantics")
        if len(features) > cap:
            raise SnapshotError(
                "budget_exceeded",
                f"角色 '{role}' 要素数 {len(features)} 超出快照预算 {cap}",
                role=role, count=len(features), limit=cap,
            )
        return RoleSnapshot(
            role=role, kind="project_artifact",
            ref=str(binding["artifact_id"]),
            features=features, content_revision=revision, fingerprint=fingerprint,
            crs=crs, scope=scope, scope_semantics=scope_semantics,
            completeness="full", total_input=total_input, label=label,
        )

    raise SnapshotError("invalid_binding", f"未知绑定 kind: {kind}", role=role)


async def _verify_session(
    session_id: str, user_id: Optional[str], owner_token: Optional[str]
) -> None:
    """会话所有权（verify_session_owner 同语义；失败 → SnapshotError）。"""
    from fastapi import HTTPException

    from app.core.database import AsyncSessionLocal
    from app.services.auth_history_bridge import verify_session_owner

    try:
        if AsyncSessionLocal is None:
            raise SnapshotError(
                "source_unauthorized",
                "异步数据库不可用 —— 无法校验会话所有权（拒绝执行）",
                session_id=str(session_id)[:64],
            )
        async with AsyncSessionLocal() as adb:
            await verify_session_owner(
                adb, session_id, user_id=user_id, owner_token=owner_token
            )
    except HTTPException as exc:
        raise SnapshotError(
            "source_unauthorized", "会话访问被拒绝（所有权校验失败）",
            session_id=str(session_id)[:64],
        ) from exc


async def _verify_project_access(
    project_id: str, user_id: Optional[str], owner_token: Optional[str]
) -> None:
    """项目访问校验（ProjectService.get_project_with_auth 同语义）。"""
    from fastapi import HTTPException

    from app.core.database import SessionLocal
    from app.services.project_service import ProjectService

    def _check() -> None:
        with SessionLocal() as db:
            project = ProjectService.get_project_with_auth(
                db=db, project_id=project_id, user_id=user_id, org_id=None,
            )
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")

    from app.core.async_runner import run_sync

    try:
        await run_sync(_check)
    except HTTPException as exc:
        raise SnapshotError(
            "source_unauthorized", "项目访问被拒绝（项目不存在或无权）",
            project_id=project_id[:64], status=int(exc.status_code),
        ) from exc


__all__ = [
    "SnapshotError", "RoleSnapshot", "resolve_role_snapshot",
    "resolve_session_ref", "apply_scope", "bbox_snapshot",
    "resolve_selection_handle_scope", "payload_fingerprint",
]
