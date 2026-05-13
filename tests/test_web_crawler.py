"""Tests for app/tools/web_crawler.py — Baidu Qianfan + DuckDuckGo search."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.web_crawler import _baidu_qianfan_search, _ddg_search


def _make_resp(body: dict, status: int = 200):
    """Build a mock aiohttp response that works as an async context manager."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.text = AsyncMock(return_value=json.dumps(body))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(resp):
    """Build a mock aiohttp session whose .post() returns resp as a context manager."""
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    return session


# ─── Baidu Qianfan ────────────────────────────────────────────────────────────

class TestBaiduQianfanSearch:

    @pytest.mark.asyncio
    async def test_missing_token_returns_error(self):
        with patch("app.tools.web_crawler.settings") as s:
            s.BAIDU_QIANFAN_TOKEN = ""
            result = await _baidu_qianfan_search("test query", 5)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_http_error_returns_error(self):
        resp = _make_resp({}, status=401)
        session = _mock_session(resp)

        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.get_shared_client", new_callable=AsyncMock, return_value=session), \
             patch("app.tools.web_crawler.get_ssl_context", return_value=None):
            s.BAIDU_QIANFAN_TOKEN = "bce-v3/test"
            s.HTTPS_PROXY = None
            s.HTTP_PROXY = None
            result = await _baidu_qianfan_search("query", 3)

        assert "error" in result
        assert "401" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_response_maps_references(self):
        body = {
            "request_id": "req-123",
            "references": [
                {
                    "title": "Test Title",
                    "snippet": "Test snippet content",
                    "url": "https://example.com",
                    "date": "2026-01-01",
                    "website": "example.com",
                    "rerank_score": 0.95,
                    "authority_score": 0.8,
                },
                {
                    "title": "Second Result",
                    "content": "Fallback content field",
                    "url": "https://example2.com",
                    "date": "",
                },
            ],
        }
        resp = _make_resp(body)
        session = _mock_session(resp)

        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.get_shared_client", new_callable=AsyncMock, return_value=session), \
             patch("app.tools.web_crawler.get_ssl_context", return_value=None):
            s.BAIDU_QIANFAN_TOKEN = "bce-v3/test"
            s.HTTPS_PROXY = None
            s.HTTP_PROXY = None
            result = await _baidu_qianfan_search("query", 5)

        assert result["type"] == "web_search"
        assert result["provider"] == "baidu_qianfan"
        assert result["count"] == 2
        assert result["request_id"] == "req-123"
        first = result["data"][0]
        assert first["title"] == "Test Title"
        assert first["snippet"] == "Test snippet content"
        assert first["link"] == "https://example.com"
        assert first["rerank_score"] == 0.95
        assert result["data"][1]["snippet"] == "Fallback content field"

    @pytest.mark.asyncio
    async def test_limit_respected(self):
        refs = [{"title": f"R{i}", "snippet": f"s{i}", "url": f"https://ex.com/{i}"} for i in range(10)]
        body = {"references": refs, "request_id": "r"}
        resp = _make_resp(body)
        session = _mock_session(resp)

        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.get_shared_client", new_callable=AsyncMock, return_value=session), \
             patch("app.tools.web_crawler.get_ssl_context", return_value=None):
            s.BAIDU_QIANFAN_TOKEN = "bce-v3/test"
            s.HTTPS_PROXY = None
            s.HTTP_PROXY = None
            result = await _baidu_qianfan_search("query", 3)

        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_empty_references_returns_zero_count(self):
        body = {"references": [], "request_id": "r"}
        resp = _make_resp(body)
        session = _mock_session(resp)

        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.get_shared_client", new_callable=AsyncMock, return_value=session), \
             patch("app.tools.web_crawler.get_ssl_context", return_value=None):
            s.BAIDU_QIANFAN_TOKEN = "bce-v3/test"
            s.HTTPS_PROXY = None
            s.HTTP_PROXY = None
            result = await _baidu_qianfan_search("query", 5)

        assert result["count"] == 0
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_network_error_returns_error(self):
        import aiohttp
        session = MagicMock()
        session.post = MagicMock(side_effect=aiohttp.ClientError("connection refused"))

        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.get_shared_client", new_callable=AsyncMock, return_value=session), \
             patch("app.tools.web_crawler.get_ssl_context", return_value=None):
            s.BAIDU_QIANFAN_TOKEN = "bce-v3/test"
            s.HTTPS_PROXY = None
            s.HTTP_PROXY = None
            result = await _baidu_qianfan_search("query", 5)

        assert "error" in result
        assert "Qianfan" in result["error"]


