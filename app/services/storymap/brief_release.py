"""G11 —— 简报发布冻结与私有分享（BRF-04/BRF-06/BRF-07）。

冻结 = 预检门 + 不可变 release：预检 ``blocked`` 一律拒绝；HTML/CSV
载荷进 CAS（内容寻址，sha256 即键）；manifest 记录文件清单/证据/指纹。
幂等：同内容（content_sha256）重复提交返回既有 release，不重复计版。

分享沿 report.py 先例（share_code + ttl + 撤销 + 公共投影限流）：
share token 只授予**该 release** 的只读素材 —— 不是读取整个项目/任意
ref 的万能钥匙。已下载的离线副本无法远程收回（UI 层披露）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.storymap.brief_export import (
    build_brief_bundle,
    build_fact_csv_rows,
    build_release_manifest,
    render_brief_html,
    rows_to_csv,
)
from app.lib.storymap.brief_preflight import PreflightReport, run_preflight
from app.lib.storymap.brief_spec import MapBriefSpec
from app.models.db_model import Conversation
from app.models.map_brief import MapBriefRelease
from app.services.storymap.brief_store import BriefNotFound, brief_row, load_brief


class PreflightBlocked(Exception):
    """预检阻断（强事实错误/跨 owner/必需图件缺失）。"""

    def __init__(self, report: PreflightReport):
        self.report = report
        super().__init__(
            "preflight blocked: " + "; ".join(
                f"{i.check}:{i.verdict}" for i in report.items
                if i.verdict == "fail"))


class ReleaseNotFound(Exception):
    """release 不存在或不属于该会话（404 语义）。"""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _session_projection(conv, *, owner_token: str = ""):
    """复用编译器的会话输入装载（台账/载荷/trace/MapSpec 指纹）。"""
    from app.services.storymap.brief_compiler import (
        _load_session_inputs,
    )

    records, payloads, _trace, mapspec_fp, _msgs = await _load_session_inputs(
        conv, owner_token=owner_token)
    mapspec: Optional[Dict[str, Any]] = None
    if mapspec_fp:
        from app.services.mapspec.store import mapspec_store_instance

        try:
            stored = await mapspec_store_instance.get_mapspec(str(conv.id))
            if isinstance(stored, dict):
                mapspec = stored
        except Exception:  # noqa: BLE001
            mapspec = None
    return records, payloads, mapspec_fp, mapspec


def _widget_data_map(spec: MapBriefSpec, payloads: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for widget in spec.linked_widgets:
        ref = widget.ref or widget.id
        data = payloads.get(ref)
        if isinstance(data, Mapping):
            out[widget.id] = data
        elif widget.data:
            out[widget.id] = widget.data
    return out


def _render_static_maps(
    spec: MapBriefSpec, mapspec: Optional[Mapping[str, Any]],
) -> Dict[str, str]:
    """map_view 块 → 静态 SVG（服务端渲染；缺席/失败 → 不伪造图件）。"""
    maps: Dict[str, str] = {}
    if not mapspec:
        return maps
    from app.services.mapspec_to_svg import compile_mapspec_to_svg

    for block in spec.blocks:
        if block.kind != "map_view" or block.map_view is None:
            continue
        try:
            svg = compile_mapspec_to_svg(
                dict(mapspec), width=960, height=600, target_dpi=150)
            if isinstance(svg, str) and svg.strip():
                maps[block.id] = svg
        except Exception:  # noqa: BLE001 — 渲染失败 → 真实告警（HTML 内披露）
            continue
    return maps


async def preflight_brief(
    db: AsyncSession, conv: Conversation, *, owner_token: str = ""
) -> PreflightReport:
    """发布预检（不落任何状态；前端/工具共用）。"""
    spec = await load_brief(db, conv)
    records, payloads, mapspec_fp, _mapspec = await _session_projection(
        conv, owner_token=owner_token)
    widget_data = _widget_data_map(spec, payloads)
    return run_preflight(
        spec, records=records, payloads=payloads, widget_data=widget_data,
        mapspec_available=bool(mapspec_fp))


async def freeze_brief_release(
    db: AsyncSession,
    conv: Conversation,
    *,
    allow_draft: bool = False,
    owner_token: str = "",
) -> Dict[str, Any]:
    """冻结发布（幂等）。返回 manifest + release 元信息。"""
    row = await brief_row(db, conv)
    spec = await load_brief(db, conv)
    records, payloads, mapspec_fp, mapspec = await _session_projection(
        conv, owner_token=owner_token)
    widget_data = _widget_data_map(spec, payloads)

    report = run_preflight(
        spec, records=records, payloads=payloads, widget_data=widget_data,
        mapspec_available=bool(mapspec_fp))
    if report.outcome == "blocked":
        raise PreflightBlocked(report)
    draft = report.outcome != "deliverable"
    if draft and not allow_draft:
        raise PreflightBlocked(report)

    static_maps = await asyncio.to_thread(
        _render_static_maps, spec, mapspec)
    spec_sha = _sha256_bytes(
        json.dumps(spec.model_dump(mode="json"), ensure_ascii=False,
                   sort_keys=True, separators=(",", ":")).encode("utf-8"))

    # 幂等键（BRF-O06）：只含**语义内容**指纹 —— brief/revision/spec/产物
    # 台账指纹/draft 位。墙钟时间戳（bundle.generated_at / manifest
    # created_at）与由其派生的 html/csv 字节哈希**不进键**：同一语义
    # 内容跨秒重复提交必须返回既有 release，而非另建版本（时间戳等非
    # 语义字段单独记录在 release 行/manifest 里）。
    ledger_sha = _sha256_bytes(
        json.dumps(
            {ref: (rec.get("artifact_sha256")
                   or _sha256_bytes(json.dumps(rec, ensure_ascii=False,
                                               sort_keys=True,
                                               default=str).encode("utf-8")))
             for ref, rec in sorted(records.items())},
            ensure_ascii=False, sort_keys=True).encode("utf-8"))
    content_sha = _sha256_bytes(
        f"{row.id}|{row.revision}|{spec_sha}|{ledger_sha}|{draft}".encode())

    existing = await db.scalar(select(MapBriefRelease).where(
        MapBriefRelease.content_sha256 == content_sha))
    if existing is not None:
        return {"release_id": existing.id,
                "release_no": existing.release_no,
                "already_released": True,
                "manifest": json.loads(existing.manifest)}

    bundle = build_brief_bundle(
        spec, payloads=payloads, static_maps=static_maps,
        release_meta={}, draft=draft)
    html = render_brief_html(bundle)
    html_bytes = html.encode("utf-8")
    html_sha = _sha256_bytes(html_bytes)
    csv_text = rows_to_csv(build_fact_csv_rows(spec))
    csv_bytes = csv_text.encode("utf-8")
    csv_sha = _sha256_bytes(csv_bytes)

    from app.services.durable_blob_store import get_filesystem_blob_store

    store = get_filesystem_blob_store()
    store.put_blob(html_sha, html_bytes, "html")
    store.put_blob(csv_sha, csv_bytes, "csv")

    release_no_row = await db.scalar(select(func.max(MapBriefRelease.release_no)).where(
        MapBriefRelease.brief_id == row.id))
    release_no = int(release_no_row or 0) + 1

    manifest = build_release_manifest(
        release_id="pending", release_no=release_no, brief_id=row.id,
        frozen_revision=row.revision, draft=draft, bundle=bundle,
        html_sha256=html_sha, html_bytes=len(html_bytes),
        csv_sha256=csv_sha, csv_bytes=len(csv_bytes), spec_sha256=spec_sha)
    manifest["content_sha256"] = content_sha

    release = MapBriefRelease(
        brief_id=row.id, conversation_id=conv.id,
        release_no=release_no, frozen_revision=row.revision,
        status="ready", manifest=json.dumps(manifest, ensure_ascii=False),
        content_sha256=content_sha, html_object_key=html_sha)
    db.add(release)
    try:
        await db.flush()
    except IntegrityError:
        # 并发双提交（同 content 或 release_no 竞争）：内容寻址无半包；
        # 回滚本帧未落内容后按幂等语义返回既有 release。
        await db.rollback()
        existing = await db.scalar(select(MapBriefRelease).where(
            MapBriefRelease.content_sha256 == content_sha))
        if existing is not None:
            return {"release_id": existing.id,
                    "release_no": existing.release_no,
                    "already_released": True,
                    "manifest": json.loads(existing.manifest)}
        raise
    manifest = dict(manifest, release_id=release.id)
    release.manifest = json.dumps(manifest, ensure_ascii=False)
    await db.flush()
    return {"release_id": release.id, "release_no": release_no,
            "already_released": False, "manifest": manifest}


async def list_releases(
    db: AsyncSession, conv: Conversation
) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(MapBriefRelease)
        .where(MapBriefRelease.conversation_id == conv.id)
        .order_by(MapBriefRelease.release_no.desc())
    )).scalars().all()
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            manifest = json.loads(row.manifest)
        except json.JSONDecodeError:
            manifest = {"corrupt": True}
        out.append({
            "release_id": row.id, "release_no": row.release_no,
            "frozen_revision": row.frozen_revision, "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "draft": bool(manifest.get("draft")),
            "fact_stats": manifest.get("fact_stats", {}),
            "counts": manifest.get("counts", {}),
            "shared": bool(row.share_code),
            "share_expires_at": row.share_expires_at.isoformat()
            if row.share_expires_at else None,
        })
    return out


async def get_release(
    db: AsyncSession, conv: Conversation, release_id: str
) -> MapBriefRelease:
    row = await db.scalar(select(MapBriefRelease).where(
        MapBriefRelease.id == release_id,
        MapBriefRelease.conversation_id == conv.id))
    if row is None:
        raise ReleaseNotFound(release_id)
    return row


async def get_release_html(
    db: AsyncSession, conv: Conversation, release_id: str
) -> bytes:
    """owner 私有下载（经 CAS digest 校验读）。"""
    row = await get_release(db, conv, release_id)
    from app.services.durable_blob_store import get_filesystem_blob_store

    data = get_filesystem_blob_store().get_blob(
        row.html_object_key, expected_sha256=row.html_object_key)
    if data is None:
        raise ReleaseNotFound(f"release payload missing: {release_id}")
    return data


async def get_release_csv(
    db: AsyncSession, conv: Conversation, release_id: str
) -> Optional[bytes]:
    row = await get_release(db, conv, release_id)
    try:
        manifest = json.loads(row.manifest)
    except json.JSONDecodeError:
        return None
    csv_entry = next((f for f in manifest.get("files", [])
                      if f.get("kind") == "csv"), None)
    if not csv_entry or not csv_entry.get("sha256"):
        return None
    from app.services.durable_blob_store import get_filesystem_blob_store

    return get_filesystem_blob_store().get_blob(
        csv_entry["sha256"], expected_sha256=csv_entry["sha256"])


# ── 分享（沿 report.py 先例；scope = 单 release 只读素材） ────────────


async def create_share(
    db: AsyncSession, conv: Conversation, release_id: str, *, ttl_days: int = 7
) -> Dict[str, Any]:
    row = await get_release(db, conv, release_id)
    ttl = max(1, min(int(ttl_days), 30))
    code = secrets.token_urlsafe(12)
    expires = datetime.now(timezone.utc) + timedelta(days=ttl)
    row.share_code = code
    # naive UTC 落库（F-04 纪律）。
    row.share_expires_at = expires.replace(tzinfo=None)
    await db.flush()
    return {"share_code": code,
            "share_url": f"/api/v1/briefs/shared/{code}/view",
            "expires_at": expires.isoformat()}


async def revoke_share(
    db: AsyncSession, conv: Conversation, release_id: str
) -> Dict[str, Any]:
    row = await get_release(db, conv, release_id)
    row.share_code = None
    row.share_expires_at = None
    await db.flush()
    return {"revoked": True, "release_id": release_id}


def share_expired(expires_at: Optional[datetime]) -> bool:
    """过期判定（naive UTC 容错，report.py 同款）。"""
    if expires_at is None:
        return False
    aware = expires_at if expires_at.tzinfo else expires_at.replace(
        tzinfo=timezone.utc)
    return aware <= datetime.now(timezone.utc)


async def get_shared_release(db: AsyncSession, share_code: str) -> Optional[MapBriefRelease]:
    """公开分享读取（限流在路由层）。只暴露该 release 的 html 载荷。"""
    if not share_code or len(share_code) > 64:
        return None
    row = await db.scalar(select(MapBriefRelease).where(
        MapBriefRelease.share_code == share_code))
    if row is None or share_expired(row.share_expires_at):
        return None
    return row


async def get_shared_release_html(row: MapBriefRelease) -> Optional[bytes]:
    from app.services.durable_blob_store import get_filesystem_blob_store

    return get_filesystem_blob_store().get_blob(
        row.html_object_key, expected_sha256=row.html_object_key)


def public_release_projection(row: MapBriefRelease) -> Dict[str, Any]:
    """公共元数据投影（不含 session/conversation/内部 ref）。"""
    try:
        manifest = json.loads(row.manifest)
    except json.JSONDecodeError:
        manifest = {}
    return {
        "release_no": row.release_no,
        "status": row.status,
        "draft": bool(manifest.get("draft")),
        "title": manifest.get("title", ""),
        "fact_stats": manifest.get("fact_stats", {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.share_expires_at.isoformat()
        if row.share_expires_at else None,
    }


__all__ = [
    "PreflightBlocked",
    "ReleaseNotFound",
    "preflight_brief",
    "freeze_brief_release",
    "list_releases",
    "get_release",
    "get_release_html",
    "get_release_csv",
    "create_share",
    "revoke_share",
    "get_shared_release",
    "get_shared_release_html",
    "public_release_projection",
    "share_expired",
]
