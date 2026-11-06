"""Live resource authorization for the deliberately narrow HTTP replay surface."""
from __future__ import annotations

import hashlib
import json
import re

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

# These endpoints use require_owned_session and have no additional resource ACL.
# A principal stamp alone cannot authorize project, job, template, or tool access.
_SESSION_REPLAY_PATH = re.compile(
    r"/api/v[12]/chat/sessions/([^/]+)/(?:canvas-actions|cartographic-observation|clarifications/answer)$"
)


async def replay_authority(request: Request, body: bytes) -> str | None:
    """Unknown resource policy means execute the real route without caching."""
    match = _SESSION_REPLAY_PATH.fullmatch(request.url.path)
    if match is None:
        return None
    from app.core.async_runner import current_loop_async_session
    from app.core.auth import authorize_session_write, get_current_user_with_version
    from app.models.db_model import Conversation
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    try:
        payload = json.loads(body)
        session_id = match.group(1)
        for supplied in (request.query_params.get("session_id"),
                         payload.get("session_id") if isinstance(payload, dict) else None):
            if supplied is not None and supplied != session_id:
                return None
        authorization = request.headers.get("authorization", "")
        user_id = None
        facts: list[object] = []
        async with current_loop_async_session() as db:
            if authorization:
                scheme, _, token = authorization.partition(" ")
                if scheme.lower() != "bearer" or not token.strip():
                    return None
                user = await get_current_user_with_version(
                    credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token.strip()), db=db)
                stored = user.get("user")
                user_id = user.get("user_id")
                facts = [user_id, user.get("role"), user.get("org_id"),
                         getattr(stored, "token_version", None)]
            conversation = (await db.execute(select(Conversation).where(
                Conversation.id == session_id
            ))).scalar_one_or_none()
            if conversation is None or not authorize_session_write(
                conversation, user_id, request.headers.get("x-session-token"),
            ):
                return None
            facts.extend([session_id, conversation.user_id,
                          str(conversation.owner_token or "")])
        return hashlib.sha256(json.dumps(facts, default=str, separators=(",", ":")).encode()).hexdigest()
    except (HTTPException, ValueError, TypeError, SQLAlchemyError):
        return None
