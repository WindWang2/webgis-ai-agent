"""出网（egress）守卫策略单测 — ADR-0197。

纯函数决策面：不真连网络、不依赖 Settings 单例（显式传参）。
接线层（aiohttp/httpx/requests）各自的测试见 test_egress_wiring_*。
"""
import ipaddress

import pytest

from app.core.egress import (
    AirGappedEgressError,
    EgressDeniedReason,
    EgressPolicy,
    evaluate_egress,
)


def _policy(mode="allowlist", profile="air_gapped", allow="",
            allow_private=True) -> EgressPolicy:
    return EgressPolicy.from_params(
        profile=profile,
        mode=mode,
        allow_raw=allow,
        allow_private=allow_private,
    )


# ── cloud 模式：全放行（Oracle：profile 关闭时行为不变）───────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://api.stepfun.com/step_plan/v1/chat/completions",
        "http://nominatim.openstreetmap.org/search?q=x",
        "https://192.168.1.10/tiles/0/0/0.png",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
def test_unrestricted_mode_allows_everything(url):
    decision = evaluate_egress(url, policy=_policy(mode="unrestricted"))
    assert decision.allowed
    assert decision.reason == ""


def test_cloud_profile_forces_unrestricted_semantics():
    """cloud profile 下即使误配 allowlist 模式，决策面也按调用参数执行；
    强制约束（air_gapped 才能配 allowlist？否——allowlist 也可独立用）
    属于 Settings validator 的职责，这里只验证决策面纯度。"""
    decision = evaluate_egress(
        "https://example.com/x", policy=_policy(mode="allowlist", profile="cloud")
    )
    assert not decision.allowed


# ── allowlist 模式：deny-by-default ─────────────────────────────────


def test_public_host_denied_by_default():
    decision = evaluate_egress(
        "https://api.stepfun.com/v1/chat/completions",
        policy=_policy(),
        dependency_id="llm_chat",
    )
    assert not decision.allowed
    assert decision.host == "api.stepfun.com"
    assert decision.reason == EgressDeniedReason.NOT_ALLOWLISTED
    assert decision.dependency_id == "llm_chat"


def test_private_loopback_literal_ip_allowed():
    for host in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.9",
                 "172.31.255.1", "169.254.1.1"):
        decision = evaluate_egress(f"http://{host}:8000/v1", policy=_policy())
        assert decision.allowed, host
    # IPv6 字面地址在 URL 里必须带方括号。
    assert evaluate_egress("http://[::1]:8000/v1", policy=_policy()).allowed
    assert evaluate_egress("http://[fd00::5]:8000/v1", policy=_policy()).allowed


@pytest.mark.parametrize("host", ["localhost", "LLM.internal", "tiles.local"])
def test_private_hostname_forms_allowed(host):
    decision = evaluate_egress(f"http://{host}:9999", policy=_policy())
    assert decision.allowed


@pytest.mark.parametrize("host", ["example.com", "api.stepfun.com", "8.8.8.8"])
def test_literal_global_ip_denied(host):
    """公网字面 IP 不在私网豁免内。"""
    if _is_ip(host):
        decision = evaluate_egress(f"http://{host}/", policy=_policy())
        assert not decision.allowed


def test_cloud_metadata_endpoints_denied_even_with_private_allowed():
    """169.254.169.254 链路本地可达 ≠ 可出网目标：元数据凭证外泄通道，
    allow_private=True 也不豁免（与 data_fabric BLOCKED_IPS_EXPLICIT 同清单）。"""
    for url in ("http://169.254.169.254/latest/meta-data/",
                "http://[fd00:ec2::254]/"):
        decision = evaluate_egress(url, policy=_policy())
        assert not decision.allowed
        assert decision.reason == EgressDeniedReason.METADATA_BLOCKED


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def test_global_literal_ip_denied_even_when_hostname_form():
    decision = evaluate_egress("http://8.8.8.8/dns-query", policy=_policy())
    assert not decision.allowed
    assert decision.reason == EgressDeniedReason.NOT_ALLOWLISTED


