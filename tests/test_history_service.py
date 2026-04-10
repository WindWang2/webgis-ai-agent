# tests/test_history_service.py
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime
from app.services.history_service import HistoryService


def make_service():
    db = MagicMock()
    return HistoryService(db), db


def test_get_or_create_conversation_creates_new():
    svc, db = make_service()
    db.get.return_value = None
    db.query.return_value.count.return_value = 0

    conv = svc.get_or_create_conversation("sess-1")

    db.add.assert_called_once()
    db.commit.assert_called()
    assert conv.id == "sess-1"


def test_get_or_create_conversation_returns_existing():
    from app.models.db_models import Conversation
    svc, db = make_service()
    existing = Conversation(id="sess-1", title="Old", created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.get.return_value = existing

    conv = svc.get_or_create_conversation("sess-1")

    db.add.assert_not_called()
    assert conv.id == "sess-1"


def test_save_message():
    svc, db = make_service()
    mock_conv = MagicMock()
    db.get.return_value = mock_conv
    svc.save_message("sess-1", "user", "hello")
    db.add.assert_called_once()
    db.commit.assert_called_once()
    # Verify updated_at was set on the conversation
    assert mock_conv.updated_at is not None


def test_list_sessions():
    svc, db = make_service()
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    result = svc.list_sessions()
    assert result == []


def test_delete_session():
    from app.models.db_models import Conversation
    svc, db = make_service()
    conv = MagicMock(spec=Conversation)
    db.get.return_value = conv
    svc.delete_session("sess-1")
    db.delete.assert_called_once_with(conv)
    db.commit.assert_called_once()


def test_delete_session_not_found():
    svc, db = make_service()
    db.get.return_value = None
    # Should not raise
    svc.delete_session("nonexistent")
    db.delete.assert_not_called()


def test_enforce_cap_deletes_oldest():
    from app.models.db_models import Conversation
    svc, db = make_service()
    db.query.return_value.count.return_value = 1002
    old1 = MagicMock(spec=Conversation, id="old1")
    old2 = MagicMock(spec=Conversation, id="old2")
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = [old1, old2]

    svc._enforce_cap()

    assert db.delete.call_count == 2
    db.commit.assert_called_once()
