"""Serialize anonymous credential rotation against fresh durable authority."""
from datetime import datetime, timezone
import logging
import secrets
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import owner_token_matches
from app.core.owner_tokens import owner_token_digest
from app.models.db_model import Conversation
from app.services.distributed_lock import LockDegradedError, LockLostError, session_lock_registry
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)


async def rotate_anonymous_credential(
    session_id: str, db: AsyncSession, *, owner_token: Optional[str],
) -> dict[str, str]:
    # The route dependency performed a read only. End its snapshot before
    # waiting, then revalidate the presented credential under the writer lock.
    await db.rollback()
    try:
        async with session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True,
        ) as lease:
            current = (await db.execute(select(Conversation).where(
                Conversation.id == session_id
            ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            if current is None or current.user_id is not None or not owner_token_matches(current.owner_token, owner_token):
                raise HTTPException(status_code=404, detail="Session not found")
            token = secrets.token_urlsafe(32)
            try:
                if await session_data_manager.set_map_state(session_id, "owner_token_digest", owner_token_digest(token)) is False:
                    raise RuntimeError("owner verifier mirror unavailable")
                current.owner_token = token
                current.updated_at = datetime.now(timezone.utc)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                # Re-read after rollback, including an uncertain DB commit.
                # Never restore the dependency's potentially obsolete token.
                if not getattr(lease, "lost", False):
                    try:
                        persisted = (await db.execute(select(Conversation.owner_token).where(
                            Conversation.id == session_id
                        ))).scalar_one_or_none()
                        if persisted:
                            await session_data_manager.set_map_state(
                                session_id, "owner_token_digest", owner_token_digest(persisted)
                            )
                    except Exception:
                        logger.exception("Failed to reconcile owner verifier after rotation failure")
                raise HTTPException(status_code=503, detail="owner token rotation unavailable") from exc
            return {"owner_token": token}
    except (TimeoutError, LockDegradedError, LockLostError) as exc:
        raise HTTPException(status_code=503, detail="owner token rotation unavailable", headers={"Retry-After": "1"}) from exc
