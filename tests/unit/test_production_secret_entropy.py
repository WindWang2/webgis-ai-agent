"""issue #1673（SWARM deep review SEC-01）：生产密钥强度下限回归。

修复前 `_ensure_jwt_secret` 生产分支只拦空值与 `change_me` 占位符，
`JWT_SECRET_KEY='jwt'`（3 字符）/ `METRICS_TOKEN='tok123'` 可静默通过启动
校验 —— HS256 密钥 3 字符等于允许任何人离线爆破后伪造 admin JWT
（`get_current_user_with_version` 只验签 + DB token_version，不感知密钥强度）。

本文件钉死：
  - 生产 + 弱 JWT（<32 字符）→ RuntimeError；
  - 生产 + 强 JWT 放行；弱 METRICS_TOKEN（<24）→ RuntimeError；
  - 生产 + 未设 METRICS_TOKEN 不拦（/metrics 端点自身 fail-closed 401）；
  - dev 模式弱/空 JWT 仍随机生成 + warning（既有便利语义不回归）。
"""
import warnings as _warnings

import pytest

from app.core.config import Settings


def _prod_kwargs(**over):
    """生产环境最小合法配置基线（对齐 tests/test_config.py 既有用例）。

    OVERPASS/NOMINATIM 用公网 IP 字面量：绕开 `_validate_external_urls`
    的 DNS 解析，保证测试确定性。
    """
    kwargs = dict(
        _env_file=None,
        ENV="production",
        JWT_SECRET_KEY="x" * 32,
        LLM_API_KEY="sk-test-key-for-entropy-test",
        DATABASE_URL="postgresql://user:pass@localhost/db",
        OVERPASS_API_URL="https://8.8.8.8/api/interpreter",
        NOMINATIM_URL="https://1.1.1.1/search",
    )
    kwargs.update(over)
    return kwargs


@pytest.mark.parametrize("weak", ["jwt", "db", "tok123", "x" * 31])
def test_production_rejects_low_entropy_jwt_secret(weak):
    """issue #1673：JWT_SECRET_KEY < 32 字符的生产启动必须 fail-fast。"""
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be >= 32"):
        Settings(**_prod_kwargs(JWT_SECRET_KEY=weak))


def test_production_rejects_short_jwt_via_env(monkeypatch):
    """issue #1673 原文复现路径：env 注入弱密钥后 Settings() 必须炸。"""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt")
    monkeypatch.setenv("METRICS_TOKEN", "tok123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("LLM_API_KEY", "x")
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be >= 32"):
        Settings(_env_file=None)


def test_production_accepts_strong_jwt_secret():
    s = Settings(**_prod_kwargs())
    assert s.JWT_SECRET_KEY == "x" * 32


def test_production_rejects_placeholder_jwt_secret():
    """既有守卫不回归：长度足够的 change_me_* 占位符仍被拒。"""
    placeholder = "change_me_to_a_very_strong_random_secret_key_at_least_32_chars"
    assert len(placeholder) >= 32
    with pytest.raises(RuntimeError, match="placeholder"):
        Settings(**_prod_kwargs(JWT_SECRET_KEY=placeholder))


def test_production_rejects_short_metrics_token():
    """/metrics 门禁的强度等于 token 强度：METRICS_TOKEN < 24 拒绝启动。"""
    with pytest.raises(RuntimeError, match="METRICS_TOKEN must be >= 24"):
        Settings(**_prod_kwargs(METRICS_TOKEN="tok123"))


def test_production_accepts_strong_metrics_token():
    s = Settings(**_prod_kwargs(METRICS_TOKEN="m" * 24))
    assert s.METRICS_TOKEN == "m" * 24


def test_production_allows_unset_metrics_token():
    """空 METRICS_TOKEN = /metrics fail-closed 401，不拦启动。"""
    s = Settings(**_prod_kwargs(METRICS_TOKEN=""))
    assert s.METRICS_TOKEN == ""


def test_dev_mode_weak_jwt_secret_does_not_fail():
    """dev：空/弱 JWT 不 fail-fast —— 随机生成 + warning（便利语义保留）。"""
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        s = Settings(_env_file=None, ENV="development", JWT_SECRET_KEY="")
    assert len(s.JWT_SECRET_KEY) >= 32
    assert any("JWT_SECRET_KEY" in str(w.message) for w in caught)


def test_dev_mode_short_metrics_token_does_not_fail():
    """dev：弱 METRICS_TOKEN 仅本地门禁，不按生产熵闸拦截。"""
    s = Settings(_env_file=None, ENV="development", METRICS_TOKEN="tok123")
    assert s.METRICS_TOKEN == "tok123"
