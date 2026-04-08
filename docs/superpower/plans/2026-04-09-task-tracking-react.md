# 任务跟踪与 ReAct 执行监控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the existing FC loop in ChatEngine with step-by-step task tracking, new SSE progress events, task query/cancel APIs, and a frontend inline progress card.

**Architecture:** TaskTracker is appended into the existing ChatEngine FC loop — no new engine, no DAG scheduler. Each tool call in the FC loop creates a TaskStep record. New SSE events (task_start, step_start, step_result, etc.) are emitted alongside existing events (tool_call, tool_result, content) for backward compatibility. Frontend receives these events and renders an inline progress card in the chat message flow.

**Tech Stack:** Python 3.13 / FastAPI / Pydantic / httpx (backend); React / TypeScript / Next.js / Tailwind CSS (frontend); pytest + vitest (tests)

**Important codebase notes:**
- `chat_engine.py` uses raw `httpx` to call LLM (NOT the OpenAI SDK). Mock `_call_llm` in tests, not `client.chat.completions`.
- Existing tests in `tests/test_chat_engine.py` are outdated — they mock `engine.client.chat.completions` which no longer exists. New tests should mock `engine._call_llm`.
- Frontend SSE parser is in `frontend/lib/api/chat.ts` — it yields `{event, data}` objects via `AsyncGenerator`.
- `chat-panel.tsx` already has a reasoning progress bar (`currentStep` state). The new task progress card replaces this with richer step-by-step tracking.

---

### Task 1: TaskTracker Models & Core Logic

**Files:**
- Create: `app/services/task_tracker.py`
- Test: `tests/test_task_tracker.py`

- [ ] **Step 1: Write failing tests for TaskTracker**

```python
# tests/test_task_tracker.py
"""TaskTracker 单元测试"""
import pytest
from app.services.task_tracker import TaskTracker, TaskStatus, StepStatus


@pytest.fixture
def tracker():
    return TaskTracker()


def test_create_task(tracker):
    task = tracker.create("session-1", "查询成都大学")
    assert task.id
    assert task.session_id == "session-1"
    assert task.original_request == "查询成都大学"
    assert task.status == TaskStatus.running
    assert task.steps == []
    assert task.created_at is not None


def test_start_step(tracker):
    task = tracker.create("s1", "test")
    step = tracker.start_step(task.id, "query_osm_poi", {"area": "成都"})
    assert step.id == "step-1"
    assert step.tool == "query_osm_poi"
    assert step.params == {"area": "成都"}
    assert step.status == StepStatus.running
    assert step.started_at is not None
    assert len(task.steps) == 1


def test_complete_step(tracker):
    task = tracker.create("s1", "test")
    step = tracker.start_step(task.id, "geocode", {"query": "北京"})
    tracker.complete_step(task.id, step.id, {"lat": 39.9, "lon": 116.4})
    assert step.status == StepStatus.completed
    assert step.result == {"lat": 39.9, "lon": 116.4}
    assert step.finished_at is not None


def test_fail_step(tracker):
    task = tracker.create("s1", "test")
    step = tracker.start_step(task.id, "geocode", {})
    tracker.fail_step(task.id, step.id, "timeout")
    assert step.status == StepStatus.failed
    assert step.error == "timeout"
    assert step.finished_at is not None


def test_complete_task(tracker):
    task = tracker.create("s1", "test")
    tracker.start_step(task.id, "geocode", {})
    tracker.complete_step(task.id, "step-1", {})
    tracker.complete_task(task.id)
    assert task.status == TaskStatus.completed
    assert task.finished_at is not None


def test_fail_task(tracker):
    task = tracker.create("s1", "test")
    tracker.fail_task(task.id, "达到最大轮数")
    assert task.status == TaskStatus.failed


def test_cancel_task(tracker):
    task = tracker.create("s1", "test")
    result = tracker.cancel(task.id)
    assert result is True
    assert task.status == TaskStatus.cancelled


def test_cancel_nonexistent_task(tracker):
    result = tracker.cancel("nonexistent")
    assert result is False


def test_get_task(tracker):
    task = tracker.create("s1", "test")
    fetched = tracker.get(task.id)
    assert fetched is task


def test_get_nonexistent_task(tracker):
    assert tracker.get("nonexistent") is None


def test_list_by_session(tracker):
    tracker.create("s1", "task1")
    tracker.create("s1", "task2")
    tracker.create("s2", "task3")
    assert len(tracker.list_by_session("s1")) == 2
    assert len(tracker.list_by_session("s2")) == 1
    assert len(tracker.list_by_session("s3")) == 0


def test_multiple_steps_auto_increment(tracker):
    task = tracker.create("s1", "test")
    s1 = tracker.start_step(task.id, "tool_a", {})
    s2 = tracker.start_step(task.id, "tool_b", {})
    assert s1.id == "step-1"
    assert s2.id == "step-2"
    assert len(task.steps) == 2


def test_is_cancelled(tracker):
    task = tracker.create("s1", "test")
    assert tracker.is_cancelled(task.id) is False
    tracker.cancel(task.id)
    assert tracker.is_cancelled(task.id) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_task_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.task_tracker'`

- [ ] **Step 3: Implement TaskTracker**

