"""Resume VNext 恢复后验证（Harness V6 Wave 14 — verify-not-assume）。

恢复锚点（``resume_anchor``）只回答「从哪里继续」；本模块回答「继续的
依据是否还成立」—— 对锚点携带的每个 ref 做恢复后验证而非假设：

- 存活性（liveness）：新 session 内载荷可读（``session_data_manager`` 真实读取）
- 产物修订（artifact revision）：``content_revision`` / ``data_fingerprint``
  与锚点快照比对（描述符经 ``get_ref_descriptor`` 真实读取）
- 数据在场（data existence）：载荷非空
- 图面依赖（mapspec dependency）：经 ``product_graph._spec_source_ref``
  真实解析图层 source → ref，悬空/失配如实披露
- 工作流指纹（workflow fingerprint）：行指纹（``rows_fingerprint`` 同一实现）
  与锚点快照比对；package 指纹有快照才比对，无快照判 unknown 不假设

裁决词表（与运行态 stale 语义对齐，不新增平行枚举）：

- ``live``：有肯定证据（载荷在场 + 修订一致）
- ``stale``：有不一致证据（修订漂移 / 台账明确过期 / 行指纹漂移）
- ``unknown``：无证据（锚点无快照 / 描述符缺席 / 台账缺席等）—— 绝不标 live

stale / unknown 的 satisfied 节点统一翻 ``stale``（待重算）并经 W5 唯一
入口 ``compute_affected_subgraph`` 求受影响闭包（复用，不另建引擎）。

红线：确定性（无随机/时间戳/LLM）、有界输出、失败降级只披露不抛错。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: ref/工作流验证裁决。
VERDICT_LIVE = "live"
VERDICT_STALE = "stale"
VERDICT_UNKNOWN = "unknown"

#: 台账中视为产物死亡的状态（与 runtime_bridge W5 复用裁决同词）。
_UNHEALTHY_RECORD_STATUS = ("expired", "stale", "superseded", "failed")

#: 有界预算。
_MAX_VERDICTS = 128
_MAX_DISCLOSURES = 32
_MAX_STALE_NODES = 32

# ── 单 ref 验证 ──────────────────────────────────────────────────────────


async def verify_resumed_refs(
    new_session_id: str,
    ref_map: Dict[str, str],
    ref_evidence: Optional[Dict[str, Any]],
    old_session_id: str = "",
) -> Dict[str, Dict[str, Any]]:
    """逐 ref 验证（key 为新 session ref id）。

    ``ref_map``：锚点旧 ref → 新 session ref（重水合映射）；
    ``ref_evidence``：锚点旧 ref → ``{content_revision, content_hash}``
    快照（旧版锚点缺席 → 全部 unknown 并披露原因）。

    修订比对分两层（session 域语义不同，不可混用）：内容身份
    ``content_hash``（canonical sha256，跨 session 稳定）在新描述符上
    比对；``content_revision``（session 域计数器）只在旧 session 存活
    时与快照比对，旧 session 已消亡则记 uncomparable（以内容哈希为准）。
    """
    from app.services.session_data import session_data_manager

    verdicts: Dict[str, Dict[str, Any]] = {}
    evidence = ref_evidence if isinstance(ref_evidence, dict) else {}
    for old_ref, new_ref in list(ref_map.items())[:_MAX_VERDICTS]:
        checks: Dict[str, str] = {}
        reasons: List[str] = []
        # 存活性 + 数据在场：真实读取新 session 载荷。
        try:
            payload = await session_data_manager.get(new_session_id, new_ref)
        except Exception:  # noqa: BLE001 — 读失败按缺席（诚实降级）
            payload = None
        if payload is None:
            # 重水合已报成功，载荷却不可读 —— 假设落空，判 stale 而非 unknown。
            checks["liveness"] = "missing"
            reasons.append("重水合后载荷在新 session 不可读")
        else:
            checks["liveness"] = "present"
            checks["data_existence"] = (
                "present" if _payload_nonempty(payload) else "empty"
            )
            if checks["data_existence"] == "empty":
                reasons.append("载荷在场但为空")
        # 描述符（新 session）与旧 session 快照。
        try:
            descriptor = await session_data_manager.get_ref_descriptor(
                new_session_id, new_ref
            )
        except Exception:  # noqa: BLE001 — 描述符读失败按缺席
            descriptor = None
        old_descriptor: Optional[dict] = None
        if old_session_id:
            try:
                old_descriptor = await session_data_manager.get_ref_descriptor(
                    old_session_id, old_ref
                )
            except Exception:  # noqa: BLE001 — 旧 session 消亡按缺席
                old_descriptor = None
        if descriptor is None:
            checks["content_identity"] = "unknown"
            reasons.append("新 session 无描述符证据")
        expected = evidence.get(old_ref)
        if not isinstance(expected, dict):
            if checks.get("content_identity") != "unknown":
                checks["content_identity"] = "unknown"
            checks["revision_counter"] = "unknown"
            reasons.append("锚点无该 ref 证据快照（旧版锚点或快照失败）")
        elif descriptor is not None:
            drift = _identity_drift(descriptor, old_descriptor, expected)
            if drift is None:
                checks["content_identity"] = "current"
                checks.setdefault("revision_counter", "current")
            else:
                kind, text = drift
                checks[kind] = "drifted"
                reasons.append(text)
            if "revision_counter" not in checks:
                checks["revision_counter"] = "uncomparable"
                reasons.append("旧 session 已消亡，修订计数不可比（以内容哈希为准）")
        # 台账健康（W5 同一状态词）：新 session 域台账天然缺席时只披露，
        # 不单独推翻载荷 + 描述符已证实的 liveness（台账是复用安全证据，
        # 非存活证据；复用裁决仍由 W5 runtime_bridge 负责）。
        try:
            from app.services.artifact_registry import get_artifact

            record = await get_artifact(new_session_id, new_ref)
        except Exception:  # noqa: BLE001 — 台账读失败按缺席披露
            record = None
        if record is None:
            checks["artifact_health"] = "unknown"
            reasons.append("新 session 无 artifact 台账记录")
        else:
            status = str(
                getattr(record, "status", "")
                or (record.get("status") if isinstance(record, dict) else "")
                or "unknown"
            )
            if status == "valid":
                checks["artifact_health"] = "healthy"
            elif status in _UNHEALTHY_RECORD_STATUS:
                checks["artifact_health"] = "unhealthy"
                reasons.append(f"台账状态为 {status}")
            else:
                checks["artifact_health"] = "unknown"
                reasons.append(f"台账状态未知（{status}）")
        verdicts[new_ref] = {
            "verdict": _combine_verdict(checks),
            "checks": checks,
            "reasons": reasons[:4],
            "source_ref": old_ref,
        }
    return verdicts


def _payload_nonempty(payload: Any) -> bool:
    """载荷非空判断（确定性；未知形状按在场处理，不误判）。"""
    if payload is None:
        return False
    if isinstance(payload, (dict, list, str, bytes)):
        return len(payload) > 0
    return True


def _identity_drift(
    descriptor: Dict[str, Any],
    old_descriptor: Optional[Dict[str, Any]],
    expected: Dict[str, Any],
) -> Optional[Tuple[str, str]]:
    """修订比对；一致 → None，漂移 → （checks 键，中文原因）。

    内容身份（``content_hash``）优先：跨 session 稳定，双方在场即裁决。
    ``content_revision`` 只在旧 session 描述符在场时比对（同 session 域
    计数器）；快照修订为 0（未知）时不比对。
    """
    exp_hash = expected.get("content_hash")
    cur_hash = descriptor.get("content_hash")
    if exp_hash and cur_hash and str(cur_hash) != str(exp_hash):
        return ("content_identity", "内容哈希漂移（重水合载荷与锚点快照不一致）")
    try:
        exp_rev = int(expected.get("content_revision") or 0)
    except (TypeError, ValueError):
        exp_rev = 0
    if exp_rev and isinstance(old_descriptor, dict):
        try:
            old_rev = int(old_descriptor.get("content_revision") or 0)
        except (TypeError, ValueError):
            old_rev = 0
        if old_rev and old_rev != exp_rev:
            return ("revision_counter",
                    f"旧 session 内修订漂移（锚点 {exp_rev} → 当前 {old_rev}）")
    exp_fp = expected.get("data_fingerprint")
    cur_fp = descriptor.get("data_fingerprint")
    if exp_fp and cur_fp and str(cur_fp) != str(exp_fp):
        return ("content_identity", "data_fingerprint 漂移（来源数据已变更）")
    if not exp_hash and not exp_rev and not exp_fp:
        return ("content_identity", "锚点快照无可用修订字段（旧版锚点或快照失败）")
    if not cur_hash and old_descriptor is None:
        return ("content_identity", "无可比对的修订证据（内容哈希缺席且旧 session 已消亡）")
    return None


def _combine_verdict(checks: Dict[str, str]) -> str:
    """checks → 裁决：死亡/漂移证据判 stale；关键证据缺席判 unknown；
    仅肯定证据齐备才判 live（台账 unknown 与修订 uncomparable 只披露，
    不单独推翻 live —— 台账是复用安全证据非存活证据，复用裁决仍归 W5）。"""
    if (
        checks.get("liveness") == "missing"
        or checks.get("data_existence") == "empty"
        or checks.get("content_identity") == "drifted"
        or checks.get("revision_counter") == "drifted"
        or checks.get("artifact_health") == "unhealthy"
    ):
        return VERDICT_STALE
    if (
        checks.get("liveness") != "present"
        or checks.get("content_identity") != "current"
    ):
        return VERDICT_UNKNOWN
    return VERDICT_LIVE


# ── 工作流指纹验证 ───────────────────────────────────────────────────────


def verify_workflow_fingerprint(
    anchor_fingerprint: Optional[Dict[str, Any]],
    restored_instance: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """工作流指纹比对（纯函数）：行指纹决定裁决，package 指纹仅披露。

    ``anchor_fingerprint``：锚点快照 ``{rows_fingerprint, state_revision,
    package_fingerprint?}``；``restored_instance``：恢复后章节内
    ``workflow_instance``（ref 已重写）。
    """
    if not isinstance(anchor_fingerprint, dict) or not anchor_fingerprint:
        return {
            "verdict": VERDICT_UNKNOWN,
            "reason": "锚点无工作流指纹快照（旧版锚点或快照失败）",
            "package": "unknown",
        }
    if not isinstance(restored_instance, dict) or not restored_instance:
        return {
            "verdict": VERDICT_UNKNOWN,
            "reason": "恢复后章节无 workflow_instance 证据",
            "package": "unknown",
        }
    old_rows = str(anchor_fingerprint.get("rows_fingerprint") or "")
    new_rows = str(restored_instance.get("rows_fingerprint") or "")
    if not old_rows or not new_rows:
        verdict = VERDICT_UNKNOWN
        reason = "行指纹证据不全（无法比对，不假设一致）"
    elif old_rows != new_rows:
        verdict = VERDICT_STALE
        reason = "行指纹漂移（恢复前后 workflow 行状态不一致）"
    else:
        verdict = VERDICT_LIVE
        reason = "行指纹一致"
    old_pkg = str(anchor_fingerprint.get("package_fingerprint") or "")
    new_pkg = str(restored_instance.get("package_fingerprint") or "")
    if not old_pkg or not new_pkg:
        package = "unknown"
    elif old_pkg == new_pkg:
        package = "stable"
    else:
        package = "changed"
        if verdict == VERDICT_LIVE:
            verdict = VERDICT_STALE
            reason = "package 指纹变更（方法包已更新，产物不可直接复用）"
    return {"verdict": verdict, "reason": reason, "package": package}


# ── 图面依赖验证 ─────────────────────────────────────────────────────────


def verify_mapspec_dependencies(
    mapspec_like: Optional[Dict[str, Any]],
    ref_map: Dict[str, str],
    ref_verdicts: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """图面依赖验证（纯函数 + 真实 source 解析）。

    ``mapspec_like``：恢复后章节内的 mapspec 形态 maddict（``sources`` +
    ``layers``；缺席 → unknown 披露）。source 解析复用
    ``product_graph._spec_source_ref``（W3 同一实现，不手写第二实现）。
    """
    from app.services.gis_harness.product_graph import _spec_source_ref

    if not isinstance(mapspec_like, dict) or not mapspec_like:
        return {
            "verdict": VERDICT_UNKNOWN,
            "reason": "恢复后章节无 mapspec 证据（图面依赖无法验证）",
            "layers": [],
        }
    rev_map = {new: old for old, new in ref_map.items()}
    layers_out: List[Dict[str, Any]] = []
    worst = VERDICT_LIVE
    layers = mapspec_like.get("layers")
    if not isinstance(layers, list):
        layers = []
    for layer in layers[:32]:
        if not isinstance(layer, dict):
            continue
        lid = str(layer.get("id") or "")[:64]
        raw_ref = _spec_source_ref(mapspec_like, str(layer.get("source") or ""))
        if not raw_ref:
            layers_out.append(
                {"layer_id": lid, "verdict": VERDICT_UNKNOWN,
                 "reason": "图层 source 无 ref 指针（悬空）"})
            worst = _worst(worst, VERDICT_UNKNOWN)
            continue
        # 恢复后章节内的 ref 可能是新 id（已重写）或旧 id（重水合失败已置空
        # 为 ""，此处只剩旧 id 残留）—— 双向归一到新 id 查裁决。
        new_ref = ref_map.get(raw_ref, raw_ref)
        verdict_entry = ref_verdicts.get(new_ref)
        if verdict_entry is None:
            if raw_ref in rev_map or raw_ref not in ref_map.values():
                layers_out.append(
                    {"layer_id": lid, "verdict": VERDICT_UNKNOWN,
                     "reason": f"依赖 {raw_ref[:32]} 无验证结论（未重水合或证据缺席）"})
                worst = _worst(worst, VERDICT_UNKNOWN)
            else:
                layers_out.append({"layer_id": lid, "verdict": VERDICT_LIVE,
                                   "reason": "依赖 ref 存活"})
            continue
        verdict = str(verdict_entry.get("verdict") or VERDICT_UNKNOWN)
        layers_out.append({
            "layer_id": lid,
            "verdict": verdict,
            "reason": "；".join(verdict_entry.get("reasons") or [])[:200] or "依赖 ref 存活",
        })
        worst = _worst(worst, verdict)
    return {
        "verdict": worst if layers_out else VERDICT_UNKNOWN,
        "reason": (
            "图面依赖全部存活" if worst == VERDICT_LIVE and layers_out
            else ("图面依赖存在 stale/unknown，需重算或重新获取" if layers_out
                  else "mapspec 内无图层证据")
        ),
        "layers": layers_out,
    }


def _worst(current: str, candidate: str) -> str:
    """裁决劣化序：stale > unknown > live。"""
    order = {VERDICT_LIVE: 0, VERDICT_UNKNOWN: 1, VERDICT_STALE: 2}
    if order.get(candidate, 1) >= order.get(current, 0):
        return candidate
    return current


# ── 编排：验证 → stale 翻标记 → recompute 闭包 ────────────────────────────


async def verify_resume(
    new_session_id: str,
    anchor: Dict[str, Any],
    ref_map: Dict[str, str],
    restored_chapter: Dict[str, Any],
) -> Dict[str, Any]:
    """恢复后验证编排（恢复管线内调用，绝不抛错——失败降级为披露）。

    返回有界报告 ``{ref_verdicts, workflow, mapspec, stale_nodes,
    recompute_plan, disclosures}``；同时把 satisfied-but-unverified 的
    stage 翻为 ``stale``（直接改 ``restored_chapter``，由调用方持久化）。
    """
    disclosures: List[str] = []
    try:
        ref_verdicts = await verify_resumed_refs(
            new_session_id, ref_map,
            (anchor.get("ref_evidence") if isinstance(anchor, dict) else None),
            old_session_id=str(anchor.get("source_session_id") or "")
            if isinstance(anchor, dict) else "",
        )
    except Exception:  # noqa: BLE001 — 验证本身失败 → 全部 unknown（不阻断恢复）
        logger.warning("[ResumeVerify] ref verify failed", exc_info=True)
        ref_verdicts = {}
        disclosures.append("ref 验证执行失败：无逐 ref 结论（unknown）")
    # 未重水合的旧 ref（missing）：同样 unknown 披露（调用方已披露 missing_refs，
    # 此处补裁决口径，口径统一：无证据不断言存活）。
    missing_old = [
        r for r in ((anchor.get("ref_ids") or []) if isinstance(anchor, dict) else [])
        if r not in ref_map
    ][:_MAX_VERDICTS]

    workflow = verify_workflow_fingerprint(
        anchor.get("workflow_fingerprint") if isinstance(anchor, dict) else None,
        (restored_chapter.get("workflow_instance")
         if isinstance(restored_chapter, dict) else None),
    )
    mapspec_like = _extract_mapspec_like(restored_chapter)
    mapspec = verify_mapspec_dependencies(mapspec_like, ref_map, ref_verdicts)

    stale_nodes, recompute_plan = _mark_stale_and_plan(
        restored_chapter, ref_map, ref_verdicts, missing_old, workflow,
        disclosures,
    )
    for old_ref in missing_old:
        disclosures.append(f"旧 ref {str(old_ref)[:32]} 未重水合：unknown（待 agent 重新获取）")
    report = {
        "ref_verdicts": dict(list(ref_verdicts.items())[:_MAX_VERDICTS]),
        "workflow": workflow,
        "mapspec": mapspec,
        "stale_nodes": stale_nodes[:_MAX_STALE_NODES],
        "recompute_plan": recompute_plan,
        "disclosures": disclosures[:_MAX_DISCLOSURES],
    }
    return report


def _extract_mapspec_like(chapter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从恢复后章节提取 mapspec 形态证据（内嵌 mapspec 优先，map_product 兜底）。"""
    if not isinstance(chapter, dict):
        return None
    for key in ("mapspec", "map_product"):
        candidate = chapter.get(key)
        if isinstance(candidate, dict) and (
            isinstance(candidate.get("sources"), (dict, list))
            or isinstance(candidate.get("layers"), list)
        ):
            return candidate
    return None