# ─── DuckDuckGo ───────────────────────────────────────────────────────────────

class TestDdgSearch:
    def test_missing_ddgs_library_returns_error(self):
        with patch("app.tools.web_crawler.DDGS", None):
            result = _ddg_search("test", 5)
        assert "error" in result
        assert "未安装" in result["error"]

    def test_successful_ddg_search(self):
        mock_results = [
            {"title": "T1", "body": "S1", "href": "https://a.com"},
            {"title": "T2", "body": "S2", "href": "https://b.com"},
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text = MagicMock(return_value=iter(mock_results))

        with patch("app.tools.web_crawler.DDGS", return_value=mock_ddgs):
            result = _ddg_search("test query", 5)

        assert result["provider"] == "duckduckgo"
        assert result["count"] == 2
        assert result["data"][0]["title"] == "T1"
        assert result["data"][0]["snippet"] == "S1"
        assert result["data"][0]["link"] == "https://a.com"

    def test_ddg_connection_error_returns_error(self):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text = MagicMock(side_effect=ConnectionError("timeout"))

        with patch("app.tools.web_crawler.DDGS", return_value=mock_ddgs):
            result = _ddg_search("test", 5)

        assert "error" in result
        assert "DuckDuckGo" in result["error"]


# ─── Registered tools (web_search + search_and_extract_poi) ───────────────────

class TestWebSearchTool:
    @pytest.fixture
    def registry(self):
        from app.tools.registry import ToolRegistry
        from app.tools.web_crawler import register_crawler_tools
        r = ToolRegistry()
        register_crawler_tools(r)
        return r

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self, registry):
        result = await registry.dispatch("web_search", {"query": "   "})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_provider_returns_error(self, registry):
        result = await registry.dispatch("web_search", {"query": "test", "provider": "unknown"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_auto_provider_picks_baidu_when_token_set(self, registry):
        resp = _make_resp({"references": [], "request_id": "r"})
        session = _mock_session(resp)
        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.get_shared_client", new_callable=AsyncMock, return_value=session), \
             patch("app.tools.web_crawler.get_ssl_context", return_value=None):
            s.BAIDU_QIANFAN_TOKEN = "bce-v3/tok"
            s.HTTPS_PROXY = None
            s.HTTP_PROXY = None
            result = await registry.dispatch("web_search", {"query": "test", "provider": "auto"})
        assert result.get("provider") == "baidu_qianfan"

    @pytest.mark.asyncio
    async def test_auto_provider_picks_ddg_when_no_token(self, registry):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text = MagicMock(return_value=iter([]))
        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.DDGS", return_value=mock_ddgs):
            s.BAIDU_QIANFAN_TOKEN = ""
            result = await registry.dispatch("web_search", {"query": "test", "provider": "auto"})
        assert result.get("provider") == "duckduckgo"

    @pytest.mark.asyncio
    async def test_search_and_extract_poi_wraps_result(self, registry):
        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler._ddg_search") as mock_ddg:
            s.BAIDU_QIANFAN_TOKEN = ""
            mock_ddg.return_value = {
                "type": "web_search",
                "provider": "duckduckgo",
                "query": "test",
                "count": 1,
                "data": [{"title": "T", "snippet": "S", "link": "https://x.com"}],
            }
            result = await registry.dispatch("search_and_extract_poi", {"query": "test", "limit": 5})

        assert result["type"] == "poi_web_search"
        assert "message" in result
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_search_and_extract_poi_error_passthrough(self, registry):
        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler._ddg_search") as mock_ddg:
            s.BAIDU_QIANFAN_TOKEN = ""
            mock_ddg.return_value = {"error": "DDG failed"}
            result = await registry.dispatch("search_and_extract_poi", {"query": "test", "limit": 5})

        assert result["type"] == "poi_web_search"
        assert "error" in result
        assert result["geojson"]["features"] == []
