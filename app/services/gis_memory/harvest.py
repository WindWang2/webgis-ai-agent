"""Turn 端收割（生产写位点，R2/R6/R7 汇聚）。

位点与 ADR-0069 ``harvest_project_memory`` 同门（chat 路由 turn 结束回调），
职责：

1. 烙印租户桥（``_gis_memory_org``/``_gis_memory_user`` → map_state），
   供工具侧零 SQL 十不禁（fail-closed）读取；
2. 排空 pending 缓冲（map_intent 的 resolved_place 候选、dispatch 的
   provider_failure 候选），烙印 org/user 后过写入门；
3. 从本 session 的 MapSpec/plan/review 收割：数据集语义 + 字段角色 +
   CRS 结论（session 作用域短 TTL）、成功策略与产品决策（project 作用域）；
4. R7 GC 位点：``sweep_expired`` + 作用域预算（store 内）顺带执行。

绝不抛异常：记忆是增值上下文，任何失败只记日志（ADR-0069 同纪律）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.services.gis_memory.contract import (
    KIND_CRS_RESOLUTION,
    KIND_DATASET_SEMANTICS,
    KIND_FIELD_ROLE,
    KIND_SUCCESSFUL_STRATEGY,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SOURCE_PROFILE,
    SOURCE_REVIEW_PASSED,
    MemoryEvidence,
    MemoryWriteRequest,
)
from app.services.gis_memory.pending import pending_memory_buffer
from app.services.gis_memory.queries import (
    MEMORY_ORG_STATE_KEY,
    MEMORY_USER_STATE_KEY,
)
from app.services.gis_memory.sanitizer import sanitize_subject
from app.services.gis_memory.store import (
    memory_stats,
    safe_record_memory,
    sweep_expired,
)

logger = logging.getLogger(__name__)

#: CRS/数据剖面类记忆的默认 TTL（秒）——确定性可重建，只做加速。
_PROFILE_TTL_S = 24 * 3600

_session_local_factory = None


def set_session_local_factory(factory) -> None:
    """测试 seam：同时覆盖 harvest 与 queries 两条 DB 通道。"""
    global _session_local_factory
    _session_local_factory = factory
    from app.services.gis_memory import queries as _queries

    _queries.set_session_local_factory(factory)


def _get_session_local():
    if _session_local_factory is not None:
        return _session_local_factory
    from app.core.database import SessionLocal

    return SessionLocal


def _harvest_sync(
    org_id: str,
    user_id: Optional[str],
    session_id: str,
    project_id: Optional[str],
    pending_reqs,
    sources: Dict[str, Any],
    recipe_id: str,
    review_passed: bool,
    user_decisions: list,
) -> Dict[str, Any]:
    SessionLocal = _get_session_local()
    written = 0
    with SessionLocal() as db:
        try:
            # 1) pending 候选（已在工具侧构造完整请求，仅缺 org/user 烙印）
            for req in pending_reqs:
                stamped = MemoryWriteRequest(
                    kind=req.kind,
                    scope=req.scope,
                    scope_id=req.scope_id,
                    subject=req.subject,
                    value=req.value,
                    evidence=req.evidence,
                    confidence=req.confidence,
                    org_id=org_id,
                    refs=req.refs,
                    user_id=user_id,
                    sensitive=req.sensitive,
                    invalidation_rule=req.invalidation_rule,
                    ttl_s=req.ttl_s,
                    fingerprint=req.fingerprint,
                )
                if safe_record_memory(db, stamped):
                    written += 1

            # 2) 数据集语义 / 字段角色 / CRS（session 剖面收割）
            for source in sources.values():
                if not isinstance(source, dict):
                    continue
                profile = source.get("profile") if isinstance(source.get("profile"), dict) else {}
                ref = (
                    source.get("ref") or source.get("ref_id")
                    or source.get("url") or source.get("dataPath")
                )
                if not ref:
                    continue
                subject = sanitize_subject(str(ref)[:200])
                semantic_value: Dict[str, Any] = {}
                feature_count = profile.get("featureCount", profile.get("feature_count"))
                if feature_count is not None:
                    semantic_value["feature_count"] = feature_count
                geoms = profile.get("geometryTypes", profile.get("geometry_types"))
                if isinstance(geoms, list) and geoms:
                    semantic_value["geometry_types"] = geoms[:8]
                crs = (
                    profile.get("crs") or profile.get("source_crs")
                    or profile.get("detected_crs")
                )
                if crs:
                    semantic_value["crs"] = str(crs)[:64]
                if semantic_value:
                    if safe_record_memory(db, MemoryWriteRequest(
                        kind=KIND_DATASET_SEMANTICS,
                        scope=SCOPE_SESSION, scope_id=session_id,
                        subject=subject, value=semantic_value,
                        evidence=MemoryEvidence(source=SOURCE_PROFILE, method="harvest"),
                        confidence=0.75, org_id=org_id, user_id=user_id,
                        refs=[subject] if subject else (),
                        invalidation_rule="dataset_version",
                        ttl_s=None,
                    )):
                        written += 1
                fields = profile.get("fields") if isinstance(profile.get("fields"), dict) else None
                if fields:
                    try:
                        from app.services.data_fabric.semantic.field_roles import (
                            infer_field_roles,
                        )

                        declared = [
                            {"name": name, "type": (fp or {}).get("type", "")}
                            for name, fp in list(fields.items())[:64]
                            if isinstance(fp, dict)
                        ]
                        roles = infer_field_roles(declared)
                        role_value = {
                            k: v[:8] for k, v in roles.items() if v
                        }
                        if role_value:
                            if safe_record_memory(db, MemoryWriteRequest(
                                kind=KIND_FIELD_ROLE,
                                scope=SCOPE_SESSION, scope_id=session_id,
                                subject=subject, value=role_value,
                                evidence=MemoryEvidence(
                                    source=SOURCE_PROFILE, method="infer_field_roles"
                                ),
                                confidence=0.7, org_id=org_id, user_id=user_id,
                                invalidation_rule="dataset_version",
                                ttl_s=None,
                            )):
                                written += 1
                    except Exception:  # noqa: BLE001 — 角色推导缺席不阻断
                        pass
                if crs:
                    if safe_record_memory(db, MemoryWriteRequest(
                        kind=KIND_CRS_RESOLUTION,
                        scope=SCOPE_SESSION, scope_id=session_id,
                        subject=subject,
                        value={"crs": str(crs)[:64]},
                        evidence=MemoryEvidence(source=SOURCE_PROFILE, method="profile"),
                        confidence=0.7, org_id=org_id, user_id=user_id,
                        ttl_s=_PROFILE_TTL_S,
                    )):
                        written += 1

            # 3) 成功策略（评审通过 + recipe 已知 → project 作用域先验）
            if review_passed and recipe_id and project_id:
                if safe_record_memory(db, MemoryWriteRequest(
                    kind=KIND_SUCCESSFUL_STRATEGY,
                    scope=SCOPE_PROJECT, scope_id=project_id,
                    subject=recipe_id,
                    value={"last_task": "verified"},
                    evidence=MemoryEvidence(source=SOURCE_REVIEW_PASSED, method="review"),
                    confidence=0.8, org_id=org_id, user_id=user_id,
                )):
                    written += 1

            # 4) 用户显式产品决策（provenance origin=user 的裁剪投影）
            for decision in user_decisions[:8]:
                subject = sanitize_subject(str(decision.get("subject", ""))[:200])
                if not subject:
                    continue
                if safe_record_memory(db, MemoryWriteRequest(
                    kind="product_decision",
                    scope=SCOPE_PROJECT if project_id else SCOPE_SESSION,
                    scope_id=project_id or session_id,
                    subject=subject,
                    value={"decision": decision.get("decision", "")},
                    evidence=MemoryEvidence(
                        source="explicit_user_decision", method="provenance"
                    ),
                    confidence=0.9, org_id=org_id, user_id=user_id,
                )):
                    written += 1

            # 5) R7 GC 位点：过期失效 + （记录写入时的）预算淘汰已在 record 内
            swept = sweep_expired(db, org_id=org_id, limit=200)
            stats = memory_stats(db, org_id=org_id)
            db.commit()
            return {"written": written, "swept": swept, "stats": stats}
        except Exception:
            db.rollback()
            raise


async def harvest_spatial_memory(
    session_id: Optional[str],
    project_id: Optional[str],
    *,
    org_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """主入口（chat 路由 turn 结束调用）。空结果不抛不吵。"""
    empty = {"written": 0, "swept": 0}
    if not session_id or not org_id:
        pending_memory_buffer.discard(session_id or "")
        return empty
    pending_reqs = pending_memory_buffer.drain(session_id)
    try:
        from app.services.mapspec.store import mapspec_store_instance
        from app.services.session_data import session_data_manager

        state, mapspec = await asyncio.gather(
            session_data_manager.get_map_state(session_id),
            mapspec_store_instance.get_mapspec(session_id),
        )
        # 3a) 租户桥烙印（工具侧零 SQL 读取的依据）
        try:
            await session_data_manager.set_map_state(
                session_id, MEMORY_ORG_STATE_KEY, org_id
            )
            if user_id:
                await session_data_manager.set_map_state(
                    session_id, MEMORY_USER_STATE_KEY, user_id
                )
        except Exception as exc:  # noqa: BLE001 — 桥烙印失败只影响工具侧检索
            logger.debug("[GISMemory] org bridge stamp failed: %s", exc)

        sources = (
            mapspec.get("sources") if isinstance(mapspec, dict) else {}
        ) or {}
        recipe_id = ""
        try:
            # accessor 是单例 ``plan_orchestrator``（memory_harvest.py 里
            # ``import get_plan`` 实为 latent bug，靠 try/except 掩盖——
            # 以代码为准，本处用真实存在的访问路径）。
            from app.services.chat.plan_orchestrator import plan_orchestrator

            plan = plan_orchestrator.get_plan(session_id)
            recipe_id = str(getattr(plan, "recipe_id", "") or "")
        except Exception:  # noqa: BLE001
            recipe_id = ""
        review = state.get("_cartographic_review") if isinstance(state, dict) else None
        review_passed = bool(
            isinstance(review, dict) and review.get("overall_passed") is True
        )
        user_decisions = _extract_user_decisions(state)
        result = await asyncio.to_thread(
            _harvest_sync,
            org_id, user_id, session_id, project_id,
            pending_reqs, sources if isinstance(sources, dict) else {},
            recipe_id, review_passed, user_decisions,
        )
        if result.get("written") or result.get("swept"):
            logger.info(
                "[GISMemory] harvest session=%s project=%s written=%s swept=%s",
                session_id, project_id, result.get("written"), result.get("swept"),
            )
        return {
            "written": int(result.get("written", 0)),
            "swept": int(result.get("swept", 0)),
        }
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        logger.warning(
            "[GISMemory] harvest skipped session=%s: %s", session_id, exc
        )
        return empty


def _extract_user_decisions(state: Any) -> list:
    """从 provenance 尾部提取用户显式决策（有界裁剪投影，零 payload）。"""
    if not isinstance(state, dict):
        return []
    provenance = state.get("_gis_provenance") or state.get("_provenance") or []
    if not isinstance(provenance, list):
        return []
    decisions: list = []
    for entry in reversed(provenance[-32:]):
        if not isinstance(entry, dict):
            continue
        if entry.get("origin") != "user":
            continue
        kind = str(entry.get("kind") or "")
        target = str(entry.get("target") or "")[:200]
        if not target:
            continue
        detail = entry.get("detail") if isinstance(entry.get("detail"), dict) else {}
        if kind == "PatchLayerPresentationIntent":
            decision = "hide_layer" if detail.get("visible") is False else "show_layer"
        elif kind:
            decision = kind
        else:
            continue
        decisions.append({"subject": f"layer:{target}", "decision": decision})
    return decisions[:8]


__all__ = ["harvest_spatial_memory", "set_session_local_factory"]
