"""
AI Chat API Route - T005 AI交互模块后端API
创建时间: 2026-04-02
"""
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import uuid
import logging
from openai import OpenAI

from app.db.session import get_db
from app.models.api_response import ApiResponse
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["AI聊天"])

# ======= LLM客户端初始化 =======
_llm_client = None
if settings.LLM_BASE_URL and settings.LLM_API_KEY is not None:
    try:
        _llm_client = OpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
        logger.info(f"✅ LLM客户端初始化成功，模型：{settings.LLM_MODEL}")
    except Exception as e:
        logger.error(f"❌ LLM客户端初始化失败：{str(e)}")

# ======= 内存存储（生产环境使用数据库）=======
_chat_sessions: dict[str, dict] = {}

# ======= Schema 定义 =======
class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None
    context: Optional[dict] = None


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    message: str
    timestamp: int


class SessionInfo(BaseModel):
    """会话信息"""
    id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int


# ======= API 实现 =======
@router.post("", response_model=ApiResponse)
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """
    发送聊天消息，获取AI回复
    """
    user_message = request.message.strip()
    
    # 创建或复用session
    session_id = request.session_id or str(uuid.uuid4())
    
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = {
            "id": session_id,
            "title": user_message[:30] + ("..." if len(user_message) > 30 else ""),
            "messages": [],
            "created_at": int(datetime.now().timestamp() * 1000),
            "updated_at": int(datetime.now().timestamp() * 1000)
        }
    
    session = _chat_sessions[session_id]
    
    # 添加用户消息
    user_msg = {
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": user_message,
        "timestamp": int(datetime.now().timestamp() * 1000)
    }
    session["messages"].append(user_msg)
    
    # 生成AI回复
    ai_response_text = _generate_ai_response(user_message, session.get("messages", []))
    
    ai_msg = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": ai_response_text,
        "timestamp": int(datetime.now().timestamp() * 1000)
    }
    session["messages"].append(ai_msg)
    session["updated_at"] = int(datetime.now().timestamp() * 1000)
    
    # 更新标题
    if len(session["messages"]) <= 2:
        session["title"] = user_message[:30] + ("..." if len(user_message) > 30 else "")
    
    return ApiResponse.ok(data={
        "session_id": session_id,
        "message": ai_response_text,
        "timestamp": ai_msg["timestamp"]
    })


@router.get("/sessions", response_model=ApiResponse)
async def get_session_list():
    """获取会话历史列表"""
    sessions = []
    for sid, sess in _chat_sessions.items():
        sessions.append({
            "id": sid,
            "title": sess.get("title", "新对话"),
            "created_at": sess.get("created_at", 0),
            "updated_at": sess.get("updated_at", 0),
            "message_count": len(sess.get("messages", []))
        })
    
    sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    
    return ApiResponse.ok(data={"sessions": sessions})


@router.get("/sessions/{session_id}", response_model=ApiResponse)
async def get_session_detail(session_id: str):
    """获取会话详细内容"""
    if session_id not in _chat_sessions:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    session = _chat_sessions[session_id]
    return ApiResponse.ok(data={
        "id": session["id"],
        "title": session.get("title", "新对话"),
        "messages": session.get("messages", []),
        "created_at": session.get("created_at", 0),
        "updated_at": session.get("updated_at", 0)
    })


@router.delete("/sessions/{session_id}", response_model=ApiResponse)
async def delete_session(session_id: str):
    """删除会话"""
    if session_id in _chat_sessions:
        del _chat_sessions[session_id]
    return ApiResponse.ok(message="会话已删除")


@router.delete("/sessions/{session_id}/clear", response_model=ApiResponse)
async def clear_session_messages(session_id: str):
    """清空会话消息（保留会话）"""
    if session_id not in _chat_sessions:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    _chat_sessions[session_id]["messages"] = []
    _chat_sessions[session_id]["updated_at"] = int(datetime.now().timestamp() * 1000)
    
    return ApiResponse.ok(message="会话消息已清空")


def _generate_ai_response(user_message: str, history: list[dict]) -> str:
    """
    生成AI回复（真实调用本地MiniMax大模型）
    """
    # 如果LLM客户端未初始化，返回提示
    if not _llm_client:
        return "⚠️ LLM服务未正确配置，请检查环境变量设置。"
    
    try:
        # 构建消息上下文
        messages = [
            {"role": "system", "content": "你是专业的WebGIS AI助手，精通空间分析、GIS开发、地理数据处理，能够帮助用户解答GIS相关问题、编写空间分析代码、指导数据处理操作。回答要专业、简洁、有操作性。"}
        ]
        
        # 添加历史消息（最多保留最近10条，避免上下文过长）
        for msg in history[-10:]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # 调用大模型
        response = _llm_client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=0.7,
            stream=False
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        logger.error(f"LLM调用失败：{str(e)}")
        return f"❌ 调用AI服务失败：{str(e)}"}


__all__ = ["router"]