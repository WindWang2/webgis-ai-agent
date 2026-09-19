"""#1417: chaos redis digest-pin + Conversation FK ondelete."""
from pathlib import Path


def test_chaos_redis_image_is_digest_pinned():
    text = Path("tests/integration/chaos/docker-compose.chaos.yml").read_text()
    assert "redis:7-alpine@sha256:" in text
    # unpinned form must not appear as a full image line
    for line in text.splitlines():
        if "image:" in line and "redis:7-alpine" in line:
            assert "@sha256:" in line, line


def test_conversation_user_fk_cascades():
    src = Path("app/models/db_model.py").read_text()
    idx = src.find("class Conversation(Base):")
    window = src[idx: idx + 500]
    assert 'ForeignKey("users.id", ondelete="CASCADE")' in window
