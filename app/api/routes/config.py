"""System Config API - 管理 LLM, MCP 和 Skills 的运行时配置"""
import json
import logging
import os
from fastapi import APIRouter, HTTPException, UploadFile, File, Body
from pydantic import BaseModel
from typing import Optional, List

from app.api.routes.chat import engine, registry
from app.tools.skills import load_skills
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["配置管理"])

# MCP 配置文件路径
MCP_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "mcp_servers.json")

class LLMConfigRequest(BaseModel):
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    use_prompt_caching: Optional[bool] = None

class MCPConfigRequest(BaseModel):
    config_json: str

@router.get("/llm")
async def get_llm_config():
    """获取当前 LLM 配置"""
    return engine.get_config()

@router.post("/llm")
async def update_llm_config(req: LLMConfigRequest):
    """更新 LLM 配置"""
    engine.update_config(
        base_url=req.base_url,
        model=req.model,
        api_key=req.api_key,
        use_prompt_caching=req.use_prompt_caching
    )
    return {"status": "ok", "config": engine.get_config()}

@router.get("/mcp")
async def get_mcp_config():
    """获取当前 MCP 配置 JSON"""
    if not os.path.exists(MCP_CONFIG_PATH):
        return {"config_json": "{}"}
    with open(MCP_CONFIG_PATH, "r", encoding="utf-8") as f:
        return {"config_json": f.read()}

@router.post("/mcp")
async def update_mcp_config(req: MCPConfigRequest):
    """保存并重载 MCP 配置"""
    try:
        # 校验 JSON 格式
        json.loads(req.config_json)
        with open(MCP_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(req.config_json)
        
        # 触发重连
        from app.main import mcp_adapter
        if mcp_adapter:
            await mcp_adapter.reload_all(MCP_CONFIG_PATH)
        
        return {"status": "ok"}
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to update MCP config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/skills")
async def list_skills():
    """列出当前已加载的技能"""
    skills_dir = "app/skills"
    if not os.path.exists(skills_dir):
        return {"skills": []}
    
    skills = []
    for filename in os.listdir(skills_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            skills.append({
                "name": filename,
                "path": os.path.join(skills_dir, filename),
                "size": os.path.getsize(os.path.join(skills_dir, filename))
            })
    return {"skills": skills}

@router.post("/skills/upload")
async def upload_skill(file: UploadFile = File(...)):
    """上传并热加载技能脚本"""
    skills_dir = "app/skills"
    if not os.path.exists(skills_dir):
        os.makedirs(skills_dir)
        
    file_path = os.path.join(skills_dir, file.filename)
    content = await file.read()
    
    # 解析 skills.md (如果是 MD 文件)
    if file.filename.endswith(".md"):
        # 简单的代码块提取逻辑
        text = content.decode("utf-8")
        import re
        code_blocks = re.findall(r"```python\n([\s\S]*?)```", text)
        if not code_blocks:
            raise HTTPException(status_code=400, detail="No python code block found in MD file")
        # 仅取第一个代码块生成 .py
        py_filename = file.filename.replace(".md", ".py")
        file_path = os.path.join(skills_dir, py_filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code_blocks[0])
    else:
        with open(file_path, "wb") as f:
            f.write(content)
            
    # 重新加载
    load_skills(registry, skills_dir)
    return {"status": "ok", "filename": os.path.basename(file_path)}

@router.post("/skills/refresh")
async def refresh_skills():
    """手动触发技能刷新"""
    load_skills(registry)
    return {"status": "ok"}
