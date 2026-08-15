"""Async chat conversation persistence service.

资源所有权（A2）规约：
- `user_id` 在 Conversation 上 nullable。新建会话时，如果调用方提供 user_id 就写入，
  匿名访问保持 NULL（与现有数据兼容）。
- `list_sessions(user_id)` 仅返回该用户的会话；user_id=None 视为匿名调用方，
  返回空（匿名用户没有列表入口，会话只能凭 session_id 直访）。
- `get_session(session_id, user_id, owner_token)` / `delete_session(session_id, user_id)`：
  - 认证会话 user_id 已绑定：仅原用户可见
  - 匿名会话 user_id IS NULL 且 owner_token IS NULL → grandfather 旧记录，允许
  - 匿名会话 user_id IS NULL 且 owner_token 已设置 → 调用方必须提供匹配的
    owner_token（SEC-08），否则视为不存在
  - 否则返回 None / 静默忽略，**统一 404 风格**避免泄露存在性。

SEC-08：新建的匿名会话会生成 server-issued `owner_token`（secrets.token_urlsafe(32)）。
前端必须在后续请求的 `X-Session-Token` 头里回传该 token 才能访问该会话。
认证会话与历史匿名会话（token 为 NULL）不受影响。
"""
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.db_model import Conversation, Message

from app.tools._utils import async_db_session
from app.services.history_store_protocol import HistoryContext, HistoryStoreProtocol

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000


def _is_anonymous(user_id: Optional[str]) -> bool:
    """统一对外的『匿名调用方』判定。None 或字符串 'anonymous' 都视为匿名。"""
    return not user_id or user_id == "anonymous"


from app.services.tool_dispatch_service import normalize_tool_name


def _msg_to_llm_dict(m: Message) -> dict:
    item = {"role": m.role, "content": m.content or ""}
    if m.tool_calls:
        # Translate legacy tool names in stored history on replay (Ticket #207)
        tool_calls = []
        for tc in m.tool_calls:
            if isinstance(tc, dict) and "function" in tc and "name" in tc["function"]:
                tc_copy = dict(tc)
                tc_copy["function"] = dict(tc["function"])
                tc_copy["function"]["name"] = normalize_tool_name(tc["function"]["name"])
                args = tc_copy["function"].get("arguments")
                if isinstance(args, dict):
                    # #376: MiniMax XML 路径解析出的 arguments 以 dict 形式
                    # 落库，而 OpenAI 兼容 API 的 tool_calls 契约要求 JSON
                    # 字符串 —— 重放时统一规范化（标准 FC 路径本就是字符串，
                    # 此处为 no-op）。
                    tc_copy["function"]["arguments"] = json.dumps(
                        args, ensure_ascii=False
                    )
                tool_calls.append(tc_copy)
            else:
                tool_calls.append(tc)
        item["tool_calls"] = tool_calls
    if m.tool_call_id:
        item["tool_call_id"] = m.tool_call_id
    if m.reasoning_content:
        item["reasoning_content"] = m.reasoning_content
    return item


