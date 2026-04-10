# Chat History Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist all chat conversations to PostgreSQL, keep most recent 1000 globally, display them in a read-only history panel inside ChatPanel, and allow deletion.

**Architecture:** A new `HistoryService` handles all DB operations and is injected into `ChatEngine`. ChatEngine writes each message to DB asynchronously (fire-and-forget) and triggers an LLM title-generation background task after each completed conversation. Three new API routes expose session list, detail, and deletion. The frontend adds a history drawer inside ChatPanel with read-only view and delete support.

**Tech Stack:** SQLAlchemy (sync, existing), FastAPI, asyncio background tasks, Next.js / React, TypeScript

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `app/services/history_service.py` | All DB operations: create/get/list/delete conversations + save messages + enforce 1000-cap + generate title |
| Modify | `app/services/chat_engine.py` | Inject HistoryService; write-through per message; trigger title gen after task_complete |
| Modify | `app/api/routes/chat.py` | Add GET /sessions, GET /sessions/{id}; update DELETE /sessions/{id} to hit DB |
| Modify | `frontend/components/chat/chat-panel.tsx` | Add history drawer (list + read-only detail), session management state, new-session button |
| Modify | `frontend/lib/api/chat.ts` | Already has getSessionList/getSessionDetail — no changes needed |

---

## Task 1: Create HistoryService

**Files:**
- Create: `app/services/history_service.py`
- Create: `tests/test_history_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_history_service.py
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime
from app.services.history_service import HistoryService


def make_service():
    db = MagicMock()
    return HistoryService(db), db


def test_get_or_create_conversation_creates_new():
    svc, db = make_service()
    db.get.return_value = None
    db.query.return_value.count.return_value = 0

    conv = svc.get_or_create_conversation("sess-1")

    db.add.assert_called_once()
    db.commit.assert_called()
    assert conv.id == "sess-1"


def test_get_or_create_conversation_returns_existing():
    from app.models.db_models import Conversation
    svc, db = make_service()
    existing = Conversation(id="sess-1", title="Old", created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.get.return_value = existing

    conv = svc.get_or_create_conversation("sess-1")

    db.add.assert_not_called()
    assert conv.id == "sess-1"


def test_save_message():
    svc, db = make_service()
    svc.save_message("sess-1", "user", "hello")
    db.add.assert_called_once()
    db.commit.assert_called_once()


def test_list_sessions():
    svc, db = make_service()
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    result = svc.list_sessions()
    assert result == []


def test_delete_session():
    from app.models.db_models import Conversation
    svc, db = make_service()
    conv = MagicMock(spec=Conversation)
    db.get.return_value = conv
    svc.delete_session("sess-1")
    db.delete.assert_called_once_with(conv)
    db.commit.assert_called_once()


def test_delete_session_not_found():
    svc, db = make_service()
    db.get.return_value = None
    # Should not raise
    svc.delete_session("nonexistent")
    db.delete.assert_not_called()


def test_enforce_cap_deletes_oldest():
    from app.models.db_models import Conversation
    svc, db = make_service()
    db.query.return_value.count.return_value = 1002
    old1 = MagicMock(spec=Conversation, id="old1")
    old2 = MagicMock(spec=Conversation, id="old2")
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = [old1, old2]

    svc._enforce_cap()

    assert db.delete.call_count == 2
    db.commit.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/kevin/project/webgis-ai-agent
pytest tests/test_history_service.py -v 2>&1 | head -30
```
Expected: ImportError or ModuleNotFoundError for `history_service`

- [ ] **Step 3: Implement HistoryService**

