"""G11 —— 证据化地图简报 REST 路由（BRF-01~07 用户入口）。

鉴权纪律与 storymap.py 同源：会话域挂 ``require_owned_session``（SEC-08
统一 404 不泄露存在性）；无状态面不存在 —— 简报一切操作都是会话/资产
作用域。公共分享面沿 report.py 先例（限流 + 公共投影 + CSP sandbox）。

错误映射：ValidationError/ValueError→422；RevisionConflict→409；
BriefNotFound/ReleaseNotFound→404；PreflightBlocked→409（携带报告）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.report_sharing import limit_public_report_access
from app.core.auth import get_current_user, get_owner_token
from app.core.database import get_async_db
from app.lib.storymap.brief_spec import MapBriefSpec
from app.lib.storymap.spec import assert_json_depth, strip_surrogates
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.storymap.brief_compiler import compile_brief_draft
from app.services.storymap.brief_release import (
    PreflightBlocked,
    ReleaseNotFound,
    create_share,
    freeze_brief_release,
    get_release,
    get_release_csv,
    get_release_html,
    get_shared_release,
    get_shared_release_html,
    list_releases,
    preflight_brief,
    public_release_projection,
    revoke_share,
)
from app.services.storymap.brief_store import (
    BriefCorrupt,
    BriefNotFound,
    RevisionConflict,
    add_block,
    add_chapter,
    brief_row,
    compute_stale,
    create_brief,
    delete_block,
    delete_chapter,
    load_brief,
    merge_regenerated,
    save_brief,
    update_block,
    update_chapter,
)

router = APIRouter(prefix="/briefs", tags=["MapBrief"])


# ── 请求 schema ─────────────────────────────────────────────────────


class BriefDraftRequest(BaseModel):
    title: Optional[str] = None
    # 生成器类型不由客户端声明：当前管线恒 deterministic，provenance
    # 标签不可谎报（LLM 接入位在服务端代码层扩展，评审 #P3）。


class BriefPatchRequest(BaseModel):
    title: Optional[str] = None
    expected_revision: int = Field(..., ge=1)


class BlockCreateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    block: Dict[str, Any]


class BlockPatchRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    patch: Dict[str, Any]


class ChapterCreateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    chapter: Dict[str, Any]


class ChapterPatchRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    patch: Dict[str, Any]


class RegenerateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    mode: str = "metrics_only"  # metrics_only | unlocked_text_only


class FreezeRequest(BaseModel):
    allow_draft: bool = False


class ShareRequest(BaseModel):
    ttl_days: int = Field(7, ge=1, le=30)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RevisionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (BriefNotFound, ReleaseNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, BriefCorrupt):
        return HTTPException(status_code=422, detail={
            "error": "brief_corrupt", "message": str(exc)})
    if isinstance(exc, PreflightBlocked):
        return HTTPException(status_code=409, detail={
            "error": "preflight_blocked",
            "report": exc.report.to_dict()})
    return HTTPException(status_code=422, detail=str(exc))


def _summary(spec: MapBriefSpec, revision: int, brief_id: str) -> Dict[str, Any]:
    return {
        "brief_id": brief_id,
        "revision": revision,
        "title": spec.metadata.title,
        "chapters": len(spec.chapters),
        "blocks": len(spec.blocks),
        "facts": len(spec.facts),
        "fact_stats": {
            s: sum(1 for f in spec.facts if f.status == s)
            for s in ("verified", "warning", "unverified")},
    }


# ── 草稿与读取 ───────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/draft")
async def create_brief_draft(
    session_id: str,
    req: BriefDraftRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Any:
    """从会话真实产物生成简报草稿（幂等创建：已存在 → 409 引导编辑）。"""
    try:
        spec = await compile_brief_draft(
            conv, db=db, title=req.title,
            owner_token=owner_token or "")
        result = await create_brief(db, conv, spec)
        await db.commit()
        return {"saved": True, **result,
                "summary": _summary(spec, result["revision"], result["brief_id"])}
    except Exception as exc:  # noqa: BLE001 — 统一映射
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}")
async def get_session_brief(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Any:
    """读会话简报完整 spec + stale 标记（产物更新 → 受影响自动块披露）。"""
    try:
        spec = await load_brief(db, conv)
        row = await brief_row(db, conv)
        from app.services.storymap.brief_compiler import _load_session_inputs

        records, _payloads, _t, _f, _m = await _load_session_inputs(
            conv, owner_token=owner_token or "", include_payloads=False)
        stale = compute_stale(spec, current_records=records)
        return {
            "brief_id": row.id, "revision": row.revision,
            "spec": spec.model_dump(mode="json"), "stale": stale,
            "summary": _summary(spec, row.revision, row.id),
        }
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.patch("/sessions/{session_id}")
async def patch_brief_meta(
    session_id: str,
    req: BriefPatchRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    """改标题（乐观 revision）。"""
    try:
        spec = await load_brief(db, conv)
        if req.title is not None:
            spec = spec.model_copy(update={
                "metadata": spec.metadata.model_copy(
                    update={"title": strip_surrogates(req.title)})})
        result = await save_brief(db, conv, spec,
                                  expected_revision=req.expected_revision)
        await db.commit()
        return {"saved": True, **result}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


# ── 块/章节编辑（BRF-03） ────────────────────────────────────────────


@router.post("/sessions/{session_id}/blocks")
async def create_block(
    session_id: str,
    req: BlockCreateRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        assert_json_depth(req.block)
        spec = await load_brief(db, conv)
        spec = add_block(spec, req.block)
        result = await save_brief(db, conv, spec,
                                  expected_revision=req.expected_revision)
        await db.commit()
        return {"saved": True, **result, "spec": spec.model_dump(mode="json")}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.patch("/sessions/{session_id}/blocks/{block_id}")
async def patch_block(
    session_id: str,
    block_id: str,
    req: BlockPatchRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    """更新块（locked 块内容编辑需先解锁；409 = revision 冲突）。"""
    try:
        assert_json_depth(req.patch)
        spec = await load_brief(db, conv)
        spec = update_block(spec, block_id, req.patch)
        result = await save_brief(db, conv, spec,
                                  expected_revision=req.expected_revision)
        await db.commit()
        return {"saved": True, **result, "spec": spec.model_dump(mode="json")}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.delete("/sessions/{session_id}/blocks/{block_id}")
async def remove_block(
    session_id: str,
    block_id: str,
    expected_revision: int = Query(..., ge=1),
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        spec = await load_brief(db, conv)
        spec = delete_block(spec, block_id)
        result = await save_brief(db, conv, spec,
                                  expected_revision=expected_revision)
        await db.commit()
        return {"saved": True, **result, "spec": spec.model_dump(mode="json")}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/chapters")
async def create_chapter(
    session_id: str,
    req: ChapterCreateRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        assert_json_depth(req.chapter)
        spec = await load_brief(db, conv)
        spec = add_chapter(spec, req.chapter)
        result = await save_brief(db, conv, spec,
                                  expected_revision=req.expected_revision)
        await db.commit()
        return {"saved": True, **result, "spec": spec.model_dump(mode="json")}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.patch("/sessions/{session_id}/chapters/{chapter_id}")
async def patch_chapter(
    session_id: str,
    chapter_id: str,
    req: ChapterPatchRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        assert_json_depth(req.patch)
        spec = await load_brief(db, conv)
        spec = update_chapter(spec, chapter_id, req.patch)
        result = await save_brief(db, conv, spec,
                                  expected_revision=req.expected_revision)
        await db.commit()
        return {"saved": True, **result, "spec": spec.model_dump(mode="json")}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.delete("/sessions/{session_id}/chapters/{chapter_id}")
async def remove_chapter(
    session_id: str,
    chapter_id: str,
    expected_revision: int = Query(..., ge=1),
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        spec = await load_brief(db, conv)
        spec = delete_chapter(spec, chapter_id)
        result = await save_brief(db, conv, spec,
                                  expected_revision=expected_revision)
        await db.commit()
        return {"saved": True, **result, "spec": spec.model_dump(mode="json")}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/regenerate")
async def regenerate_brief(
    session_id: str,
    req: RegenerateRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Any:
    """重生成（manual/locked 块永不覆盖；BRF-O03/O11）。"""
    try:
        current = await load_brief(db, conv)
        fresh = await compile_brief_draft(
            conv, db=db, title=current.metadata.title,
            owner_token=owner_token or "")
        spec = merge_regenerated(current, fresh, mode=req.mode)
        result = await save_brief(db, conv, spec,
                                  expected_revision=req.expected_revision)
        await db.commit()
        return {"saved": True, **result, "spec": spec.model_dump(mode="json")}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


# ── 证据核对（BRF-03 联动："这个比例是怎么来的？"） ──────────────────


@router.get("/sessions/{session_id}/evidence/{fact_id}")
async def inspect_evidence(
    session_id: str,
    fact_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Any:
    """事实 → 完整链路（绑定/权威值/复算/依赖事实/产物投影）。"""
    try:
        spec = await load_brief(db, conv)
        from app.services.storymap.brief_compiler import _load_session_inputs

        records, payloads, _t, _f, _m = await _load_session_inputs(
            conv, owner_token=owner_token or "")
        from app.lib.storymap.brief_factcheck import (
            check_fact,
            resolve_field_path,
        )

        fact = next((f for f in spec.facts if f.fact_id == fact_id), None)
        if fact is None:
            raise HTTPException(status_code=404, detail=f"fact {fact_id} not found")
        values = {f.fact_id: f.value for f in spec.facts}
        outcome = check_fact(fact, records=records, payloads=payloads,
                             fact_values=values)
        authoritative = None
        if fact.basis.artifact_ref and fact.basis.field_path:
            authoritative, _ = resolve_field_path(
                payloads.get(fact.basis.artifact_ref), fact.basis.field_path)
        deps = {}
        for dep_id in (fact.denominator_fact_id, fact.numerator_fact_id,
                       fact.baseline_fact_id):
            if dep_id:
                dep = next((f for f in spec.facts if f.fact_id == dep_id), None)
                if dep is not None:
                    deps[dep_id] = {"label": dep.label, "value": dep.value,
                                    "unit": dep.unit, "status": dep.status}
        record = records.get(fact.basis.artifact_ref)
        return {
            "fact": fact.model_dump(mode="json"),
            "check": {"status": outcome.status, "reasons": outcome.reasons,
                      "recompute": outcome.recompute,
                      "contradicted": outcome.contradicted},
            "authoritative_value": authoritative,
            "dependencies": deps,
            "artifact": {
                "registered": record is not None,
                "record": record,
            } if fact.basis.artifact_ref else None,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


# ── 预检与发布（BRF-04） ─────────────────────────────────────────────


@router.post("/sessions/{session_id}/preflight")
async def run_brief_preflight(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Any:
    try:
        report = await preflight_brief(db, conv, owner_token=owner_token or "")
        return report.to_dict()
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/releases")
async def freeze_release(
    session_id: str,
    req: FreezeRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Any:
    """冻结发布（幂等；预检 blocked → 409 携带报告）。"""
    try:
        result = await freeze_brief_release(
            db, conv, allow_draft=req.allow_draft,
            owner_token=owner_token or "")
        await db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/releases")
async def list_brief_releases(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        return {"releases": await list_releases(db, conv)}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/releases/{release_id}/manifest")
async def get_release_manifest(
    session_id: str,
    release_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        row = await get_release(db, conv, release_id)
        return json.loads(row.manifest)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="release manifest corrupt") from exc
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/releases/{release_id}/download")
async def download_release_html(
    session_id: str,
    release_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    """owner 私有 HTML 下载（CAS digest 校验）。"""
    try:
        data = await get_release_html(db, conv, release_id)
        release_no = await release_no_of(db, conv, release_id)
        return Response(
            content=data, media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="brief-release-v{release_no}.html"',
            })
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/releases/{release_id}/facts.csv")
async def download_release_csv(
    session_id: str,
    release_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> Any:
    try:
        data = await get_release_csv(db, conv, release_id)
        if data is None:
            raise HTTPException(status_code=404, detail="facts csv not available")
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="brief-facts-{release_id}.csv"',
            })
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


async def release_no_of(db, conv, release_id) -> str:
    row = await get_release(db, conv, release_id)
    return str(row.release_no)


# ── 分享（BRF-06；沿 report.py 先例） ────────────────────────────────


@router.post("/sessions/{session_id}/releases/{release_id}/share")
async def share_release(
    session_id: str,
    release_id: str,
    req: ShareRequest,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
) -> Any:
    """显式分享（默认不分享；token 只授予该 release 只读素材）。"""
    try:
        result = await create_share(db, conv, release_id, ttl_days=req.ttl_days)
        await db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.delete("/sessions/{session_id}/releases/{release_id}/share")
async def unshare_release(
    session_id: str,
    release_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
) -> Any:
    """撤销分享（服务端读取即刻失败；已下载离线副本无法收回 —— UI 披露）。"""
    try:
        result = await revoke_share(db, conv, release_id)
        await db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.get("/shared/{share_code}", dependencies=[
    Depends(limit_public_report_access)])
async def shared_brief_info(share_code: str, db: AsyncSession = Depends(get_async_db)) -> Any:
    """公共元数据投影（不含 session/内部 ref）。"""
    row = await get_shared_release(db, share_code)
    if row is None:
        raise HTTPException(status_code=404, detail="share link invalid or expired")
    return public_release_projection(row)


@router.get("/shared/{share_code}/view", dependencies=[
    Depends(limit_public_report_access)])
async def shared_brief_view(share_code: str, db: AsyncSession = Depends(get_async_db)) -> Any:
    """匿名只读查看冻结 HTML（CSP sandbox；单文件自解压，无服务端状态）。"""
    row = await get_shared_release(db, share_code)
    if row is None:
        raise HTTPException(status_code=404, detail="share link invalid or expired")
    data = await get_shared_release_html(row)
    if data is None:
        raise HTTPException(status_code=404, detail="release payload missing")
    return HTMLResponse(
        content=data,
        headers={
            "Content-Security-Policy":
                "default-src 'none'; style-src 'unsafe-inline'; "
                "img-src data:; script-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })
