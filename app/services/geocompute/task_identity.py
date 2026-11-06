"""Authenticate the caller projection crossing the Celery message boundary."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Mapping, TypedDict

from app.core.config import settings
from app.services.geocompute.errors import GeoComputeError

class TaskCaller(TypedDict, total=False):
    user_id: str
    username: str
    role: str
    org_id: int | str | None
    ver: int


def project_task_caller(caller: Mapping[str, object]) -> TaskCaller:
    """Only authorization scalars cross the queue; ORM objects/secrets stay local."""
    output: TaskCaller = {}
    uid = caller.get("user_id")
    if isinstance(uid, (str, int)):
        output["user_id"] = str(uid)
    for key in ("username", "role"):
        value = caller.get(key)
        if isinstance(value, str):
            if key == "username":
                output["username"] = value
            else:
                output["role"] = value
    org = caller.get("org_id")
    if org is None or isinstance(org, (str, int)):
        output["org_id"] = org
    ver = caller.get("ver")
    if isinstance(ver, int):
        output["ver"] = ver
    return output


class TaskIdentity(TypedDict):
    issued_at: int
    signature: str


_MAX_AGE_SECONDS = 86400


def _signature(kwargs: Mapping[str, object], issued_at: int) -> str:
    payload = {key: kwargs.get(key) for key in ("caller", "session_id", "node", "owner_scope")}
    payload["issued_at"] = issued_at
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    secret = str(settings.JWT_SECRET_KEY or "").encode()
    if not secret:
        raise GeoComputeError("task identity signing unavailable")
    key = hmac.new(secret, b"webgis:geocompute-task-identity:v1", hashlib.sha256).digest()
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def sign_task_identity(kwargs: Mapping[str, object]) -> TaskIdentity:
    issued_at = int(time.time())
    return {"issued_at": issued_at, "signature": _signature(kwargs, issued_at)}


def verify_task_identity(kwargs: Mapping[str, object], identity: Any) -> None:
    if kwargs.get("caller") is None:
        return
    try:
        issued_at = int(identity["issued_at"])
        supplied = str(identity["signature"])
        age = time.time() - issued_at
        valid = -60 <= age <= _MAX_AGE_SECONDS and hmac.compare_digest(supplied, _signature(kwargs, issued_at))
    except (TypeError, KeyError, ValueError):
        valid = False
    if not valid:
        raise GeoComputeError("unverified task caller identity")