def _mark_stale_and_plan(
    chapter: Dict[str, Any],
    ref_map: Dict[str, str],
    ref_verdicts: Dict[str, Dict[str, Any]],
    missing_old: List[str],
    workflow: Dict[str, Any],
    disclosures: List[str],
) -> Tuple[List[str], Dict[str, Any]]:
    """satisfied-but-unverified stage 翻 stale → W5 闭包（纯内存改 chapter）。

    种子规则（保守正确，宁可多算）：stage 状态为 satisfied 且其 bound_ref
    的裁决非 live（stale/unknown/缺席/悬空），或工作流指纹 stale → 该 stage
    进种子集；种子经 ``compute_affected_subgraph``（W5 唯一入口）求正向
    闭包，闭包内 satisfied 节点翻 stale（``upstream_stale``）。
    """
    from app.services.gis_harness.workflow_v4.recompute import (
        WorkflowChange,
        compute_affected_subgraph,
    )

    if not isinstance(chapter, dict):
        return [], {}
    instance = chapter.get("workflow_instance")
    if not isinstance(instance, dict):
        return [], {}
    stages = instance.get("stages")
    if not isinstance(stages, list):
        return [], {}
    missing_new = set(ref_map.values())
    _ = missing_new  # 新 id 全集（语义锚点：裁决缺席即无证据）
    workflow_stale = workflow.get("verdict") == VERDICT_STALE

    seeds: List[WorkflowChange] = []
    seed_ids: List[str] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("state") or "") != "satisfied":
            continue
        node_id = str(stage.get("capability") or stage.get("bound_ref") or "")[:64]
        if not node_id:
            continue
        bound = str(stage.get("bound_ref") or "")
        reason = ""
        if not bound:
            reason = "resume_ref_dangling"
        else:
            entry = ref_verdicts.get(bound)
            verdict = str((entry or {}).get("verdict") or VERDICT_UNKNOWN)
            if verdict != VERDICT_LIVE:
                reason = (
                    "resume_ref_stale" if verdict == VERDICT_STALE
                    else "resume_ref_unknown"
                )
        if not reason and workflow_stale:
            reason = "resume_workflow_stale"
        if reason:
            stage["state"] = "stale"
            stage["stale_reason"] = reason
            seeds.append(WorkflowChange(
                dimension="data", target_kind="node",
                target=node_id, detail=reason[:200]))
            seed_ids.append(node_id)
            disclosures.append(f"节点 {node_id} 翻 stale（{reason}）")
    if not seeds:
        return [], {}
    dag = _instance_dag(instance)
    try:
        plan = compute_affected_subgraph(dag, seeds)
    except Exception:  # noqa: BLE001 — 闭包失败时种子集即闭包（保守降级）
        logger.warning("[ResumeVerify] recompute closure failed", exc_info=True)
        disclosures.append("受影响子图计算失败：以种子集为闭包（保守）")
        return sorted(set(seed_ids))[:_MAX_STALE_NODES], {
            "recompute": sorted(set(seed_ids))[:_MAX_STALE_NODES],
            "reuse": [],
            "explanations": ["closure-fallback: seeds as recompute set"],
        }
    dirty = set(plan.recompute) | set(seed_ids)
    by_cap = {
        str(s.get("capability") or ""): s for s in stages
        if isinstance(s, dict) and s.get("capability")
    }
    for node_id in sorted(dirty):
        stage = by_cap.get(node_id)
        if stage is None or str(stage.get("state") or "") != "satisfied":
            continue
        stage["state"] = "stale"
        stage["stale_reason"] = "upstream_stale"
    stale_nodes = sorted({
        str(s.get("capability") or "")
        for s in stages
        if isinstance(s, dict) and str(s.get("state") or "") == "stale"
        and s.get("capability")
    })[:_MAX_STALE_NODES]
    bounded = plan.to_bounded_dict()
    bounded["recompute"] = sorted(dirty)[:32]
    return stale_nodes, bounded


