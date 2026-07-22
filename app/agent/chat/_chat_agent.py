"""ChatAgent — bridge between the new Agent system and existing ChatEngine infrastructure.

Design (aligned with Pi earendil-works/pi):
- ChatAgent(Agent) subclasses Agent, injecting GIS-specific behavior:
  - _build_system_prompt: reuse ChatEngine's SYSTEM_PROMPT + skills
  - _select_tools: ToolCatalog + ToolRegistry双层筛选
  - _dispatch_tool: delegate to ChatEngine's dispatcher (preserves dedup, WS broadcast, GeoJSON ref, planner)
  - _build_chat_stream_fn: wraps LLM stream with token-forwarding to SSE queue
- Maps AgentLoop events → ChatEngine SSE events for frontend compatibility
- Reuses TaskTracker, planner, decision_log, session_data_manager
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional

from app.agent._agent import Agent
from app.agent._event import EventBus, create_emit_fn
from app.agent._types import (
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentState,
    AfterToolCallContext,
    BeforeToolCallContext,
    ModelInfo,
    StreamFn,
    StreamOptions,
)
from app.agent._stream import StreamDoneEvent, TextDeltaEvent, ToolCallDeltaEvent
from app.services.chat.prompt import SYSTEM_PROMPT
from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision
from app.services.task_tracker import TaskTracker, detect_geojson
from app.services.session_data import session_data_manager
from app.services.tool_catalog import ToolCatalog
from app.tools.skills import list_md_skills
from app.tools.registry import ToolRegistry
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# AgentLoop event → SSE event mapper
# ═══════════════════════════════════════════════════════════════

def _map_event_to_sse(event: AgentEvent, session_id: str, task_id: str) -> Optional[str]:
    """Map AgentLoop event to ChatEngine SSE string, or None to skip."""
    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")

    if event_type == "message_update":
        msg = event.get("message", {})
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            return sse_event("tool_call", {
                "name": tc.get("function", {}).get("name", ""),
                "arguments": tc.get("function", {}).get("arguments", ""),
            })
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")
        if content:
            return sse_event("token", {
                "content": content,
                "is_reasoning": False,
                "session_id": session_id,
            })
        if reasoning:
            return sse_event("token", {
                "content": reasoning,
                "is_reasoning": True,
                "session_id": session_id,
            })
        return None

    if event_type == "tool_execution_start":
        return sse_event("step_start", {
            "task_id": task_id,
            "step_id": event.get("toolCallId", ""),
            "step_index": 0,
            "tool": event.get("toolName", ""),
            "session_id": session_id,
        })

    if event_type == "tool_execution_end":
        result = event.get("result", {})
        is_error = event.get("isError", False)
        if is_error:
            return sse_event("step_error", {
                "task_id": task_id,
                "step_id": event.get("toolCallId", ""),
                "tool": event.get("toolName", ""),
                "error": result.get("content", [{}])[0].get("text", "unknown error") if result.get("content") else "unknown error",
            })
        slim = _slim_for_sse(result)
        return sse_event("step_result", {
            "task_id": task_id,
            "step_id": event.get("toolCallId", ""),
            "tool": event.get("toolName", ""),
            "result": slim,
            "session_id": session_id,
        })

    if event_type == "agent_end":
        return sse_event("task_complete", {
            "task_id": task_id,
            "step_count": 0,
            "summary": "",
        })

    return None


def _slim_for_sse(result: dict) -> Any:
    """Slim tool result for SSE transport (reuse sse_helpers logic)."""
    try:
        from app.services.chat.sse_helpers import slim_event_result
        return slim_event_result(result)
    except Exception:
        return result


# ═══════════════════════════════════════════════════════════════
# ChatAgent
# ═══════════════════════════════════════════════════════════════

class ChatAgent(Agent):
    """ChatAgent bridges the new Agent system to existing ChatEngine infrastructure.

    Inherits: Agent (stateful lifecycle, EventBus, hooks)
    Injects: GIS-specific tool dispatch, system prompt, context building
    """

    def __init__(
        self,
        engine: Any,  # ChatEngine — provides registry, catalog, tracker, config
        state: Optional[AgentState] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """Initialize ChatAgent with existing ChatEngine infrastructure.

        Args:
            engine: ChatEngine instance providing registry, catalog, tracker, LLM config
            state: Optional pre-built AgentState (created from session if None)
            event_bus: Optional pre-built EventBus (created if None)
        """
        self._engine = engine
        self._registry: ToolRegistry = engine.registry
        self._catalog: Optional[ToolCatalog] = getattr(engine, 'catalog', None)
        self._tracker: TaskTracker = engine.tracker
        self._session_id: str = ""

        # Build event_bus
        bus = event_bus or EventBus()

        # Build state
        if state is None:
            state = AgentState(
                systemPrompt=self._build_system_prompt(),
                model=ModelInfo(
                    id=engine.model,
                    base_url=engine.base_url,
                ),
            )

        # Call super().__init__ first to set _event_bus and _get_signal,
        # which _build_chat_stream_fn needs at the end of init.
        super().__init__(
            state=state,
            event_bus=bus,
            stream_function=lambda *a, **k: None,  # placeholder, replaced below
        )

        # Now build the real stream_fn (needs self._event_bus and self._get_signal)
        self._stream_function = self._build_chat_stream_fn()

    # ── Override Agent lifecycle hooks ─────────────────────────

    async def _build_system_prompt(self) -> str:
        """Build system prompt with dynamically injected skill list (same as ChatEngine)."""
        skills = list_md_skills()
        if skills:
            lines = [f"- **{s['name']}**: {s['description']}" for s in skills]
            skill_text = "\n".join(lines)
        else:
            skill_text = "（暂无预置技能）"
        return SYSTEM_PROMPT.format(skill_list=skill_text)

    async def _select_tools(self, context: AgentContext) -> list[dict]:
        """Select tools via ToolCatalog (keyword matching) + ToolRegistry.

        Mirrors ChatEngine._select_tools exactly for compatibility.
        """
        messages = context.messages
        if self._catalog is not None:
            user_text = ""
            for m in reversed(messages):
                content = m.get("content") if isinstance(m, dict) else ""
                if isinstance(content, str):
                    user_text = content
                    break
                elif isinstance(content, list):
                    user_text = " ".join(
                        seg.get("text", "") for seg in content if isinstance(seg, dict)
                    )
                    break
            try:
                schemas = self._catalog.select_schemas(user_text, session_id=self._session_id)
                if schemas:
                    # Inject execute function into each tool schema for AgentLoop dispatch
                    return [self._inject_execute(s) for s in schemas]
            except Exception:
                pass
        schemas = self._registry.get_schemas()
        return [self._inject_execute(s) for s in schemas]

    def _inject_execute(self, tool_schema: dict) -> dict:
        """Inject an 'execute' key into tool schema so AgentLoop can dispatch.

        AgentLoop's _execute_prepared_tool_call does:
            execute_fn = tool.get("execute")
            if execute_fn: result_content = await execute_fn(**args)

        We inject a wrapper that calls ChatAgent._dispatch_tool.
        """
        if "execute" not in tool_schema:
            tool_schema = dict(tool_schema)  # shallow copy to avoid mutating registry cache
            tool_name = tool_schema.get("function", {}).get("name", "")
            tool_schema["execute"] = self._make_execute_wrapper(tool_name)
        return tool_schema

    def _make_execute_wrapper(self, tool_name: str) -> Callable:
        """Create an execute wrapper for a specific tool."""
        async def _execute(**kwargs: Any) -> Any:
            return await self._dispatch_tool(tool_name, kwargs)
        return _execute

    async def _dispatch_tool(
        self,
        tool_name: str,
        args: dict,
    ) -> dict:
        """Dispatch tool via ChatEngine's dispatcher, preserving all GIS logic.

        This is called by AgentLoop when it encounters a tool call.
        We delegate to ChatEngine._dispatch_tool which preserves:
        - Dedup (executed_tools set)
        - GeoJSON ref storage
        - WS broadcast
        - Event logging
        - Self-healing hints

        Returns dict compatible with AgentLoop's ExecutedToolCallOutcome:
            {"content": [...], "details": ..., "terminate": bool}
        """
        # Build tool_call dict in ChatEngine format
        tc = {
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args,
            }
        }

        # Parse arguments for tracker
        tool_args_raw = tc["function"]["arguments"]
        try:
            tool_args_dict = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
        except (json.JSONDecodeError, TypeError):
            tool_args_dict = {}

        # Create task step
        step = None
        try:
            step = self._tracker.start_step(self._task_id, tool_name, tool_args_dict if isinstance(tool_args_dict, dict) else {})
        except Exception:
            pass

        # Dispatch via ChatEngine
        outcome = await self._engine._dispatch_tool(tc, self._session_id, self._executed_tools)

        # Complete step
        if step:
            try:
                self._tracker.complete_step(self._task_id, step.id, outcome.get("result"))
            except Exception:
                pass

        # Log decision
        self._log_tool_decision(tool_name, tool_args_dict, outcome)

        # Update planner
        self._mark_planner_step(tool_name)

        # Return AgentLoop-compatible result
        llm_payload = outcome.get("llm_payload", "")
        is_error = outcome.get("is_error", False)

        if is_error:
            return {
                "content": [{"type": "text", "text": llm_payload}],
                "details": outcome.get("result"),
                "terminate": False,
            }

        return {
            "content": [{"type": "text", "text": llm_payload}],
            "details": outcome.get("result"),
            "terminate": False,
        }

    def _log_tool_decision(
        self,
        tool_name: str,
        tool_args: dict,
        outcome: dict,
    ) -> None:
        """Log tool decision for observability (mirrors ChatEngine._log_tool_decision)."""
        try:
            from app.services.chat.dispatcher import is_suspicious_result
            from app.services.chat import planner

            if outcome.get("is_error"):
                quality = "error"
            elif is_suspicious_result(outcome.get("result")):
                quality = "empty"
            else:
                quality = "ok"

            plan = planner.get_plan(self._session_id)
            active = self._catalog.active_domains(self._session_id) if self._catalog else set()

            # Get the user message for context
            user_message = ""
            for m in reversed(self._state.messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
                    user_message = content[:200] if isinstance(content, str) else ""
                    break

            log_tool_decision(ToolDecisionRecord(
                session_id=self._session_id,
                round=0,  # ChatAgent doesn't track round_index the same way
                user_message=user_message,
                active_domains=sorted(active),
                from_plan=plan is not None,
                subset_size=len(self._state.tools),
                total_tools=len(self._registry.get_schemas()),
                tool_chosen=tool_name,
                tool_args=tool_args if isinstance(tool_args, dict) else {},
                result_quality=quality,
                plan_step_matched=None,
            ))
        except Exception as e:
            logger.debug(f"[ChatAgent] decision log failed: {e}")

    def _mark_planner_step(self, tool_name: str) -> None:
        """Mark planner step as done if a plan exists for this session."""
        try:
            from app.services.chat import planner
            planner.mark_step_done(self._session_id, tool_name, self._registry)
        except Exception:
            pass

    # ── SSE streaming ──────────────────────────────────────────

    def _build_chat_stream_fn(self) -> StreamFn:
        """Build StreamFn that forwards tokens to SSE queue in real-time.

        The stream_fn wraps the LLM stream and:
        1. Accumulates content/reasoning as tokens arrive
        2. For each token: emits message_update via emit_fn (bridge picks it up)
        3. On stream end: yields StreamDoneEvent with assembled message

        AgentLoop uses the assembled message for tool call extraction.
        The emit_fn → EventBus → bridge listener → SSE queue path handles
        real-time token delivery to the frontend.
        """
        engine = self._engine

        async def _stream_fn(
            model: ModelInfo,
            context: AgentContext,
            options: Optional[StreamOptions] = None,
        ) -> AsyncGenerator[StreamDoneEvent, None]:
            # Build LLM config
            cfg = None
            try:
                cfg = engine._llm_config()
            except Exception:
                pass

            # Get API key
            api_key = None
            if options and options.api_key:
                api_key = options.api_key

            # Convert messages to LLM format
            llm_messages = _convert_messages(context.messages)
            tools = context.tools or None

            # Build options for LLM stream
            llm_opts = None
            if options or api_key:
                llm_opts = StreamOptions(
                    signal=options.signal if options else None,
                    api_key=api_key,
                )

            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls_accum: dict[int, dict] = {}
            finish_reason: Optional[str] = None
            in_think_block = False

            try:
                async for event_type, event_data in engine._call_llm_stream(llm_messages, tools):
                    # Process reasoning tokens
                    delta_reasoning = (
                        event_data.get("reasoning")
                        or event_data.get("reasoning_content")
                        or event_data.get("thinking_content")
                        or event_data.get("thinking")
                    )
                    if delta_reasoning:
                        reasoning_parts.append(delta_reasoning)
                        partial_msg = {"role": "assistant", "content": "", "reasoning_content": "".join(reasoning_parts)}
                        await emit_fn({
                            "type": "message_update",
                            "message": partial_msg,
                            "assistantMessageEvent": {"type": "thinking_delta", "content": delta_reasoning},
                        })

                    # Process content tokens (with <think> handling)
                    delta_content = event_data.get("content")
                    if delta_content:
                        remaining = delta_content
                        while remaining:
                            if not in_think_block:
                                idx = remaining.find("<think>")
                                if idx == -1:
                                    content_parts.append(remaining)
                                    partial_msg = {"role": "assistant", "content": "".join(content_parts)}
                                    if reasoning_parts:
                                        partial_msg["reasoning_content"] = "".join(reasoning_parts)
                                    await emit_fn({
                                        "type": "message_update",
                                        "message": partial_msg,
                                        "assistantMessageEvent": {"type": "text_delta", "content": remaining},
                                    })
                                    remaining = ""
                                else:
                                    pre = remaining[:idx]
                                    if pre:
                                        content_parts.append(pre)
                                        partial_msg = {"role": "assistant", "content": "".join(content_parts)}
                                        if reasoning_parts:
                                            partial_msg["reasoning_content"] = "".join(reasoning_parts)
                                        await emit_fn({
                                            "type": "message_update",
                                            "message": partial_msg,
                                            "assistantMessageEvent": {"type": "text_delta", "content": pre},
                                        })
                                    in_think_block = True
                                    remaining = remaining[idx + 7:]
                            else:
                                idx = remaining.find("</think>")
                                if idx == -1:
                                    reasoning_parts.append(remaining)
                                    partial_msg = {"role": "assistant", "content": "".join(content_parts), "reasoning_content": "".join(reasoning_parts)}
                                    await emit_fn({
                                        "type": "message_update",
                                        "message": partial_msg,
                                        "assistantMessageEvent": {"type": "thinking_delta", "content": remaining},
                                    })
                                    remaining = ""
                                else:
                                    think_chunk = remaining[:idx]
                                    if think_chunk:
                                        reasoning_parts.append(think_chunk)
                                    in_think_block = False
                                    remaining = remaining[idx + 8:].lstrip()

                    # Process tool call deltas
                    delta_tool_calls = event_data.get("tool_calls")
                    if delta_tool_calls:
                        for tc_delta in delta_tool_calls:
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_calls_accum:
                                tool_calls_accum[idx] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tc_entry = tool_calls_accum[idx]
                            if tc_delta.get("id"):
                                tc_entry["id"] = tc_delta["id"]
                            if tc_delta.get("type"):
                                tc_entry["type"] = tc_delta["type"]
                            fn_delta = tc_delta.get("function", {})
                            if fn_delta.get("name"):
                                tc_entry["function"]["name"] += fn_delta["name"]
                            if fn_delta.get("arguments"):
                                tc_entry["function"]["arguments"] += fn_delta["arguments"]

                            partial_msg = {
                                "role": "assistant",
                                "content": "".join(content_parts),
                                "tool_calls": list(tool_calls_accum.values()),
                            }
                            if reasoning_parts:
                                partial_msg["reasoning_content"] = "".join(reasoning_parts)
                            await emit_fn({
                                "type": "message_update",
                                "message": partial_msg,
                                "assistantMessageEvent": {
                                    "type": "toolcall_delta",
                                    "toolCallId": tc_entry["id"],
                                    "name": tc_entry["function"]["name"],
                                    "arguments": tc_entry["function"]["arguments"],
                                },
                            })

                    finish_reason = event_data.get("finish_reason") or finish_reason

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[ChatAgent] LLM stream error: {e}")
                error_msg: AgentMessage = {
                    "role": "assistant",
                    "content": "",
                    "stop_reason": "error",
                    "error_message": str(e),
                }
                await emit_fn({"type": "message_start", "message": error_msg})
                await emit_fn({"type": "message_end", "message": error_msg})
                yield StreamDoneEvent(message=error_msg)
                return

            # Assemble final message
            assembled_content = "".join(content_parts)
            assembled_reasoning = "".join(reasoning_parts)
            assembled_message: AgentMessage = {"role": "assistant", "content": assembled_content}
            if assembled_reasoning:
                assembled_message["reasoning_content"] = assembled_reasoning
            if tool_calls_accum:
                assembled_tool_calls = []
                for idx in sorted(tool_calls_accum.keys()):
                    tc = tool_calls_accum[idx]
                    assembled_tool_calls.append({
                        "id": tc["id"],
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    })
                assembled_message["tool_calls"] = assembled_tool_calls
            assembled_message["stop_reason"] = finish_reason or "end_turn"

            await emit_fn({"type": "message_end", "message": assembled_message})
            yield StreamDoneEvent(message=assembled_message)

        # Create the emit_fn bridge inside the stream_fn closure
        emit_fn = create_emit_fn(self._event_bus, self._get_signal)
        return _stream_fn

    # ── Core API: SSE streaming ────────────────────────────────

    async def chat_stream(
        self,
        message: str,
        session_id: Optional[str] = None,
        map_state: Optional[dict] = None,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream chat response as SSE events (compatible with ChatEngine.chat_stream).

        Yields SSE-formatted strings that the existing FastAPI route can forward.
        """
        if not session_id:
            session_id = str(uuid.uuid4())
        self._session_id = session_id

        # Sync frontend map state
        if map_state:
            for k, v in map_state.items():
                await session_data_manager.set_map_state(session_id, k, v)
            try:
                from app.services.viewport_naming import schedule_populate_from_map_state
                schedule_populate_from_map_state(map_state)
            except ImportError:
                pass

        # Load or create session
        messages = await self._load_session_from_db(session_id, user_id=user_id)
        self._state.messages = messages
        self._executed_tools: set[tuple[str, str]] = set()

        # Apply skill
        if skill_name:
            self._apply_skill(self._state.messages, skill_name)

        # Add user message
        self._state.messages.append({"role": "user", "content": message})
        await self._save_msg_async(session_id, "user", message)

        # Create task
        task = self._tracker.create(session_id, message)
        self._task_id = task.id
        yield sse_event("task_start", {"task_id": task.id, "session_id": session_id})

        # Maybe plan
        plan = await self._maybe_plan(session_id, message, self._state.messages)
        if plan is not None:
            yield sse_event("plan_ready", {
                "session_id": session_id,
                "task_id": task.id,
                "intent": plan.intent,
                "domains": plan.domains,
                "steps": [
                    {"n": s.n, "goal": s.goal, "tool_family": s.tool_family, "done": False}
                    for s in plan.steps
                ],
            })

        # Set up event bridge
        sse_queue: asyncio.Queue = asyncio.Queue()
        unsub = self._event_bus.subscribe(
            lambda event, signal: sse_queue.put_nowait(_map_event_to_sse(event, session_id, task.id))
        )

        try:
            # Run AgentLoop via Agent.prompt()
            agent_task = asyncio.create_task(self.prompt(message))

            while True:
                if agent_task.done():
                    break
                try:
                    sse_str = sse_queue.get_nowait()
                    if sse_str:
                        yield sse_str
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0)

            # Drain remaining SSE events
            while True:
                try:
                    sse_str = sse_queue.get_nowait()
                    if sse_str:
                        yield sse_str
                except asyncio.QueueEmpty:
                    break

            # Wait for agent to complete
            await agent_task

        except asyncio.CancelledError:
            self.abort()
            if not agent_task.done():
                await agent_task
            raise
        except Exception as e:
            logger.error(f"[ChatAgent] stream error: {e}")
            yield sse_event("error", {"error": "Internal server error"})
        finally:
            unsub()
            # Send final events
            yield sse_event("content", {"content": "", "session_id": session_id, "streaming_done": True})
            try:
                from app.services.chat import planner as _planner
                plan_obj = _planner.get_plan(session_id)
                if plan_obj:
                    skipped = [s.n for s in plan_obj.steps if not s.done]
                    yield sse_event("plan_finalized", {
                        "session_id": session_id,
                        "task_id": task.id,
                        "skipped": skipped,
                    })
            except Exception:
                pass
            yield sse_event("done", {"session_id": session_id})

    # ── Helpers (reuse ChatEngine logic) ───────────────────────

    async def _maybe_plan(self, session_id: str, message: str, messages: list[dict]) -> Optional[Any]:
        """Run planner if needed (mirrors ChatEngine._maybe_plan)."""
        try:
            from app.services.chat import planner
            has_plan = planner.get_plan(session_id) is not None
            if not planner.should_plan(message, messages, has_plan):
                return None
            env = self._build_map_state_summary(session_id)
            cfg = self._engine._planner_llm_config() if hasattr(self._engine, '_planner_llm_config') else self._engine._llm_config()
            return await planner.make_plan(cfg, session_id, message, env)
        except Exception as e:
            logger.warning(f"[ChatAgent] planning failed: {e}")
            return None

    def _build_map_state_summary(self, session_id: str) -> str:
        try:
            from app.services.chat.context_builder import build_map_state_summary
            return build_map_state_summary(session_id)
        except Exception:
            return ""

    def _apply_skill(self, messages: list[dict], skill_name: Optional[str]) -> None:
        """Inject or refresh skill instructions (mirrors ChatEngine._apply_skill)."""
        if not skill_name:
            return
        try:
            from app.tools.skills import get_md_skill
            skill = get_md_skill(skill_name)
            if not skill:
                return
            marker = f"[Skill指令: {skill_name}]"
            messages[:] = [
                m for m in messages
                if not (m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith(marker))
            ]
            messages.append({"role": "system", "content": f"{marker}\n\n{skill['body']}"})
        except Exception:
            pass

    async def _load_session_from_db(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> list[dict]:
        """Load conversation history from DB (mirrors ChatEngine._load_session_from_db)."""
        messages: list[dict] = []
        try:
            from app.tools._utils import async_db_session
            from app.services.history_service_async import AsyncHistoryService
            async with async_db_session() as db:
                conv = await AsyncHistoryService(db).get_or_create_conversation(session_id, user_id=user_id)
                if conv and conv.messages:
                    sorted_msgs = sorted(conv.messages, key=lambda x: x.id)
                    messages = [self._db_msg_to_llm(m) for m in sorted_msgs]
        except Exception as e:
            logger.warning(f"[ChatAgent] History: failed to load conversation {session_id}: {e}")

        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            messages.insert(0, {"role": "system", "content": self._build_system_prompt()})
        return messages

    @staticmethod
    def _db_msg_to_llm(msg: Any) -> dict:
        """Convert DB message to LLM dict (mirrors ChatEngine._db_msg_to_llm)."""
        d = {"role": msg.role, "content": msg.content or ""}
        if msg.reasoning_content:
            d["reasoning_content"] = msg.reasoning_content
        if msg.tool_calls:
            try:
                d["tool_calls"] = msg.tool_calls if isinstance(msg.tool_calls, list) else json.loads(msg.tool_calls)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"[ChatAgent] Failed to parse tool_calls for message {msg.id}")
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        return d

    async def _save_msg_async(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls=None,
        tool_result=None,
        tool_call_id=None,
        reasoning_content=None,
    ) -> None:
        """Save message to DB asynchronously (mirrors ChatEngine._save_msg_async)."""
        try:
            if tool_result is not None and isinstance(tool_result, str) and len(tool_result) > 100000:
                tool_result = tool_result[:100000] + "...[truncated]"
            from app.tools._utils import async_db_session
            from app.services.history_service_async import AsyncHistoryService
            async with async_db_session() as db:
                await AsyncHistoryService(db).save_message(
                    session_id, role, content, tool_calls, tool_result, tool_call_id, reasoning_content
                )
        except Exception as e:
            logger.error(f"[ChatAgent] Failed to save message: {e}")

    async def clear_session(self, session_id: str, user_id: Optional[str] = None) -> bool:
        """Clear session (mirrors ChatEngine.clear_session)."""
        try:
            from app.tools._utils import async_db_session
            from app.services.history_service_async import AsyncHistoryService
            async with async_db_session() as db:
                deleted = await AsyncHistoryService(db).delete_session(session_id, user_id=user_id)
        except Exception as e:
            logger.warning(f"[ChatAgent] History: failed to delete session {session_id}: {e}")
            return False
        if deleted:
            self.reset()
            await session_data_manager.clear_session(session_id)
            if self._catalog is not None:
                self._catalog.reset_session(session_id)
            try:
                from app.services.chat import planner
                planner.clear_plan(session_id)
            except ImportError:
                pass
            try:
                from app.services.chat.context.layer_schema import clear_layer_schema_cache
                clear_layer_schema_cache(session_id)
            except ImportError:
                pass
        return deleted


# ═══════════════════════════════════════════════════════════════
# Message conversion (reused from _stream.py)
# ═══════════════════════════════════════════════════════════════

def _convert_messages(messages: list[AgentMessage]) -> list[dict]:
    """Convert AgentMessage list to LLM-compatible dict list."""
    result: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            entry: dict = {"role": "assistant", "content": msg.get("content", "")}
            if msg.get("reasoning_content"):
                entry["reasoning_content"] = msg["reasoning_content"]
            if msg.get("tool_calls"):
                entry["tool_calls"] = msg["tool_calls"]
            result.append(entry)
        elif role == "tool":
            entry = {
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            }
            result.append(entry)
        else:
            result.append({"role": role, "content": msg.get("content", "")})
    return result