```python
# app/services/history_service.py
"""Chat conversation persistence service."""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.db_models import Conversation, Message

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000


class HistoryService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_conversation(self, session_id: str) -> Conversation:
        conv = self.db.get(Conversation, session_id)
        if conv:
            return conv
        conv = Conversation(
            id=session_id,
            title="新对话",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        self._enforce_cap()
        return conv

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls=None,
        tool_result=None,
    ) -> None:
        msg = Message(
            conversation_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_result=tool_result,
            created_at=datetime.utcnow(),
        )
        self.db.add(msg)
        # Touch updated_at on the parent conversation
        conv = self.db.get(Conversation, session_id)
        if conv:
            conv.updated_at = datetime.utcnow()
        self.db.commit()

    def update_title(self, session_id: str, title: str) -> None:
        conv = self.db.get(Conversation, session_id)
        if conv:
            conv.title = title[:200]
            self.db.commit()

    def list_sessions(self, limit: int = MAX_SESSIONS) -> list[Conversation]:
        return (
            self.db.query(Conversation)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .all()
        )

    def get_session(self, session_id: str) -> Optional[Conversation]:
        return self.db.get(Conversation, session_id)

    def delete_session(self, session_id: str) -> None:
        conv = self.db.get(Conversation, session_id)
        if conv:
            self.db.delete(conv)
            self.db.commit()

    def _enforce_cap(self) -> None:
        total = self.db.query(Conversation).count()
        if total <= MAX_SESSIONS:
            return
        overflow = total - MAX_SESSIONS
        oldest = (
            self.db.query(Conversation)
            .order_by(Conversation.updated_at.asc())
            .limit(overflow)
            .all()
        )
        for conv in oldest:
            self.db.delete(conv)
        self.db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_history_service.py -v
```
Expected: 7 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/history_service.py tests/test_history_service.py
git commit -m "feat: add HistoryService for conversation persistence"
```

---

## Task 2: Integrate HistoryService into ChatEngine

**Files:**
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Write failing test for write-through**

```python
# tests/test_chat_engine_history.py
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call


@pytest.mark.asyncio
async def test_chat_stream_persists_user_message():
    """ChatEngine should save user message to DB at stream start."""
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    engine = ChatEngine(registry)

    mock_history = MagicMock()
    mock_conv = MagicMock(id="test-session")
    mock_history.get_or_create_conversation.return_value = mock_conv
    engine._history = mock_history

    # Patch _call_llm to return minimal assistant response
    async def fake_llm(messages, tools=None):
        return {
            "choices": [{
                "message": {"role": "assistant", "content": "done", "tool_calls": None},
                "finish_reason": "stop"
            }]
        }
    engine._call_llm = fake_llm

    events = []
    async for event in engine.chat_stream("hello", session_id="test-session"):
        events.append(event)

    mock_history.get_or_create_conversation.assert_called_with("test-session")
    # save_message called at least for the user message
    assert mock_history.save_message.call_count >= 1
    first_call = mock_history.save_message.call_args_list[0]
    assert first_call[0][1] == "user"  # role
    assert first_call[0][2] == "hello"  # content
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_chat_engine_history.py::test_chat_stream_persists_user_message -v
```
Expected: FAIL — `engine._history` is not called yet

- [ ] **Step 3: Modify ChatEngine to inject and use HistoryService**

In `app/services/chat_engine.py`, make these changes:

**3a. Add imports at top:**
```python
import asyncio
from app.core.database import SessionLocal
from app.services.history_service import HistoryService
```

**3b. Modify `__init__` to create a HistoryService:**
```python
def __init__(self, tool_registry: ToolRegistry):
    self.registry = tool_registry
    self.base_url = settings.LLM_BASE_URL.rstrip("/")
    self.model = settings.LLM_MODEL
    self.api_key = settings.LLM_API_KEY
    self.max_rounds = 10
    self._sessions: dict[str, list[dict]] = {}
    self._geojson_cache: dict[str, dict[str, str]] = {}
    self.tracker = TaskTracker()
    # History persistence
    self._history: HistoryService = HistoryService(SessionLocal())
```

**3c. Modify `_get_or_create_session` to also create DB record:**
```python
def _get_or_create_session(self, session_id: str) -> list[dict]:
    if session_id not in self._sessions:
        self._sessions[session_id] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        try:
            self._history.get_or_create_conversation(session_id)
        except Exception as e:
            logger.warning(f"History: failed to create conversation {session_id}: {e}")
    return self._sessions[session_id]
```

**3d. Add `_save_msg_async` helper (fire-and-forget):**
```python
def _save_msg_async(self, session_id: str, role: str, content: str, tool_calls=None, tool_result=None):
    """Persist a message to DB without blocking the SSE stream."""
    try:
        self._history.save_message(session_id, role, content, tool_calls, tool_result)
    except Exception as e:
        logger.warning(f"History: failed to save message: {e}")
