"""Series Production 执行器 —— 逐成员真实渲染 + 指纹缓存 + 成员 QA（WP5）。

渲染路径（全部既有权威，零新渲染器）：
- 成员有 ``print_layout`` → G34 纸面出版链（``print_layout_authoring
  .service.submit_export``：真实 SVG/PDF/PNG + 毫米几何 + manifest sidecar）；
- 无 ``print_layout`` → 出版导出链（``publication_export
  .render_publication_pdf`` + ref 水合）。

增量语义（任务书 A6）：成员缓存键 = 全部冻结输入 canonical sha256；同
输入复用已落盘产物（先复核 checksum 再复用），改一个成员只重渲染该成员。
失败/取消不伪装：逐成员 ``failed``/``cancelled``/``blocked`` 回执，
manifest 如实携带；未完成文件绝不标 complete。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.services.design_specs.contract import canonical_json
from app.services.series_production.contract import (
    MemberArtifactRecord,
    MemberQaRecord,
    MemberResult,
    SeriesFreezePlan,
    output_formats,
)

logger = logging.getLogger(__name__)

#: 渲染路径版本（进缓存键 —— 渲染语义变化即失效旧缓存）。
SERIES_RENDER_PATH = "series_production.v1|print_layout|publication_pdf"

_CACHE_DIR_NAME = "series_jobs"
_CACHE_ENTRY_LIMIT = 256


# ── 缓存 ─────────────────────────────────────────────────────────────


def member_cache_key(member_payload: Dict[str, Any], formats: List[str], dpi: int) -> str:
    basis = {
        "render_path": SERIES_RENDER_PATH,
        "dpi": int(dpi),
        "formats": sorted(formats),
        "member": member_payload,
    }
    digest = hashlib.sha256(canonical_json(basis).encode("utf-8")).hexdigest()
    return f"seriesmember-sha256:{digest[:40]}"


def _cache_dir() -> Path:
    from app.services.export_paths import exports_root

    root = Path(exports_root()) / _CACHE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_entry_path(key: str) -> Path:
    safe = key.replace(":", "_")
    return _cache_dir() / f"{safe}.json"


def _load_cache_entry(key: str) -> Optional[Dict[str, Any]]:
    path = _cache_entry_path(key)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 缓存损坏 = miss（不阻断生产）
        return None
    # 复用前复核：登记的产物文件仍在且 checksum 一致，才允许复用
    from app.services.export_paths import exports_root

    for item in entry.get("artifacts", []):
        fpath = Path(exports_root()) / str(item.get("filename") or "")
        if not fpath.is_file():
            return None
        digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
        if digest != str(item.get("checksum")):
            return None
    return entry


def _invalidate_cache_entry(key: str) -> None:
    """删除损坏的缓存登记（同键下次直接重渲染）。"""
    with contextlib.suppress(Exception):
        _cache_entry_path(key).unlink()


def _write_cache_entry(key: str, artifacts: List[Dict[str, Any]]) -> None:
    path = _cache_entry_path(key)
    with contextlib.suppress(Exception):
        path.write_text(
            json.dumps({"cache_key": key, "artifacts": artifacts}, ensure_ascii=False),
            encoding="utf-8",
        )
    _evict_cache()


def _evict_cache() -> None:
    """有界缓存：条目数上限 mtime 逐出（同 atlas 页缓存纪律）。"""
    cache = _cache_dir()
    entries = sorted(
        cache.glob("*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0
    )
    for stale in entries[:-_CACHE_ENTRY_LIMIT] if len(entries) > _CACHE_ENTRY_LIMIT else []:
        with contextlib.suppress(Exception):
            stale.unlink()


# ── 成员 QA（真实产物机器解析；非 HTTP 200 式假检查） ──────────────────


def qa_member_artifacts(artifacts: List[Dict[str, Any]], title: str) -> MemberQaRecord:
    from app.services.export_paths import exports_root

    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []
    ok = bool(artifacts)
    for item in artifacts:
        fmt = str(item.get("fmt") or "")
        fpath = Path(exports_root()) / str(item.get("filename") or "")
        if not fpath.is_file():
            checks.append({"check": "file_exists", "fmt": fmt, "ok": False})
            ok = False
            continue
        payload = fpath.read_bytes()
        if fmt == "pdf":
            magic_ok = payload[:5] == b"%PDF-"
            checks.append({"check": "pdf_magic", "fmt": fmt, "ok": magic_ok})
            if magic_ok:
                try:
                    import io

                    from pypdf import PdfReader

                    reader = PdfReader(io.BytesIO(payload))
                    pages = len(reader.pages)
                    checks.append({"check": "pdf_pages", "fmt": fmt, "ok": pages >= 1,
                                   "pages": pages})
                    if pages < 1:
                        ok = False
                except Exception as exc:  # noqa: BLE001
                    checks.append({"check": "pdf_parse", "fmt": fmt, "ok": False,
                                   "error": str(exc)[:120]})
                    ok = False
            else:
                ok = False
        elif fmt == "svg":
            magic_ok = payload.lstrip()[:5] in (b"<?xml", b"<svg ") or payload.lstrip().startswith(b"<svg")
            checks.append({"check": "svg_magic", "fmt": fmt, "ok": magic_ok})
            if magic_ok:
                try:
                    root = SafeET.fromstring(payload.decode("utf-8", errors="replace"))
                    has_draw = root.findall(".//{http://www.w3.org/2000/svg}path") or \
                        root.findall(".//{http://www.w3.org/2000/svg}g")
                    checks.append({"check": "svg_parse", "fmt": fmt, "ok": bool(has_draw),
                                   "nodes": len(has_draw)})
                    if not has_draw:
                        ok = False
                    # 标题语义（warning 级 —— chrome 渲染策略可能不同）
                    texts = " ".join(
                        (t.text or "") for t in root.findall(".//{http://www.w3.org/2000/svg}text")
                    )
                    if title and title[:20] not in texts:
                        warnings.append(
                            f"svg 未检出标题文本（title='{title[:40]}'）—— 请核对图面 chrome"
                        )
                except (SafeET.ParseError, DefusedXmlException) as exc:
                    checks.append({"check": "svg_parse", "fmt": fmt, "ok": False,
                                   "error": str(exc)[:120]})
                    ok = False
            else:
                ok = False
        elif fmt == "png":
            magic_ok = payload[:8] == b"\x89PNG\r\n\x1a\n"
            checks.append({"check": "png_magic", "fmt": fmt, "ok": magic_ok})
            if not magic_ok:
                ok = False
    return MemberQaRecord(ok=ok, checks=checks[:16], warnings=warnings[:8])


# ── 成员渲染（两条真实路径） ──────────────────────────────────────────


async def _has_print_layout(session_id: str, document: Dict[str, Any]) -> bool:
    layout = document.get("layout") or {}
    plan = layout.get("print_layout")
    return isinstance(plan, dict) and bool(plan)


async def _render_member(
    member: Any,
    document: Dict[str, Any],
    formats: List[str],
    dpi: int,
    *,
    cancel_check: Any = None,
) -> List[Dict[str, Any]]:
    """单成员 → 产物记录列表（真实落盘；print_layout 路径优先）。"""
    artifacts: List[Dict[str, Any]] = []
    if await _has_print_layout(str(member.session_id), document):
        from app.services.print_layout_authoring.service import submit_export

        for fmt in formats:
            if cancel_check is not None and cancel_check():
                return artifacts
            receipt = await submit_export(
                str(member.session_id), fmt=fmt, dpi=dpi,
            )
            if not receipt.get("success"):
                raise RuntimeError(
                    f"print_layout export failed: {receipt.get('code')}: "
                    f"{str(receipt.get('message') or '')[:120]}"
                )
            artifacts.append({
                "fmt": fmt,
                "filename": receipt.get("filename"),
                "checksum": receipt.get("checksum"),
                "bytes_size": receipt.get("bytes"),
            })
        return artifacts

    # 出版链（无 print_layout 成员）：水合 ref → 真实 PDF
    from app.services.publication_export import (
        hydrate_ref_sources,
        render_publication_pdf,
    )

    payload = await hydrate_ref_sources(document, str(member.session_id))
    result = await asyncio.to_thread(
        render_publication_pdf, payload,
        title=str(member.title or ""), target_dpi=int(dpi),
    )
    pdf_bytes = result.pdf
    if not pdf_bytes:
        raise RuntimeError("publication render produced empty pdf")
    artifact = {
        "bytes": pdf_bytes,
        "media_type": "application/pdf",
        "extension": ".pdf",
        "manifest": {
            "render_path": "series_production:publication_pdf",
            "member_id": str(member.member_id),
            "mapspec_fingerprint": str(member.mapspec_fingerprint),
            "target_dpi": int(dpi),
        },
    }
    from app.services.mixed_map_export import persist_mixed_export

    for fmt in formats:
        if fmt == "pdf":
            delivered = await persist_mixed_export(
                str(member.session_id), artifact,
                principal=f"series:{member.member_id}",
            )
            if not delivered.get("success"):
                raise RuntimeError(
                    f"persist failed: {str(delivered.get('code') or '')[:120]}")
            artifacts.append({
                "fmt": "pdf",
                "filename": delivered.get("filename"),
                "checksum": delivered.get("checksum"),
                "bytes_size": delivered.get("bytes"),
            })
        elif fmt == "svg":
            raise RuntimeError(
                "svg 格式要求成员启用纸面版式（print_layout）——出版链当前只产 PDF"
            )
        else:  # png
            raise RuntimeError(
                "png 需要 rsvg-convert（print_layout 链）；请为成员启用 print_layout"
            )
    return artifacts


# ── 执行器 ───────────────────────────────────────────────────────────


async def execute_series_production(
    plan: SeriesFreezePlan,
    *,
    job_id: Optional[str] = None,
    cancel_check: Any = None,
) -> Dict[str, Any]:
    """冻结计划 → 逐成员生产 + manifest（增量；诚实状态）。

    返回 `{results, manifest, summary}`；单成员失败不中断其余成员；
    全部失败 → manifest 携带全失败事实（调用方决定 job 终态）。
    """
    formats = output_formats(plan.output)
    dpi = int(plan.output.get("dpi") or 300)

    from app.services.mapspec_store import mapspec_store

    results: List[MemberResult] = []
    for member in plan.members:
        if cancel_check is not None and cancel_check():
            results.append(MemberResult(
                member_id=member.member_id, status="skipped",
                code="CANCELLED", message="job cancelled before member production",
            ))
            continue
        try:
            document = await mapspec_store.get_mapspec(str(member.session_id))
            if not isinstance(document, dict) or not document:
                results.append(MemberResult(
                    member_id=member.member_id, status="blocked",
                    code="SESSION_UNAVAILABLE",
                    message=f"成员会话 '{str(member.session_id)[:48]}' 无权威 MapSpec",
                ))
                continue
            # 冻结对账：会话当前 fingerprint ≠ 冻结 pin → stale（不用新内容）
            from app.services.series_production.fingerprint import document_fingerprint

            current_fp = await asyncio.to_thread(document_fingerprint, document)
            if current_fp[:64] != str(member.mapspec_fingerprint)[:64]:
                results.append(MemberResult(
                    member_id=member.member_id, status="blocked",
                    code="STALE_MEMBER",
                    message=(
                        "成员指纹与冻结计划不符（会话已变更）——重冻计划后生产；"
                        f"frozen={str(member.mapspec_fingerprint)[:16]}, "
                        f"current={current_fp[:16]}"
                    ),
                ))
                continue
            member_payload = member.model_dump()
            key = member_cache_key(member_payload, formats, dpi)
            cached = _load_cache_entry(key)
            if cached is not None:
                artifacts = [MemberArtifactRecord(**a) for a in cached["artifacts"]]
                qa = qa_member_artifacts([a.model_dump() for a in artifacts], member.title)
                if qa.ok:
                    results.append(MemberResult(
                        member_id=member.member_id,
                        status="cached_reused",
                        artifacts=artifacts, qa=qa, cache_key=key,
                        member_fingerprint=current_fp[:64],
                    ))
                    continue
                # R1-P3：缓存条目产物损坏 → 失效该条目（重渲染），
                # 不在同键上反复失败到逐出。
                _invalidate_cache_entry(key)
            produced = await _render_member(
                member, document, formats, dpi, cancel_check=cancel_check,
            )
            qa = qa_member_artifacts(produced, member.title)
            _write_cache_entry(key, produced)
            results.append(MemberResult(
                member_id=member.member_id,
                status="complete" if qa.ok else "failed",
                code=None if qa.ok else "QA_FAILED",
                artifacts=[MemberArtifactRecord(**a) for a in produced],
                qa=qa, cache_key=key, member_fingerprint=current_fp[:64],
            ))
        except Exception as exc:  # noqa: BLE001 —— 单成员失败如实记录，继续其余
            results.append(MemberResult(
                member_id=member.member_id, status="failed",
                code="RENDER_FAILED", message=str(exc)[:280],
            ))

    from app.services.series_production.manifest import build_series_manifest

    manifest = build_series_manifest(plan, results)
    summary = {
        "total": len(results),
        "complete": sum(1 for r in results if r.status == "complete"),
        "cached_reused": sum(1 for r in results if r.status == "cached_reused"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "blocked": sum(1 for r in results if r.status == "blocked"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
    }
    return {"results": results, "manifest": manifest, "summary": summary}


__all__ = [
    "SERIES_RENDER_PATH",
    "execute_series_production",
    "member_cache_key",
    "qa_member_artifacts",
]