```python
# app/services/task_tracker.py
"""任务状态跟踪器 — 记录 FC 循环中每步工具调用的状态"""
import uuid
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, field
from typing import Any


class StepStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


class TaskStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class TaskStep:
    id: str
    tool: str
    params: dict
    status: StepStatus = StepStatus.running
    result: Any = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


@dataclass
class TaskInfo:
    id: str
    session_id: str
    original_request: str
    steps: list[TaskStep] = field(default_factory=list)
    status: TaskStatus = TaskStatus.running
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class TaskTracker:
    """内存任务跟踪器"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._session_tasks: dict[str, list[str]] = {}

    def create(self, session_id: str, request: str) -> TaskInfo:
        task = TaskInfo(
            id=f"task-{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            original_request=request,
        )
        self._tasks[task.id] = task
        self._session_tasks.setdefault(session_id, []).append(task.id)
        return task

    def start_step(self, task_id: str, tool: str, params: dict) -> TaskStep:
        task = self._tasks[task_id]
        step = TaskStep(
            id=f"step-{len(task.steps) + 1}",
            tool=tool,
            params=params,
        )
        task.steps.append(step)
        return step

    def complete_step(self, task_id: str, step_id: str, result: Any) -> None:
        step = self._find_step(task_id, step_id)
        step.status = StepStatus.completed
        step.result = result
        step.finished_at = datetime.now(timezone.utc)

    def fail_step(self, task_id: str, step_id: str, error: str) -> None:
        step = self._find_step(task_id, step_id)
        step.status = StepStatus.failed
        step.error = error
        step.finished_at = datetime.now(timezone.utc)

    def complete_task(self, task_id: str) -> None:
        task = self._tasks[task_id]
        task.status = TaskStatus.completed
        task.finished_at = datetime.now(timezone.utc)

    def fail_task(self, task_id: str, error: str) -> None:
        task = self._tasks[task_id]
        task.status = TaskStatus.failed
        task.finished_at = datetime.now(timezone.utc)

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.status = TaskStatus.cancelled
        task.finished_at = datetime.now(timezone.utc)
        return True

    def is_cancelled(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return task is not None and task.status == TaskStatus.cancelled

    def get(self, task_id: str) -> TaskInfo | None:
        return self._tasks.get(task_id)

    def list_by_session(self, session_id: str) -> list[TaskInfo]:
        task_ids = self._session_tasks.get(session_id, [])
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    def _find_step(self, task_id: str, step_id: str) -> TaskStep:
        task = self._tasks[task_id]
        for step in task.steps:
            if step.id == step_id:
                return step
        raise KeyError(f"Step {step_id} not found in task {task_id}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_task_tracker.py -v`
Expected: All 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/task_tracker.py tests/test_task_tracker.py
git commit -m "feat: add TaskTracker for FC loop step tracking"
```

---

### Task 2: GeoJSON Detection Helper

**Files:**
- Modify: `app/services/task_tracker.py` (add function at bottom)
- Test: `tests/test_task_tracker.py` (add tests)

- [ ] **Step 1: Write failing tests for detect_geojson**

Append to `tests/test_task_tracker.py`:

```python
from app.services.task_tracker import detect_geojson


def test_detect_geojson_feature_collection():
    data = {"type": "FeatureCollection", "features": []}
    assert detect_geojson(data) is True


def test_detect_geojson_nested():
    data = {"count": 10, "geojson": {"type": "FeatureCollection", "features": []}}
    assert detect_geojson(data) is True


def test_detect_geojson_no_match():
    data = {"status": "ok", "count": 5}
    assert detect_geojson(data) is False


def test_detect_geojson_non_dict():
    assert detect_geojson("hello") is False
    assert detect_geojson(42) is False
    assert detect_geojson(None) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_task_tracker.py::test_detect_geojson_feature_collection -v`
Expected: FAIL — `ImportError: cannot import name 'detect_geojson'`

- [ ] **Step 3: Implement detect_geojson**

Append to `app/services/task_tracker.py`:

```python
def detect_geojson(result: Any) -> bool:
    """检测工具返回结果是否包含 GeoJSON FeatureCollection"""
    if not isinstance(result, dict):
        return False
    if result.get("type") == "FeatureCollection":
        return True
    for v in result.values():
        if isinstance(v, dict) and v.get("type") == "FeatureCollection":
            return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_task_tracker.py -v`
Expected: All 17 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/task_tracker.py tests/test_task_tracker.py
git commit -m "feat: add detect_geojson helper"
```

---

### Task 3: Enhance ChatEngine with TaskTracker & New SSE Events

