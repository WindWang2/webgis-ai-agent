"""只读地图发布 Agent 工具（04 线 WP5）—— inspect/prepare/draft/preview/validate。

边界（红线）：
- Pi 可以检查可授权性、准备发布计划、创建**私有草稿**、解释阻断与 QA；
- Pi **不能**替用户发布或签发对外链接 —— publish/link 是作者在 UI/REST
  的显式确认动作（服务端 confirm 门 + durable job），本工具族不提供。
- 全部 session_id 经运行时上下文归属校验（与 upload_tools 同纪律）。
"""
import logging
from typing import Dict, List, Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _verified_session(provided: Optional[str]) -> Optional[str]:
    from app.tools.upload_tools import _resolve_session_id

    return _resolve_session_id(provided)


def _plan_summary(plan: Dict) -> Dict:
    """PublicationPlan → 有界摘要（给 LLM 的可验证元数据，不含全量数据）。"""
    layers = plan.get("layers") or []
    return {
        "spec_fingerprint": plan.get("spec_fingerprint"),
        "spec_valid": plan.get("spec_valid"),
        "title": plan.get("title"),
        "scope": plan.get("scope"),
        "layers": [
            {
                "layer_id": lp.get("layer_id"),
                "title": lp.get("title"),
                "projectable": lp.get("projectable"),
                "feature_count": lp.get("feature_count"),
                "candidate_fields": lp.get("candidate_fields"),
                "field_whitelist": lp.get("field_whitelist"),
                "blocker": (lp.get("blocker") or {}).get("message") if lp.get("blocker") else None,
            }
            for lp in layers
        ],
        "blockers": plan.get("blockers") or [],
        "publishable": (
            plan.get("spec_valid")
            and any(lp.get("projectable") for lp in layers)
            and not any(b.get("reason") == "mapspec_invalid" for b in (plan.get("blockers") or []))
        ),
    }


