"""Web Crawler - 盲区智能爬行者 (Sub-Agent)"""
import logging
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None

logger = logging.getLogger(__name__)

class QueryWebCrawlerArgs(BaseModel):
    query: str = Field(..., description="搜索关键词，例如：成都市高新南区星巴克地址列表")
    limit: int = Field(5, description="期望提取的结果数量，范围1-20")

def register_crawler_tools(registry: ToolRegistry):
    """注册网络爬虫探测工具"""
    
    @tool(registry, name="search_and_extract_poi",
           description="（Sub-Agent 盲区探测器）当基础查询无法获取商业地点、新闻事件或最新 POI 数据时，通过公网搜索引擎爬取非结构化文本内容，交由大语言模型进一步提取为含有真实信息的地理要素集。",
           args_model=QueryWebCrawlerArgs)
    async def search_and_extract_poi(query: str, limit: int = 5) -> dict:
        if DDGS is None:
            return {"type": "poi_web_search", "query": query, "geojson": {"type": "FeatureCollection", "features": []}, "error": "Search library not installed."}
        
        logger.info(f"[Crawler] Sub-Agent executing web search for: {query}")
        try:
            results = []
            with DDGS() as ddgs:
                # 默认搜中文结果并且设置地域
                search_params = {"keywords": query, "region": "cn-zh", "max_results": limit}
                for r in ddgs.text(**search_params):
                    results.append({
                        "title": r.get('title', ''),
                        "snippet": r.get('body', ''),
                        "link": r.get('href', '')
                    })
            
            # 返回提取的搜索摘要文本集
            # 我们不在这里直接转成 GeoJSON，而是将爬取结果返回给大模型
            # 依靠 LLM 自身的抽取能力将这些 snippet 再去调用具体的标定工具，
            # 或者是直接当作 "Observation" 进行总结。
            # 这里按照要求返回非结构化转置格式。
            return {
                "type": "poi_web_search",
                "query": query,
                "count": len(results),
                "data": results,
                # 注意：如果大模型在这次调用中只是得到了文字，它会在下一次循环中使用 geocode 等工具将其转为点
                "message": "Web search returned text results. Please extract locations and use `geocoding` or similar tools to get exact coordinates if needed, or answer the user directly based on the snippets."
            }
        except Exception as e:
            logger.error(f"[Crawler] Error during web search: {str(e)}")
            return {"type": "poi_web_search", "error": str(e), "data": []}