**Files:**
- Modify: `app/services/chat_engine.py`
- Test: `tests/test_chat_engine_tracking.py` (new file — don't modify outdated `test_chat_engine.py`)

- [ ] **Step 1: Write failing tests for enhanced chat_stream**

```python
# tests/test_chat_engine_tracking.py
"""ChatEngine TaskTracker 集成测试"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry, tool


@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool(r, name="geocode", description="Geocode a location")
    def geocode(query: str) -> dict:
        return {"lat": 39.9, "lon": 116.4, "name": query}

    return r


@pytest.fixture
def engine(registry):
    return ChatEngine(registry)


def _make_llm_response(content=None, tool_calls=None):
    """构造模拟的 LLM API 响应（httpx JSON 格式）"""
    message = {}
    if content is not None:
        message["content"] = content
    else:
        message["content"] = ""
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


@pytest.mark.asyncio
async def test_stream_emits_task_start(engine):
    """chat_stream 应在开始时发送 task_start 事件"""
    resp = _make_llm_response(content="你好")
    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=resp):
        events = []
        async for event in engine.chat_stream("你好", session_id="s1"):
            events.append(event)

    parsed = [_parse_sse(e) for e in events]
    task_starts = [p for p in parsed if p["event"] == "task_start"]
    assert len(task_starts) == 1
    assert "task_id" in task_starts[0]["data"]


@pytest.mark.asyncio
async def test_stream_emits_task_complete(engine):
    """chat_stream 应在结束时发送 task_complete 事件"""
    resp = _make_llm_response(content="分析完成")
    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=resp):
        events = []
        async for event in engine.chat_stream("你好", session_id="s1"):
            events.append(event)

    parsed = [_parse_sse(e) for e in events]
    task_completes = [p for p in parsed if p["event"] == "task_complete"]
    assert len(task_completes) == 1
    assert task_completes[0]["data"]["step_count"] == 0


@pytest.mark.asyncio
async def test_stream_emits_step_events_on_tool_call(engine):
    """工具调用时应发送 step_start 和 step_result 事件"""
    tool_call_resp = _make_llm_response(tool_calls=[{
        "id": "call_1",
        "function": {"name": "geocode", "arguments": '{"query": "北京"}'},
    }])
    final_resp = _make_llm_response(content="北京坐标是 39.9, 116.4")

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, side_effect=[tool_call_resp, final_resp]):
        events = []
        async for event in engine.chat_stream("北京的坐标", session_id="s1"):
            events.append(event)

    parsed = [_parse_sse(e) for e in events]
    event_types = [p["event"] for p in parsed]

    assert "task_start" in event_types
    assert "step_start" in event_types
    assert "step_result" in event_types
    assert "task_complete" in event_types
    # Existing events still present
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "content" in event_types

    # Verify step_result has has_geojson field
    step_results = [p for p in parsed if p["event"] == "step_result"]
    assert "has_geojson" in step_results[0]["data"]


@pytest.mark.asyncio
async def test_stream_step_error_on_tool_failure(engine):
    """工具执行失败时应发送 step_error 事件"""
    tool_call_resp = _make_llm_response(tool_calls=[{
        "id": "call_1",
        "function": {"name": "unknown_tool", "arguments": "{}"},
    }])
    final_resp = _make_llm_response(content="抱歉，工具执行失败")

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, side_effect=[tool_call_resp, final_resp]):
        events = []
        async for event in engine.chat_stream("test", session_id="s1"):
            events.append(event)

    parsed = [_parse_sse(e) for e in events]
    event_types = [p["event"] for p in parsed]
    assert "step_error" in event_types


@pytest.mark.asyncio
async def test_tracker_accessible(engine):
    """ChatEngine 应暴露 tracker 属性"""
    assert hasattr(engine, "tracker")
    assert engine.tracker is not None


@pytest.mark.asyncio
async def test_task_cancellation_stops_loop(engine):
    """取消任务应中断 FC 循环"""
    call_count = 0

    async def mock_llm(messages, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_llm_response(tool_calls=[{
                "id": f"call_{call_count}",
                "function": {"name": "geocode", "arguments": '{"query": "test"}'},
            }])
        # Should not reach here if cancelled
        return _make_llm_response(content="done")

    with patch.object(engine, "_call_llm", side_effect=mock_llm):
        events = []
        async for event in engine.chat_stream("test", session_id="s1"):
            events.append(event)
            parsed = _parse_sse(event)
            # Cancel after first step_start
            if parsed["event"] == "step_start":
                task_id = parsed["data"]["task_id"]
                engine.tracker.cancel(task_id)

    parsed_all = [_parse_sse(e) for e in events]
    event_types = [p["event"] for p in parsed_all]
    assert "task_cancelled" in event_types


def _parse_sse(raw: str) -> dict:
    """解析 SSE 格式字符串为 {event, data}"""
    event_name = ""
    data_str = ""
    for line in raw.strip().split("\n"):
        if line.startswith("event: "):
            event_name = line[7:].strip()
        elif line.startswith("data: "):
            data_str = line[6:]
    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, ValueError):
        data = data_str
    return {"event": event_name, "data": data}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_chat_engine_tracking.py -v`
Expected: FAIL — tests expecting `task_start`/`step_start`/etc. events that don't exist yet

- [ ] **Step 3: Modify ChatEngine to embed TaskTracker**

Edit `app/services/chat_engine.py`. The key changes:
1. Import `TaskTracker` and `detect_geojson`
2. Create `self.tracker = TaskTracker()` in `__init__`
3. Add `_sse_event()` helper method
4. Modify `chat_stream` to emit task/step events while keeping all existing events

Replace the entire file with:

```python
"""对话引擎 - 直接 HTTPX 调用（避免 OpenAI SDK 版本问题）"""
import json
import logging
import uuid
from typing import AsyncGenerator, Optional

import httpx

from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.services.task_tracker import TaskTracker, detect_geojson

logger = logging.getLogger(__name__)


def _sse_event(event_type: str, data: dict) -> str:
    """构造 SSE 格式事件字符串"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatEngine:
    def __init__(self, tool_registry: ToolRegistry):
        self.registry = tool_registry
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL
        self.api_key = settings.LLM_API_KEY
        self.max_rounds = 10
        # 内存对话存储: session_id -> messages list
        self._sessions: dict[str, list[dict]] = {}
        # 任务跟踪器
        self.tracker = TaskTracker()

    def _get_or_create_session(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            self._sessions[session_id] = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
        return self._sessions[session_id]

    async def _call_llm(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        """直接调用 LLM API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def chat(self, message: str, session_id: Optional[str] = None) -> dict:
        """非流式对话"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})

        # FC 循环
        for _ in range(self.max_rounds):
            tools = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages, tools)
            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})

            if assistant_msg.get("tool_calls"):
                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.get("content", ""),
                    "tool_calls": assistant_msg.get("tool_calls", [])
                })

                for tc in assistant_msg.get("tool_calls", []):
                    try:
                        result = await self.registry.dispatch(tc["function"]["name"], tc["function"]["arguments"])
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                    except Exception as e:
                        result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                        logger.error(f"Tool {tc['function']['name']} error: {e}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })
                continue
            else:
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})
                return {"content": content, "session_id": session_id}

        return {"content": "达到最大工具调用轮数", "session_id": session_id}

    async def chat_stream(self, message: str, session_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """流式对话，yield SSE 格式事件，含任务跟踪"""
        if not session_id:
            session_id = str(uuid.uuid4())

        messages = self._get_or_create_session(session_id)
        messages.append({"role": "user", "content": message})

        # 创建任务
        task = self.tracker.create(session_id, message)
        yield _sse_event("task_start", {"task_id": task.id})

        for _ in range(self.max_rounds):
            # 检查取消
            if self.tracker.is_cancelled(task.id):
                yield _sse_event("task_cancelled", {"task_id": task.id})
                return

            tools = self.registry.get_schemas() if self.registry.get_schemas() else None
            response = await self._call_llm(messages, tools)
            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})

            if assistant_msg.get("tool_calls"):
                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.get("content", ""),
                    "tool_calls": assistant_msg.get("tool_calls", [])
                })

                for tc in assistant_msg.get("tool_calls", []):
                    tool_name = tc["function"]["name"]
                    tool_args_raw = tc["function"]["arguments"]

                    # 解析参数用于跟踪
                    try:
                        tool_args_dict = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
                    except (json.JSONDecodeError, TypeError):
                        tool_args_dict = {}

                    # step_start
                    step = self.tracker.start_step(task.id, tool_name, tool_args_dict)
                    yield _sse_event("step_start", {
                        "task_id": task.id,
                        "step_id": step.id,
                        "step_index": len(task.steps),
                        "tool": tool_name,
                    })

                    # 现有 tool_call 事件（保持兼容）
                    yield f"event: tool_call\ndata: {json.dumps({'name': tool_name, 'arguments': tool_args_raw}, ensure_ascii=False)}\n\n"

                    # 执行工具
                    try:
                        result = await self.registry.dispatch(tool_name, tool_args_raw)
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                        self.tracker.complete_step(task.id, step.id, result)

                        # step_result
                        has_geojson = detect_geojson(result)
                        yield _sse_event("step_result", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "result": result,
                            "has_geojson": has_geojson,
                        })

                        # 现有 tool_result 事件（保持兼容）
                        yield f"event: tool_result\ndata: {json.dumps({'name': tool_name, 'result': result}, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                        logger.error(f"Tool {tool_name} error: {e}")
                        self.tracker.fail_step(task.id, step.id, str(e))

                        # step_error
                        yield _sse_event("step_error", {
                            "task_id": task.id,
                            "step_id": step.id,
                            "tool": tool_name,
                            "error": str(e),
                        })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })

                    # 检查取消（每步执行后）
                    if self.tracker.is_cancelled(task.id):
                        yield _sse_event("task_cancelled", {"task_id": task.id})
                        return

                continue
            else:
                # 最终回复
                content = assistant_msg.get("content", "")
                messages.append({"role": "assistant", "content": content})

                # 现有 content 事件（保持兼容）
                yield f"event: content\ndata: {json.dumps({'content': content, 'session_id': session_id}, ensure_ascii=False)}\n\n"

                # task_complete
                self.tracker.complete_task(task.id)
                yield _sse_event("task_complete", {
                    "task_id": task.id,
                    "step_count": len(task.steps),
                    "summary": content[:100],
                })
                return

        self.tracker.fail_task(task.id, "达到最大工具调用轮数")
        yield _sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
        yield f"event: content\ndata: {json.dumps({'content': '达到最大工具调用轮数', 'session_id': session_id}, ensure_ascii=False)}\n\n"

    def clear_session(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]


SYSTEM_PROMPT = """你是一个专业的 GIS 分析助手，擅长地理空间数据查询、分析和可视化。

## 核心工具使用规则

### POI 查询（必须首先使用）
当用户查询某个区域的地物（学校、医院、餐厅、公园等）时，必须使用 `query_osm_poi` 工具：
- 示例：「查询北京的大学」→ query_osm_poi(area="北京", category="school")
- 示例：「成都天府广场5公里内的公园」→ query_osm_poi(area="成都天府广场5公里内", category="park")

### 热力图/密度图（两步操作）
当用户要求制作密度图或热力图时：
1. 先用 `query_osm_poi` 获取 POI 数据
2. 然后「不要」调用 heatmap_data 工具（因为需要传入大量 GeoJSON 数据，容易出错），而是直接告诉用户已获取数据并在地图上显示

### 道路/建筑/边界查询
- 道路网络：`query_osm_roads`
- 建筑物：`query_osm_buildings`
- 行政边界：`query_osm_boundary`

### 地理编码
- 地名→坐标：`geocode`
- 坐标→地名：`reverse_geocode`

### 空间分析
- 缓冲区分析：`buffer_analysis`
- 空间统计：`spatial_stats`

## 重要规则
1. 对于任何涉及「查找XX地方的XX」的请求，必须调用 query_osm_poi
2. 不要将大量 GeoJSON 数据作为参数传递给工具（会导致 JSON 解析错误）
3. 用中文回复用户
4. 工具调用后，简要说明查询结果"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_chat_engine_tracking.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/test_chat_engine_tracking.py
git commit -m "feat: embed TaskTracker in ChatEngine FC loop with new SSE events"
```

---

### Task 4: Task Query & Cancel API

**Files:**
- Create: `app/api/routes/task.py`
- Modify: `app/api/routes/__init__.py`
- Modify: `app/main.py`
- Test: `tests/test_task_api.py`

- [ ] **Step 1: Write failing tests for task API**

```python
# tests/test_task_api.py
"""任务 API 端点测试"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _seed_tracker(client):
    """通过 chat 路由获取 engine 的 tracker 并注入测试数据"""
    # 直接访问 chat 模块的 engine 实例
    from app.api.routes.chat import engine
    task = engine.tracker.create("test-session", "查询北京大学")
    step = engine.tracker.start_step(task.id, "query_osm_poi", {"area": "北京"})
    engine.tracker.complete_step(task.id, step.id, {"count": 10})
    engine.tracker.complete_task(task.id)
    return task


def test_get_task(client):
    task = _seed_tracker(client)
    resp = client.get(f"/api/v1/tasks/{task.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == task.id
    assert data["status"] == "completed"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["tool"] == "query_osm_poi"


def test_get_task_not_found(client):
    resp = client.get("/api/v1/tasks/nonexistent")
    assert resp.status_code == 404


def test_list_tasks(client):
    _seed_tracker(client)
    resp = client.get("/api/v1/tasks", params={"session_id": "test-session"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) >= 1


def test_cancel_task(client):
    from app.api.routes.chat import engine
    task = engine.tracker.create("cancel-session", "取消测试")
    resp = client.delete(f"/api/v1/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True


def test_cancel_task_not_found(client):
    resp = client.delete("/api/v1/tasks/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_task_api.py -v`
Expected: FAIL — 404 for `/api/v1/tasks/...` routes

- [ ] **Step 3: Create task route**

```python
# app/api/routes/task.py
"""Task API Routes - 任务状态查询与取消"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.api.routes.chat import engine

router = APIRouter(prefix="/tasks", tags=["任务管理"])


class TaskStepResponse(BaseModel):
    id: str
    tool: str
    status: str
    error: str | None = None

    class Config:
        from_attributes = True


class TaskStatusResponse(BaseModel):
    task_id: str
    session_id: str
    original_request: str
    status: str
    steps: list[TaskStepResponse]

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    tasks: list[TaskStatusResponse]


class TaskCancelResponse(BaseModel):
    cancelled: bool


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str):
    """查询任务状态和步骤详情"""
    task = engine.tracker.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=task.id,
        session_id=task.session_id,
        original_request=task.original_request,
        status=task.status.value,
        steps=[
            TaskStepResponse(id=s.id, tool=s.tool, status=s.status.value, error=s.error)
            for s in task.steps
        ],
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(session_id: Optional[str] = None):
    """列出任务，可按 session 过滤"""
    if session_id:
        tasks = engine.tracker.list_by_session(session_id)
    else:
        tasks = list(engine.tracker._tasks.values())
    return TaskListResponse(
        tasks=[
            TaskStatusResponse(
                task_id=t.id,
                session_id=t.session_id,
                original_request=t.original_request,
                status=t.status.value,
                steps=[
                    TaskStepResponse(id=s.id, tool=s.tool, status=s.status.value, error=s.error)
                    for s in t.steps
                ],
            )
            for t in tasks
        ]
    )


@router.delete("/{task_id}", response_model=TaskCancelResponse)
async def cancel_task(task_id: str):
    """取消正在执行的任务"""
    result = engine.tracker.cancel(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskCancelResponse(cancelled=True)
```

- [ ] **Step 4: Register task router in __init__.py and main.py**

Edit `app/api/routes/__init__.py`:
```python
"""
API 路由模块
"""

from app.api.routes import health, map, layer, chat, report, task
__all__ = ["health", "map", "layer", "chat", "report", "task"]
```

Edit `app/main.py` — add the import and router registration:

Add to imports:
```python
from app.api.routes import health, map, chat, layer, report, task
```

Add after the last `app.include_router(...)` line:
```python
app.include_router(task.router, prefix="/api/v1", tags=["任务管理"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_task_api.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/routes/task.py app/api/routes/__init__.py app/main.py tests/test_task_api.py
git commit -m "feat: add task query and cancel API endpoints"
```

---

### Task 5: Frontend — Extend SSE Types & Task API Client

**Files:**
- Modify: `frontend/lib/api/chat.ts`
- Create: `frontend/lib/api/task.ts`

- [ ] **Step 1: Update SSEEventType in chat.ts**

In `frontend/lib/api/chat.ts`, replace the `SSEEventType` definition:

Old:
```typescript
export type SSEEventType = 'message' | 'thinking' | 'planning' | 'acting' | 'observing' | 'done' | 'tool_error';
```

New:
```typescript
export type SSEEventType =
  | 'message'
  | 'thinking'
  | 'planning'
  | 'acting'
  | 'observing'
  | 'done'
  | 'tool_error'
  | 'task_start'
  | 'step_start'
  | 'step_result'
  | 'step_error'
  | 'task_complete'
  | 'task_error'
  | 'task_cancelled';
```

- [ ] **Step 2: Create task API client**

```typescript
// frontend/lib/api/task.ts
/**
 * Task API — 任务状态查询与取消
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export interface TaskStepInfo {
  id: string;
  tool: string;
  status: "running" | "completed" | "failed";
  error?: string;
}

export interface TaskInfo {
  task_id: string;
  session_id: string;
  original_request: string;
  status: "running" | "completed" | "failed" | "cancelled";
  steps: TaskStepInfo[];
}

/**
 * 查询单个任务状态
 */
export async function getTask(taskId: string): Promise<TaskInfo> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`);
  if (!res.ok) throw new Error(`Task API error: ${res.status}`);
  return res.json();
}

