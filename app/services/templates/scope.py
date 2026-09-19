"""Tenant visibility for cartography templates (HTTP + agent tool surfaces).

Mirrors the historic ``_template_scope_clause`` / ``_template_visible`` contract
from the HTTP gallery: builtin ∪ creator_id ∪ org_id. Never treat
``org_id IS NULL`` user rows as public — JWT historically omitted org_id, so
that predicate leaked every tenant's saved templates.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import or_

from app.models.db_model import CartographyTemplate


def template_scope_clause(user_id: Optional[str], org_id, role: Optional[str] = None):
    """SQLAlchemy predicate, or ``None`` when admin (unscoped)."""
    if role == "admin":
        return None
    if user_id is None:
        return CartographyTemplate.is_builtin.is_(True)
    clauses = [
        CartographyTemplate.is_builtin.is_(True),
        CartographyTemplate.creator_id == user_id,
    ]
    if org_id is not None:
        clauses.append(CartographyTemplate.org_id == org_id)
    return or_(*clauses)


def template_visible(
    tmpl: CartographyTemplate,
    user_id: Optional[str],
    org_id,
    role: Optional[str] = None,
) -> bool:
    if tmpl.is_builtin or role == "admin":
        return True
    if org_id is not None and tmpl.org_id == org_id:
        return True
    return user_id is not None and tmpl.creator_id == user_id
