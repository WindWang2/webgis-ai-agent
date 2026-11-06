"""One-way anonymous credential storage, distinct from transport credentials."""
from __future__ import annotations

import hashlib
from typing import Optional

from sqlalchemy.engine import Dialect
from sqlalchemy.types import String, TypeDecorator


class OwnerTokenDigest(str):
    """A verifier loaded by the database, never a client-presented credential."""


def owner_token_digest(token: str) -> str:
    if isinstance(token, OwnerTokenDigest):
        return str(token)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def transport_owner_token(token: Optional[str]) -> Optional[str]:
    return None if isinstance(token, OwnerTokenDigest) else token


class OwnerTokenColumn(TypeDecorator[str]):
    """Hash both inserts and equality-query parameters; leave loaded hashes stable."""

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value: Optional[str], dialect: Dialect) -> Optional[str]:
        return owner_token_digest(value) if value is not None else None

    def process_result_value(self, value: Optional[str], dialect: Dialect) -> Optional[str]:
        return OwnerTokenDigest(value) if value is not None else None
