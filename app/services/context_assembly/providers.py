"""Typed context providers (F04) — adapters over existing single-render builders.

Each provider projects ONE authority; none re-renders authority data:
- verdict/memory/knowledge/gis_memory/card → the same builders the legacy
  ``_build_cartography_turn_context`` orchestrates (single render source);
- plan/surface/active-tools/v6/evicted → the same helpers ``bind_turn_prompt``
  uses (imported, not copied);
- harness state → the canonical ``HarnessTurnContext`` (hk.ctx.v1) projection
  (ADR-0208 D2: prompt assembly finally consumes the kernel's typed turn
  context);
- environment → caller-injected (the situation compiler owns the
  forward-only snapshot advance; the pipeline must not re-derive it).

``collect_cartography_wave`` is shared by the typed assembly AND the legacy
byte-equivalent compat path — one implementation, two consumers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Tuple

from app.services.context_assembly.contract import (
    ContextDomain,
    ContextItem,
    ProviderReport,
    SharedTurnFacts,
    TurnContextRequest,
    SourceScopeUnavailable,
    bounded_item,
)
from app.services.context_assembly.flags import harness_state_block_enabled

logger = logging.getLogger(__name__)


class BaseProvider:
    """Fail-open collection wrapper: build() errors → ProviderReport.error."""

    provider_id: str = ""
    domain: ContextDomain = ContextDomain.ENVIRONMENT

    def enabled(self, req: TurnContextRequest, facts: SharedTurnFacts) -> bool:
        return True

    async def collect(
        self, req: TurnContextRequest, facts: SharedTurnFacts
    ) -> ProviderReport:
        started = time.monotonic()
        if not self.enabled(req, facts):
            return ProviderReport(
                provider_id=self.provider_id, domain=self.domain,
                skipped_reason="disabled",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        try:
            items = await self.build(req, facts)
            return ProviderReport(
                provider_id=self.provider_id, domain=self.domain,
                items=items,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 — provider 缺席绝不阻断 turn
            logger.debug("[ctx_provider] %s failed: %s", self.provider_id,
                         type(exc).__name__)
            return ProviderReport(
                provider_id=self.provider_id, domain=self.domain,
                skipped_reason=f"error:{type(exc).__name__}",
                error=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    async def build(
        self, req: TurnContextRequest, facts: SharedTurnFacts
    ) -> List[ContextItem]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Wave A providers
# ---------------------------------------------------------------------------


class CartographyVerdictProvider(BaseProvider):
    """Bounded harness verdict (read-only; agent queries status actively)."""

    provider_id = "cartography_verdict"
    domain = ContextDomain.CARTOGRAPHY_VERDICT

    async def build(self, req, facts):
        from app.lib.cartography.quality_loop import cartographic_fingerprint
        from app.lib.cartography.verdict_summary import (
            render_verdict_for_llm,
            should_inject_verdict,
        )

        state = facts.map_state if isinstance(facts.map_state, dict) else {}
        review = state.get("_cartographic_review")
        mapspec = facts.mapspec if isinstance(facts.mapspec, dict) else None
        current_fingerprint = (
            cartographic_fingerprint(mapspec) if mapspec is not None else None
        )
        if not should_inject_verdict(review, current_fingerprint):
            return []
        text = render_verdict_for_llm(review)
        if not text:
            return []
        return [bounded_item(
            item_id="carto:verdict", provider_id=self.provider_id,
            domain=self.domain, content=text,
            freshness=str(current_fingerprint or ""),
            evidence_ref="cartographic_fingerprint",
            scope_id=facts.source_session_id,
        )]


class CartographyMemoryProvider(BaseProvider):
    """ADR-0069 project prior block (active facts only; stale never injected)."""

    provider_id = "cartography_memory"
    domain = ContextDomain.CARTOGRAPHY_MEMORY

    def enabled(self, req, facts):
        return bool(req.project_id)

    async def build(self, req, facts):
        from app.services.chat.context_assembler import _build_project_memory_block

        text = await asyncio.to_thread(
            _build_project_memory_block, req.project_id,
            user_id=req.user_id or None, org_id=req.org_id or None,
        )
        if not text:
            return []
        source_scope = getattr(text, "source_scope", None)
        if not source_scope:
            raise SourceScopeUnavailable("derived context is missing its source scope")
        return [bounded_item(
            item_id="carto:memory", provider_id=self.provider_id,
            domain=self.domain, content=text,
            **source_scope,
            evidence_ref="project_memory:active_facts",
        )]


class ProjectKnowledgeProvider(BaseProvider):
    """#1395 ``<project_knowledge>`` card (already XML-fenced by renderer)."""

    provider_id = "project_knowledge"
    domain = ContextDomain.PROJECT_KNOWLEDGE

    def enabled(self, req, facts):
        return bool(req.project_id)

    async def build(self, req, facts):
        from app.services.chat.context_assembler import _build_project_knowledge_block

        text = await asyncio.to_thread(
            _build_project_knowledge_block,
            req.project_id,
            org_id=req.org_id or None,
            user_id=req.user_id or None,
        )
        if not text:
            return []
        source_scope = getattr(text, "source_scope", None)
        if not source_scope:
            raise SourceScopeUnavailable("derived context is missing its source scope")
        return [bounded_item(
            item_id="knowledge:card", provider_id=self.provider_id,
            domain=self.domain, content=text,
            **source_scope,
            evidence_ref="project_knowledge:active_entries",
        )]


