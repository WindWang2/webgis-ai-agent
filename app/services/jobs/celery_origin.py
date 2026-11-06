"""Bounded prefork-safe delivery pool and broker-carried tool origin."""
from collections.abc import Mapping
from dataclasses import fields
from functools import lru_cache

import redis

from app.services.jobs.context import JobOrigin, current_origin


@lru_cache(maxsize=4)
def delivery_client(pid: int, url: str) -> redis.Redis:
    # PID is part of the key: a prefork child never reuses a parent's sockets.
    return redis.from_url(url, socket_connect_timeout=2, socket_timeout=1)


def origin_headers() -> dict[str, object]:
    origin = current_origin()
    if origin is None:
        return {}
    return {"webgis_job_origin": {
        field.name: getattr(origin, field.name)
        for field in fields(JobOrigin) if field.name not in {"created_job_ids", "owner_token"}
    }}


def restore_origin(headers: object) -> JobOrigin | None:
    if not isinstance(headers, Mapping):
        return None
    payload = headers.get("webgis_job_origin")
    if not isinstance(payload, Mapping):
        return None

    def text(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    org_id = payload.get("org_id")
    return JobOrigin(
        session_id=text("session_id"), owner_id=text("owner_id"),
        project_id=text("project_id"),
        org_id=org_id if isinstance(org_id, int) and not isinstance(org_id, bool) else None,
        run_id=text("run_id"), turn_id=text("turn_id"), tool_call_id=text("tool_call_id"),
        agent_task_id=text("agent_task_id"), agent_step_id=text("agent_step_id"),
        tool_name=text("tool_name"),
    )