```

**3e. In `chat_stream`, after the user message is appended (line ~207), add persistence:**
```python
messages.append({"role": "user", "content": message})
# Persist user message
asyncio.get_event_loop().run_in_executor(
    None, self._save_msg_async, session_id, "user", message
)
```

**3f. After the final assistant content is appended (around line ~347 `messages.append({"role": "assistant", "content": content})`), add:**
```python
messages.append({"role": "assistant", "content": content})
# Persist assistant message
asyncio.get_event_loop().run_in_executor(
    None, self._save_msg_async, session_id, "assistant", content
)
```

**3g. After `task_complete` yield, trigger title generation:**
```python
yield _sse_event("task_complete", {...})
yield _sse_event("done", {"session_id": session_id})
# Generate title in background
asyncio.get_event_loop().run_in_executor(
    None, self._generate_title, session_id, message
)
return
```

**3h. Add `_generate_title` method:**
```python
def _generate_title(self, session_id: str, first_user_message: str) -> None:
    """Call LLM synchronously to generate a short title, then update DB."""
    import httpx as _httpx
    try:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "用不超过15个字概括以下用户问题，只输出标题，不要任何解释或标点以外的内容。"},
                {"role": "user", "content": first_user_message[:500]},
            ],
            "max_tokens": 64,
        }
        resp = _httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        title = resp.json()["choices"][0]["message"]["content"].strip()
        if title:
            self._history.update_title(session_id, title)
    except Exception as e:
        logger.warning(f"History: title generation failed for {session_id}: {e}")
```

**3i. Update `clear_session` to also delete from DB:**
```python
def clear_session(self, session_id: str):
    if session_id in self._sessions:
        del self._sessions[session_id]
    self._geojson_cache.pop(session_id, None)
    try:
        self._history.delete_session(session_id)
    except Exception as e:
        logger.warning(f"History: failed to delete session {session_id}: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_chat_engine_history.py -v
```
Expected: PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/test_chat_engine_history.py
git commit -m "feat: integrate HistoryService write-through into ChatEngine"
```

---

## Task 3: Add API Routes for Session History

**Files:**
- Modify: `app/api/routes/chat.py`
- Create: `tests/test_chat_history_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_history_routes.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from datetime import datetime


def make_conv(id_, title, updated):
    c = MagicMock()
    c.id = id_
    c.title = title
    c.created_at = datetime(2026, 1, 1)
    c.updated_at = updated
    c.messages = []
    return c


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_list_sessions_returns_json(client):
    conv = make_conv("s1", "Test", datetime(2026, 4, 10))
    with patch("app.api.routes.chat.engine._history") as mock_hist:
        mock_hist.list_sessions.return_value = [conv]
        resp = client.get("/chat/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["id"] == "s1"
    assert data["sessions"][0]["title"] == "Test"


def test_get_session_detail(client):
    conv = make_conv("s1", "Test", datetime(2026, 4, 10))
    msg = MagicMock()
    msg.id = 1
    msg.role = "user"
    msg.content = "hello"
    msg.tool_calls = None
    msg.tool_result = None
    msg.created_at = datetime(2026, 4, 10)
    conv.messages = [msg]
    with patch("app.api.routes.chat.engine._history") as mock_hist:
        mock_hist.get_session.return_value = conv
        resp = client.get("/chat/sessions/s1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "s1"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "user"


def test_get_session_detail_not_found(client):
    with patch("app.api.routes.chat.engine._history") as mock_hist:
        mock_hist.get_session.return_value = None
        resp = client.get("/chat/sessions/nonexistent")
    assert resp.status_code == 404


def test_delete_session(client):
    with patch("app.api.routes.chat.engine.clear_session") as mock_clear:
        resp = client.delete("/chat/sessions/s1")
    assert resp.status_code == 200
    mock_clear.assert_called_once_with("s1")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_chat_history_routes.py -v 2>&1 | head -30
```
Expected: FAIL — routes don't exist yet

- [ ] **Step 3: Add routes to `app/api/routes/chat.py`**

Add these routes after the existing `DELETE /sessions/{session_id}` route:

```python
@router.get("/sessions")
async def list_sessions():
    """列出所有历史会话（最多1000条，按最近更新排序）"""
    sessions = engine._history.list_sessions()
    return {
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at.timestamp() * 1000,
                "updated_at": s.updated_at.timestamp() * 1000,
            }
            for s in sessions
        ]
    }


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """获取会话详情（只读）"""
    conv = engine._history.get_session(session_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": conv.id,
        "title": conv.title,
        "created_at": conv.created_at.timestamp() * 1000,
        "updated_at": conv.updated_at.timestamp() * 1000,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.timestamp() * 1000,
            }
            for m in conv.messages
            if m.role in ("user", "assistant")  # exclude tool messages from history view
        ],
    }
```

Also update the existing `DELETE /sessions/{session_id}` to call `clear_session` (which now also deletes from DB):

```python
@router.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """清除会话（内存 + DB）"""
    engine.clear_session(session_id)
    return {"status": "ok"}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_chat_history_routes.py -v
```
Expected: 4 tests PASSED

- [ ] **Step 5: Manually verify via curl**

```bash
# Start the server first, then:
curl -s http://localhost:8000/chat/sessions | python3 -m json.tool
```
Expected: `{"sessions": []}` or list of existing sessions

- [ ] **Step 6: Commit**

```bash
git add app/api/routes/chat.py tests/test_chat_history_routes.py
git commit -m "feat: add GET /chat/sessions and GET /chat/sessions/{id} routes"
```

---

## Task 4: Frontend — History Panel in ChatPanel

**Files:**
- Modify: `frontend/components/chat/chat-panel.tsx`

The ChatPanel will gain three view modes:
- `'chat'` — current live conversation (default)
- `'history'` — session list drawer
- `'history-detail'` — read-only message view of a selected historical session

- [ ] **Step 1: Add state and type imports at the top of `chat-panel.tsx`**

After the existing imports, add:
```typescript
import { getSessionList, getSessionDetail } from "@/lib/api/chat"
import { ChatSession } from "@/lib/types/chat"
import { History, ArrowLeft, Trash2 } from "lucide-react"
```

- [ ] **Step 2: Add history-related state inside the `ChatPanel` component**

After the existing `useState` declarations:
```typescript
type ViewMode = 'chat' | 'history' | 'history-detail'
const [viewMode, setViewMode] = useState<ViewMode>('chat')
const [sessions, setSessions] = useState<ChatSession[]>([])
const [historyDetail, setHistoryDetail] = useState<ChatSession | null>(null)
const [sessionsLoading, setSessionsLoading] = useState(false)
const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
```

- [ ] **Step 3: Add `loadSessions` function**

After the existing `useEffect` hooks:
```typescript
const loadSessions = async () => {
  setSessionsLoading(true)
  try {
    const data = await getSessionList()
    setSessions(data.sessions || [])
  } catch (e) {
    console.error("Failed to load sessions", e)
  } finally {
    setSessionsLoading(false)
  }
}

const openHistory = () => {
  setViewMode('history')
  loadSessions()
}

const openHistoryDetail = async (sessionId: string) => {
  try {
    const detail = await getSessionDetail(sessionId)
    setHistoryDetail(detail)
    setViewMode('history-detail')
  } catch (e) {
    console.error("Failed to load session detail", e)
  }
}

const handleDeleteSession = async (sessionId: string) => {
  try {
    await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}/chat/sessions/${sessionId}`, { method: "DELETE" })
    setSessions(prev => prev.filter(s => s.id !== sessionId))
    if (historyDetail?.id === sessionId) {
      setViewMode('history')
      setHistoryDetail(null)
    }
  } catch (e) {
    console.error("Failed to delete session", e)
  } finally {
    setDeleteConfirm(null)
  }
}
```

- [ ] **Step 4: Add history button to the ChatPanel header**

Find the existing header `<div>` (the one containing the bot icon and title), and add a history toggle button. The header currently looks like:

```tsx
<div className="flex items-center gap-3 p-4 border-b border-border bg-background-secondary/50">
  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
    <Bot className="h-4 w-4 text-primary" />
  </div>
  <div>
    <h2 className="text-sm font-semibold text-foreground">WebGIS AI 助手</h2>
    <p className="text-xs text-muted-foreground">智能地理分析</p>
  </div>