class GisMemoryProvider(BaseProvider):
    """ADR-0255 GIS spatial-memory prior (narrow interface, fail-open)."""

    provider_id = "gis_memory"
    domain = ContextDomain.GIS_MEMORY

    def enabled(self, req, facts):
        return bool(req.org_id)

    async def build(self, req, facts):
        from app.services.gis_memory.queries import (
            MemoryProjectionInput,
            build_memory_projection,
        )

        text = await build_memory_projection(
            MemoryProjectionInput(
                org_id=req.org_id,
                session_id=req.session_id,
                project_id=req.project_id or None,
                user_id=req.user_id or None,
                query_text=req.query_text or "",
            )
        )
        if not text:
            return []
        source_scope = getattr(text, "source_scope", None)
        if not source_scope:
            raise SourceScopeUnavailable("derived context is missing its source scope")
        return [bounded_item(
            item_id="memory:gis", provider_id=self.provider_id,
            domain=self.domain, content=text,
            # ADR-0255: org-scoped cross-session prior — renderable anywhere
            # inside its org, never outside it.
            **source_scope,
            evidence_ref="gis_memory:projection",
        )]


class SessionPlanProvider(BaseProvider):
    """SessionPlan bounded projection (same builder as legacy bind path)."""

    provider_id = "session_plan"
    domain = ContextDomain.SESSION_PLAN

    def enabled(self, req, facts):
        return bool(req.session_id) and facts.plan is not None

    async def build(self, req, facts):
        from app.services.session_plan import format_session_plan_projection

        text = format_session_plan_projection(facts.plan, facts.mapspec)
        if not text:
            return []
        return [bounded_item(
            item_id="plan:projection", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=str(getattr(facts.plan, "session_id", "") or ""),
            revision=str(getattr(facts.plan, "revision", "") or "")
            or str(getattr(facts.plan, "version", "") or ""),
            evidence_ref="session_plan:envelope",
        )]


class ToolSurfaceProvider(BaseProvider):
    """V4 tool-surface preference row + ADR-0103 active-tools marker.

    The marker is the one legitimate OOB control plane; it travels as a
    control-plane item (never fenced/trimmed) exactly like legacy.
    """

    provider_id = "tool_surface"
    domain = ContextDomain.TOOL_SURFACE

    def enabled(self, req, facts):
        return bool(req.session_id) and facts.plan is not None and getattr(facts.plan, "session_id", "") == req.session_id

    async def build(self, req, facts):
        from app.services.chat.pi_turn_context import (
            _active_tools_block_for,
            _compile_surface,
            _render_surface_block,
        )

        items: List[ContextItem] = []
        surface = _compile_surface(facts.plan)
        if surface is None:
            return items
        surface_text = _render_surface_block(surface)
        if surface_text:
            items.append(bounded_item(
                item_id="surface:row", provider_id=self.provider_id,
                domain=ContextDomain.TOOL_SURFACE, content=surface_text,
                scope="session", scope_id=str(getattr(facts.plan, "session_id", "") or ""),
            ))
        marker = await _active_tools_block_for(
            req.message, surface, facts.plan,
            session_id=req.session_id, turn_id=req.turn_id or "",
        )
        if marker:
            items.append(bounded_item(
                item_id="surface:active_tools", provider_id=self.provider_id,
                domain=ContextDomain.ACTIVE_TOOLS, content=marker,
                scope="session", scope_id=str(getattr(facts.plan, "session_id", "") or ""),
                control_plane=True,
            ))
        return items


