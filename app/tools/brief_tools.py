"""G11 —— 简报 Agent 工具面（BRF-06 工具语义）。

六个工具沿既有链（Pi → ToolRegistry → dispatch → 领域服务 → 类型化
回执）：``brief_create_draft / brief_inspect_evidence / brief_update_block /
brief_preflight / brief_freeze_release / brief_export``。

- ``session_id`` 形参精确匹配 registry 注入探针（LLM 不可伪造；
  注册 schema 不暴露该参数）。
- 回执紧凑：章节/指标数、证据缺口、产物 ref、真实文件状态 —— 大
  FeatureCollection/全量日志不进模型上下文。
- 导出是**私有**动作：只返回 owner 下载地址；对外分享必须经用户显式
  走 REST 分享端点（工具面不提供 share 动作）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def _open_db():
    from app.core.database import get_async_db

    gen = get_async_db()
    return gen, await gen.__anext__()


async def _get_conversation(db, session_id: str):
    from sqlalchemy import select

    from app.models.db_model import Conversation

    return await db.scalar(select(Conversation).where(
        Conversation.id == session_id))


def _fact_gaps(spec) -> list:
    return [f.fact_id for f in spec.facts if f.status != "verified"][:16]


def register_brief_tools(registry: ToolRegistry):
    """注册简报工具（tier=2，domains=["brief"]）。"""

    @registry.tool(
        tier=2, domains=["brief"], name="brief_create_draft",
        capabilities=["brief_draft"],
        description=(
            "把当前会话的真实分析产物（统计表/图表/图层）整理成一份可编辑"
            "的地图简报草稿：章节、指标卡、图表、地图视图、方法与来源。"
            "每个自动数值绑定产物证据（来源/字段/口径），可核验。"
            "✅ 用于：『把刚才的学校分布分析整理成一份地图简报，含各区统计"
            "和方法说明』。仅在用户要求整理简报时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "简报标题（可选）"},
            },
            "required": [],
        },
        side_effect="artifact_creation",
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="medium",
        tags=("简报", "地图简报", "草稿", "证据", "storymap", "brief"),
        output_semantic_type="brief",
        result_size_policy="inline_small",
        data_mutations=("artifact_write",),
        failure_modes=("missing_data", "already_exists"),
    )
    async def brief_create_draft(
        title: Optional[str] = None, session_id: str = ""
    ) -> dict:
        if not session_id:
            return {"error": "无法确定当前会话 ID。"}
        from app.services.storymap.brief_compiler import compile_brief_draft
        from app.services.storymap.brief_store import (
            RevisionConflict, create_brief, load_brief,
        )

        gen = db = None
        try:
            gen, db = await _open_db()
            conv = await _get_conversation(db, session_id)
            if conv is None:
                return {"error": f"会话 {session_id} 不存在"}
            try:
                existing = await load_brief(db, conv)
                return {
                    "type": "brief_already_exists",
                    "brief": _summary(existing),
                    "message": "本会话已有简报草稿；可直接编辑或重生成，"
                               "无需重复创建。",
                }
            except Exception:  # noqa: BLE001 — 无草稿 → 正常创建
                pass
            spec = await compile_brief_draft(conv, db=db, title=title)
            result = await create_brief(db, conv, spec)
            await db.commit()
            return {
                "type": "brief_created",
                "brief_id": result["brief_id"],
                "revision": result["revision"],
                "summary": _summary(spec),
                "evidence_gaps": _fact_gaps(spec),
                "artifact_refs": list(spec.provenance.artifact_refs)[:16],
                "message": f"简报「{spec.metadata.title}」草稿已生成："
                           f"{len(spec.chapters)} 章 / {len(spec.blocks)} 块 / "
                           f"{len(spec.facts)} 个指标（"
                           f"{sum(1 for f in spec.facts if f.status == 'verified')}"
                           " 个已核验）。可在简报编辑器中调整。",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("brief_create_draft failed: %s", exc)
            return {"error": f"简报草稿生成失败：{exc}"}
        finally:
            if gen is not None:
                await gen.aclose()

    @registry.tool(
        tier=2, domains=["brief"], name="brief_inspect_evidence",
        capabilities=["brief_evidence"],
        description=(
            "核 对简报中某个指标的证据链：值来自哪个产物、哪个字段、什么"
            "口径/单位/时间窗，以及确定性核验结果与分子/分母/基期。"
            "✅ 用于：『这个比例是怎么来的？带我看对应数据和分母』。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "fact_id": {"type": "string", "description": "指标事实 ID（fact-*）"},
            },
            "required": ["fact_id"],
        },
        side_effect="pure",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("简报", "证据", "溯源", "核对"),
        output_semantic_type="evidence_report",
        result_size_policy="inline_small",
        failure_modes=("missing_data",),
    )
    async def brief_inspect_evidence(fact_id: str = "", session_id: str = "") -> dict:
        if not session_id:
            return {"error": "无法确定当前会话 ID。"}
        from app.lib.storymap.brief_factcheck import (
            check_fact,
            resolve_field_path,
        )
        from app.services.storymap.brief_compiler import _load_session_inputs
        from app.services.storymap.brief_store import load_brief

        gen = db = None
        try:
            gen, db = await _open_db()
            conv = await _get_conversation(db, session_id)
            if conv is None:
                return {"error": f"会话 {session_id} 不存在"}
            spec = await load_brief(db, conv)
            fact = next((f for f in spec.facts if f.fact_id == fact_id), None)
            if fact is None:
                return {"error": f"简报中没有指标 {fact_id}"}
            records, payloads, _t, _fp, _m = await _load_session_inputs(conv)
            values = {f.fact_id: f.value for f in spec.facts}
            outcome = check_fact(fact, records=records, payloads=payloads,
                                 fact_values=values)
            authoritative = None
            if fact.basis.artifact_ref and fact.basis.field_path:
                authoritative, _ = resolve_field_path(
                    payloads.get(fact.basis.artifact_ref),
                    fact.basis.field_path)
            deps = {}
            for dep_id in (fact.denominator_fact_id, fact.numerator_fact_id,
                           fact.baseline_fact_id):
                dep = next((f for f in spec.facts if f.fact_id == dep_id), None)
                if dep is not None:
                    deps[dep_id] = {"label": dep.label, "value": dep.value,
                                    "unit": dep.unit, "status": dep.status}
            return {
                "type": "brief_evidence",
                "fact": {
                    "fact_id": fact.fact_id, "label": fact.label,
                    "kind": fact.kind, "value": fact.value, "unit": fact.unit,
                    "status": fact.status,
                },
                "source": {
                    "artifact_ref": fact.basis.artifact_ref,
                    "field_path": fact.basis.field_path,
                    "source_label": fact.basis.source_label,
                    "method": fact.basis.method,
                    "temporal_window": fact.basis.temporal_window,
                    "produced_at": fact.basis.produced_at,
                },
                "verification": {
                    "status": outcome.status, "reasons": outcome.reasons[:8],
                    "authoritative_value": authoritative,
                    "contradicted": outcome.contradicted,
                },
                "dependencies": deps,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("brief_inspect_evidence failed: %s", exc)
            return {"error": f"证据核对失败：{exc}"}
        finally:
            if gen is not None:
                await gen.aclose()

    @registry.tool(
        tier=2, domains=["brief"], name="brief_update_block",
        capabilities=["brief_edit"],
        description=(
            "编辑简报内容块：更新文字（text/method/limitation）或锁定/解锁"
            "块。锁定后的块不会被重生成覆盖。用户改过的内容请设置 "
            "origin=manual。✅ 用于：『把结论改成……并锁定』。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "block_id": {"type": "string", "description": "块 ID（blk-*）"},
                "text": {"type": "string", "description": "新的正文（可选）"},
                "locked": {"type": "boolean", "description": "锁定状态（可选）"},
                "origin": {"type": "string", "enum": ["auto", "manual"],
                           "description": "内容来源标记（可选）"},
            },
            "required": ["block_id"],
        },
        side_effect="state_mutation",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("简报", "编辑", "锁定"),
        output_semantic_type="brief",
        result_size_policy="inline_small",
        data_mutations=("session_state",),
        failure_modes=("missing_data", "locked_block"),
    )
    async def brief_update_block(
        block_id: str = "", text: Optional[str] = None,
        locked: Optional[bool] = None, origin: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "无法确定当前会话 ID。"}
        from app.services.storymap.brief_store import (
            brief_row, load_brief, save_brief, update_block,
        )

        gen = db = None
        try:
            gen, db = await _open_db()
            conv = await _get_conversation(db, session_id)
            if conv is None:
                return {"error": f"会话 {session_id} 不存在"}
            spec = await load_brief(db, conv)
            row = await brief_row(db, conv)
            patch: dict[str, Any] = {}
            if text is not None:
                patch["text"] = text
            if locked is not None:
                patch["locked"] = bool(locked)
            if origin in ("auto", "manual"):
                patch["origin"] = origin
            if not patch:
                return {"error": "没有可更新的字段（text/locked/origin）"}
            spec = update_block(spec, block_id, patch)
            result = await save_brief(db, conv, spec,
                                      expected_revision=row.revision)
            await db.commit()
            return {
                "type": "brief_block_updated",
                "block_id": block_id, "revision": result["revision"],
                "message": f"块 {block_id} 已更新（revision {result['revision']}）。",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("brief_update_block failed: %s", exc)
            return {"error": f"块更新失败：{exc}"}
        finally:
            if gen is not None:
                await gen.aclose()

    @registry.tool(
        tier=2, domains=["brief"], name="brief_preflight",
        capabilities=["brief_preflight"],
        description=(
            "发布预检：检查来源授权/引用可解析/数值单位一致性/地图版本/"
            "导出可用性/隐私字段，给出 pass/fail/not_evaluated 与总判定"
            "（deliverable/draft_exportable/blocked）。"
            "✅ 用于：『先检查能否交付』。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        side_effect="pure",
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="medium",
        tags=("简报", "预检", "发布"),
        output_semantic_type="preflight_report",
        result_size_policy="inline_small",
        failure_modes=("missing_data",),
    )
    async def brief_preflight(session_id: str = "") -> dict:
        if not session_id:
            return {"error": "无法确定当前会话 ID。"}
        from app.services.storymap.brief_release import preflight_brief

        gen = db = None
        try:
            gen, db = await _open_db()
            conv = await _get_conversation(db, session_id)
            if conv is None:
                return {"error": f"会话 {session_id} 不存在"}
            report = await preflight_brief(db, conv)
            payload = report.to_dict()
            return {
                "type": "brief_preflight",
                "outcome": payload["outcome"],
                "fact_stats": payload["fact_stats"],
                "items": [
                    {"check": i["check"], "verdict": i["verdict"],
                     "detail": i["detail"][:200]}
                    for i in payload["items"]
                ],
                "message": {
                    "deliverable": "预检通过，可冻结发布。",
                    "draft_exportable": "预检存在未评估项：可显式导出带披露"
                                        "的草稿，或先补齐证据。",
                    "blocked": "预检阻断：存在强事实错误/授权/必需图件问题，"
                               "不能发布。",
                }[payload["outcome"]],
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("brief_preflight failed: %s", exc)
            return {"error": f"预检失败：{exc}"}
        finally:
            if gen is not None:
                await gen.aclose()

    @registry.tool(
        tier=2, domains=["brief"], name="brief_freeze_release",
        capabilities=["brief_release"],
        description=(
            "冻结发布：生成不可变发布版本（HTML + 证据 manifest + CSV），"
            "pin 当前数据/图件/证据指纹。之后继续编辑草稿不影响旧版本。"
            "预检 blocked 时拒绝。allow_draft=true 时允许带未验证披露的草稿"
            "版本（标 draft，不冒充正式发布）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "allow_draft": {
                    "type": "boolean",
                    "description": "允许草稿级发布（默认 false）",
                    "default": False,
                },
            },
            "required": [],
        },
        side_effect="artifact_creation",
        deterministic=True,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("简报", "发布", "冻结", "版本"),
        output_semantic_type="brief_release",
        result_size_policy="inline_small",
        data_mutations=("artifact_write",),
        failure_modes=("preflight_blocked", "missing_data"),
    )
    async def brief_freeze_release(
        allow_draft: bool = False, session_id: str = ""
    ) -> dict:
        if not session_id:
            return {"error": "无法确定当前会话 ID。"}
        from app.services.storymap.brief_release import freeze_brief_release

        gen = db = None
        try:
            gen, db = await _open_db()
            conv = await _get_conversation(db, session_id)
            if conv is None:
                return {"error": f"会话 {session_id} 不存在"}
            result = await freeze_brief_release(
                db, conv, allow_draft=allow_draft)
            await db.commit()
            manifest = result["manifest"]
            return {
                "type": "brief_release_frozen",
                "release_id": result["release_id"],
                "release_no": result["release_no"],
                "already_released": result["already_released"],
                "draft": manifest.get("draft"),
                "files": [
                    {"name": f["name"], "status": f["status"],
                     "bytes": f["bytes"]}
                    for f in manifest.get("files", [])
                ],
                "download_url": (
                    f"/api/v1/briefs/sessions/{session_id}/releases/"
                    f"{result['release_id']}/download"),
                "message": f"发布版本 v{result['release_no']} 已冻结"
                           f"（{len(manifest.get('evidence', []))} 条证据 pin）。"
                           "下载地址仅限所有者。",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("brief_freeze_release failed: %s", exc)
            detail = str(exc)
            if "preflight blocked" in detail:
                return {"error": "预检未通过，发布被阻断：" + detail,
                        "type": "brief_preflight_blocked"}
            return {"error": f"发布失败：{exc}"}
        finally:
            if gen is not None:
                await gen.aclose()

    @registry.tool(
        tier=2, domains=["brief"], name="brief_export",
        capabilities=["brief_export"],
        description=(
            "导出简报为可离线阅读的私有 HTML（单文件，含图表数据与静态图件；"
            "无外链）或 JSON manifest。默认 private：只给所有者下载地址，"
            "不对外分享。✅ 用于：『导出私有离线 HTML；不要公开原始点数据』。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string", "enum": ["html", "json"],
                    "description": "导出格式（默认 html）", "default": "html",
                },
                "release_id": {
                    "type": "string",
                    "description": "指定发布版本 ID（可选；缺省导出最新 release）",
                },
            },
            "required": [],
        },
        side_effect="pure",
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("简报", "导出", "离线", "HTML"),
        output_semantic_type="brief_export",
        result_size_policy="inline_small",
        failure_modes=("missing_data",),
    )
    async def brief_export(
        format: str = "html", release_id: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "无法确定当前会话 ID。"}
        from sqlalchemy import select

        from app.models.map_brief import MapBriefRelease
        from app.services.storymap.brief_release import list_releases

        gen = db = None
        try:
            gen, db = await _open_db()
            conv = await _get_conversation(db, session_id)
            if conv is None:
                return {"error": f"会话 {session_id} 不存在"}
            releases = await list_releases(db, conv)
            if not releases:
                return {"error": "尚无发布版本；请先 brief_freeze_release。"}
            chosen = next((r for r in releases
                           if r["release_id"] == release_id), None) \
                if release_id else releases[0]
            if chosen is None:
                return {"error": f"发布版本 {release_id} 不存在"}
            if format == "json":
                return {
                    "type": "brief_export",
                    "format": "json",
                    "url": (f"/api/v1/briefs/sessions/{session_id}/releases/"
                            f"{chosen['release_id']}/manifest"),
                    "release": chosen,
                }
            return {
                "type": "brief_export",
                "format": "html",
                "url": (f"/api/v1/briefs/sessions/{session_id}/releases/"
                        f"{chosen['release_id']}/download"),
                "facts_csv_url": (
                    f"/api/v1/briefs/sessions/{session_id}/releases/"
                    f"{chosen['release_id']}/facts.csv"),
                "release": chosen,
                "message": "离线 HTML 已就绪（仅所有者可下载；默认不含原始"
                           "点数据，敏感字段已脱敏）。",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("brief_export failed: %s", exc)
            return {"error": f"导出失败：{exc}"}
        finally:
            if gen is not None:
                await gen.aclose()


def _summary(spec) -> dict:
    return {
        "title": spec.metadata.title,
        "chapters": len(spec.chapters),
        "blocks": len(spec.blocks),
        "facts": len(spec.facts),
        "fact_stats": {
            s: sum(1 for f in spec.facts if f.status == s)
            for s in ("verified", "warning", "unverified")},
        "revision_note": "revision 由服务端管理",
    }