</div>
```

Replace with:
```tsx
<div className="flex items-center gap-3 p-4 border-b border-border bg-background-secondary/50">
  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
    <Bot className="h-4 w-4 text-primary" />
  </div>
  <div className="flex-1">
    <h2 className="text-sm font-semibold text-foreground">WebGIS AI 助手</h2>
    <p className="text-xs text-muted-foreground">智能地理分析</p>
  </div>
  <button
    onClick={viewMode === 'chat' ? openHistory : () => setViewMode('chat')}
    className="flex h-8 w-8 items-center justify-center rounded-lg hover:bg-card transition-colors"
    title={viewMode === 'chat' ? "历史会话" : "返回对话"}
  >
    {viewMode === 'chat'
      ? <History className="h-4 w-4 text-muted-foreground" />
      : <ArrowLeft className="h-4 w-4 text-muted-foreground" />
    }
  </button>
</div>
```

- [ ] **Step 5: Add history list view panel**

Locate the return JSX in ChatPanel. The outermost `<div className="flex flex-col h-full ...">` wraps everything. Inside, after the header, add a conditional render before the messages area:

```tsx
{/* History List View */}
{viewMode === 'history' && (
  <div className="flex-1 overflow-y-auto">
    <div className="p-3">
      <p className="text-xs text-muted-foreground px-2 mb-2">最近 {sessions.length} 条会话</p>
      {sessionsLoading && (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {!sessionsLoading && sessions.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-8">暂无历史会话</p>
      )}
      <ul className="space-y-1">
        {sessions.map(s => (
          <li key={s.id} className="group flex items-center gap-2 px-3 py-2.5 rounded-lg hover:bg-card transition-colors cursor-pointer"
              onClick={() => openHistoryDetail(s.id)}>
            <div className="flex-1 min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{s.title || '新对话'}</p>
              <p className="text-xs text-muted-foreground">
                {new Date(s.updatedAt).toLocaleDateString('zh-CN')}
              </p>
            </div>
            {deleteConfirm === s.id ? (
              <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                <button onClick={() => handleDeleteSession(s.id)}
                  className="text-xs px-2 py-1 bg-red-500 text-white rounded">确认</button>
                <button onClick={() => setDeleteConfirm(null)}
                  className="text-xs px-2 py-1 bg-muted rounded">取消</button>
              </div>
            ) : (
              <button
                onClick={e => { e.stopPropagation(); setDeleteConfirm(s.id) }}
                className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-100 hover:text-red-600 rounded transition-opacity"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  </div>
)}
```

- [ ] **Step 6: Add history detail read-only view**

After the history list block, add:
```tsx
{/* History Detail View (read-only) */}
{viewMode === 'history-detail' && historyDetail && (
  <div className="flex flex-col flex-1 overflow-hidden">
    <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 text-xs text-amber-700 flex items-center justify-between">
      <span>只读模式 — {historyDetail.title}</span>
      <button onClick={() => { setViewMode('history'); setHistoryDetail(null) }}
        className="underline hover:no-underline">返回列表</button>
    </div>
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      {historyDetail.messages.map((msg, i) => (
        <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>
          <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full
            ${msg.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}>
            {msg.role === 'user' ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
          </div>
          <div className={`max-w-[85%] rounded-xl px-3 py-2 text-sm
            ${msg.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-card border border-border'}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
          </div>
        </div>
      ))}
    </div>
  </div>
)}
```

- [ ] **Step 7: Wrap existing messages + input in conditional**

The existing messages list and input box should only render when `viewMode === 'chat'`. Wrap them:

```tsx
{viewMode === 'chat' && (
  <>
    {/* existing messages ScrollArea */}
    {/* existing TaskProgress */}
    {/* existing attachments preview */}
    {/* existing input bar */}
  </>
)}
```

- [ ] **Step 8: Verify `getSessionList` returns the right shape**

`frontend/lib/api/chat.ts` `getSessionList()` currently does:
```typescript
export async function getSessionList() {
  const res = await fetch(`${API_BASE}/chat/sessions`);
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
  return res.json();
}
```
This returns `{ sessions: [...] }`. The frontend uses `data.sessions`. Confirm `getSessionDetail` returns a `ChatSession`-compatible shape. The backend returns `{ id, title, created_at, updated_at, messages }` with timestamps in milliseconds — `ChatSession.updatedAt` expects `number`. No changes needed to `chat.ts`.

- [ ] **Step 9: Commit**

```bash
git add frontend/components/chat/chat-panel.tsx
git commit -m "feat: add history drawer and read-only session view to ChatPanel"
```

---

## Task 5: End-to-End Verification

- [ ] **Step 1: Start backend**

```bash
cd /home/kevin/project/webgis-ai-agent
uvicorn app.main:app --reload --port 8000
```

- [ ] **Step 2: Verify DB tables exist**

```bash
python3 -c "
from app.core.database import Engine
from app.models.db_models import Base
Base.metadata.create_all(bind=Engine)
print('Tables OK')
"
```
Expected: `Tables OK`

- [ ] **Step 3: Send a test message and verify DB persistence**

```bash
curl -N -s -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，介绍一下北京"}' | head -20
```

Then check sessions:
```bash
curl -s http://localhost:8000/chat/sessions | python3 -m json.tool
```
Expected: at least one session with a generated title

- [ ] **Step 4: Start frontend and verify history panel**

```bash
cd frontend && npm run dev
```

Open browser → click the History (🕐) icon in the chat panel header → sessions list should appear → click a session → read-only view with message bubbles should render → hover over session item → delete button appears → click delete → confirmation inline → confirm → session removed from list

- [ ] **Step 5: Run full test suite**

```bash
cd /home/kevin/project/webgis-ai-agent
pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all passing

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: complete chat history persistence with read-only view and deletion"
```