def test_explicit_allowlist_host_allowed():
    policy = _policy(allow="tiles.intranet.example, LLM.corp.cn")
    assert evaluate_egress("https://tiles.intranet.example/v1/t.png",
                           policy=policy).allowed
    assert evaluate_egress("http://llm.corp.cn:8000/v1", policy=policy).allowed


def test_allowlist_wildcard_suffix():
    policy = _policy(allow="*.internal.example")
    assert evaluate_egress("https://a.internal.example/x", policy=policy).allowed
    assert evaluate_egress("https://a.b.internal.example/x", policy=policy).allowed
    assert not evaluate_egress("https://internal.example/x", policy=policy).allowed
    assert not evaluate_egress("https://evil-internal.example/x", policy=policy).allowed


def test_allowlist_match_is_exact_host_not_suffix():
    policy = _policy(allow="tiles.intranet.example")
    assert not evaluate_egress("https://evil.tiles.intranet.example.attacker.io/x",
                               policy=policy).allowed


def test_allow_private_false_blocks_private_too():
    policy = _policy(allow_private=False)
    assert not evaluate_egress("http://127.0.0.1:8000/", policy=policy).allowed
    assert not evaluate_egress("http://10.0.0.5/", policy=policy).allowed
    # allowlist 命中仍放行（显式允许 > 私网开关）
    strict = _policy(allow="10.0.0.5", allow_private=False)
    assert evaluate_egress("http://10.0.0.5/", policy=strict).allowed


def test_host_case_and_ipv6_brackets_normalized():
    policy = _policy(allow="Tiles.Intranet.Example")
    assert evaluate_egress("https://tiles.intranet.example/x", policy=policy).allowed
    decision = evaluate_egress("http://[::1]:8000/", policy=_policy())
    assert decision.allowed
    assert decision.host == "::1"


# ── 输入边界 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    ["", "not-a-url", "http:///path", "   "],
)
def test_unparseable_url_denied_fail_closed(url):
    decision = evaluate_egress(url, policy=_policy())
    assert not decision.allowed
    assert decision.reason == EgressDeniedReason.INVALID_URL


def test_non_http_scheme_passes_through():
    """file:/ref:/data: 等不构成 HTTP 出网面——守卫放行（SSRF 由各层自把关）。"""
    for url in ("file:///tmp/x.geojson", "ref:geojson-abc123"):
        assert evaluate_egress(url, policy=_policy()).allowed


def test_ws_schemes_are_gated():
    assert not evaluate_egress("wss://external.example/ws", policy=_policy()).allowed
    assert evaluate_egress("ws://127.0.0.1:8000/ws", policy=_policy()).allowed


# ── typed 错误 ──────────────────────────────────────────────────────


def test_assert_raises_typed_error_with_evidence():
    policy = _policy()
    with pytest.raises(AirGappedEgressError) as excinfo:
        policy.assert_allowed("https://api.stepfun.com/v1",
                              dependency_id="llm_chat")
    err = excinfo.value
    assert err.host == "api.stepfun.com"
    assert err.reason == EgressDeniedReason.NOT_ALLOWLISTED
    assert err.dependency_id == "llm_chat"
    assert "api.stepfun.com" in str(err)
    assert "air_gapped" in str(err) or "allowlist" in str(err)


def test_assert_allowed_passes_silently_for_private():
    policy = _policy()
    policy.assert_allowed("http://127.0.0.1:18000/v1/models")  # 不抛


def test_error_is_runtime_error_subclass():
    assert issubclass(AirGappedEgressError, RuntimeError)


# ── 健壮性：decision 可序列化（观测/evidence 面）─────────────────────


def test_decision_as_dict_roundtrip():
    decision = evaluate_egress("https://api.stepfun.com/v1", policy=_policy(),
                               dependency_id="llm_chat")
    payload = decision.as_dict()
    assert payload["allowed"] is False
    assert payload["host"] == "api.stepfun.com"
    assert payload["reason"] == "not_allowlisted"
    assert payload["dependency_id"] == "llm_chat"
    assert payload["mode"] == "allowlist"