class EnvironmentProvider(BaseProvider):
    """Caller-injected situation/env projection (side-effectful upstream)."""

    provider_id = "environment"
    domain = ContextDomain.ENVIRONMENT

    def enabled(self, req, facts):
        return bool(req.environment_block)

    async def build(self, req, facts):
        from app.services.context_assembly.contract import content_fingerprint

        # provider_id="caller": this item is caller-injected (the route layer
        # owns the situation advance); the scope-gate caller exemption keys
        # on this identity.
        return [bounded_item(
            item_id="env:projection", provider_id="caller",
            domain=self.domain, content=req.environment_block,
            scope="turn", scope_id=req.session_id,
            freshness=content_fingerprint(req.environment_block),
            evidence_ref="caller:situation_advance",
        )]


class V6BlocksProvider(BaseProvider):
    """V6 Wave 13 three-layer projection (same builder as legacy bind)."""

    provider_id = "v6_blocks"
    domain = ContextDomain.V6_BLOCKS

    def enabled(self, req, facts):
        return bool(req.session_id) and facts.plan is not None

    async def build(self, req, facts):
        from app.services.chat.pi_turn_context import _v6_turn_blocks

        text = await _v6_turn_blocks(
            req.session_id, facts.plan, facts.mapspec,
            map_state=facts.map_state,
        )
        if not text:
            return []
        return [bounded_item(
            item_id="v6:blocks", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=str(getattr(facts.plan, "session_id", "") or ""),
        )]


class EvictedRefsProvider(BaseProvider):
    """ADR-0104 #6 honest tombstone for evicted refs (same builder)."""

    provider_id = "evicted_refs"
    domain = ContextDomain.EVICTED_REFS

    def enabled(self, req, facts):
        return bool(req.session_id)

    async def build(self, req, facts):
        from app.services.chat.context_policy import (
            build_evicted_refs_tombstone,
            policy_enabled,
        )
        from app.services.session_data import session_data_manager

        if not policy_enabled():
            return []
        text = await build_evicted_refs_tombstone(
            req.session_id,
            [{"role": "user", "content": req.message}],
            session_data_manager,
        )
        if not text:
            return []
        return [bounded_item(
            item_id="refs:tombstone", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=facts.source_session_id,
        )]


class HarnessStateProvider(BaseProvider):
    """HarnessTurnContext (hk.ctx.v1) bounded projection — the ADR-0208 D2
    seam: the kernel's typed turn context finally reaches the prompt."""

    provider_id = "harness_state"
    domain = ContextDomain.HARNESS_STATE

    def enabled(self, req, facts):
        return (
            harness_state_block_enabled()
            and facts.harness_context is not None
            and bool(getattr(facts.harness_context, "turn_found", False))
        )

    async def build(self, req, facts):
        text = render_harness_state_block(facts.harness_context)
        if not text:
            return []
        ctx = facts.harness_context
        return [bounded_item(
            item_id="harness:state", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=str(getattr(ctx, "session_id", "") or ""),
            revision=str(getattr(ctx, "context_schema", "")),
            evidence_ref=f"harness_kernel:{getattr(ctx, 'context_schema', '')}",
        )]