def _instance_dag(instance: Dict[str, Any]) -> Dict[str, Any]:
    """workflow_instance stages/dependencies → bounded DAG（闭包计算用）。

    节点 id 取 capability（依赖边同词，见 workflow_instance 构造处）；
    边同时给 ``from/to``（port 形态，引擎自归一）与 ``depends_on``。
    """
    nodes: List[Dict[str, Any]] = []
    for stage in (instance.get("stages") or [])[:64]:
        if not isinstance(stage, dict):
            continue
        cap = str(stage.get("capability") or "")[:64]
        if not cap:
            continue
        nodes.append({"node_id": cap, "depends_on": []})
    by_id = {n["node_id"]: n for n in nodes}
    edges: List[Dict[str, str]] = []
    for dep in (instance.get("dependencies") or [])[:64]:
        if not isinstance(dep, dict):
            continue
        consumer = str(dep.get("consumer") or "")[:64]
        producer = str(dep.get("producer") or "")[:64]
        if consumer in by_id and producer in by_id:
            by_id[consumer].setdefault("depends_on", []).append(producer)
            edges.append({
                "from": f"{producer}.output",
                "to": f"{consumer}.input",
            })
    return {"nodes": nodes, "edges": edges}


__all__ = [
    "VERDICT_LIVE",
    "VERDICT_STALE",
    "VERDICT_UNKNOWN",
    "verify_resumed_refs",
    "verify_workflow_fingerprint",
    "verify_mapspec_dependencies",
    "verify_resume",
]