/**
 * 列出任务（可按 session 过滤）
 */
export async function listTasks(sessionId?: string): Promise<{ tasks: TaskInfo[] }> {
  const params = sessionId ? `?session_id=${sessionId}` : "";
  const res = await fetch(`${API_BASE}/tasks${params}`);
  if (!res.ok) throw new Error(`Task API error: ${res.status}`);
  return res.json();
}

/**
 * 取消任务
 */
export async function cancelTask(taskId: string): Promise<{ cancelled: boolean }> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Task API error: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/api/chat.ts frontend/lib/api/task.ts
git commit -m "feat: extend SSE event types and add task API client"
```

---

### Task 6: Frontend — Task Context

**Files:**
- Create: `frontend/lib/contexts/task-context.tsx`

- [ ] **Step 1: Create TaskContext**

```tsx
// frontend/lib/contexts/task-context.tsx
"use client";

import { createContext, useContext, useCallback, useState, type ReactNode } from "react";

export interface TaskStep {
  id: string;
  tool: string;
  stepIndex: number;
  status: "running" | "completed" | "failed";
  result?: unknown;
  hasGeojson?: boolean;
  error?: string;
}

export interface TaskState {
  id: string;
  steps: TaskStep[];
  status: "running" | "completed" | "failed" | "cancelled";
  stepCount?: number;
  summary?: string;
}

