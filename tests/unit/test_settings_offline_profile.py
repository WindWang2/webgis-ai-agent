"""部署 profile 组合校验（ADR-0197）—— Settings validator 语义。

直接构造 Settings（绕过 .env / os.environ），只测校验面：
- cloud 默认 = 行为不变（Master 兼容性 Oracle）；
- air_gapped 强制 allowlist；矛盾组合 fail-fast；
- air_gapped 下 LLM endpoint 必须过 egress 决策（私网/allowlist）。
"""
import pytest

from app.core.config import Settings


def _settings(**kw) -> Settings:
    base = dict(
        JWT_SECRET_KEY="test-secret",
        LLM_API_KEY="sk-test",
        DATABASE_URL="sqlite:///./data/webgis.db",
    )
    base.update(kw)
    return Settings(**base)


def test_cloud_default_is_valid_and_unchanged():
    s = _settings()
    assert s.DEPLOYMENT_PROFILE == "cloud"
    assert s.NETWORK_EGRESS_MODE == "unrestricted"
    assert s.NETWORK_EGRESS_ALLOW == ""
    assert s.NETWORK_EGRESS_ALLOW_PRIVATE is True


def test_air_gapped_without_allowlist_mode_rejected():
    with pytest.raises(RuntimeError, match="allowlist"):
        _settings(DEPLOYMENT_PROFILE="air_gapped",
                  NETWORK_EGRESS_MODE="unrestricted")


def test_air_gapped_with_public_llm_endpoint_rejected():
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        _settings(DEPLOYMENT_PROFILE="air_gapped",
                  NETWORK_EGRESS_MODE="allowlist")


def test_air_gapped_with_private_llm_endpoint_accepted():
    s = _settings(DEPLOYMENT_PROFILE="air_gapped",
                  NETWORK_EGRESS_MODE="allowlist",
                  LLM_BASE_URL="http://127.0.0.1:11434/v1")
    assert s.DEPLOYMENT_PROFILE == "air_gapped"


def test_air_gapped_public_llm_saved_by_explicit_allowlist():
    s = _settings(DEPLOYMENT_PROFILE="air_gapped",
                  NETWORK_EGRESS_MODE="allowlist",
                  NETWORK_EGRESS_ALLOW="api.stepfun.com")
    assert s.NETWORK_EGRESS_ALLOW == "api.stepfun.com"


def test_cloud_with_allowlist_mode_is_legal_half_offline():
    """cloud + allowlist = 半离线内网场景（守卫独立于 profile 可用）。"""
    s = _settings(NETWORK_EGRESS_MODE="allowlist")
    assert s.NETWORK_EGRESS_MODE == "allowlist"


@pytest.mark.parametrize("bad", ["sovereign", "offline", ""])
def test_unknown_profile_value_rejected(bad):
    with pytest.raises(ValueError, match="DEPLOYMENT_PROFILE"):
        _settings(DEPLOYMENT_PROFILE=bad)


@pytest.mark.parametrize("bad", ["deny", "allow", ""])
def test_unknown_egress_mode_value_rejected(bad):
    with pytest.raises(ValueError, match="NETWORK_EGRESS_MODE"):
        _settings(NETWORK_EGRESS_MODE=bad)


def test_case_is_normalized_for_values():
    s = _settings(DEPLOYMENT_PROFILE=" air_gapped ",
                  NETWORK_EGRESS_MODE=" Allowlist ",
                  LLM_BASE_URL="http://127.0.0.1:11434/v1")
    assert s.DEPLOYMENT_PROFILE == "air_gapped"
    assert s.NETWORK_EGRESS_MODE == "allowlist"
