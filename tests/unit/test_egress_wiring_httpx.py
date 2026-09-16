"""httpx 接缝的 egress 守卫接线测试（ADR-0197）。

覆盖 LLM 连接池（LLMHttpClientRegistry）与 guarded_async_client/guarded_client
统一入口。hook 在连接前触发——公网 host 不会发出真实连接。
"""
import httpx
import pytest

from app.core.config import settings
from app.core.egress import (
    AirGappedEgressError,
    guarded_async_client,
    guarded_client,
    reset_policy_cache,
)
from app.services.chat.llm_client import LLMHttpClientRegistry


@pytest.fixture(autouse=True)
def _clean_policy_cache_and_proxies(monkeypatch):
    """清空策略缓存 + 剥离环境代理：conftest 基线有意不钉代理键
    （钉扎会改变 httpx/requests 语义），但开发 shell 的真实代理会让
    "loopback 必连接失败"的前堤失效——这些测试只关心守卫与连接层，
    不关心代理路径。"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    reset_policy_cache()
    yield
    reset_policy_cache()


def _set_allowlist(monkeypatch, allow="", allow_private="true"):
    monkeypatch.setattr(settings, "DEPLOYMENT_PROFILE", "air_gapped")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW", allow)
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW_PRIVATE", allow_private)


def _set_unrestricted(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_PROFILE", "cloud")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "unrestricted")


@pytest.mark.asyncio
async def test_llm_pool_denies_public_base_url_before_connect(monkeypatch):
    _set_allowlist(monkeypatch)
    registry = LLMHttpClientRegistry()
    client = await registry.acquire("https://api.stepfun.com/step_plan/v1")
    try:
        with pytest.raises(AirGappedEgressError) as excinfo:
            await client.post(
                "https://api.stepfun.com/step_plan/v1/chat/completions",
                json={"model": "x", "messages": []},
            )
        assert excinfo.value.dependency_id == "llm_chat"
    finally:
        await registry.aclose_all()


@pytest.mark.asyncio
async def test_llm_pool_allows_private_llm_endpoint(monkeypatch):
    """私网放行路径：真实回环服务 200 往返（LLM 池 client 完整可用）。"""
    from tests.data.egress_fixtures import loopback_http_server

    _set_allowlist(monkeypatch)
    with loopback_http_server() as base:
        registry = LLMHttpClientRegistry()
        client = await registry.acquire(f"{base}/v1")
        try:
            resp = await client.get(f"{base}/v1/models")
            assert resp.status_code == 200
        finally:
            await registry.aclose_all()


@pytest.mark.asyncio
async def test_llm_pool_unrestricted_has_no_hook(monkeypatch):
    _set_unrestricted(monkeypatch)
    registry = LLMHttpClientRegistry()
    client = await registry.acquire("https://api.stepfun.com/v1")
    try:
        # cloud 默认：client 未挂 event hook（零行为变化）。
        assert not client.event_hooks.get("request")
    finally:
        await registry.aclose_all()


@pytest.mark.asyncio
async def test_guarded_async_client_denies_and_preserves_caller_hooks(monkeypatch):
    _set_allowlist(monkeypatch)
    seen = []

    async def caller_hook(request):
        seen.append(str(request.url))

    client = guarded_async_client(
        dependency_id="visual_judge", event_hooks={"request": [caller_hook]}
    )
    try:
        with pytest.raises(AirGappedEgressError):
            await client.get("https://example.com/v1/x")
        assert seen == []  # 守卫在调用方 hook 之前（挂载顺序：守卫最后）
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_guarded_async_client_allows_private(monkeypatch):
    from tests.data.egress_fixtures import loopback_http_server

    _set_allowlist(monkeypatch)
    with loopback_http_server() as base:
        client = guarded_async_client(dependency_id="visual_judge")
        try:
            resp = await client.get(f"{base}/ok")
            assert resp.status_code == 200
        finally:
            await client.aclose()


def test_guarded_sync_client_denies_public(monkeypatch):
    _set_allowlist(monkeypatch)
    client = guarded_client(dependency_id="district-fallback")
    try:
        with pytest.raises(AirGappedEgressError):
            client.get("https://restapi.amap.com/v3/config/district")
    finally:
        client.close()