interface TaskContextValue {
  currentTask: TaskState | null;
  handleTaskStart: (taskId: string) => void;
  handleStepStart: (taskId: string, stepId: string, stepIndex: number, tool: string) => void;
  handleStepResult: (taskId: string, stepId: string, tool: string, result: unknown, hasGeojson: boolean) => void;
  handleStepError: (taskId: string, stepId: string, error: string) => void;
  handleTaskComplete: (taskId: string, stepCount: number, summary: string) => void;
  handleTaskError: (taskId: string, error: string) => void;
  handleTaskCancelled: (taskId: string) => void;
  clearTask: () => void;
}

const TaskContext = createContext<TaskContextValue | null>(null);

export function TaskProvider({ children }: { children: ReactNode }) {
  const [currentTask, setCurrentTask] = useState<TaskState | null>(null);

  const handleTaskStart = useCallback((taskId: string) => {
    setCurrentTask({ id: taskId, steps: [], status: "running" });
  }, []);

  const handleStepStart = useCallback(
    (taskId: string, stepId: string, stepIndex: number, tool: string) => {
      setCurrentTask((prev) => {
        if (!prev || prev.id !== taskId) return prev;
        return {
          ...prev,
          steps: [...prev.steps, { id: stepId, tool, stepIndex, status: "running" }],
        };
      });
    },
    []
  );

  const handleStepResult = useCallback(
    (taskId: string, stepId: string, tool: string, result: unknown, hasGeojson: boolean) => {
      setCurrentTask((prev) => {
        if (!prev || prev.id !== taskId) return prev;
        return {
          ...prev,
          steps: prev.steps.map((s) =>
            s.id === stepId ? { ...s, status: "completed" as const, result, hasGeojson } : s
          ),
        };
      });
    },
    []
  );

  const handleStepError = useCallback((taskId: string, stepId: string, error: string) => {
    setCurrentTask((prev) => {
      if (!prev || prev.id !== taskId) return prev;
      return {
        ...prev,
        steps: prev.steps.map((s) =>
          s.id === stepId ? { ...s, status: "failed" as const, error } : s
        ),
      };
    });
  }, []);

  const handleTaskComplete = useCallback(
    (taskId: string, stepCount: number, summary: string) => {
      setCurrentTask((prev) => {
        if (!prev || prev.id !== taskId) return prev;
        return { ...prev, status: "completed", stepCount, summary };
      });
    },
    []
  );

  const handleTaskError = useCallback((taskId: string, error: string) => {
    setCurrentTask((prev) => {
      if (!prev || prev.id !== taskId) return prev;
      return { ...prev, status: "failed" };
    });
  }, []);

  const handleTaskCancelled = useCallback((taskId: string) => {
    setCurrentTask((prev) => {
      if (!prev || prev.id !== taskId) return prev;
      return { ...prev, status: "cancelled" };
    });
  }, []);

  const clearTask = useCallback(() => {
    setCurrentTask(null);
  }, []);

  return (
    <TaskContext.Provider
      value={{
        currentTask,
        handleTaskStart,
        handleStepStart,
        handleStepResult,
        handleStepError,
        handleTaskComplete,
        handleTaskError,
        handleTaskCancelled,
        clearTask,
      }}
    >
      {children}
    </TaskContext.Provider>
  );
}

