"""
Chat Context Assembler — Deep module for prompt context composition.

Encapsulates map state ambient summaries, history token budget management,
XML security fencing, and execution plan blocks behind a unified assembly seam.
"""
from dataclasses import dataclass
import logging
from typing import List, Optional

from app.services.session_data import session_data_manager
from app.services.session_data_protocol import SessionStoreProtocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextAssemblyResult:
    """Structured result of prompt context assembly with observability metrics."""

    messages: List[dict]
    estimated_tokens: int
    history_turns_included: int
    layer_count: int

    def to_messages(self) -> List[dict]:
        """Return the raw OpenAI-compatible message dict list."""
        return self.messages


class ChatContextAssembler:
    """Deep context assembly engine for chat engine prompt composition."""

    def __init__(self, store: Optional[SessionStoreProtocol] = None) -> None:
        self.store = store or session_data_manager

    async def assemble(
        self,
        session_id: str,
        messages: List[dict],
    ) -> ContextAssemblyResult:
        """
        Assemble the complete LLM request message list from session state,
        ambient environment summary, execution plan, and history token budget.
        """
        if not messages:
            return ContextAssemblyResult(
                messages=[], estimated_tokens=0, history_turns_included=0, layer_count=0
            )

        from app.services.chat.context_builder import (
            build_last_analysis_context,
            build_map_state_summary,
            build_plan_block,
            truncate_history_by_budget,
            _build_truncation_notice,
        )
        from app.services.chat.context.session_overview import build_session_overview
        from app.services.chat.context.history_compression import _estimate_tokens

        # Extract session metadata & map state
        store = self.store
        if hasattr(store, "get_session_metadata"):
            metadata = await store.get_session_metadata(session_id)
            map_state = metadata.get("map_state") or {}
            list_refs = metadata.get("list_refs") or {}
            event_log = metadata.get("event_log") or []
            started_at = metadata.get("started_at")

            project_id = metadata.get("project_id")
            project_block = ""
            if project_id:
                try:
                    from app.core.database import SessionLocal
                    from app.services.project_service import ProjectService
                    with SessionLocal() as db:
                        proj = ProjectService.get_project_with_auth(db, project_id)
                        if proj:
                            datasets = ProjectService.list_project_datasets(db, project_id)
                            wfs = ProjectService.list_project_workflows(db, project_id)
                            project_block = (
                                f"\n<active_project_workspace>\n"
                                f"Project: {proj.name} (ID: {proj.id})\n"
                                f"Datasets attached ({len(datasets)}): {', '.join([d.name for d in datasets[:5]])}\n"
                                f"Workflows ({len(wfs)}): {', '.join([w.name for w in wfs[:5]])}\n"
                                f"</active_project_workspace>"
                            )
                except Exception as ex:
                    logger.warning(f"Failed to assemble project context block: {ex}")

            env_summary = (env_summary or "") + project_block
            overview = await build_session_overview(
                session_id,
                messages,
                started_at=started_at,
                event_log=event_log,
                inventory=list_refs,
                _fetched=True,
            )
        else:
            env_summary = await build_map_state_summary(session_id)
            overview = await build_session_overview(session_id, messages)
            map_state = {}

        if overview:
            env_summary += f"\n- 会话概览: {overview}"

        sys_msg = dict(messages[0])
        sys_msg["content"] = sys_msg.get("content", "") + "\n\n" + env_summary

        head = [sys_msg]

        from app.services.chat.planner import get_plan

        plan = get_plan(session_id)
        if plan is not None:
            head.append({"role": "system", "content": build_plan_block(plan)})

        last_ctx = build_last_analysis_context(messages)
        if last_ctx:
            head.append({"role": "system", "content": last_ctx})

        history, dropped = truncate_history_by_budget(messages[1:])
        if dropped > 0:
            head.append({"role": "system", "content": _build_truncation_notice(dropped)})
            logger.info(f"[HISTORY-TRUNC] session={session_id} dropped {dropped} turns")
        head.extend(history)

        layers = map_state.get("layers", {}) if isinstance(map_state, dict) else {}
        layer_count = len(layers) if isinstance(layers, dict) else 0

        # Calculate estimated total tokens across assembled messages
        total_tokens = sum(
            _estimate_tokens(str(m.get("content", ""))) for m in head
        )

        return ContextAssemblyResult(
            messages=head,
            estimated_tokens=total_tokens,
            history_turns_included=len(history),
            layer_count=layer_count,
        )


__all__ = ["ChatContextAssembler", "ContextAssemblyResult"]
