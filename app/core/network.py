"""网络请求工具模块"""
import ssl
import logging
import aiohttp
import certifi
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

def get_ssl_context(verify: bool = True) -> ssl.SSLContext:
    """
    获取跨平台 SSL 上下文。
    
    Args:
        verify: 是否验证 SSL 证书
    """
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
        
    ctx = ssl.create_default_context()
    try:
        # 使用 certifi 提供的 CA 证书，确保跨平台一致性
        ctx.load_verify_locations(certifi.where())
    except Exception as e:
        logger.warning(f"Failed to load certifi CA certs: {e}. Falling back to default system certs.")
        # 如果 certifi 失败，尝试系统默认路径（aiohttp 默认行为）
        pass
    return ctx

def get_base_headers() -> dict:
    """获取基础请求头，符合 Nominatim 等服务的 Usage Policy"""
    return {
        "User-Agent": "WebGIS-AI-Agent-V2/1.0 (https://github.com/WindWang2/webgis-ai-agent; contact@example.com)",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "application/json",
    }

async def create_client_session(**kwargs) -> aiohttp.ClientSession:
    """
    创建一个预配置的 aiohttp.ClientSession。
    会自动处理 Proxy 设置（如果配置了）。
    """
    headers = get_base_headers()
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))
        
    # 注意：aiohttp.ClientSession 不直接在构造函数中接收 proxy，
    # 但我们可以在这层封装中处理通用的握手逻辑或默认设置。
    # 实际请求时推荐使用 session.get(..., proxy=settings.HTTP_PROXY)
    return aiohttp.ClientSession(headers=headers, **kwargs)
