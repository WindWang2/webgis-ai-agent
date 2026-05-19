"""HMAC 签名 helper：sign_path / verify_signature 行为契约。"""
import time
import pytest

from app.core.signing import make_signature, sign_path, verify_signature


def test_round_trip_valid():
    exp, sig = sign_path("foo/bar.png", ttl_seconds=300)
    assert verify_signature("foo/bar.png", exp, sig) is True


def test_wrong_path_fails():
    exp, sig = sign_path("foo/bar.png")
    assert verify_signature("foo/EVIL.png", exp, sig) is False


def test_expired_fails():
    exp = int(time.time()) - 5
    sig = make_signature("foo/bar.png", exp)
    assert verify_signature("foo/bar.png", exp, sig) is False


def test_garbage_inputs():
    assert verify_signature("p", None, "x") is False
    assert verify_signature("p", "abc", "x") is False
    assert verify_signature("p", int(time.time()) + 60, "") is False


def test_ttl_floor():
    """ttl < 60 仍要至少 60s，避免误传 0 立刻过期。"""
    exp, _ = sign_path("x", ttl_seconds=0)
    assert exp >= int(time.time()) + 60


def test_signature_changes_with_path():
    e1, s1 = sign_path("a", ttl_seconds=300)
    e2, s2 = sign_path("b", ttl_seconds=300)
    # 即便 exp 相同（同一秒签的），sig 必然不同
    assert s1 != s2 or e1 != e2