def register_publication_tools(registry: ToolRegistry) -> None:
    """注册只读地图发布工具族。"""

    @tool(
        registry, name="publication_inspect",
        capabilities=["map_export_publishing"],
        tier=2, domains=["cartography"],
        description=(
            "检查当前会话地图的**发布可授权性**（只读）：逐层列出能否安全投影发布"
            "（矢量可物化 / 瓦片栅格类阻断）、字段候选（可公开字段白名单建议）、"
            "要素数、typed 阻断理由与整体可发布判定。"
            "\n何时用：用户说『这张图能不能分享/发布给读者』『哪些层可以公开』。"
            "\n何时不用：要实际创建草稿/发布 — 用 publication_prepare / 前端发布面板。"
            "\n关键约束：只读无副作用；阻断是真实语义（许可/凭证/数据形态），不可凭"
            "自然语言绕过。"
        ),
        param_descriptions={
            "session_id": "会话 ID（缺省 = 当前运行时上下文绑定的会话）",
        },
        side_effect="pure",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        tags=("发布", "分享", "可授权", "只读地图", "publication"),
        failure_modes=("missing_data", "invalid_args"),
    )
    def publication_inspect(session_id: Optional[str] = None) -> dict:
        session = _verified_session(session_id)
        if not session:
            return {"error": "session_id 未通过归属校验", "code": "session_forbidden"}
        from app.core.async_runner import run_sync
        from app.services.publication.service import (
            PublicationRequestError,
            plan_publication_from_session,
        )

        try:
            planned = run_sync(plan_publication_from_session(session))
        except PublicationRequestError as exc:
            return {"error": exc.message, "code": exc.reason}
        return {"status": "inspected", **_plan_summary(planned["plan"])}

    @tool(
        registry, name="publication_prepare",
        capabilities=["map_export_publishing"],
        tier=2, domains=["cartography"],
        description=(
            "准备一份**发布计划建议**（只读，不创建任何草稿/链接）：按作者意图"
            "选择图层与公开字段白名单，产出发布面（标题/用途/署名/许可/缺失值说明）、"
            "逐层投影计划、typed 阻断与可访问 alt text 建议。"
            "\n何时用：用户表达『想把这张学校专题图发布成读者可看的只读地图，"
            "只公开名称和规模字段』——先给出计划与改动建议。"
            "\n何时不用：计划确认后创建草稿 → publication_draft（仍不能发布）。"
            "\n关键约束：license 必填（版权不明不可发布）；发布与链接签发必须由"
            "作者在工作台显式确认，Agent 无法代行。"
        ),
        param_descriptions={
            "session_id": "会话 ID（缺省 = 当前会话）",
            "selected_layer_ids": "作者选择发布的图层 id 列表（空 = 全部可投影层）",
            "field_whitelists": "逐层公开字段白名单 {layer_id: [fields]}（空 = 全部候选字段建议）",
            "title": "发布标题（空 = 回退 MapSpec 标题）",
            "purpose": "发布用途说明",
            "attribution": "署名",
            "license": "数据/成图许可（必填才能发布，如 CC0-SIMULATED / ODbL）",
            "missing_values_note": "缺失值说明（随读者可见）",
            "alt_text": "可访问替代文本建议（Agent 可给；读者以真实统计为准）",
            "palette": "色带名（色觉检验实测输入；空 = 默认色带）",
        },
        side_effect="pure",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        tags=("发布", "计划", "白名单", "许可", "alt text"),
        failure_modes=("invalid_args", "missing_data"),
    )
    def publication_prepare(
        session_id: Optional[str] = None,
        selected_layer_ids: Optional[List[str]] = None,
        field_whitelists: Optional[Dict[str, List[str]]] = None,
        title: str = "",
        purpose: str = "",
        attribution: str = "",
        license: str = "",
        missing_values_note: str = "",
        alt_text: str = "",
        palette: str = "",
    ) -> dict:
        session = _verified_session(session_id)
        if not session:
            return {"error": "session_id 未通过归属校验", "code": "session_forbidden"}
        from app.services.publication.service import (
            PublicationRequestError,
            license_blocker_if_missing,
            plan_publication_from_session,
        )
        from app.services.publication.contract import PublicationMeta

        from app.core.async_runner import run_sync

        try:
            planned = run_sync(plan_publication_from_session(
                session,
                selected_layer_ids=selected_layer_ids,
                field_whitelists=field_whitelists,
            ))
        except PublicationRequestError as exc:
            return {"error": exc.message, "code": exc.reason}
        summary = _plan_summary(planned["plan"])
        meta = PublicationMeta(
            title=title or summary.get("title") or "未命名发布",
            purpose=purpose, attribution=attribution, license=license,
            missing_values_note=missing_values_note,
        )
        lic = license_blocker_if_missing(meta)
        publish_blockers = list(summary["blockers"])
        if lic is not None:
            publish_blockers.append(lic.model_dump(mode="json"))
        return {
            "status": "planned",
            "plan": summary,
            "meta_proposal": meta.model_dump(mode="json"),
            "alt_text_proposal": alt_text,
            "publish_blockers": publish_blockers,
            "next_step": (
                "作者确认计划后在工作台创建草稿并显式发布（publication_draft 仅创建私有草稿）"
            ),
        }

    @tool(
        registry, name="publication_draft",
        capabilities=["map_export_publishing"],
        tier=2, domains=["cartography"],
        description=(
            "把当前会话地图冻结为**私有发布草稿**（不对外可见、无链接）：快照按"
            "发布计划冻结（发布后不可变），返回 publication_id 供前端发布面板"
            "预览/确认发布/签发链接。"
            "\n何时用：作者看过 publication_prepare 的计划并明确同意后的落草稿步骤。"
            "\n何时不用：只是探查 → publication_inspect/prepare；要发布或给链接 → "
            "作者本人在工作台显式操作（本工具族无发布能力）。"
            "\n关键约束：草稿是私有产物（artifact_creation）；重复调用会产生多个"
            "草稿版本（同 product 续版用 base_publication_id）。"
        ),
        param_descriptions={
            "session_id": "会话 ID（缺省 = 当前会话）",
            "selected_layer_ids": "发布图层选择",
            "field_whitelists": "逐层公开字段白名单",
            "title": "发布标题（必填，空则回退 MapSpec 标题）",
            "purpose": "用途",
            "attribution": "署名",
            "license": "许可（发布阶段必填）",
            "missing_values_note": "缺失值说明",
            "alt_text": "可访问 alt text",
            "palette": "色带名",
            "base_publication_id": "在此发布的基础上创建显式新版本（version+1，meta 继承）",
        },
        side_effect="artifact_creation",
        deterministic=False,  # 落库产物（行 id/时间）
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="text",
        result_size_policy="inline_small",
        data_mutations=("artifact_write",),
        tags=("发布", "草稿", "快照", "publication"),
        failure_modes=("invalid_args", "missing_data", "blocked"),
    )
    def publication_draft(
        session_id: Optional[str] = None,
        selected_layer_ids: Optional[List[str]] = None,
        field_whitelists: Optional[Dict[str, List[str]]] = None,
        title: str = "",
        purpose: str = "",
        attribution: str = "",
        license: str = "",
        missing_values_note: str = "",
        alt_text: str = "",
        palette: str = "",
        base_publication_id: str = "",
    ) -> dict:
        session = _verified_session(session_id)
        if not session:
            return {"error": "session_id 未通过归属校验", "code": "session_forbidden"}

        from app.core.async_runner import run_sync
        from app.core.database import AsyncSessionLocal
        from app.services.jobs.context import current_origin
        from app.services.publication.service import (
            PublicationRequestError,
            create_draft,
        )

        origin = current_origin()
        if AsyncSessionLocal is None:
            return {"error": "async DB 不可用", "code": "db_unavailable"}

        async def _create():
            async with AsyncSessionLocal() as db:
                return await create_draft(
                    db,
                    session_id=session,
                    owner_token=(origin.owner_token if origin else None),
                    creator_id=(origin.owner_id if origin else None),
                    org_id=(origin.org_id if origin else None),
                    meta_params={
                        "title": title,
                        "purpose": purpose,
                        "attribution": attribution,
                        "license": license,
                        "missing_values_note": missing_values_note,
                    },
                    selected_layer_ids=selected_layer_ids,
                    field_whitelists=field_whitelists,
                    alt_text=alt_text,
                    palette=palette,
                    base_publication_id=base_publication_id or None,
                )

        try:
            row = run_sync(_create())
        except PublicationRequestError as exc:
            return {"error": exc.message, "code": exc.reason, "status_code": exc.status_code}
        return {
            "status": "draft_created",
            "publication_id": row.id,
            "product_id": row.product_id,
            "version": row.version,
            "title": row.title,
            "row_status": row.status,
            "blockers": (row.snapshot or {}).get("blockers") or [],
            "next_step": (
                "作者在工作台发布面板预览并显式确认发布（confirm）——"
                "发布与链接签发不可由 Agent 代行"
            ),
        }

    @tool(
        registry, name="publication_validate",
        capabilities=["map_export_publishing"],
        tier=2, domains=["cartography"],
        description=(
            "校验一份发布草稿的**可发布性**（只读）：license/schema/预算等硬阻断、"
            "物化就绪状态、QA 摘要与可访问清单完整性；并明确说明发布确认与链接"
            "签发的归属（作者显式操作）。"
            "\n何时用：作者问『这个草稿现在能发布吗』『还差什么』。"
            "\n关键约束：不给链接、不代行发布。"
        ),
        param_descriptions={
            "publication_id": "发布草稿 id",
        },
        side_effect="pure",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        tags=("发布", "校验", "QA", "publication"),
        failure_modes=("invalid_args", "missing_data"),
    )
    def publication_validate(publication_id: str = "") -> dict:
        from app.core.async_runner import run_sync
        from app.core.database import AsyncSessionLocal
        from app.services.publication.contract import PublicationMeta
        from app.services.publication.service import (
            license_blocker_if_missing,
            publish_blockers,
        )

        if AsyncSessionLocal is None:
            return {"error": "async DB 不可用", "code": "db_unavailable"}
        if not publication_id:
            return {"error": "publication_id 必填", "code": "invalid_args"}

        from app.services.jobs.context import current_origin as _current_origin

        async def _validate():
            from sqlalchemy import select

            from app.models.map_publication import MapPublication
            from app.services.publication.service import _owns

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(MapPublication).where(MapPublication.id == publication_id)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                # R1 P2-2：作者面纪律与 REST 同语义 —— 归属不一致（含匿名
                # origin fail-closed）不给跨 owner 状态/QA 泄露面。
                # 归属链：三元组匹配 或 运行时会话绑定 == 行会话
                # （与 verify_session_owner 的会话归属语义一致）。
                _origin = _current_origin()
                # 运行时会话绑定（registry dispatch / Pi RPC 链）与 jobs origin 双链取证。
                from app.lib.runtime.context import current_runtime_context as _cur_rt

                _bound_session = getattr(_cur_rt(), "session_id", None) or getattr(
                    _origin, "session_id", None
                )
                if not (
                    (_bound_session and _bound_session == row.session_id)
                    or _owns(
                        row,
                        getattr(_origin, "owner_id", None),
                        getattr(_origin, "owner_token", None),
                    )
                ):
                    return {"code": "access_denied"}
                hard = publish_blockers(row.snapshot or {})
                lic = license_blocker_if_missing(
                    PublicationMeta(**(row.snapshot or {}).get("meta", {}))
                )
                if lic is not None:
                    hard.append(lic)
                return {
                    "publication_id": row.id,
                    "row_status": row.status,
                    "access_policy": row.access_policy,
                    "version": row.version,
                    "materialized": bool(row.materialized),
                    "spec_fingerprint": row.spec_fingerprint,
                    "hard_blockers": [b.model_dump(mode="json") for b in hard],
                    "soft_blockers": [
                        b for b in (row.snapshot or {}).get("blockers", [])
                        if b.get("reason") not in ("license_unknown", "snapshot_budget_exceeded", "mapspec_invalid")
                    ],
                    "qa": (row.snapshot or {}).get("qa") or {},
                    "accessibility_complete": bool((row.snapshot or {}).get("accessibility", {}).get("complete")),
                    # 物化由发布 job 承担（durable）；草稿级可发布性只看
                    # 硬阻断（license/预算/schema）。
                    "can_publish": row.status == "draft" and not hard,
                    "consent_boundary": (
                        "发布（confirm）与链接签发必须由作者在工作台/REST 显式操作；"
                        "Agent 不能代行对外可见副作用"
                    ),
                }

        try:
            verdict = run_sync(_validate())
        except Exception as exc:  # noqa: BLE001 — typed 错误面
            return {"error": f"{type(exc).__name__}: {exc}", "code": "validate_failed"}
        if verdict is None:
            return {"error": "发布不存在", "code": "publication_not_found"}
        if verdict.get("code") == "access_denied":
            return {"error": "无权校验该发布（归属不符）", "code": "access_denied"}
        return {"status": "validated", **verdict}