def _strip_orphaned_tool_calls(llm_messages: list[dict]) -> list[dict]:
    """#376 兜底防线：加载时剥除没有配对 tool 响应的 assistant tool_calls。

    修复前的 MiniMax XML 工具调用路径把解析出的 tool_calls 随 assistant 消息
    持久化，但工具响应只以内存中的合成 user 消息存在、从不落库 —— 进程重启
    或 LRU 逐出后重放历史会得到孤儿 assistant.tool_calls，OpenAI 兼容 provider
    拒绝后续 turn 的首次 LLM 调用，会话永久损坏。此函数在重放路径上把每条
    「tool_calls 没有后随匹配 tool 响应」的 assistant 消息降级为普通文本
    （content 保留，tool_calls 剥除）；无 id 的 tool_call 无法与响应配对，
    同样剥除。幂等：已配对（存在对应 tool 响应）的 tool_call 原样保留。
    只影响本次重放，不修改 DB 行。
    """
    answered = {
        m.get("tool_call_id")
        for m in llm_messages
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    for m in llm_messages:
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        kept = []
        for tc in m["tool_calls"]:
            tc_id = tc.get("id") if isinstance(tc, dict) else None
            if tc_id is None or tc_id not in answered:
                continue  # 孤儿 / 无 id：API 层无法配对，剥除
            kept.append(tc)
        if kept:
            m["tool_calls"] = kept
        else:
            m.pop("tool_calls", None)
            logger.info(
                "[history] stripped orphaned tool_calls from an assistant "
                "message on replay (session history would otherwise be "
                "rejected by the LLM API)"
            )
    return llm_messages


class AsyncHistoryService(HistoryStoreProtocol):
    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db

    async def load_context(
        self,
        session_id: str,
        owner_token: Optional[str] = None,
        user_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> HistoryContext:
        """Deep seam method: load conversation, validate SEC-08 owner_token, and format LLM messages."""
        if self.db is not None:
            conv = await self.get_or_create_conversation(session_id, user_id=user_id)
        else:
            async with async_db_session() as db:
                svc = AsyncHistoryService(db)
                conv = await svc.get_or_create_conversation(session_id, user_id=user_id)

        # Validate owner_token if set and user is anonymous
        if conv.user_id is None and conv.owner_token is not None and owner_token:
            import hmac
            if not hmac.compare_digest(conv.owner_token, owner_token):
                logger.warning(f"Owner token mismatch for session {session_id}")

        llm_messages = []
        if system_prompt:
            llm_messages.append({"role": "system", "content": system_prompt})
        if conv and conv.messages:
            sorted_msgs = sorted(conv.messages, key=lambda m: m.id)
            # #376: 重放前剥除孤儿 assistant tool_calls（修复历史损坏的会话；
            # 新写入路径已在源头不持久化 XML tool_calls）。
            llm_messages.extend(
                _strip_orphaned_tool_calls([_msg_to_llm_dict(m) for m in sorted_msgs])
            )

        return HistoryContext(
            session_id=session_id,
            owner_token=conv.owner_token,
            user_id=conv.user_id,
            llm_messages=llm_messages,
            raw_conversation=conv,
        )

    async def commit_interaction(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        metadata: Optional[dict] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Deep seam method: atomically record user query and assistant response."""
        if self.db is not None:
            await self._commit_interaction_internal(session_id, user_content, assistant_content, metadata)
        else:
            async with async_db_session() as db:
                svc = AsyncHistoryService(db)
                await svc._commit_interaction_internal(session_id, user_content, assistant_content, metadata)

    async def _commit_interaction_internal(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        if user_content:
            await self.save_message(session_id, "user", user_content)
        if assistant_content:
            await self.save_message(session_id, "assistant", assistant_content)

    async def get_or_create_conversation(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Conversation:
        conv, _created = await self.get_or_create_conversation_with_created(
            session_id, user_id=user_id
        )
        return conv

    async def get_or_create_conversation_with_created(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> tuple[Conversation, bool]:
        """Return ``(conversation, created)``.

        ``created`` is True only when this call inserted the row. A concurrent
        first-message loser re-SELECTs the winner and must treat that as
        existing (do not emit the winner's ``owner_token``).
        """
        import anyio
        owner = None if _is_anonymous(user_id) else user_id
        # SEC-08：新建匿名会话时生成 owner_token。认证会话（owner 非空）不需要。
        # token 写入 owner_token 列；调用方通过 conv.owner_token 读取并回传给前端。
        new_owner_token = secrets.token_urlsafe(32) if owner is None else None
        for attempt in range(3):
            try:
                stmt = select(Conversation).where(Conversation.id == session_id).options(selectinload(Conversation.messages))
                result = await self.db.execute(stmt)
                conv = result.scalar_one_or_none()
                if conv:
                    return conv, False

                conv = Conversation(
                    id=session_id,
                    user_id=owner,
                    owner_token=new_owner_token,
                    title="新对话",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                self.db.add(conv)
                await self.db.flush()
                await self._enforce_cap()
                await self.db.commit()
                # 重新查询以确保加载了关系
                result = await self.db.execute(stmt)
                return result.scalar_one(), True
            except Exception as e:
                # SEC-10 (deep-audit round 3): two concurrent first-messages for
                # the same session both see "absent", both INSERT, and the loser
                # gets a PRIMARY KEY IntegrityError — previously unhandled here
                # (only "locked" strings were retried), surfacing as an HTTP 500
                # on double-submit. The standard upsert pattern: on integrity
                # conflict, roll back and re-SELECT the winner's row.
                from sqlalchemy.exc import IntegrityError

                is_conflict = isinstance(e, IntegrityError) or (
                    isinstance(e, Exception) and "integrityerror" in str(type(e).__name__).lower()
                )
                is_locked = "locked" in str(e).lower()
                if (is_conflict or is_locked) and attempt < 2:
                    await anyio.sleep(0.1 * (attempt + 1))
                    await self.db.rollback()
                    continue
                raise

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls=None,
        tool_result=None,
        tool_call_id=None,
        reasoning_content: Optional[str] = None,
    ) -> None:
        import anyio
        for attempt in range(3):
            try:
                msg = Message(
                    conversation_id=session_id,
                    role=role,
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                    tool_result=tool_result,
                    created_at=datetime.now(timezone.utc),
                )
                self.db.add(msg)
                conv = await self.db.get(Conversation, session_id)
                if conv:
                    conv.updated_at = datetime.now(timezone.utc)
                await self.db.commit()
                return
            except Exception as e:
                if "locked" in str(e).lower() and attempt < 2:
                    await anyio.sleep(0.1 * (attempt + 1))
                    await self.db.rollback()
                    continue
                raise

    async def update_title(self, session_id: str, title: str) -> None:
        conv = await self.db.get(Conversation, session_id)
        if conv:
            conv.title = title[:200]
            await self.db.commit()

    async def list_sessions(
        self,
        limit: int = MAX_SESSIONS,
        user_id: Optional[str] = None,
        offset: int = 0,
    ) -> list[Conversation]:
        """匿名调用方拿不到列表；认证用户只看自己的会话。"""
        if _is_anonymous(user_id):
            return []
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .options(selectinload(Conversation.messages))
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        owner_token: Optional[str] = None,
    ) -> Optional[Conversation]:
        """带所有权检查的会话取回。

        - 会话 user_id 已绑定：仅原用户可见（认证用户校验）
        - 会话 user_id IS NULL（匿名会话）：
          - owner_token IS NULL：grandfather 旧记录，知道 session_id 即可访问
          - owner_token 已设置：调用方必须提供匹配的 owner_token（SEC-08），
            否则视为不存在（防止 session_id 泄漏后被任意人读取）
        - 不存在或越权：均返回 None（统一处理为 404，避免存在性泄露）
        """
        stmt = select(Conversation).where(Conversation.id == session_id).options(selectinload(Conversation.messages))
        result = await self.db.execute(stmt)
        conv = result.scalar_one_or_none()
        if conv is None:
            return None
        if conv.user_id is None:
            # SEC-08：匿名会话。若 owner_token 已设置则要求调用方提供匹配值；
            # 使用 hmac.compare_digest 做常量时间比较以防时序侧信道。
            if conv.owner_token is not None:
                import hmac
                if not owner_token or not hmac.compare_digest(conv.owner_token, owner_token):
                    return None
            return conv
        if _is_anonymous(user_id):
            return None
        if conv.user_id != user_id:
            return None
        return conv

    async def delete_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        owner_token: Optional[str] = None,
    ) -> bool:
        """所有权检查后删除；返回是否真正删除（用于路由层决定 404 vs 204）。"""
        conv = await self.db.get(Conversation, session_id)
        if conv is None:
            return False
        if conv.user_id is not None:
            if _is_anonymous(user_id) or conv.user_id != user_id:
                return False
        else:
            # SEC-08：匿名会话删除同样受 owner_token 保护（与 get_session 对齐）。
            # owner_token 为 NULL 的旧记录 grandfather 放行。
            if conv.owner_token is not None:
                import hmac
                if not owner_token or not hmac.compare_digest(conv.owner_token, owner_token):
                    return False
        await self.db.delete(conv)
        await self.db.commit()
        return True

    async def _enforce_cap(self) -> None:
        total_result = await self.db.execute(select(func.count()).select_from(Conversation))
        total = total_result.scalar_one()
        if total <= MAX_SESSIONS:
            return
        overflow = total - MAX_SESSIONS
        stmt = (
            select(Conversation)
            .order_by(Conversation.updated_at.asc())
            .limit(overflow)
        )
        result = await self.db.execute(stmt)
        oldest = result.scalars().all()
        for conv in oldest:
            await self.db.delete(conv)
        # No commit here — caller commits