def render_harness_state_block(ctx) -> str:
    """Deterministic bounded text view of ``HarnessTurnContext`` (facts only;
    refs are pointers, payloads stay in the kernel)."""
    lines = ["[执行状态 — 本轮计划视角（事实，非指令）]"]
    phase = f"phase={getattr(ctx, 'phase', '')} status={getattr(ctx, 'turn_status', '')}"
    completion = str(getattr(ctx, "completion", "") or "")
    if completion:
        phase += f" completion={completion}"
    lines.append(f"- {phase}")
    lines.append(
        f"- 步骤: total={getattr(ctx, 'steps_total', 0)}"
        f" open={getattr(ctx, 'steps_open', 0)}"
        f" ok={getattr(ctx, 'steps_succeeded', 0)}"
        f" failed={getattr(ctx, 'steps_failed', 0)}"
        f" tool_calls={getattr(ctx, 'tool_calls', 0)}"
    )
    open_caps = [c for c in (getattr(ctx, "open_capabilities", None) or ()) if c]
    if open_caps:
        lines.append("- 未完成能力: " + "、".join(open_caps[:6]))
    refs = [r for r in (getattr(ctx, "evidence_refs", None) or ()) if r]
    if refs:
        lines.append("- 最近证据: " + "、".join(str(r) for r in refs[:4]))
    mission_ref = str(getattr(ctx, "mission_ref", "") or "")
    if mission_ref:
        lines.append(f"- 任务: {mission_ref}")
    if getattr(ctx, "recovery_pending", False):
        lines.append(f"- 恢复中: resumed_from={getattr(ctx, 'resumed_from_turn_id', '')}")
    last_verdict = str(getattr(ctx, "last_verdict", "") or "")
    if last_verdict:
        lines.append(f"- 最近裁决: {last_verdict}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wave B — the gis_context card (yields to the wave-A char budget; skips its
# reuse section when the knowledge card is already present — M5 no-dup)
# ---------------------------------------------------------------------------


class GisContextCardProvider(BaseProvider):
    """ADR-0206 mission working-context card (default-ON, killable,
    invalidation fail-closed inside the builder)."""

    provider_id = "gis_context_card"
    domain = ContextDomain.GIS_CONTEXT_CARD

    def enabled(self, req, facts):
        return bool(req.session_id)

    async def build(self, req, facts, *, prior_chars: int = 0,
                    knowledge_present: bool = False):
        from app.services.gis_context.hotpath import assemble_gis_context_card

        text, _rc = await assemble_gis_context_card(
            req.session_id,
            org_id=req.org_id or "",
            project_id=req.project_id or "",
            user_id=req.user_id or "",
            turn_id=req.turn_id or "",
            query_text=req.query_text or "",
            state=facts.map_state if isinstance(facts.map_state, dict) else None,
            mapspec=facts.mapspec if isinstance(facts.mapspec, dict) else None,
            include_reuse=not knowledge_present,
            budget_used=prior_chars,
        )
        if not text:
            return []
        source_scope = getattr(text, "source_scope", None)
        if not source_scope:
            raise SourceScopeUnavailable("derived context is missing its source scope")
        return [bounded_item(
            item_id="gisctx:card", provider_id=self.provider_id,
            domain=self.domain, content=text,
            **source_scope,
            evidence_ref="gis_working_context:card",
        )]


class RepresentationBindingProvider(BaseProvider):
    """ADR-0219 用户制图约束摘要（C6）：requirement↔mapspec 语义绑定的
    有界上下文块——用户 pin 的 palette/表达、隐藏/锁定图层、显式禁用
    组件、最近语义编辑 revision、漂移信号。

    只读 ``facts.map_state`` 中的 requirement 文档（orchestrator 每轮
    一次加载的 N+1 纪律），零额外 IO；文档缺席/superseded/kill-switch
    关闭 → 空投影（空会话 byte-equivalence 不受影响）。
    """

    provider_id = "representation_binding"
    domain = ContextDomain.REPRESENTATION_BINDING

    def enabled(self, req: TurnContextRequest, facts: SharedTurnFacts) -> bool:
        from app.services.gis_harness.representation_binding.flags import (
            # kill-switch GIS_REQUIREMENT_CONTEXT_BLOCK=0 可整体关闭需求上下文块注入
            requirement_context_block_enabled,
        )

        return requirement_context_block_enabled() and bool(req.session_id)

    async def build(
        self, req: TurnContextRequest, facts: SharedTurnFacts
    ) -> List[ContextItem]:
        from app.services.gis_harness.requirement_ir import service as req_service
        from app.services.gis_harness.representation_binding.agent_context import (
            render_summary_block,
            representation_binding_summary,
        )

        state = facts.map_state if isinstance(facts.map_state, dict) else {}
        payload = state.get(req_service.REQUIREMENT_STATE_KEY)
        if not isinstance(payload, dict):
            return []
        document = payload.get("document")
        if not isinstance(document, dict):
            return []
        try:
            from app.services.gis_harness.requirement_ir.contracts import (
                RequirementDocument,
            )

            doc = RequirementDocument.model_validate(document)
        except Exception:  # noqa: BLE001 — schema skew → 空投影
            return []
        text = render_summary_block(
            representation_binding_summary(doc, facts.mapspec))
        if not text:
            return []
        return [bounded_item(
            item_id="rb:summary", provider_id=self.provider_id,
            domain=self.domain, content=text,
            revision=str(doc.revision),
            freshness=str(payload.get("folded_patch_count", 0)),
            evidence_ref="requirement_ir_document",
            scope_id=facts.source_session_id,
        )]


#: wave A order is irrelevant to output (render order = domain rank); keep a
#: canonical tuple for deterministic parallel fan-out. The user-message item
#: is assembled by the orchestrator itself (identity, not context).
class GisStateRebuildProvider(BaseProvider):
    """权威 GIS 状态重建块（ADR-0288 / W09；G22-P2 恢复目录扩展）。

    compaction / worker 重启 / 会话恢复后，模型对「地图现在是什么」的认知
    只能来自**权威源现取**（MapSpec store + SessionPlan 信封），绝不来自
    压缩 summary（summary 不获得修改权）。块内只有事实与指针：图层
    （含可见性/隐藏状态）、场景 revision、待完成能力、最近证据 refs；
    引用失效/被删除 → 明确「缺失」披露，不虚构旧图层。

    G22-P2：用户约束（workbench 锁定、隐藏层、G16 标注覆写、G15 多画幅
    active frame、agent undo 栈）全层投影——不受通用目录扫描界限制，预算
    挤压时通用段先让位，用户约束不因字典顺序消失；durable job 与失效数据
    ref 经 SharedTurnFacts 补充（缺席 = 不投影）。selection 无持久权威源
    ——本投影不发明（不把草稿/选区当已提交事实）。有界：域级 1600 字符
    cap（contract 表）+ 段内预算 1540。
    """

    provider_id = "gis_state_rebuild"
    domain = ContextDomain.GIS_STATE_REBUILD

    def enabled(self, req, facts):
        return bool(req.session_id) and facts.mapspec is not None

    async def build(self, req, facts):
        text = render_gis_state_rebuild_block(
            facts.mapspec, facts.plan,
            durable_jobs=getattr(facts, "durable_jobs", None),
            invalid_source_refs=getattr(facts, "invalid_source_refs", None),
        )
        if not text:
            return []
        revision = str(
            (facts.mapspec or {}).get("revision")
            or (facts.mapspec or {}).get("meta", {}).get("revision")
            or ""
        )
        return [bounded_item(
            item_id="gis:state-rebuild", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=str(facts.source_session_id or req.session_id),
            revision=revision,
            evidence_ref="mapspec_store:current",
        )]


def _layer_is_hidden(layer: dict) -> bool:
    """Canonical MapSpec visibility lives at ``layer.layout.visibility``
    (``"visible" | "none"``); fall back to legacy top-level flags. Unknown
    shape → visible (never invent a hidden state the source doesn't assert)."""
    layout = layer.get("layout")
    if isinstance(layout, dict):
        vis = layout.get("visibility")
        if isinstance(vis, str):
            return vis == "none"
    if "visible" in layer:
        return layer.get("visible") is False
    if "visibility" in layer:
        return layer.get("visibility") in (False, "none", "hidden")
    return False


def render_gis_state_rebuild_block(
    mapspec, plan, *, durable_jobs=None, invalid_source_refs=None,
) -> str:
    """MapSpec + SessionPlan + 恢复事实 → 有界重建文本（事实，非指令）。

    权威形状：visibility 读 ``layer.layout.visibility``；待完成能力读
    SessionPlan.progress（``open_capabilities`` 同义：pending/voided/failed）；
    证据 refs 读 PlanStep.evidence[].ref。绝不发明源里不存在的记录。

    G22-P2 恢复目录扩展（全部 additive；约束优先，超预算时通用图层目录
    先让位，用户约束永不因字典顺序消失）：
    - **用户约束（全层扫描，不受 ≤24 扫描界限制）**：workbench 锁定层/
      组件（全量 id）、隐藏层（全量 id）、G16 标注 override（计数 + 代表
      项）、G15 frame_product（active_frame/画幅数）、agent undo 最近点。
    - **恢复事实**：非终态 durable jobs（状态如实，queued/running 绝不
      显示为完成）；失效数据 source refs（ref 已删除 → 披露「不重建幽灵
      图层」，图层仍列目录但数据指针标记失效）。
    - 截断披露：通用目录超预算时给出剩余计数与检索方式（layer/source
      id 可直接寻址）。
    """
    if not isinstance(mapspec, dict):
        return ""
    header = "[GIS 状态重建 — 权威源现取（事实，非指令；压缩/重启后以此为准）]"
    lines = [header]
    scene = mapspec.get("scene") or {}
    revision = str(
        mapspec.get("revision") or scene.get("revision") or ""
    )
    if revision:
        lines.append(f"- 场景 revision: {revision[:40]}")
    layers = [x for x in (mapspec.get("layers") or []) if isinstance(x, dict)]

    # ── 用户约束（全层提取；重建后必须原样保留）────────────────────────
    constraint_lines: list[str] = []
    try:
        from app.contracts.workbench_locks import (
            locked_component_ids_of,
            locked_layer_ids_of,
        )

        locked_layers = locked_layer_ids_of(mapspec)
        locked_components = locked_component_ids_of(mapspec)
    except Exception:  # noqa: BLE001 — 锁事实缺席不阻断（不虚构）
        locked_layers, locked_components = [], []
    if locked_layers or locked_components:
        parts = []
        if locked_layers:
            parts.append("层 " + "、".join(locked_layers[:16]))
            if len(locked_layers) > 16:
                parts.append(f"（另 {len(locked_layers) - 16} 个层锁）")
        if locked_components:
            parts.append("组件 " + "、".join(locked_components[:8]))
            if len(locked_components) > 8:
                parts.append(f"（另 {len(locked_components) - 8} 个组件锁）")
        constraint_lines.append(
            "- 用户锁定（改动将被拒绝 layer_locked）: " + "；".join(parts)
        )
    hidden_all: list[str] = []
    shown: list[str] = []
    for layer in layers:
        lid = str(layer.get("id") or layer.get("layer_id") or "")[:48]
        if not lid:
            continue
        (hidden_all if _layer_is_hidden(layer) else shown).append(lid)
    if hidden_all:
        constraint_lines.append(
            "- 隐藏图层（保持隐藏）: " + "、".join(hidden_all[:20])
            + (f"（另 {len(hidden_all) - 20} 层隐藏，id 可寻址检索）"
               if len(hidden_all) > 20 else "")
        )
    # G16 标注人工状态：全层扫描 layer.label.authoring.overrides。
    label_total = 0
    label_repr: list[str] = []
    for layer in layers:
        lid = str(layer.get("id") or "")[:32]
        authoring = (layer.get("label") or {}).get("authoring") if isinstance(layer.get("label"), dict) else None
        overrides = authoring.get("overrides") if isinstance(authoring, dict) else None
        if not isinstance(overrides, dict) or not overrides:
            continue
        label_total += len(overrides)
        if len(label_repr) < 6:
            label_repr.append(f"{lid}({','.join(sorted(overrides)[:3])})")
    if label_total:
        constraint_lines.append(
            f"- 标注人工覆写 {label_total} 条（保持）: " + "、".join(label_repr)
        )
    # G15 frame_product：active frame / 画幅契约（committed spec 内现读）。
    fp = mapspec.get("frame_product")
    if isinstance(fp, dict) and fp:
        fp_bits = [f"product={fp.get('product_kind') or '?'}"]
        if fp.get("enabled") is not None:
            fp_bits.append(f"enabled={fp.get('enabled')}")
        if fp.get("active_frame"):
            fp_bits.append(f"active_frame={str(fp.get('active_frame'))[:32]}")
        fp_frames = [x for x in (fp.get("frames") or []) if isinstance(x, dict)]
        if fp_frames:
            fp_bits.append(
                "frames=" + "、".join(
                    f"{str(f.get('frame_id'))[:24]}:{str(f.get('title'))[:16]}"
                    for f in fp_frames[:6]
                ) + (f"（另 {len(fp_frames) - 6} 幅）" if len(fp_frames) > 6 else "")
            )
        constraint_lines.append("- 多画幅产品（保持绑定）: " + "；".join(fp_bits))
    # agent undo 最近点（#2035 撤销栈权威）。
    try:
        from app.services.mapspec.agent_undo import undo_points_of

        undo_points = undo_points_of(mapspec)
    except Exception:  # noqa: BLE001
        undo_points = []
    if undo_points:
        latest = max(p.get("revision") for p in undo_points)
        constraint_lines.append(
            f"- agent 撤销栈: {len(undo_points)} 点，最近 rev {latest}"
        )

    # ── 恢复事实（durable jobs / 失效 ref）──────────────────────────────
    recovery_lines: list[str] = []
    if isinstance(durable_jobs, list) and durable_jobs:
        job_bits = [
            f"{str(j.get('id'))[:14]}={str(j.get('status'))[:10]}"
            for j in durable_jobs if isinstance(j, dict)
        ][:6]
        extra = len(durable_jobs) - len(job_bits)
        recovery_lines.append(
            "- 非终态任务（状态如实，未完成不视为完成）: " + "、".join(job_bits)
            + (f"（另 {extra} 个）" if extra > 0 else "")
        )
    if isinstance(invalid_source_refs, list) and invalid_source_refs:
        ref_bits = [
            f"{str(r.get('source'))[:24]}→{str(r.get('ref'))[:24]}"
            for r in invalid_source_refs if isinstance(r, dict)
        ][:6]
        recovery_lines.append(
            "- 失效数据引用（ref 已删除，不重建幽灵图层）: " + "、".join(ref_bits)
        )

    # ── 通用目录（预算让位段）────────────────────────────────────────────
    generic_lines: list[str] = []
    if layers:
        generic_lines.append(f"- 图层总数: {len(layers)}")
        if shown:
            generic_lines.append("- 可见图层: " + "、".join(shown[:12]))
            if len(shown) > 12:
                generic_lines.append(f"- （可见图层另 {len(shown) - 12} 层未逐一列出）")
        if len(layers) > 24:
            generic_lines.append(f"- （图层列表截断：共 {len(layers)}，仅列前 12 可见；隐藏/锁定/标注已全量列于上）")
    # 待完成能力：SessionPlan.progress（pending/voided/failed = open）。
    open_caps: list[str] = []
    if plan is not None:
        try:
            from app.services.session_plan import open_capabilities

            open_caps = list(open_capabilities(plan))[:8]
        except Exception:  # noqa: BLE001 — 事实缺席不阻断
            open_caps = []
    if open_caps:
        generic_lines.append("- 待完成能力: " + "、".join(c for c in open_caps if c)[:200])
    # 最近证据 refs：PlanStep.evidence[].ref（指针，非载荷）。
    refs: list[str] = []
    if plan is not None:
        try:
            steps = list(getattr(plan, "steps", []) or [])
            for step in reversed(steps):
                for ev in reversed(list(getattr(step, "evidence", []) or [])):
                    ref = str(getattr(ev, "ref", "") or "")
                    if ref and ref not in refs:
                        refs.append(ref)
                    if len(refs) >= 4:
                        break
                if len(refs) >= 4:
                    break
        except Exception:  # noqa: BLE001 — 事实缺席不阻断
            refs = []
    if refs:
        generic_lines.append("- 最近证据 refs: " + "、".join(refs[:4]))

    # ── 组装：约束 → 恢复事实 → 通用目录；通用段逐行让位直到 ≤ 预算 ────
    #: 域 cap 1600（contract 表）；留 60 字符给截断披露行。
    budget = 1540
    kept_generic: list[str] = []
    dropped_generic = 0
    for line in generic_lines:
        candidate = len("\n".join(lines + constraint_lines + recovery_lines
                                 + kept_generic + [line]))
        if candidate > budget:
            dropped_generic = len(generic_lines) - len(kept_generic)
            break
        kept_generic.append(line)
    lines.extend(constraint_lines)
    lines.extend(recovery_lines)
    lines.extend(kept_generic)
    if dropped_generic > 0:
        lines.append(
            f"- （目录截断：{dropped_generic} 行通用信息未列；layer/source id 可直接寻址检索）"
        )
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


WAVE_A_PROVIDERS = (
    CartographyVerdictProvider,
    CartographyMemoryProvider,
    ProjectKnowledgeProvider,
    GisMemoryProvider,
    SessionPlanProvider,
    ToolSurfaceProvider,
    EnvironmentProvider,
    V6BlocksProvider,
    EvictedRefsProvider,
    HarnessStateProvider,
    RepresentationBindingProvider,
    GisStateRebuildProvider,
)


def build_default_providers() -> List[BaseProvider]:
    return [cls() for cls in WAVE_A_PROVIDERS]


async def collect_cartography_wave(
    req: TurnContextRequest,
    facts: SharedTurnFacts,
) -> List[Tuple[ContextDomain, str]]:
    """The five cartography blocks in legacy join order (verdict → memory →
    knowledge → gis_memory → gis_context). Shared by the typed assembly and
    the legacy byte-equivalent compat path."""
    from app.services.context_assembly.flags import provider_latency_budget_s

    budget_s = provider_latency_budget_s()
    wave_a = [
        CartographyVerdictProvider(),
        CartographyMemoryProvider(),
        ProjectKnowledgeProvider(),
        GisMemoryProvider(),
    ]
    reports = await asyncio.gather(
        *[asyncio.wait_for(p.collect(req, facts), timeout=budget_s) for p in wave_a],
        return_exceptions=True,
    )
    blocks: List[Tuple[ContextDomain, str]] = []
    items_by_provider: dict = {}
    for provider, report in zip(wave_a, reports):
        if isinstance(report, Exception):  # noqa: BLE001 — timeout/cancel → skip
            blocks.append((provider.domain, ""))
            continue
        items_by_provider[provider.provider_id] = report
        item = report.items[0] if report.items else None
        if item is not None and item.domain is ContextDomain.GIS_MEMORY:
            from app.services.context_assembly.fence import apply_domain_fence, scrub_item
            item = scrub_item(apply_domain_fence(item))
        blocks.append((provider.domain, item.content if item is not None else ""))
    knowledge_present = any(
        report.items for report in items_by_provider.values()
        if getattr(report, "provider_id", "") == "project_knowledge"
    )
    prior_chars = sum(len(text) for _, text in blocks)
    try:
        card_items = await asyncio.wait_for(
            GisContextCardProvider().build(
                req, facts,
                prior_chars=prior_chars,
                knowledge_present=knowledge_present,
            ),
            timeout=budget_s,
        )
        blocks.append((
            ContextDomain.GIS_CONTEXT_CARD,
            card_items[0].content if card_items else "",
        ))
    except Exception:  # noqa: BLE001 — card 缺席按空投影
        blocks.append((ContextDomain.GIS_CONTEXT_CARD, ""))
    return blocks


__all__ = [
    "BaseProvider",
    "CartographyMemoryProvider",
    "CartographyVerdictProvider",
    "EnvironmentProvider",
    "EvictedRefsProvider",
    "GisContextCardProvider",
    "GisMemoryProvider",
    "HarnessStateProvider",
    "ProjectKnowledgeProvider",
    "RepresentationBindingProvider",
    "SessionPlanProvider",
    "ToolSurfaceProvider",
    "V6BlocksProvider",
    "WAVE_A_PROVIDERS",
    "build_default_providers",
    "collect_cartography_wave",
    "render_harness_state_block",
]
