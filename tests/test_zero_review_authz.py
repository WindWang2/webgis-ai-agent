"""Shipped-path tests for zero-based-review authz / token / actor-id fixes."""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.auth import actor_ids, authorize_session_write
from app.core.database import Base
from app.models.db_model import Organization, User
from app.services.project_service import ProjectService


def test_actor_ids_reads_user_id_not_id():
    assert actor_ids({"user_id": "alice", "role": "viewer"}) == ("alice", None)
    assert actor_ids({"id": "legacy", "org_id": 3}) == ("legacy", 3)
    assert actor_ids({"user_id": "anonymous"}) == (None, None)
    assert actor_ids(None) == (None, None)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    org = Organization(id=1, name="org", slug="org")
    session.add(org)
    session.add(User(id="alice", org_id=1, username="alice", email="a@x", role="editor"))
    session.add(User(id="bob", org_id=1, username="bob", email="b@x", role="editor"))
    session.commit()
    yield session
    session.close()


def test_project_owner_cannot_be_read_by_other_user(db):
    alice_proj = ProjectService.create_project(db, name="Alice only", owner_id="alice")
    visible = ProjectService.get_project_with_auth(db, alice_proj.id, user_id="alice")
    hidden = ProjectService.get_project_with_auth(db, alice_proj.id, user_id="bob")
    anon = ProjectService.get_project_with_auth(db, alice_proj.id, user_id=None)
    assert visible is not None
    assert hidden is None
    assert anon is None


def test_anonymous_list_does_not_include_owned_projects(db):
    ProjectService.create_project(db, name="owned", owner_id="alice")
    ProjectService.create_project(db, name="public", owner_id=None)
    rows, _total = ProjectService.list_projects(db, user_id=None, org_id=None)
    names = {r.name for r in rows}
    assert "public" in names
    assert "owned" not in names


def test_require_tenant_owned_blocks_other_owner_without_org_claim():
    from app.api.routes.data_fabric import _require_tenant_owned

    src = MagicMock()
    src.org_id = None
    src.owner_id = "alice"
    with pytest.raises(HTTPException) as ei:
        _require_tenant_owned(src, {"user_id": "bob"})
    assert ei.value.status_code == 404
    assert _require_tenant_owned(src, {"user_id": "alice"}) is src


def test_require_existing_session_owner_blocks_foreign_conversation(db):
    from app.api.routes.data_fabric import _require_existing_session_owner
    from app.models.db_model import Conversation

    conv = Conversation(id="sess-alice", title="t", user_id="alice")
    db.add(conv)
    db.commit()
    _require_existing_session_owner(db, "no-such-session", {"user_id": "bob"}, None)
    with pytest.raises(HTTPException) as ei:
        _require_existing_session_owner(db, "sess-alice", {"user_id": "bob"}, None)
    assert ei.value.status_code == 404
    _require_existing_session_owner(db, "sess-alice", {"user_id": "alice"}, None)

    # Grandfather anonymous (no owner_token): session_id is capability.
    anon = Conversation(id="sess-anon", title="t", user_id=None, owner_token=None)
    db.add(anon)
    db.commit()
    _require_existing_session_owner(db, "sess-anon", {"user_id": "bob"}, None)

    # SEC-08 anonymous: token required. Bob without the header is a write-IDOR.
    sec08 = Conversation(id="sess-sec08", title="t", user_id=None, owner_token="tok-secret")
    db.add(sec08)
    db.commit()
    with pytest.raises(HTTPException) as ei2:
        _require_existing_session_owner(db, "sess-sec08", {"user_id": "bob"}, None)
    assert ei2.value.status_code == 404
    _require_existing_session_owner(db, "sess-sec08", {"user_id": "bob"}, "tok-secret")


def test_authorize_session_write_matches_get_session_contract():
    class _C:
        def __init__(self, user_id=None, owner_token=None):
            self.user_id = user_id
            self.owner_token = owner_token

    assert authorize_session_write(None, "bob", None) is True
    assert authorize_session_write(_C("alice"), "alice", None) is True
    assert authorize_session_write(_C("alice"), "bob", None) is False
    assert authorize_session_write(_C("alice"), None, None) is False
    assert authorize_session_write(_C(None, None), "bob", None) is True
    assert authorize_session_write(_C(None, "tok"), "bob", None) is False
    assert authorize_session_write(_C(None, "tok"), "bob", "wrong") is False
    assert authorize_session_write(_C(None, "tok"), "bob", "tok") is True
    assert authorize_session_write(_C(None, "tok"), None, "tok") is True


def test_owner_token_compare_digest_mismatch():
    from app.services.session_data_protocol import BaseSessionStore, SessionRefDataResult

    store = BaseSessionStore()
    denied = store._validate_owner_token({"owner_token": "secret-token"}, "wrong-token")
    assert isinstance(denied, SessionRefDataResult)
    assert denied.error_type == "PermissionDenied"
    assert store._validate_owner_token({"owner_token": "secret-token"}, "secret-token") is None
