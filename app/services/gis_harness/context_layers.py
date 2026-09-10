"""ContextLayers —— 九域分层上下文投影 + durable 侧预算（V7 ADR-0130 D3）。

V6 基线（durable_context.py）有 durable/rebuildable/forbidden 三分层词表
与 recovery_state 载体，但上下文仍是「workflow 位置级」散键 —— V7 Goal
Phase C 要求的 turn/session/project/workspace/map/data/workflow/artifact/
capability 九域视角没有统一对象；「下一句『只看武侯区并改成蓝色系』增量
继续」需要按域恢复，而不是整块 replay。

V7 契约：

- **九域 = 权威事实的确定性投影**（全 rebuildable —— 与 durable_context
  分层词表一致：本模块不新增任何 durable 事实键的*载荷*，只有域摘要
  指纹 ``context_digest`` 进锚点）。同输入同投影；删块可随时重建。
- **预算**：域字节上限 + 总预算 + 确定性裁剪序（``DOMAIN_PRUNE_ORDER``
  —— 低价值域先压缩为单行摘要，再超限先淘汰）；违规不静默
  （``violations`` 留痕，对齐 chat/context_budget 的「规划/度量/留痕、
  不截断语义内容」哲学 —— 这里的「截断」只发生在投影字节面，权威
  事实源零触碰）。
- **freshness**：每域携带 ``source_fingerprint``（该域输入事实指纹）——
  消费方比对指纹即可判新鲜，不信任旧值。
- **checkpoint / crash recovery**：整块单键写（``_context_layers``，
  map_state additive）；带 ``revision``（单调）+ ``content_fingerprint``；
  恢复 = 锚点摘要比对 + ``derive_context_layers`` 从权威状态重建
  （重建等价性由测试钉死）。
- **conflict resolution**：同域并发写 → revision 单调 + 后写胜 +
  fingerprint 不符时重建（诚实降级，不猜测）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_instance import canonical_fingerprint

logger = logging.getLogger(__name__)

#: map_state 单键（additive；白名单纪律归 durable_context 管辖）。
CONTEXT_LAYERS_KEY = "_context_layers"

#: 九域封闭词表。
CONTEXT_DOMAINS: tuple = (
    "turn", "session", "project", "workspace", "map",
    "data", "workflow", "artifact", "capability",
)

#: 确定性裁剪序（超预算时先压缩/淘汰：低价值投影先让位）。
DOMAIN_PRUNE_ORDER: tuple = (
    "workspace", "capability", "project", "turn",
    "artifact", "data", "session", "map", "workflow",
)

#: 域字节上限（默认；payload 截断在此层面，权威事实不受影响）。
DOMAIN_BYTE_CAPS: Dict[str, int] = {
    "turn": 256,
    "session": 512,
    "project": 384,
    "workspace": 192,
    "map": 512,
    "data": 640,
    "workflow": 640,
    "artifact": 640,
    "capability": 512,
}
#: 总预算（九域 payload 合计；有界一切）。
TOTAL_BYTE_BUDGET = 3072


def _bounded_str(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


# ── 域投影（纯函数；每域从权威事实派生）────────────────────────────────


def _turn_domain(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    rs = runtime_state if isinstance(runtime_state, dict) else {}
    return {
        "phase": _bounded_str(rs.get("phase"), 24),
        "revision": int(rs.get("phase_revision") or 0),
        "suspended": bool(rs.get("suspended")),
        "last_trigger": _bounded_str(rs.get("last_trigger"), 32),
    }


def _session_domain(chapter: Dict[str, Any], recovery: Dict[str, Any]) -> Dict[str, Any]:
    loops = {k: int(v) for k, v in list((recovery.get("loops") or {}).items())[:6]}
    return {
        "user_goal": _bounded_str(chapter.get("query"), 160),
        "workflow_position": _bounded_str(
            (recovery.get("position") or {}).get("step"), 80),
        "loops_remaining": loops,
    }


def _project_domain(chapter: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan_id": _bounded_str(chapter.get("plan_id"), 64),
        "recipe_id": _bounded_str(chapter.get("recipe_id"), 64),
        "envelope": _bounded_str(chapter.get("envelope_id"), 64),
    }


def _workspace_domain(chapter: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "manifest_fingerprint": _bounded_str(
            chapter.get("manifest_fingerprint"), 32),
        "plan_stale_marker": _bounded_str(chapter.get("plan_stale"), 16),
    }


def _map_domain(map_digest: Dict[str, Any]) -> Dict[str, Any]:
    md = map_digest if isinstance(map_digest, dict) else {}
    return {
        "mapspec_revision": int(md.get("mapspec_revision") or 0),
        "render_observation_seq": int(md.get("render_observation_seq") or 0),
        "layer_count": int(md.get("layer_count") or 0),
        "component_count": int(md.get("component_count") or 0),
    }


def _data_domain(recovery: Dict[str, Any]) -> Dict[str, Any]:
    fps = recovery.get("source_fingerprints") if isinstance(
        recovery, dict) else None
    items = [
        {"ref": _bounded_str((f or {}).get("ref"), 64),
         "fingerprint": _bounded_str((f or {}).get("fingerprint"), 32)}
        for f in (fps or [])[:16] if isinstance(f, dict)
    ] if isinstance(fps, list) else []
    return {"refs": items}


def _workflow_domain(chapter: Dict[str, Any]) -> Dict[str, Any]:
    block = chapter.get("workflow_instance")
    if not isinstance(block, dict):
        return {"revision": 0, "stages_digest": ""}
    stages = [
        _bounded_str(s.get("capability"), 40) + ":" + _bounded_str(s.get("state"), 12)
        for s in (block.get("stages") or [])[:12] if isinstance(s, dict)
    ]
    return {
        "revision": int(block.get("state_revision") or 0),
        "stages_digest": ",".join(stages)[:320],
    }


def _artifact_domain(chapter: Dict[str, Any]) -> Dict[str, Any]:
    refs = []
    for row in list(chapter.get("data_requirements") or []) + list(
            chapter.get("analysis_steps") or []):
        if not isinstance(row, dict):
            continue
        bound = str(row.get("bound_ref") or "")
        if bound:
            refs.append(_bounded_str(row.get("capability"), 40) + "→"
                        + _bounded_str(bound, 48))
    return {"bound_refs": refs[:16]}


def _capability_domain(chapter: Dict[str, Any]) -> Dict[str, Any]:
    caps = sorted({
        str(r.get("capability") or "")
        for r in list(chapter.get("data_requirements") or [])
        + list(chapter.get("analysis_steps") or [])
        if isinstance(r, dict) and r.get("capability")
    })
    return {"capabilities": [c[:48] for c in caps[:16]]}


def derive_context_layers(
    chapter: Optional[Dict[str, Any]],
    *,
    map_digest: Optional[Dict[str, Any]] = None,
    recovery: Optional[Dict[str, Any]] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
    stored: Optional[Dict[str, Any]] = None,
) -> "ContextLayersState":
    """权威事实 → 九域投影（确定性纯函数；重建等价）。

    输入缺席的域按空投影（诚实缺席 —— 不猜）。
    """
    chapter = chapter if isinstance(chapter, dict) else {}
    recovery = recovery if isinstance(recovery, dict) else new_empty_recovery()
    payloads: Dict[str, Dict[str, Any]] = {
        "turn": _turn_domain(runtime_state or {}),
        "session": _session_domain(chapter, recovery),
        "project": _project_domain(chapter),
        "workspace": _workspace_domain(chapter),
        "map": _map_domain(map_digest or {}),
        "data": _data_domain(recovery),
        "workflow": _workflow_domain(chapter),
        "artifact": _artifact_domain(chapter),
        "capability": _capability_domain(chapter),
    }
    domains: Dict[str, DomainBlock] = {}
    for name in CONTEXT_DOMAINS:
        payload = payloads[name]
        domains[name] = DomainBlock(
            source_fingerprint=canonical_fingerprint(payload)[:32],
            payload=payload,
        )
    state = ContextLayersState(domains=domains)
    if isinstance(stored, dict) and stored.get("schema") == "context_layers.v1":
        state.revision = int(stored.get("revision") or 0) + 1
    else:
        state.revision = 1
    apply_context_budget(state)
    state.content_fingerprint = canonical_fingerprint(
        {k: v.to_bounded_dict() for k, v in state.domains.items()})[:32]
    return state


def new_empty_recovery() -> Dict[str, Any]:
    return {"loops": {}, "position": {}, "source_fingerprints": []}


# ── 预算 ─────────────────────────────────────────────────────────────────


def _payload_bytes(block: DomainBlock) -> int:
    import json

    try:
        return len(json.dumps(block.payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 — 度量失败按上界保守
        return 1 << 20


def apply_context_budget(state: "ContextLayersState") -> None:
    """确定性预算执行：域上限压缩 → 总预算按 DOMAIN_PRUNE_ORDER 淘汰。

    违规留痕（violations）—— 不静默。权威事实源零触碰。
    """
    violations: List[str] = []
    for name, cap in DOMAIN_BYTE_CAPS.items():
        block = state.domains.get(name)
        if block is None:
            continue
        size = _payload_bytes(block)
        if size <= cap:
            continue
        # 压缩为单行摘要（确定性）
        summary = canonical_fingerprint(block.payload)[:16]
        block.payload = {"compacted": True, "digest": summary}
        block.compacted = True
        violations.append(f"domain_over_cap:{name}({size}>{cap})")
    total = sum(_payload_bytes(b) for b in state.domains.values())
    if total > TOTAL_BYTE_BUDGET:
        for name in DOMAIN_PRUNE_ORDER:
            if total <= TOTAL_BYTE_BUDGET:
                break
            block = state.domains.get(name)
            if block is None or not block.payload:
                continue
            total -= _payload_bytes(block)
            block.payload = {}
            block.pruned = True
            violations.append(f"domain_pruned:{name}")
        if total > TOTAL_BYTE_BUDGET:
            violations.append(f"total_over_budget({total}>{TOTAL_BYTE_BUDGET})")
    state.violations = violations[:8]


# ── 模型 ─────────────────────────────────────────────────────────────────


class DomainBlock(BaseModel):
    """一个上下文域（payload 有界 + 源指纹即新鲜度）。"""

    source_fingerprint: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    compacted: bool = False
    pruned: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        import json

        try:
            payload = json.loads(json.dumps(
                self.payload, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001 — 序列化失败按摘要
            payload = {"digest": canonical_fingerprint(self.payload)[:16]}
        return {
            "source_fingerprint": self.source_fingerprint[:32],
            "payload": payload,
            "compacted": self.compacted,
            "pruned": self.pruned,
        }


class ContextLayersState(BaseModel):
    """九域上下文状态（map_state[CONTEXT_LAYERS_KEY] 的 schema）。"""

    schema_version: str = "context_layers.v1"
    revision: int = 1
    content_fingerprint: str = ""
    domains: Dict[str, DomainBlock] = Field(default_factory=dict)
    violations: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema_version,
            "revision": int(self.revision),
            "content_fingerprint": self.content_fingerprint[:32],
            "domains": {
                k: v.to_bounded_dict()
                for k, v in list(self.domains.items())[:len(CONTEXT_DOMAINS)]
            },
            "violations": [v[:96] for v in self.violations[:8]],
        }

    def digest(self) -> str:
        """整体摘要指纹（进锚点 / runtime commit 标记；[:32]）。"""
        return self.content_fingerprint[:32]


# ── 锚点桥（durable 白名单纪律：只带摘要，不带载荷）────────────────────


def context_digest_for_anchor(state: Optional[ContextLayersState]) -> Dict[str, Any]:
    """九域状态 → 锚点内嵌摘要（rebuildable 纪律 —— 载荷永不进锚点）。"""
    if state is None or not state.domains:
        return {}
    return {
        "v": 1,
        "digest": state.digest(),
        "revision": int(state.revision),
        "domains": {
            name: {
                "fp": block.source_fingerprint[:32],
                "compacted": block.compacted,
                "pruned": block.pruned,
            }
            for name, block in list(state.domains.items())[:len(CONTEXT_DOMAINS)]
        },
    }


# ── 服务入口 ─────────────────────────────────────────────────────────────


async def checkpoint_context_layers(session_id: str) -> Optional[Dict[str, Any]]:
    """turn 边界 / 终验后 checkpoint（读权威事实 → 投影 → 单键写）。

    原子性：整块单键一次写（session_data set_map_state 单键语义）+
    revision 单调 + content_fingerprint 校验 —— 读侧 fingerprint 不符即
    重建，不信任半写（诚实降级）。
    """
    if not session_id:
        return None
    try:
        from app.services.session_plan import load_session_plan
        from app.services.session_data import session_data_manager
        from app.services.gis_harness.durable_context import load_recovery_state
        from app.services.gis_harness.render_observation import (
            load_render_observation,
            observation_sequence,
        )
        from app.services.mapspec_store import mapspec_store

        plan = await load_session_plan(session_id)
        chapter = plan.gis_chapter if plan is not None and isinstance(
            plan.gis_chapter, dict) else {}
        if not chapter:
            return None
        recovery = await load_recovery_state(session_id)
        try:
            map_state = await session_data_manager.get_map_state(session_id)
        except Exception:  # noqa: BLE001 — 观察缺席按空
            map_state = None
        observation = await load_render_observation(session_id, map_state)
        try:
            spec = await mapspec_store.get_mapspec(session_id) or {}
        except Exception:  # noqa: BLE001 — spec 读失败按空
            spec = {}
        map_digest = {
            "mapspec_revision": int((map_state or {}).get(
                "_cartographic_mutation_revision") or 0),
            "render_observation_seq": observation_sequence(observation),
            "layer_count": len((spec.get("layers") or []) if isinstance(
                spec, dict) else []),
            "component_count": len((spec.get("components") or []) if isinstance(
                spec, dict) else []),
        }
        runtime_state = chapter.get("runtime_state") if isinstance(
            chapter.get("runtime_state"), dict) else {}
        stored = (map_state or {}).get(CONTEXT_LAYERS_KEY)
        state = derive_context_layers(
            chapter,
            map_digest=map_digest,
            recovery=recovery,
            runtime_state=runtime_state,
            stored=stored if isinstance(stored, dict) else None,
        )
        block = state.to_bounded_dict()
        await session_data_manager.set_map_state(
            session_id, CONTEXT_LAYERS_KEY, block)
        return block
    except Exception:  # noqa: BLE001 — checkpoint 失败不阻断（可重建）
        logger.debug(
            "[ContextLayers] checkpoint failed session=%s", session_id,
            exc_info=True)
        return None


__all__ = [
    "CONTEXT_LAYERS_KEY",
    "CONTEXT_DOMAINS",
    "DOMAIN_PRUNE_ORDER",
    "DOMAIN_BYTE_CAPS",
    "TOTAL_BYTE_BUDGET",
    "DomainBlock",
    "ContextLayersState",
    "derive_context_layers",
    "apply_context_budget",
    "context_digest_for_anchor",
    "checkpoint_context_layers",
]
