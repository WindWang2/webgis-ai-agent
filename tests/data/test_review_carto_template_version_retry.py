"""Deep-review carto-platform CP-18：并发 create_version 撞唯一约束 → 重试而非 500。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Query

from app.core.database import Base, Engine, SessionLocal
from app.models.db_model import CartographyTemplate
from app.models.template_version import TemplateVersion
from app.services.templates import versioning

_TID = "tmpl_cp18_retry"


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        db.merge(CartographyTemplate(
            id=_TID, kind="layout", name="cp18", category="layout", keywords=[],
            description="", payload={}, is_builtin=False, version=1, creator_id="u",
        ))
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(TemplateVersion).filter_by(template_id=_TID).delete()
        db.query(CartographyTemplate).filter_by(id=_TID).delete()
        db.commit()


def _stale_first(monkeypatch, stale_reads: int):
    """模拟并发：前 N 次读 latest 返回 None（另一事务已插入 v1 但本方未见）。"""
    real_first = Query.first
    state = {"left": stale_reads}

    def first(self):
        ent = self.column_descriptions[0].get("entity") if self.column_descriptions else None
        if ent is TemplateVersion and state["left"] > 0:
            state["left"] -= 1
            return None
        return real_first(self)

    monkeypatch.setattr(Query, "first", first)


def test_cp18_conflict_is_retried(monkeypatch):
    with SessionLocal() as db:
        v1 = versioning.create_version(db, _TID, {"a": 1})
        assert v1.version == 1
        _stale_first(monkeypatch, 1)
        v2 = versioning.create_version(db, _TID, {"a": 2})
        assert v2.version == 2 and v2.parent_version_id == v1.id


def test_cp18_exhausted_retries_raise_conflict(monkeypatch):
    with SessionLocal() as db:
        versioning.create_version(db, _TID, {"a": 1})
        _stale_first(monkeypatch, 99)
        with pytest.raises(versioning.TemplateVersionConflictError):
            versioning.create_version(db, _TID, {"a": 2})


def test_cp18_route_maps_conflict_to_409():
    from app.api.routes.template_versions import _map_error

    exc = _map_error(versioning.TemplateVersionConflictError("x"))
    assert exc.status_code == 409
