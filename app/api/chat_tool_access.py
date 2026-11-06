"""The direct admin tool endpoint retains normal session ownership rules."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.history_service_async import AsyncHistoryService


async def authorize_and_audit_tool_execution(
    db: AsyncSession, *, session_id: Optional[str], user: dict,
    owner_token: Optional[str], tool_name: str, tier: int,
) -> None:
    actor = user.get("user_id")
    if session_id:
        session = await AsyncHistoryService(db).get_session_meta(
            session_id, user_id=actor, owner_token=owner_token,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
    from app.core.tenancy import effective_org_id
    from app.services.audit import record_audit

    org_id = await effective_org_id(user, db)
    await record_audit(
        db, action="admin.tool.execute", actor_id=actor, org_id=org_id,
        target_type="session", target_id=session_id,
        detail={"tool": tool_name, "tier": tier},
    )