export function useTask() {
  const context = useContext(TaskContext);
  if (!context) {
    throw new Error("useTask must be used within TaskProvider");
  }
  return context;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/contexts/task-context.tsx
git commit -m "feat: add TaskContext for frontend task state management"
```

---

### Task 7: Frontend — Task Progress Card Component

**Files:**
- Create: `frontend/components/chat/task-progress.tsx`

- [ ] **Step 1: Create TaskProgress component**

```tsx
// frontend/components/chat/task-progress.tsx
"use client";

import { useState } from "react";
import { Loader2, Check, X, ChevronDown, ChevronUp, Ban } from "lucide-react";
import { cancelTask } from "@/lib/api/task";
import type { TaskState, TaskStep } from "@/lib/contexts/task-context";

interface TaskProgressProps {
  task: TaskState;
}

function StepIcon({ status }: { status: TaskStep["status"] }) {
  switch (status) {
    case "running":
      return <Loader2 className="h-3 w-3 animate-spin text-cyan-400" />;
    case "completed":
      return <Check className="h-3 w-3 text-green-400" />;
    case "failed":
      return <X className="h-3 w-3 text-red-400" />;
  }
}

export function TaskProgress({ task }: TaskProgressProps) {
  const [expanded, setExpanded] = useState(true);
  const [cancelling, setCancelling] = useState(false);

  const isFinished = task.status !== "running";
  const completedCount = task.steps.filter((s) => s.status === "completed").length;
  const totalCount = task.steps.length;
  const progressPct = totalCount > 0 ? (completedCount / totalCount) * 100 : 0;

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await cancelTask(task.id);
    } catch {
      // Cancel failed — let SSE events update the state
    } finally {
      setCancelling(false);
    }
  };

  // Auto-collapse when finished
  const showExpanded = expanded && !isFinished;

  return (
    <div className="rounded-lg border border-cyan-500/30 bg-cyan-950/20 p-3 text-sm">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {!isFinished && <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-400" />}
          {task.status === "completed" && <Check className="h-3.5 w-3.5 text-green-400" />}
          {task.status === "failed" && <X className="h-3.5 w-3.5 text-red-400" />}
          {task.status === "cancelled" && <Ban className="h-3.5 w-3.5 text-gray-400" />}
          <span className="text-cyan-200 font-medium">
            {isFinished
              ? `${task.status === "completed" ? "完成" : task.status === "cancelled" ? "已取消" : "失败"} (${completedCount} 步)`
              : `执行中... (${completedCount}/${totalCount})`}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {!isFinished && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="text-xs text-gray-400 hover:text-red-400 px-1.5 py-0.5 rounded transition-colors"
            >
              {cancelling ? "取消中..." : "取消"}
            </button>
          )}
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-gray-400 hover:text-cyan-300 p-0.5"
          >
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>

      {/* Progress bar */}
      {!isFinished && totalCount > 0 && (
        <div className="mt-2 w-full h-1 bg-gray-700 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-cyan-400 to-blue-500 transition-all duration-500 ease-out"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      )}

      {/* Steps list */}
      {expanded && task.steps.length > 0 && (
        <div className="mt-2 space-y-1">
          {task.steps.map((step) => (
            <div key={step.id} className="flex items-center gap-2 text-xs">
              <StepIcon status={step.status} />
              <span className="text-gray-300">{step.tool}</span>
              {step.error && <span className="text-red-400 truncate max-w-48">({step.error})</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/chat/task-progress.tsx
git commit -m "feat: add TaskProgress inline card component"
```

---

### Task 8: Frontend — Integrate Task Events into ChatPanel

**Files:**
- Modify: `frontend/components/chat/chat-panel.tsx`

This is the integration task. We need to:
1. Import and use `useTask` context
2. Handle new SSE event types in the `streamChat` loop
3. Render `TaskProgress` card inline in the message flow

- [ ] **Step 1: Wire up TaskProvider in the app layout**

First, find the root layout or app wrapper where `AnalysisProvider` is rendered:

Run: `grep -r "AnalysisProvider" frontend/ --include="*.tsx" -l` to find where to add `TaskProvider`.

Wrap `TaskProvider` around children at the same level as `AnalysisProvider`.

Example — if `AnalysisProvider` is in `frontend/app/layout.tsx`:

```tsx
import { TaskProvider } from "@/lib/contexts/task-context";

// Inside the component, wrap alongside AnalysisProvider:
<AnalysisProvider>
  <TaskProvider>
    {children}
  </TaskProvider>
</AnalysisProvider>
```

- [ ] **Step 2: Modify chat-panel.tsx to handle task SSE events**

In `frontend/components/chat/chat-panel.tsx`, make these changes:

Add imports at the top:
```tsx
import { useTask } from "@/lib/contexts/task-context"
import { TaskProgress } from "@/components/chat/task-progress"
```

Inside the `ChatPanel` component, destructure task context:
```tsx
const {
  currentTask,
  handleTaskStart,
  handleStepStart,
  handleStepResult,
  handleStepError,
  handleTaskComplete,
  handleTaskError,
  handleTaskCancelled,
  clearTask,
} = useTask()
```

In the `handleSend` function, inside the `for await (const event of streamChat(...))` loop, add new event handling cases **before** the existing `tool_call` case:

```tsx
        if (eventType === "task_start" && data?.task_id) {
          handleTaskStart(data.task_id)
        } else if (eventType === "step_start" && data?.task_id) {
          handleStepStart(data.task_id, data.step_id, data.step_index, data.tool)
        } else if (eventType === "step_result" && data?.task_id) {
          handleStepResult(data.task_id, data.step_id, data.tool, data.result, data.has_geojson)
          // Also handle GeoJSON rendering via onToolResult
          if (data.has_geojson && onToolResult) {
            onToolResult(data.tool, data.result)
          }
        } else if (eventType === "step_error" && data?.task_id) {
          handleStepError(data.task_id, data.step_id, data.error)
        } else if (eventType === "task_complete" && data?.task_id) {
          handleTaskComplete(data.task_id, data.step_count, data.summary)
        } else if (eventType === "task_error" && data?.task_id) {
          handleTaskError(data.task_id, data.error)
        } else if (eventType === "task_cancelled" && data?.task_id) {
          handleTaskCancelled(data.task_id)
        } else if (eventType === "session" && data?.session_id) {
```

In the `finally` block of `handleSend`, add task cleanup after the `setTimeout`:
```tsx
    } finally {
      setTimeout(() => {
        setCurrentStep(null)
        clearTask()  // Clear task state after animation
      }, 2000)
      setIsLoading(false)
      inputRef.current?.focus()
    }
```

In the JSX, render `TaskProgress` card inline. Replace the existing `{/* Reasoning Progress Bar */}` section with:

```tsx
      {/* Task Progress Card (inline, replaces old reasoning bar) */}
      {currentTask && (
        <div className="border-b border-cyan-500/20 px-4 py-2 bg-cyan-950/30">
          <TaskProgress task={currentTask} />
        </div>
      )}
```

Remove the old reasoning progress bar block (the `{currentStep && (...)}` section from line 257-272).

- [ ] **Step 3: Verify the frontend builds**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && npm run build`
Expected: Build succeeds without errors

- [ ] **Step 4: Commit**

```bash
git add frontend/components/chat/chat-panel.tsx frontend/app/layout.tsx  # or wherever TaskProvider was added
git commit -m "feat: integrate task progress card into chat panel"
```

---

### Task 9: Frontend — Draft Layer Support in AnalysisContext

**Files:**
- Modify: `frontend/lib/contexts/analysis-context.tsx`

- [ ] **Step 1: Add draft flag to AnalysisResultItem**

In `frontend/lib/contexts/analysis-context.tsx`, add `draft` field to `AnalysisResultItem`:

```typescript
export interface AnalysisResultItem {
  id: string;
  title: string;
  type: 'text' | 'chart' | 'map';
  content: string;
  geoJson?: GeoJSONData;
  layerStyles?: LayerStyle[];
  timestamp: number;
  draft?: boolean;  // true = intermediate result, renders at 0.5 opacity
}
```

- [ ] **Step 2: Add promoteDraftLayers action**

Add a new action to the context that converts all draft layers to final:

In the `AnalysisContextValue` interface, add:
```typescript
  promoteDraftLayers: () => void;
```

In the `AnalysisProvider` component, add:
```typescript
  const promoteDraftLayers = useCallback(() => {
    setState(prev => ({
      ...prev,
      results: prev.results.map(r => r.draft ? { ...r, draft: false } : r),
    }));
  }, []);
```

Include `promoteDraftLayers` in the value object.

- [ ] **Step 3: Verify the frontend builds**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/contexts/analysis-context.tsx
git commit -m "feat: add draft layer support to AnalysisContext"
```

---

### Task 10: Integration Verification

**Files:** None — this is a verification-only task.

- [ ] **Step 1: Run all backend tests**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_task_tracker.py tests/test_chat_engine_tracking.py tests/test_task_api.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run frontend build**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Verify backward compatibility**

Confirm that existing SSE events (`tool_call`, `tool_result`, `content`) are still emitted by checking the test in `test_chat_engine_tracking.py::test_stream_emits_step_events_on_tool_call` which verifies both old and new events are present.

- [ ] **Step 4: Commit final verification**

No code changes needed — all previous commits cover the implementation.
