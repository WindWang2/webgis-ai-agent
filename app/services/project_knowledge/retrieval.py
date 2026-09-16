"""ProjectKnowledge 检索与复用判定（跨 Mission 空间复用的核心读路径）。

判定纪律（D4，fail-closed）：
- 判定**只看**指纹与作用域（bbox 覆盖 / temporal 相等 / method 相等 /
  上游 dataset 指纹 / claim 状态），**名字相似永不参与判定**；
- 作用域字段任何一侧未知 = 不能确认 = 判定降级（绝不 wildcard 放行）；
- 检索时对每条候选做 lazy liveness 复核：权威 token 漂移 → 行降级 stale
  （写回），权威行消失 → invalidated —— 缓存正确性不依赖任何失效通知；
- 正向证明缺失（claim 不可解析/非 SUPPORTED）永远不给 exact。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.services.project_knowledge import store as ks
from app.services.project_knowledge.contract import (
    AS_SESSION_REF,
    EK_ARTIFACT,
    EK_DATASET_VERSION,
    EK_MAP_PRODUCT,
    KnowledgeEntry,
    REL_DERIVED_FROM,
    REL_VERIFIED_BY,
    ReuseCandidate,
    ReuseQuery,
    ST_ACTIVE,
    VERDICT_EXACT,
    VERDICT_NOT_REUSABLE,
    VERDICT_RECOMPUTE_PARTIAL,
    bbox_contains,
    validate_bbox,
)
from app.services.project_knowledge.liveness import live_version_token

logger = logging.getLogger(__name__)

#: 复用候选默认实体类别（mission/failure/place/preference 是上下文，不是资产）。
REUSE_KINDS = (EK_ARTIFACT, EK_MAP_PRODUCT, EK_DATASET_VERSION)

_VERDICT_ORDER = {VERDICT_EXACT: 0, VERDICT_RECOMPUTE_PARTIAL: 1, VERDICT_NOT_REUSABLE: 2}


def _claim_statuses_for(claim_ids: List[str]) -> dict[str, Optional[str]]:
    """尽力解析 claim id → 状态；解析不到 = None（不可伪造 SUPPORTED）。"""
    out: dict[str, Optional[str]] = {}
    if not claim_ids:
        return out
    try:
        from app.services.gis_harness.hotpath_convergence.session_ctx import (
            get_session_context,
        )

        ctx = get_session_context()
        store = getattr(ctx, "claim_store", None) if ctx is not None else None
        for cid in claim_ids:
            claim = store.get_claim(cid) if store is not None else None
            out[cid] = str(claim.status.value) if claim is not None else None
    except Exception:  # noqa: BLE001 — claim 解析失败绝不影响检索主路径
        for cid in claim_ids:
            out.setdefault(cid, None)
    return out


def _evaluate_entry(
    db: Session,
    *,
    entry: KnowledgeEntry,
    query: ReuseQuery,
    request_stale_inputs: Optional[List[str]] = None,
) -> ReuseCandidate:
    """单条候选的 lazy 复核 + 判定（纯读权威 + 允许写回投影降级）。"""
    reasons: List[str] = []
    stale_causes: List[str] = []

    # 0. 行状态门：只有 active 参与；stale/invalidated 明示不可复用。
    if entry.status != ST_ACTIVE:
        return ReuseCandidate(
            entry=entry,
            verdict=VERDICT_NOT_REUSABLE,
            reasons=[f"entry_status={entry.status}({entry.invalidation_rule or '-'}）"],
            stale_causes=[f"entry_status={entry.status}"],
        )

    # 1. lazy liveness：权威 token 漂移 → 写回降级；消失 → invalidated。
    live = live_version_token(
        db,
        project_id=entry.project_id,
        authority_store=entry.authority_store,
        authority_id=entry.authority_id,
    )
    if live is None:
        ks.demote_stale_by_token(
            db, org_id=entry.org_id, project_id=entry.project_id,
            authority_store=entry.authority_store,
            authority_id=entry.authority_id, live_token=None,
        )
        return ReuseCandidate(
            entry=entry, verdict=VERDICT_NOT_REUSABLE,
            reasons=["权威行已消失/detached"], stale_causes=["authority_gone"],
        )
    if (live or "") != (entry.version_token or ""):
        ks.demote_stale_by_token(
            db, org_id=entry.org_id, project_id=entry.project_id,
            authority_store=entry.authority_store,
            authority_id=entry.authority_id, live_token=live,
        )
        return ReuseCandidate(
            entry=entry, verdict=VERDICT_NOT_REUSABLE,
            reasons=["权威版本已前进，投影降级 stale"],
            stale_causes=["authority_advanced"],
        )

    # 2. AOI 覆盖：entry.bbox 必须覆盖 request.bbox（两侧都已知才可确认）。
    entry_bbox = validate_bbox(entry.bbox)
    request_bbox = validate_bbox(query.bbox)
    if entry_bbox is not None and request_bbox is not None:
        if bbox_contains(entry_bbox, request_bbox):
            reasons.append("AOI 覆盖成立")
        else:
            return ReuseCandidate(
                entry=entry, verdict=VERDICT_NOT_REUSABLE,
                reasons=["AOI 不覆盖：产物范围不包含请求区域"],
                stale_causes=["aoi_mismatch"],
            )
    else:
        stale_causes.append("aoi_unverified")

    # 3. temporal：两侧都已知才可确认相等。
    if entry.temporal_label and query.temporal_label:
        if str(entry.temporal_label) == str(query.temporal_label):
            reasons.append(f"时间标签一致（{entry.temporal_label}）")
        else:
            return ReuseCandidate(
                entry=entry, verdict=VERDICT_NOT_REUSABLE,
                reasons=[
                    f"时间不一致：产物 {entry.temporal_label} vs 请求 "
                    f"{query.temporal_label}",
                ],
                stale_causes=["temporal_mismatch"],
            )
    else:
        stale_causes.append("temporal_unverified")

    # 4. method：两侧都已知才可确认相等。
    if entry.method_key and query.method_key:
        if str(entry.method_key) == str(query.method_key):
            reasons.append(f"方法一致（{entry.method_key}）")
        else:
            return ReuseCandidate(
                entry=entry, verdict=VERDICT_NOT_REUSABLE,
                reasons=["方法不一致（capability:algorithm）"],
                stale_causes=["method_mismatch"],
            )
    else:
        stale_causes.append("method_unverified")

    # 5. 上游 dataset 指纹：ref-tag 里带索引时 token，与请求给定的当前值比对。
    # 未知出处（token 为空，如权威 lineage 指纹列 NULL）≢ 指纹一致 ——
    # 一律 upstream_unverified，绝不产生正向 reason（review P1-1）。
    checked_upstream = False
    for tag in entry.refs:
        if tag.relation != REL_DERIVED_FROM or tag.authority != "project_dataset":
            continue
        expected = (query.dataset_fingerprints or {}).get(tag.id)
        if expected is None:
            stale_causes.append(f"upstream_unverified:{tag.id}")
            continue
        checked_upstream = True
        if not tag.token:
            stale_causes.append(f"upstream_unverified:{tag.id}")
        elif str(expected) != tag.token:
            stale_causes.append(f"upstream_drift:{tag.id}")
        else:
            reasons.append(f"上游数据集指纹一致（{tag.id}）")
    if query.dataset_fingerprints and not checked_upstream:
        # 请求给了指纹清单但产物没有上游投影可核对 —— 无法确认一致性。
        stale_causes.append("upstream_unprojected")

    # 6. verified_by claim：只有可解析且 SUPPORTED 才是正向证明。
    claim_ids = [t.id for t in entry.refs if t.relation == REL_VERIFIED_BY]
    if claim_ids and entry.authority_store != AS_SESSION_REF:
        statuses = _claim_statuses_for(claim_ids)
        for cid, status in statuses.items():
            if status == "supported":
                reasons.append(f"claim 支持（{cid}）")
            elif status is None:
                stale_causes.append(f"claim_unverifiable:{cid}")
            else:
                stale_causes.append(f"claim_{status}:{cid}")

    # 6b. 请求声称的输入指纹 vs 权威当前值：规划者拿 stale 输入规划 →
    # 全局降级（验证 liveness/version/scope 是本检索的职责之一）。
    if request_stale_inputs:
        stale_causes.extend(request_stale_inputs)

    # 7. session_ref 权威的条目不可 durable 复核 → 永不给 exact。
    if entry.authority_store == AS_SESSION_REF:
        stale_causes.append("authority_session_scoped")

    # 请求级 stale_cause 的软硬分类：声称的输入漂移（stale）→ soft（
    # 可重算）；声称的输入已消失（gone）→ hard（无输入可重算，review
    # P3-8）。
    hard = [c for c in stale_causes if not (
        c.startswith("upstream_") or c.startswith("claim_")
        or c.startswith("request_input_stale:")
        or c.endswith("_unverified") or c.endswith("_unprojected")
        or c.endswith("_unverifiable") or c == "authority_session_scoped"
    )]
    if hard:
        verdict = VERDICT_NOT_REUSABLE
    elif stale_causes:
        verdict = VERDICT_RECOMPUTE_PARTIAL
    else:
        verdict = VERDICT_EXACT
    return ReuseCandidate(
        entry=entry, verdict=verdict, reasons=reasons, stale_causes=stale_causes,
    )


def find_reuse_candidates(
    db: Session,
    *,
    org_id: str,
    project_id: str,
    query: ReuseQuery,
) -> List[ReuseCandidate]:
    """跨 Mission 复用检索：active 候选 → lazy 复核 → 判定 → 排序截断。

    返回有界候选（exact 优先，其后 recompute_partial / not_reusable）；
    不可复用者保留在结果尾部并携带 stale_causes（调用方可解释「为什么
    不能直接复用」）。caller commit（lazy 降级有写回）。
    """
    if not org_id or not project_id:
        return []
    kinds = tuple(query.kinds) if query.kinds else REUSE_KINDS

    # 请求级输入 liveness 复核：query.dataset_fingerprints 声称的是
    # 「新 Mission 计划使用的输入指纹」—— 若与权威当前值漂移/消失，
    # 所有候选一律不得 exact（规划输入本身已 stale）。
    request_stale_inputs: List[str] = []
    for ds_id, claimed in (query.dataset_fingerprints or {}).items():
        live = live_version_token(
            db, project_id=project_id,
            authority_store="project_dataset", authority_id=ds_id,
        )
        if live is None:
            request_stale_inputs.append(f"request_input_gone:{ds_id}")
        elif (live or "") != str(claimed):
            request_stale_inputs.append(f"request_input_stale:{ds_id}")

    entries = ks.get_active_entries(
        db, org_id=org_id, project_id=project_id,
        kinds=kinds, limit=200,
    )
    candidates = [
        _evaluate_entry(
            db, entry=e, query=query, request_stale_inputs=request_stale_inputs,
        )
        for e in entries
    ]
    candidates.sort(key=lambda c: (
        _VERDICT_ORDER.get(c.verdict, 3),
        -(c.entry.weight or 0.0),
        c.entry.id,
    ))
    return candidates[: max(1, min(int(query.limit), 64))]


__all__ = ["REUSE_KINDS", "find_reuse_candidates"]
