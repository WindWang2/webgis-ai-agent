"""发布生命周期服务（WP2）—— prepare/preview/draft/publish/link/revoke。

与 atlas_batch 同构：
- 提交面（REST/Pi 工具共用）做纯规划 + typed 拒绝；
- 物化/发布事务走 durable job（``tasks.run_publication_publish``）；
- 幂等键 = publication_id:version（重复发布复用同一 job）；
- CAS：draft → published 仅此一跃；revoke 任意非 revoked → revoked。

发布后不可变：快照 JSON 在 draft 时冻结；publish 只做物化 + CAS，
不改快照内容。作者编辑原图 = 显式创建新版本（新 fingerprint）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.cartography.accessibility_manifest import build_accessibility_manifest
from app.models.map_publication import (
    PUBLICATION_STATUS_DRAFT,
    PUBLICATION_STATUS_PUBLISHED,
    PUBLICATION_STATUS_REVOKED,
    MapPublication,
    utcnow_naive,
)
from app.services.publication import store as pub_store
from app.services.publication.access import mint_share_code, share_expiry
from app.services.publication.contract import (
    DataMode,
    PublicationBlocker,
    PublicationBlockerReason,
    PublicationLayerInfo,
    PublicationMeta,
    PublicationQaSummary,
    PublicationScope,
    PublicationSnapshotDocument,
    PublicationSourceInfo,
    build_snapshot_document,
)
from app.services.publication.plan import (
    LayerPlan,
    canonical_fingerprint,
    derive_scope,
    plan_layers,
    validate_spec_schema,
)
from app.services.publication.projection import (
    DEFAULT_GEOMETRY_PRECISION,
    PUBLICATION_TOTAL_BUDGET_BYTES,
    estimate_fc_bytes,
    project_feature_collection,
)

logger = logging.getLogger(__name__)

PUBLICATION_TASK_TYPE = "publication_publish"
PUBLICATION_DISPLAY_NAME = "只读地图发布"


class PublicationRequestError(ValueError):
    """typed 请求拒绝（路由映射 400/404/409；携带机器可读 reason）。"""

    def __init__(self, reason: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status_code = status_code


# ── 计划面（preview/prepare 与 draft 共用） ───────────────────────────


async def hydrate_geojson_sources(
    spec: Dict[str, Any], session_id: str
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """geojson ref 源 → FC（复用会话数据面）。返回 ({source_key: fc}, 失败 keys)。"""
    from app.services.session_data import session_data_manager

    hydrated: Dict[str, Dict[str, Any]] = {}
    failed: List[str] = []
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    for source_key, entry in sources.items():
        if not isinstance(entry, dict) or entry.get("type") != "geojson":
            continue
        if isinstance(entry.get("inlineData"), dict):
            inline = entry["inlineData"]
            hydrated[str(source_key)] = inline
            continue
        ref = entry.get("ref") or entry.get("ref_id")
        if not (isinstance(ref, str) and ref.startswith("ref:")):
            url = entry.get("url")
            if isinstance(url, str) and url.startswith("ref:"):
                ref = url
            else:
                continue
        try:
            payload = await session_data_manager.get(session_id, ref)
        except Exception as exc:  # noqa: BLE001 — per-source best-effort（诚实记录失败）
            logger.info("publication: ref %s hydration failed: %s", ref, type(exc).__name__)
            payload = None
        if isinstance(payload, dict):
            hydrated[str(source_key)] = payload
        else:
            failed.append(str(source_key))
    return hydrated, failed


async def plan_publication_from_session(
    session_id: str,
    *,
    selected_layer_ids: Optional[Sequence[str]] = None,
    field_whitelists: Optional[Dict[str, Sequence[str]]] = None,
    state_hint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """会话 MapSpec → 发布计划（纯规划；typed 结构化结果，不抛业务失败）。

    返回 {plan: PublicationPlan-shaped dict, hydrated, spec}。
    """
    from app.services.mapspec.store import mapspec_store_instance as mapspec_store

    spec = await mapspec_store.get_mapspec(session_id, state_hint=state_hint)
    if not isinstance(spec, dict):
        raise PublicationRequestError(
            "mapspec_missing", "会话没有已提交的 MapSpec，无法发布", status_code=404
        )
    spec_revision = None
    if isinstance(state_hint, dict):
        raw_rev = state_hint.get("_cartographic_mutation_revision")
        if isinstance(raw_rev, int) and not isinstance(raw_rev, bool):
            spec_revision = raw_rev
    spec_valid, spec_msg = validate_spec_schema(spec)
    hydrated, failed_sources = await hydrate_geojson_sources(spec, session_id)
    layers, blockers = plan_layers(
        spec,
        hydrated=hydrated,
        selected_layer_ids=selected_layer_ids,
        field_whitelists=field_whitelists,
    )
    for source_key in failed_sources:
        blockers.append(PublicationBlocker(
            reason=PublicationBlockerReason.SOURCE_DATA_UNAVAILABLE,
            message="会话数据不可用（ref 解析失败）",
            source_id=source_key,
        ))
    if not spec_valid:
        blockers.append(PublicationBlocker(
            reason=PublicationBlockerReason.MAPSPEC_INVALID,
            message=f"MapSpec 未通过权威 schema 校验：{spec_msg}",
        ))
    plan = {
        "spec_fingerprint": canonical_fingerprint(spec),
        "spec_revision": spec_revision,
        "layers": [lp.model_dump(mode="json") for lp in layers],
        "blockers": [b.model_dump(mode="json") for b in blockers],
        "scope": derive_scope(spec, DEFAULT_GEOMETRY_PRECISION).model_dump(mode="json"),
        "title": str((spec.get("layout") or {}).get("title") or (spec.get("meta") or {}).get("title") or ""),
        "spec_valid": spec_valid,
    }
    return {"plan": plan, "hydrated": hydrated, "spec": spec}


def _resolve_meta(spec: Dict[str, Any], meta_params: Dict[str, Any]) -> PublicationMeta:
    fallback_title = str(
        (spec.get("layout") or {}).get("title") or (spec.get("meta") or {}).get("title") or ""
    )[:300]
    title = str(meta_params.get("title") or fallback_title or "").strip()
    if not title:
        raise PublicationRequestError("title_required", "发布标题必填（缺省回退 MapSpec 标题也为空）")
    return PublicationMeta(
        title=title,
        purpose=str(meta_params.get("purpose") or "")[:600],
        attribution=str(meta_params.get("attribution") or "")[:300],
        license=str(meta_params.get("license") or "")[:120],
        missing_values_note=str(meta_params.get("missing_values_note") or "")[:300],
    )


def _source_infos(spec: Dict[str, Any], layer_plans: List[LayerPlan]) -> List[PublicationSourceInfo]:
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    infos: List[PublicationSourceInfo] = []
    seen: set = set()
    for lp in layer_plans:
        if lp.source_id in seen or not lp.source_id:
            continue
        seen.add(lp.source_id)
        entry = sources.get(lp.source_id)
        infos.append(PublicationSourceInfo(
            source_id=lp.source_id,
            name=str((entry or {}).get("name") or lp.source_id)[:200],
            kind=str((entry or {}).get("type") or lp.source_kind)[:40],
            attribution=str((entry or {}).get("attribution") or "")[:300],
            license=str((entry or {}).get("license") or "")[:120],
            data_mode=DataMode.materialized if lp.projectable else DataMode.remote_projection,
        ))
    return infos


def build_publication_snapshot(
    *,
    spec: Dict[str, Any],
    plan: Dict[str, Any],
    hydrated: Dict[str, Dict[str, Any]],
    meta: PublicationMeta,
    product_id: str,
    version: int,
    alt_text: str = "",
    palette: str = "",
) -> Tuple[PublicationSnapshotDocument, str, List[PublicationBlocker]]:
    """计划 + 会话数据 → 冻结快照（draft 时刻；发布后不可变）。

    额外预算门：全部可投影层投影后累计字节 > 总预算 → 整个快照带
    SNAPSHOT_BUDGET_EXCEEDED 阻断（publish 阶段拒绝）。
    """
    layer_plans = [LayerPlan(**lp) for lp in plan["layers"]]
    scope = PublicationScope(**plan["scope"])
    blockers: List[PublicationBlocker] = [
        PublicationBlocker(**b) for b in plan["blockers"]
    ]

    layer_infos: List[PublicationLayerInfo] = []
    total_bytes = 0
    for lp in layer_plans:
        if not lp.projectable:
            continue
        fc = hydrated.get(lp.source_id)
        if fc is None:
            continue
        projected, blocker = project_feature_collection(
            fc,
            field_whitelist=lp.field_whitelist,
            precision=scope.geometry_precision,
        )
        if blocker is not None:
            blockers.append(PublicationBlocker(
                reason=blocker.reason, message=blocker.message,
                layer_id=lp.layer_id, source_id=lp.source_id,
            ))
            continue
        size = estimate_fc_bytes(projected)
        total_bytes += size
        layer_infos.append(PublicationLayerInfo(
            layer_id=lp.layer_id,
            title=lp.title,
            source_id=lp.source_id,
            field_whitelist=tuple(lp.field_whitelist),
            feature_count=len(projected.get("features") or []),
            data_mode=DataMode.materialized,
            reader_visibility_allowed=True,
        ))
    if total_bytes > PUBLICATION_TOTAL_BUDGET_BYTES:
        blockers.append(PublicationBlocker(
            reason=PublicationBlockerReason.SNAPSHOT_BUDGET_EXCEEDED,
            message=f"物化总字节 {total_bytes} 超过发布预算 {PUBLICATION_TOTAL_BUDGET_BYTES}",
        ))

    # WP4：可访问性清单（accessibility_manifest 孤儿模块的接线点）。
    accessibility = build_accessibility_manifest(
        spec, palette=palette, palette_context="screen", alt_text=alt_text
    )

    qa = PublicationQaSummary(
        spec_validity="schema_valid" if plan.get("spec_valid") else "schema_invalid",
        compile_ok=bool(plan.get("spec_valid")),
        checks=tuple([
            ("schema_validation", "pass" if plan.get("spec_valid") else "fail"),
            ("projection_budget", "pass" if total_bytes <= PUBLICATION_TOTAL_BUDGET_BYTES else "fail"),
            ("accessibility_manifest", "complete" if accessibility.get("complete") else "incomplete"),
        ]),
    )
    document, fingerprint = build_snapshot_document(
        product_id=product_id,
        version=version,
        meta=meta,
        spec=spec,
        layers=layer_infos,
        sources=_source_infos(spec, layer_plans),
        scope=scope,
        accessibility=accessibility,
        qa=qa,
        blockers=blockers,
    )
    return document, fingerprint, blockers


def selected_or_all(layer_plans: List[LayerPlan]) -> List[str]:
    return [lp.layer_id for lp in layer_plans if lp.projectable]


def publish_blockers(snapshot: Dict[str, Any]) -> List[PublicationBlocker]:
    """快照中的阻断（publish 门：license 缺失/预算超限/schema 无效 → 拒绝发布）。"""
    blockers = [PublicationBlocker(**b) for b in (snapshot.get("blockers") or [])]
    hard = {
        PublicationBlockerReason.LICENSE_UNKNOWN,
        PublicationBlockerReason.SNAPSHOT_BUDGET_EXCEEDED,
        PublicationBlockerReason.MAPSPEC_INVALID,
    }
    return [b for b in blockers if b.reason in hard]


def license_blocker_if_missing(meta: PublicationMeta) -> Optional[PublicationBlocker]:
    if not meta.license:
        return PublicationBlocker(
            reason=PublicationBlockerReason.LICENSE_UNKNOWN,
            message="发布许可未声明（license 必填；版权不明不可发布）",
        )
    return None


# ── 生命周期面 ─────────────────────────────────────────────────────────


async def create_draft(
    db: AsyncSession,
    *,
    session_id: str,
    owner_token: Optional[str],
    creator_id: Optional[str],
    org_id: Optional[str],
    meta_params: Dict[str, Any],
    selected_layer_ids: Optional[Sequence[str]] = None,
    field_whitelists: Optional[Dict[str, Sequence[str]]] = None,
    alt_text: str = "",
    palette: str = "",
    product_id: Optional[str] = None,
    base_publication_id: Optional[str] = None,
) -> MapPublication:
    """创建发布草稿（纯私有：无公开副作用、无 share link）。

    ``base_publication_id``：显式新版本（同 product_id，version+1，meta 继承
    上一版本除非覆盖）。
    """
    planned = await plan_publication_from_session(
        session_id,
        selected_layer_ids=selected_layer_ids,
        field_whitelists=field_whitelists,
    )
    spec = planned["spec"]
    plan = planned["plan"]
    meta = _resolve_meta(spec, meta_params)

    if base_publication_id:
        base = await pub_store.get_publication(db, base_publication_id)
        if base is None:
            raise PublicationRequestError(
                "publication_not_found", "基础发布版本不存在", status_code=404
            )
        product_id = base.product_id
        version = await pub_store.next_version_for_product(db, product_id)
        # meta 继承：未显式给的键沿用上一版本（作者少打字，语义显式）。
        base_meta = ((base.snapshot or {}).get("meta") or {})
        merged = {k: v for k, v in base_meta.items() if v}
        merged.update({k: v for k, v in meta_params.items() if v})
        meta = _resolve_meta(spec, merged)
    else:
        product_id = product_id or uuid.uuid4().hex
        version = 1

    document, fingerprint, blockers = build_publication_snapshot(
        spec=spec,
        plan=plan,
        hydrated=planned["hydrated"],
        meta=meta,
        product_id=product_id,
        version=version,
        alt_text=alt_text,
        palette=palette,
    )
    hard = [b for b in blockers if b.reason in {
        PublicationBlockerReason.MAPSPEC_INVALID,
        PublicationBlockerReason.SNAPSHOT_BUDGET_EXCEEDED,
    }]
    if hard:
        raise PublicationRequestError(
            hard[0].reason.value,
            f"发布计划被阻断：{hard[0].message}",
            status_code=422,
        )

    row = MapPublication(
        id=uuid.uuid4().hex,
        product_id=product_id,
        version=version,
        session_id=session_id,
        title=meta.title,
        status=PUBLICATION_STATUS_DRAFT,
        access_policy="private",
        spec_fingerprint=fingerprint,
        spec_revision=plan.get("spec_revision"),
        snapshot=document.model_dump(mode="json"),
        materialized=0,
        creator_id=creator_id,
        org_id=org_id,
        owner_token=owner_token,
    )
    db.add(row)
    await db.commit()
    return row


async def require_owned_publication(
    db: AsyncSession,
    publication_id: str,
    *,
    creator_id: Optional[str],
    owner_token: Optional[str],
) -> MapPublication:
    """作者侧归属校验（三元组一致性；与 durable job 的归属链同语义）。"""
    publication = await pub_store.get_publication(db, publication_id)
    if publication is None:
        raise PublicationRequestError(
            "publication_not_found", "发布不存在", status_code=404
        )
    if not _owns(publication, creator_id, owner_token):
        raise PublicationRequestError(
            "access_denied", "无权操作该发布", status_code=403
        )
    return publication


def _owns(publication: MapPublication, creator_id: Optional[str], owner_token: Optional[str]) -> bool:
    """归属判定：owner_token 常量时间匹配 或 creator_id 精确匹配。

    匿名会话只有 owner_token 链；登录会话 creator_id 链。两者都缺失 → 拒。
    """
    import hashlib
    import hmac as _hmac

    from app.core.owner_tokens import OwnerTokenDigest

    if owner_token and publication.owner_token:
        stored = publication.owner_token
        if isinstance(stored, OwnerTokenDigest):
            stored = str(stored)
        digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        return _hmac.compare_digest(digest, stored)
    if creator_id and publication.creator_id:
        return creator_id == publication.creator_id
    return False


async def publish_cas(
    db: AsyncSession,
    publication: MapPublication,
    *,
    confirm: bool,
) -> Tuple[bool, List[PublicationBlocker]]:
    """发布 CAS：draft → published。``confirm`` 必须显式为 True。

    发布门：快照硬阻断（license/预算/schema）任一存在 → typed 拒绝。
    """
    if confirm is not True:
        raise PublicationRequestError(
            "confirmation_required",
            "发布需要显式确认（confirm=true）——对外可见副作用由作者明确批准",
            status_code=400,
        )
    hard = publish_blockers(publication.snapshot or {})
    license_blocker = license_blocker_if_missing(
        PublicationMeta(**(publication.snapshot or {}).get("meta", {}))
    )
    if license_blocker is not None:
        hard.append(license_blocker)
    if hard:
        raise PublicationRequestError(
            hard[0].reason.value,
            f"发布被阻断：{hard[0].message}",
            status_code=422,
        )
    if not pub_store.artifacts_ready(publication.id, [
        lyr["layer_id"] for lyr in (publication.snapshot or {}).get("layers", [])
    ]):
        raise PublicationRequestError(
            "artifacts_not_ready",
            "物化产物未就绪（发布 job 尚未完成或失败）",
            status_code=409,
        )
    moved = await pub_store.transition_status(
        db, publication, expected=(PUBLICATION_STATUS_DRAFT,), target=PUBLICATION_STATUS_PUBLISHED
    )
    return moved, hard


async def mint_link(
    db: AsyncSession,
    publication: MapPublication,
    *,
    ttl_days: Optional[int] = None,
) -> str:
    """签发可撤销只读链接（显式动作；仅 published 可签发）。"""
    if publication.status != PUBLICATION_STATUS_PUBLISHED:
        raise PublicationRequestError(
            "not_published", "仅已发布的版本可签发链接", status_code=409
        )
    code = mint_share_code()
    publication.access_policy = "link"
    publication.share_code = code
    publication.share_expires_at = share_expiry(ttl_days)
    publication.revoked_at = None
    await db.commit()
    return code


async def revoke_link(db: AsyncSession, publication: MapPublication) -> None:
    publication.share_code = None
    publication.share_expires_at = None
    if publication.access_policy == "link":
        publication.access_policy = "private"
    await db.commit()


async def revoke_publication(db: AsyncSession, publication: MapPublication) -> bool:
    """撤销发布（终态可见；读者侧立即 404）。"""
    return await pub_store.transition_status(
        db, publication,
        expected=(PUBLICATION_STATUS_DRAFT, PUBLICATION_STATUS_PUBLISHED),
        target=PUBLICATION_STATUS_REVOKED,
    )


__all__ = [
    "PUBLICATION_TASK_TYPE",
    "PUBLICATION_DISPLAY_NAME",
    "PublicationRequestError",
    "hydrate_geojson_sources",
    "plan_publication_from_session",
    "build_publication_snapshot",
    "publish_blockers",
    "license_blocker_if_missing",
    "create_draft",
    "require_owned_publication",
    "publish_cas",
    "mint_link",
    "revoke_link",
    "revoke_publication",
    "utcnow_naive",
]
