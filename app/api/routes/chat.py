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

from app.db.session import get_db
from app.models.api_response import ApiResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["AI聊天"])

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
    生成AI回复（模拟实现，生产环境接入LLM）
    """
    msg_lower = user_message.lower()
    
    if "buffer" in msg_lower or "缓冲" in msg_lower:
        return '''以下是缓冲区分析的Python代码示例：

```python
from shapely.geometry import Point

# 创建点对象
point = Point(0, 0)

# 创建1000米的缓冲区（约0.009度）
buffer = point.buffer(0.009)

print(f"缓冲区面积: {buffer.area}")
```

您可以在图层面板中上传数据并执行Buffer分析。'''

    elif "clip" in msg_lower or "裁剪" in msg_lower:
        return '''裁剪分析的代码示例：

```python
from shapely.ops import unary_union
import geopandas as gpd

# 加载图层
vector_layer = gpd.read_file("data.geojson")

# 定义裁剪范围
clipped = vector_layer.clip(box_bounds)

# 保存结果
clipped.to_file("clipped_output.geojson")
```
'''

    elif "intersect" in msg_lower or "相交" in msg_lower:
        return '''空间相交分析的代码：

```python
import geopandas as gpd

layer1 = gpd.read_file("layer1.geojson")
layer2 = gpd.read_file("layer2.geojson")

# 执行相交分析
result = layer1.overlay(layer2, how="intersect")
print(f"相交要素数量: {len(result)}")
```
'''

    elif any(k in msg_lower for k in ["统计", "statistics", "面积", "周长"]):
        return '''统计分析功能可以计算：

```python
import geopandas as gpd

layer = gpd.read_file("my_layer.geojson")

# 计算面积和周长
layer["area"] = layer.geometry.area
layer["perimeter"] = layer.geometry.length

# 基本统计
stats = {
    "总要素数": len(layer),
    "总面积": layer["area"].sum(),
    "平均面积": layer["area"].mean()
}
print(stats)
```
'''

    elif any(k in msg_lower for k in ["帮助", "help", "能做什么"]):
        return '''我可以帮您进行以下GIS操作：

1. **缓冲区分析 (Buffer)** - 创建指定距离的缓冲区
2. **裁剪分析 (Clip)** - 用一个图层裁剪另一个图层  
3. **相交分析 (Intersect)** - 找出两个图层的交集
4. **融合分析 (Dissolve)** - 合并相同属性的要素
5. **联合分析 (Union)** - 合并两个图层
6. **统计分析** - 计算面积、周长、要素数量等

请告诉我您想执行的分析操作！'''

    else:
        return f'''收到您的消息：「{user_message}」

我是WebGIS AI助手，可以帮您：
- 执行空间分析（Buffer、Clip、Intersect、Dissolve、Union）
- 管理图层（上传、查询、导出）
- 解答GIS相关问题

请问有什么可以帮您的？'''


__all__ = ["router"]