# Chat History Persistence — Design Spec

**Date:** 2026-04-10  
**Status:** Approved

## Overview

Persist chat conversation history to PostgreSQL, keeping the most recent 1000 conversations globally. Users can browse historical sessions in read-only mode and delete individual sessions.

## Requirements

| # | Requirement |
|---|-------------|
| R1 | All conversation messages are persisted to PostgreSQL in real time |
| R2 | A maximum of 1000 conversations are retained globally; oldest are auto-deleted on overflow |
| R3 | Historical sessions are listed in the sidebar, ordered by most recent activity |
| R4 | Clicking a historical session displays its messages in read-only mode |
| R5 | Users can delete any session from the sidebar |
| R6 | Each session gets an LLM-generated title based on the first user message |
| R7 | Service restarts do not lose conversation history |

## Architecture

### Data Layer

Existing SQLAlchemy models are used without schema changes:

- **`Conversation`** — `id` (UUID string), `title`, `created_at`, `updated_at`
- **`Message`** — `id`, `conversation_id` (FK), `role`, `content`, `tool_calls`, `tool_result`, `created_at`
- Cascade delete is already configured on `Conversation → Message`

### Backend: ChatEngine Changes (`app/services/chat_engine.py`)

**Session initialization:**
- `_get_or_create_session(session_id)` queries DB first; falls back to creating a new `Conversation` row
- In-memory `_sessions` dict continues as write-through cache to avoid repeated DB reads within a live session

**Per-message persistence:**
- After each `messages.append(...)`, the new message is written to the `Message` table asynchronously (fire-and-forget, does not block the SSE stream)

**Title generation:**
- After `task_complete`, a background asyncio task sends the first user message to the LLM with a short system prompt requesting a ≤20-character title
- On completion, `Conversation.title` is updated in DB

**1000-session cap enforcement:**
- After creating a new `Conversation`, a single DELETE query removes the oldest conversations if total count exceeds 1000:
  ```sql
  DELETE FROM conversations
  WHERE id IN (
    SELECT id FROM conversations
    ORDER BY updated_at ASC
    LIMIT GREATEST(0, count - 1000)
  )
  ```
- Executed in the same DB session as the INSERT to avoid race conditions

### Backend: New API Routes (`app/api/routes/chat.py`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/chat/sessions` | List all sessions ordered by `updated_at` DESC, max 1000 |
| `GET` | `/chat/sessions/{id}` | Read-only session detail with all messages |
| `DELETE` | `/chat/sessions/{id}` | Delete session and cascade-delete its messages |

The existing `DELETE /chat/sessions/{id}` currently only clears in-memory state; it will be updated to also delete the DB record.

### Frontend Changes

**Session list loading:**
- On app mount, call `GET /chat/sessions` and populate `ChatSidebar`
- Refresh list after each new conversation is created

**Read-only history view:**
- Clicking a sidebar session calls `GET /chat/sessions/{id}`
- Chat panel renders historical messages (user/assistant bubbles) in read-only mode
- Input box is hidden; a "只读模式" banner + "新建对话" button are shown instead

**Title display:**
- Sidebar shows "新对话" as placeholder while LLM generates the title
- A single polling refresh occurs ~5 seconds after a new session starts to pick up the generated title

**Session deletion:**
- Delete button on each sidebar item (visible on hover)
- Confirmation dialog before calling `DELETE /chat/sessions/{id}`
- After deletion, if the deleted session was active, switch to a new empty session

## Data Flow

```
User sends message
  → ChatEngine.chat_stream()
    → _get_or_create_session() [DB lookup / create Conversation]
    → messages.append(user_msg)  → async write Message to DB
    → LLM call
    → messages.append(assistant_msg) → async write Message to DB
    → task_complete
      → background: LLM generates title → UPDATE Conversation.title
      → enforce 1000-session cap
      → yield SSE done event
```

## Error Handling

- DB write failures for individual messages are logged but do not interrupt the SSE stream (best-effort persistence)
- If title generation fails, the session retains the "新对话" placeholder title
- If `GET /chat/sessions` fails on mount, the sidebar shows an empty state with a retry option (no crash)

## Out of Scope

- Per-session message count limits (not requested)
- User authentication / per-user session isolation (existing system has no auth)
- Export or search of historical sessions
- Real-time title push via SSE (polling is sufficient for this use case)
