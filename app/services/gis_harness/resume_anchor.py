"""Project-level Resume Anchor（Harness V5 — ADR-0118 决策 D8）。

V4 基线缺口：plan / workflow_instance / map_product / observation 全部
session-TTL 绑定；RELOAD_REF 的 durable 半边（RefSpill）只有 24h TTL。
session 过期 = 工作流断头，无法在项目权限下继续。

V5 契约：

- **锚点 = 恢复指针，不是第二真相**：只持久化「从哪里继续」的最小
  事实（user_goal + gis_chapter 关键块 + progress + trace 游标 + ref
  清单）；恢复后的新 session 由既有 session store 承载（SessionPlan /
  save_session_plan 既有通道），DB 里不复制 session 状态机。
- **有界**：chapter 只拷 workflow_instance / map_product / intent /
  query 等关键键（各自本就有界）；ref 清单有界 ≤128。
- **诚实恢复**：旧 ref 载荷能从旧 session（未过期）或 RefSpill（24h
  内被驱逐过的）重水合 → 拷进新 session；否则进 ``missing_refs``
  披露清单 —— 绝不伪造数据在场。
- **授权**：写锚点走 require_owned_session；恢复要求当前用户 ==
  锚点 user_id（匿名锚点不可恢复，与 chat_resume 匿名拒绝同门）。

V6 W14（Resume VNext — verify-not-assume）：建锚时快照逐 ref 修订证据
（content_revision / content_hash / data_fingerprint）与工作流指纹（行指纹 + 实例修订
+ package 指纹）；恢复后经 ``resume_verify.verify_resume`` 验证存活性/
修订/在场/图面依赖/指纹 —— satisfied-but-unverified 的 stage 翻 stale
并进 W5 ``compute_affected_subgraph`` 闭包；无证据只判 stale/unknown。
授权语义不变（本模块仍是唯一鉴权点）。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 锚点内 ref 清单上限（bounded everything）。
MAX_ANCHOR_REFS = 128
#: 锚点内证据快照上限（与 ref 清单同界）。
MAX_ANCHOR_EVIDENCE = 128
#: gis_chapter 中可跨 session 恢复的关键块键。
#: 不变式（review R2 Q7，钉死勿动）：``data_requirements`` 永不进此表 ——
#: 恢复章节无数据行在场证据；下游按行缺席走 pending 兜底 → verify unknown
#: 路径（无证据不断言存活）。陈旧绑定证据一旦跨 session 复活，verify 会把
#: 旧 satisfied 误判为可继续 —— 故绑定类行一律不恢复，只恢复指针与指纹。
RESTORABLE_CHAPTER_KEYS = (
    "workflow_instance",
    "map_product",
    "intent",
    "query",
    "goal",
)
_ANCHOR_SCHEMA_VERSION = 2


async def build_anchor(session_id: str) -> Optional[Dict[str, Any]]:
    """从（可能即将过期的）session 提取有界恢复锚点。session 空 → None。"""
    from app.services.session_plan import load_session_plan
    from app.services.gis_harness.trace_store import last_seq
    from app.services.session_data import session_data_manager

    plan = await load_session_plan(session_id)
    if plan is None:
        return None
    chapter = plan.gis_chapter if isinstance(plan.gis_chapter, dict) else {}
    restored_chapter: Dict[str, Any] = {
        k: chapter[k] for k in RESTORABLE_CHAPTER_KEYS if k in chapter
    }
    try:
        ref_ids = list((await session_data_manager.list_refs(session_id)).keys())
    except Exception:  # noqa: BLE001 — ref 清单缺席照常建锚（恢复时披露）
        ref_ids = []
    _ref_count = len(ref_ids)
    ref_ids = ref_ids[:MAX_ANCHOR_REFS]
    # V6 W14：逐 ref 证据快照（恢复后验证的比对基准；快照失败的 ref 在
    # 恢复时判 unknown 并披露，绝不假设存活）。
    ref_evidence = await _snapshot_ref_evidence(session_id, ref_ids)
    workflow_fingerprint = _snapshot_workflow_fingerprint(chapter)
    # V6（ADR-0119 D4）：durable context —— recovery 状态 + reasoning 摘要
    # 入锚（分层词表内的 durable facts；不含 LLM raw context / 密钥）。
    from app.services.gis_harness.durable_context import (
        RECOVERY_STATE_KEY,
        reasoning_digest,
        recovery_state_for_anchor,
    )

    recovery_state: Dict[str, Any] = {}
    try:
        map_state = await session_data_manager.get_map_state(session_id)
        if isinstance(map_state, dict):
            raw = map_state.get(RECOVERY_STATE_KEY)
            if isinstance(raw, dict):
                recovery_state = recovery_state_for_anchor(raw)
    except Exception:  # noqa: BLE001 — recovery 态缺席照常建锚
        recovery_state = {}
    # V7（ADR-0134 D3）：九域上下文摘要入锚（rebuildable 纪律 —— 只带
    # 域指纹/压缩态，载荷可由权威事实重建，永不复制）。
    context_digest: Dict[str, Any] = {}
    try:
        from app.services.gis_harness.context_layers import (
            ContextLayersState,
            DomainBlock,
            checkpoint_context_layers,
            context_digest_for_anchor,
        )

        layers_block = await checkpoint_context_layers(session_id)
        if isinstance(layers_block, dict) and isinstance(
                layers_block.get("domains"), dict):
            state = ContextLayersState(
                schema_version=str(layers_block.get("schema") or ""),
                revision=int(layers_block.get("revision") or 1),
                content_fingerprint=str(
                    layers_block.get("content_fingerprint") or ""),
                domains={
                    k: DomainBlock(
                        source_fingerprint=str(
                            (v or {}).get("source_fingerprint") or ""),
                        payload=(v or {}).get("payload") or {},
                        compacted=bool((v or {}).get("compacted")),
                        pruned=bool((v or {}).get("pruned")),
                    )
                    for k, v in layers_block["domains"].items()
                    if isinstance(v, dict)
                },
            )
            context_digest = context_digest_for_anchor(state)
    except Exception:  # noqa: BLE001 — 摘要缺席照常建锚
        context_digest = {}
    return {
        "schema_version": _ANCHOR_SCHEMA_VERSION,
        "created_at": time.time(),
        "source_session_id": session_id,
        "user_goal": str(plan.user_goal or ""),
        "envelope_id": str(plan.envelope_id or ""),
        "progress": [p.model_dump() for p in (plan.progress or [])][:32],
        "gis_chapter": restored_chapter,
        "trace_last_seq": last_seq(session_id),
        "ref_evidence": ref_evidence,
        "workflow_fingerprint": workflow_fingerprint,
        "ref_ids": ref_ids,
        "refs_truncated": _ref_count > MAX_ANCHOR_REFS,
        "recovery_state": recovery_state,
        "reasoning_digest": reasoning_digest(chapter, recovery_state),
        "context_digest": context_digest,
    }


async def _snapshot_ref_evidence(
    session_id: str, ref_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """逐 ref 快照修订证据（有界；单个失败跳过，恢复时判 unknown）。"""
    from app.services.session_data import session_data_manager

    evidence: Dict[str, Dict[str, Any]] = {}
    for ref_id in ref_ids[:MAX_ANCHOR_EVIDENCE]:
        try:
            descriptor = await session_data_manager.get_ref_descriptor(
                session_id, ref_id)
        except Exception:  # noqa: BLE001 — 描述符读失败按无证据
            descriptor = None
        if not isinstance(descriptor, dict):
            continue
        entry: Dict[str, Any] = {}
        try:
            entry["content_revision"] = int(descriptor.get("content_revision") or 0)
        except (TypeError, ValueError):
            entry["content_revision"] = 0
        # 内容身份（跨 session 稳定）：恢复后验证的主比对键。
        content_hash = descriptor.get("content_hash")
        if isinstance(content_hash, str) and content_hash:
            entry["content_hash"] = content_hash[:64]
        fingerprint = descriptor.get("data_fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            entry["data_fingerprint"] = fingerprint[:64]
        evidence[ref_id] = entry
    return evidence


def _snapshot_workflow_fingerprint(chapter: Dict[str, Any]) -> Dict[str, Any]:
    """工作流指纹快照（行指纹 + 实例修订 + package 指纹；缺席不断言）。"""
    snapshot: Dict[str, Any] = {}
    try:
        from app.services.gis_harness.workflow_instance import rows_fingerprint
        from app.services.gis_harness.runtime_bridge import WORKFLOW_RUNTIME_KEY

        rows_fp = rows_fingerprint(chapter) if isinstance(chapter, dict) else ""
        if not rows_fp and isinstance(chapter, dict):
            instance = chapter.get("workflow_instance")
            if isinstance(instance, dict):
                rows_fp = str(instance.get("rows_fingerprint") or "")
                try:
                    snapshot["state_revision"] = int(
                        instance.get("state_revision") or 0)
                except (TypeError, ValueError):
                    pass
        if rows_fp:
            snapshot["rows_fingerprint"] = str(rows_fp)[:2048]
        if isinstance(chapter, dict):
            block = chapter.get(WORKFLOW_RUNTIME_KEY)
            if isinstance(block, dict) and block.get("package_fingerprint"):
                snapshot["package_fingerprint"] = str(
                    block.get("package_fingerprint"))[:64]
    except Exception:  # noqa: BLE001 — 指纹快照失败按无证据（恢复时 unknown）
        logger.warning("[ResumeAnchor] workflow fingerprint snapshot failed",
                       exc_info=True)
    return snapshot


async def save_anchor(
    db: Any,
    *,
    session_id: str,
    user_id: Optional[str],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 + 落库（路由侧已做 require_owned_session）。"""
    from app.models.project import WorkflowResumeAnchor

    anchor = await build_anchor(session_id)
    if anchor is None:
        raise ValueError("session has no resumable plan")
    # review R2 #4：同 session upsert（保留最新一条）—— 否则反复保存无界
    # 增行。异 session 各自一行（锚点按 session 语义，同 session 旧锚点
    # 指向的恢复面被新锚点完全覆盖）。
    from sqlalchemy import select as _select

    existing = (
        await db.execute(
            _select(WorkflowResumeAnchor)
            .where(WorkflowResumeAnchor.session_id == session_id)
            .order_by(WorkflowResumeAnchor.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.user_id = user_id
        existing.project_id = project_id
        existing.anchor = anchor
        await db.commit()
        return {"anchor_id": existing.id, "anchor": anchor}
    row = WorkflowResumeAnchor(
        session_id=session_id,
        user_id=user_id,
        project_id=project_id,
        anchor=anchor,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"anchor_id": row.id, "anchor": anchor}


async def resume_from_anchor(
    db: Any,
    *,
    anchor_id: str,
    user_id: Optional[str],
) -> Dict[str, Any]:
    """在项目/用户权限下从锚点恢复新 session（旧 session 不复活）。

    授权：锚点必须存在且 ``user_id`` 与当前用户一致（含双方皆 None 的
    匿名拒绝 —— 匿名不可恢复）。
    """
    from app.models.project import WorkflowResumeAnchor
    from app.services.session_plan import (
        SessionPlan,
        save_session_plan,
    )
    from app.services.session_data import session_data_manager

    row = await db.get(WorkflowResumeAnchor, anchor_id)
    if row is None:
        raise LookupError("resume anchor not found")
    # review R2 Q8：跨 session 直读的唯一门 —— 恢复管线直接读旧 session 载
    # 荷（旧 session 消亡即按缺席披露），读旧载荷的授权仅系于此处的 user_id
    # 一致比对。跨 user 共享 anchor 不在 roadmap（共享即授权外读 —— 如需协
    # 作语义另立 ADR，不在本模块隐式实现）；project_id 不参与门控。
    if not user_id or str(row.user_id or "") != str(user_id):
        raise PermissionError("resume anchor is not owned by current user")

    def _rewrite_refs(node: Any) -> Any:
        """chapter 内的旧 ref id → 新 session id（review R2 #1）。

        ref id 是 session 域能力令牌：恢复后的 workflow_instance /
        map_product 里若保留旧 id，plan 会带着悬空引用谎报「已绑定」。
        可重水合的 ref 重写为新 id；不可恢复的 ref 形字符串置空并记入
        dangling_refs（诚实披露 —— 置空 = 未绑定，等 agent 重新获取，
        绝不悬空谎报）。
        """
        if isinstance(node, dict):
            return {k: _rewrite_refs(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_rewrite_refs(v) for v in node]
        if isinstance(node, str) and node.startswith("ref:"):
            if node in ref_map:
                return ref_map[node]
            dangling.append(node)
            return ""
        return node

    anchor = row.anchor if isinstance(row.anchor, dict) else {}
    old_sid = str(anchor.get("source_session_id") or row.session_id)
    new_sid = f"resume-{anchor_id[:8]}-{uuid.uuid4().hex[:8]}"
    dangling: List[str] = []

    # ref 载荷重水合：旧 session 仍在 → 直取；否则 RefSpill（24h）兜底；
    # 双双缺席 → missing_refs 诚实披露（不伪造）。
    # review R2 Q10：顺序 IO 说明 —— 低频路径（中断恢复才走），正确优先：
    # 逐 ref 直读 + spill 兜底 + 新 session 写；ref_ids 建锚时已截断 ≤
    # MAX_ANCHOR_REFS（128），轮数恒有界，不做并发扇出（扇出省毫秒级、
    # 引入限流/半写复杂度 —— 不值得）。
    # review R1 #1（CRITICAL 修复）：ref id 是 session 域能力令牌 —— store
    # 生成**新** id，必须返回 old→new 映射（ref_map），restored_refs 以
    # 新 id 计，调用方用新 id 寻址；绝不把旧 id 谎报为可用。
    ref_map: Dict[str, str] = {}
    restored_refs: List[str] = []
    missing_refs: List[str] = []
    for ref_id in anchor.get("ref_ids") or []:
        payload = None
        try:
            payload = await session_data_manager.get(old_sid, ref_id)
        except Exception:  # noqa: BLE001 — 旧 session 读失败按缺席
            payload = None
        if payload is None:
            try:
                from app.services.session_data import RefSpillStore

                payload = RefSpillStore().load(old_sid, ref_id)
            except Exception:  # noqa: BLE001 — spill 缺席按缺席
                payload = None
        if payload is None:
            missing_refs.append(ref_id)
            continue
        try:
            new_ref = await session_data_manager.store(new_sid, payload)
            if new_ref:
                ref_map[ref_id] = new_ref
                restored_refs.append(new_ref)
            else:
                missing_refs.append(ref_id)
        except Exception:  # noqa: BLE001 — 新 session 写失败也如实披露
            missing_refs.append(ref_id)

    plan = SessionPlan(
        envelope_id=f"sp-{uuid.uuid4().hex[:12]}",
        session_id=new_sid,
        user_goal=str(anchor.get("user_goal") or ""),
        gis_chapter={
            k: _rewrite_refs(v)
            for k, v in (anchor.get("gis_chapter") or {}).items()
            if k in RESTORABLE_CHAPTER_KEYS
        },
        progress=[],
        previous_goal="",
    )
    plan.gis_chapter = dict(plan.gis_chapter or {}) if plan.gis_chapter else {}
    plan.gis_chapter["resumed_from"] = {
        "anchor_id": anchor_id,
        "source_session_id": old_sid,
        "resumed_at": time.time(),
        # 未重水合成功的旧 ref id（已置空；此处披露供 agent 重新获取）
        "dangling_refs": sorted(set(dangling))[:32],
    }
    # V6 W14：恢复后验证（verify-not-assume）—— 对重水合 refs 做存活性/
    # 修订/在场/图面/指纹验证；satisfied-but-unverified 的 stage 就地翻
    # stale 并求 W5 recompute 闭包；验证结论并入 resumed_from 披露。
    # 无证据只判 stale/unknown，绝不标 live。
    try:
        from app.services.gis_harness.resume_verify import verify_resume

        verify_report = await verify_resume(
            new_sid, anchor, ref_map,
            plan.gis_chapter if isinstance(plan.gis_chapter, dict) else {},
        )
    except Exception:  # noqa: BLE001 — 验证编排永不阻断恢复（降级披露）
        logger.warning("[ResumeAnchor] post-resume verify failed sid=%s",
                       new_sid, exc_info=True)
        verify_report = {
            "ref_verdicts": {},
            "workflow": {"verdict": "unknown",
                         "reason": "验证执行失败，无结论"},
            "mapspec": {"verdict": "unknown",
                       "reason": "验证执行失败，无结论", "layers": []},
            "stale_nodes": [],
            "recompute_plan": {},
            "disclosures": ["恢复后验证执行失败：全部结论 unknown"],
        }
    plan.gis_chapter["resumed_from"]["verify"] = {
        "workflow_verdict": str(verify_report.get("workflow", {}).get("verdict") or "unknown"),
        "workflow_reason": str(verify_report.get("workflow", {}).get("reason") or "")[:200],
        "mapspec_verdict": str(verify_report.get("mapspec", {}).get("verdict") or "unknown"),
        "stale_nodes": list(verify_report.get("stale_nodes") or [])[:32],
        "recompute_plan": verify_report.get("recompute_plan") or {},
        "disclosures": list(verify_report.get("disclosures") or [])[:32],
        "ref_verdicts": {
            str(k)[:64]: {
                "verdict": str(v.get("verdict") or "unknown"),
                "reasons": list(v.get("reasons") or [])[:4],
            }
            for k, v in list((verify_report.get("ref_verdicts") or {}).items())[:128]
            if isinstance(v, dict)
        },
    }
    await save_session_plan(plan)
    await session_data_manager.set_map_state(
        new_sid, "_resumed_from", {"anchor_id": anchor_id, "source_session_id": old_sid}
    )

    # V6（ADR-0119 D4）：锚点内 recovery_state 重注入新 session —— 恢复后
    # 的 continuation 裁决从 durable 证据（循环预算余量）出发，不机械
    # replay 也不凭空重置预算。同时续接 durable 恢复账本（D5）。
    try:
        from app.services.gis_harness.durable_context import (
            RECOVERY_STATE_KEY,
            new_recovery_state,
            recovery_state_for_anchor,
        )

        carried_state = recovery_state_for_anchor(
            anchor.get("recovery_state") or {})
        if carried_state:
            await session_data_manager.set_map_state(
                new_sid, RECOVERY_STATE_KEY, carried_state)
        else:
            await session_data_manager.set_map_state(
                new_sid, RECOVERY_STATE_KEY, new_recovery_state())
    except Exception:  # noqa: BLE001 — 注入失败不阻断恢复
        logger.warning("[ResumeAnchor] recovery_state re-inject failed",
                       exc_info=True)

    # V6（ADR-0119 D5）：恢复预算续接 —— 旧 session 的 durable 恢复账本
    # （有界截取）拷进新 session。恢复后的会话**不**重新获得完整重试
    # 预算（防「重启/恢复 → 预算复活 → 无限重试」）。失败不阻断恢复
    # （恢复本身成功，账本缺席 = 诚实降级，recovery_state 披露）。
    carried_entries = 0
    try:
        from app.services.gis_harness.recovery_ledger import (
            get_recovery_ledger,
        )

        carried_entries = get_recovery_ledger().copy_between_sessions(
            old_sid, new_sid)
    except Exception:  # noqa: BLE001 — 预算续接失败不阻断恢复
        logger.warning("[ResumeAnchor] recovery ledger carry-over failed",
                       exc_info=True)

    # review R2 #2：立即创建归属当前用户的 Conversation 行 —— 否则所有
    # require_owned_session 路径（observation 上报/map mutation）对新
    # session 404，且首个发言者可「认领」该 session（所有权泄漏窗口）。
    owner_token: Optional[str] = None
    try:
        from app.models.db_model import Conversation

        existing = await db.get(Conversation, new_sid)
        if existing is None:
            conv = Conversation(id=new_sid, user_id=user_id,
                                title=str(anchor.get("user_goal") or "恢复的会话")[:200])
            db.add(conv)
            await db.commit()
    except Exception:  # noqa: BLE001 — DB 不可用时恢复本身仍成功（诚实降级：
        # conversation 行缺失 → require_owned_session 路径 404，agent 主链路
        # 经 chat get-or-create 兜底）
        logger.warning("[ResumeAnchor] conversation row create failed sid=%s",
                       new_sid, exc_info=True)

    return {
        "session_id": new_sid,
        "source_session_id": old_sid,
        "owner_token": owner_token,
        "user_goal": plan.user_goal,
        "restored_chapter_keys": [
            k for k in RESTORABLE_CHAPTER_KEYS
            if k in (plan.gis_chapter or {})
        ],
        "restored_refs": restored_refs,
        "ref_map": ref_map,
        "missing_refs": missing_refs,
        "trace_last_seq": int(anchor.get("trace_last_seq") or 0),
        # V6 W14：恢复后验证结论（verify-not-assume；无证据不断言存活）。
        "ref_verdicts": verify_report.get("ref_verdicts") or {},
        "workflow_verify": verify_report.get("workflow") or {},
        "mapspec_verify": verify_report.get("mapspec") or {},
        "stale_nodes": verify_report.get("stale_nodes") or [],
        "recompute_plan": verify_report.get("recompute_plan") or {},
        "verify_disclosures": verify_report.get("disclosures") or [],
        "recovery_budget_carried": carried_entries,
    }


__all__ = [
    "build_anchor",
    "save_anchor",
    "resume_from_anchor",
    "RESTORABLE_CHAPTER_KEYS",
    "MAX_ANCHOR_REFS",
]
