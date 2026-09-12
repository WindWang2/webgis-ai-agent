"""配置管理子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/config.py` 内联迁出；路由文件只 import。
所有端点 admin-only（审计 S29）。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class LLMConfigRequest(BaseModel):
    """POST /config/llm 请求体（None = 保持现值）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "api_key": "sk-...",
                    "use_prompt_caching": True,
                }
            ]
        }
    )

    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    use_prompt_caching: Optional[bool] = None


class LLMConfig(BaseModel):
    """当前生效的 LLM 配置（api_key 恒为掩码）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "api_key": "***abcd",
                    "use_prompt_caching": True,
                }
            ]
        }
    )

    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: str = ""
    use_prompt_caching: bool = True


class LLMConfigResponse(LLMConfig):
    """GET /config/llm 返回。"""


class LLMConfigUpdateResponse(BaseModel):
    """POST /config/llm 返回（更新后的完整配置）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "config": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o-mini",
                        "api_key": "***abcd",
                        "use_prompt_caching": True,
                    },
                }
            ]
        }
    )

    status: str
    config: LLMConfig


class LLMTestRequest(BaseModel):
    """POST /config/llm/test 请求体（#390）。

    字段全可选：缺省时使用引擎当前生效的配置。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"base_url": None, "api_key": None, "model": None}]
        }
    )

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class LLMTestResponse(BaseModel):
    """POST /config/llm/test 返回（失败走 502 + detail HTTPException）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"status": "ok", "detail": "连接成功: gpt-4o-mini"}]
        }
    )

    status: str
    detail: str


class RagTestRequest(BaseModel):
    """POST /config/rag/test 请求体（#390）。

    当前后端使用内置本地向量库，address/collection 仅作展示用途。
    """

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"address": None, "collection": None}]}
    )

    address: Optional[str] = None
    collection: Optional[str] = None


class RagTestResponse(BaseModel):
    """POST /config/rag/test 返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "store": "local-faiss",
                    "detail": "内置本地向量库（FAISS）就绪，已索引 42 个分块",
                }
            ]
        }
    )

    status: str
    store: str
    detail: str


class SkillListItem(BaseModel):
    """已加载技能文件条目。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"name": "crop.py", "type": "python", "size": 1024}]
        }
    )

    name: str
    type: str  # python | workflow
    size: int


class SkillsListResponse(BaseModel):
    """GET /config/skills 返回。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"skills": []}]}
    )

    skills: list[SkillListItem] = []


class SkillUploadResponse(BaseModel):
    """POST /config/skills/upload 返回（含显式 RCE 警示，#399）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "filename": "crop.py",
                    "security": "warning: skill code executes in-process ...",
                }
            ]
        }
    )

    status: str
    filename: str
    security: str


class RefreshSkillsResponse(BaseModel):
    """POST /config/skills/refresh 返回。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "ok"}]}
    )

    status: str
