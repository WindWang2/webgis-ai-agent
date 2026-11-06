"""Deep-review carto-platform CP-18：并发 create_version 撞唯一约束 → 重试而非 500。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Query, Session, sessionmaker

from app.core.database import Base
from app.models.db_model import CartographyTemplate
from app.models.template_version import TemplateVersion
from app.services.templates import versioning

_TID = "tmpl_cp18_retry"


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'cp18.db'}")
    factory = sessionmaker(bind=engine)
    try:
        Base.metadata.create_all(bind=engine)
        with factory() as db:
            db.add(CartographyTemplate(
                id=_TID, kind="layout", name="cp18", category="layout", keywords=[],
                description="", payload={}, is_builtin=False, version=1, creator_id="u",
            ))
            db.commit()
        yield factory
    finally:
        engine.dispose()


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


def test_cp18_conflict_is_retried(monkeypatch, session_factory):
    with session_factory() as db:
        v1 = versioning.create_version(db, _TID, {"a": 1})
        assert v1.version == 1
        _stale_first(monkeypatch, 1)
        v2 = versioning.create_version(db, _TID, {"a": 2})
        assert v2.version == 2 and v2.parent_version_id is None
        assert versioning.resolve_payload(db, v2)[0] == {"a": 2}


def test_cp18_exhausted_retries_raise_conflict(monkeypatch, session_factory):
    with session_factory() as db:
        versioning.create_version(db, _TID, {"a": 1})
        _stale_first(monkeypatch, 99)
        with pytest.raises(versioning.TemplateVersionConflictError):
            versioning.create_version(db, _TID, {"a": 2})


def test_cp18_route_maps_conflict_to_409():
    from app.api.routes.template_versions import _map_error

    exc = _map_error(versioning.TemplateVersionConflictError("x"))
    assert exc.status_code == 409
