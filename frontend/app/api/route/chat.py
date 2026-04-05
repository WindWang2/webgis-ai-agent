"""
AI 对话 API 路由
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, AsyncGenerator
import asyncio
import json

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    attachments: Optional[list] = None


class ChatResponse(BaseModel):
    message: str
    analysis_type: Optional[str] = None
    data: Optional[dict] = None


async def generate_response(message: str) -> AsyncGenerator[str, None]:
    """
    流式生成 AI 回复
    """
    # 模拟 AI 分析过程
    analysis_prompt = ""
    
    if any(kw in message for kw in ["附近", "周边", "距离", "米", "公里"]):
        analysis_prompt = "正在进行邻近分析"
    elif any(kw in message for kw in ["人口", "密度", "分布"]):
        analysis_prompt = "正在分析人口数据分布"
    elif any(kw in message for kw in ["公园", "绿地", "绿化"]):
        analysis_prompt = "正在查找绿地设施"
    elif any(kw in message for kw in ["交通", "道路", "拥堵"]):
        analysis_prompt = "正在进行交通分析"
    else:
        analysis_prompt = "正在解析您的请求"
    
    # Yield thinking progress
    for word in f"收到指令：「{message}」。{analysis_prompt}，请稍候...":
        yield word
        await asyncio.sleep(0.02)
    
    # Yield final response
    response_text = "\n\n✅ 已识别分析意图，正在进行处理..."
    for word in response_text:
        yield word
        await asyncio.sleep(0.01)


@router.post("")
async def chat(request: ChatRequest):
    """
    处理 AI 对话请求，返回流式响应
    """
    async def stream_generator():
        async for chunk in generate_response(request.message):
            yield chunk
    
    return stream_generator()


@router.post("/complete")
async def chat_complete(request: ChatRequest):
    """
    处理 AI 对话请求返回完整响应（非流式）
    """
    message = request.message
    
    # Simple intent detection
    analysis_type = "general"
    data = {}
    
    if any(kw in message for kw in ["附近", "周边", "距离"]):
        analysis_type = "proximity"
        data = {"type": "buffer", "distance": 5000}
    elif any(kw in message for kw in ["人口", "分布"]):
        analysis_type = "population"
    elif any(kw in message for kw in ["公园", "绿地"]):
        analysis_type = "poi_search"
        data = {"category": "park"}
    elif any(kw in message for kw in ["交通", "道路"]):
        analysis_type = "traffic"
    
    return {
        "message": f"已识别您的请求：{analysis_type}",
        "analysis_type": analysis_type,
        "data": data
    }