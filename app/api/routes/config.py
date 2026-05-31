"""System Config API - 管理 LLM 和 Skills 的运行时配置

⚠️ 安全：本路由的所有写入端点（POST/PUT/DELETE）要求 Bearer JWT 鉴权
(Depends(get_current_user))。当前系统未提供 /login 端点，因此这些端点
仅可由运维通过 JWT_SECRET_KEY 手工签发的 token 访问，等同于 admin-only。
"""
import logging
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional

from app.api.routes.chat import get_engine, get_registry
from app.tools.skills import load_skills
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["配置管理"])


def _validate_or_reject_skill_code(code: str) -> None:
    """Validate skill code via AST deny-list. Raises HTTPException on failure.

    If the validator module cannot be imported, the upload is REJECTED (not skipped)
    to prevent unvalidated code execution.
    """
    try:
        from app.tools.skills import _validate_skill_code  # type: ignore
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="安全校验模块不可用，拒绝上传以防止未校验代码执行",
        )
    validation_errors = _validate_skill_code(code)
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail=f"技能代码未通过安全检查: {'; '.join(validation_errors)}",
        )

# 允许的技能文件扩展名
_ALLOWED_SKILL_EXTS = {".py", ".md"}

class LLMConfigRequest(BaseModel):
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    use_prompt_caching: Optional[bool] = None

@router.get("/llm")
async def get_llm_config(_user: dict = Depends(get_current_user)):
    """获取当前 LLM 配置（admin only）"""
    return get_engine().get_config()

@router.post("/llm")
async def update_llm_config(
    req: LLMConfigRequest,
    _user: dict = Depends(get_current_user),
):
    """更新 LLM 配置（admin only）"""
    get_engine().update_config(
        base_url=req.base_url,
        model=req.model,
        api_key=req.api_key,
        use_prompt_caching=req.use_prompt_caching
    )
    return {"status": "ok", "config": get_engine().get_config()}

@router.get("/skills")
async def list_skills():
    """列出当前已加载的技能（.py + .md）"""
    skills_dir = "app/skills"
    if not os.path.exists(skills_dir):
        return {"skills": []}

    skills = []
    for filename in os.listdir(skills_dir):
        if filename.startswith("__"):
            continue
        filepath = os.path.join(skills_dir, filename)
        if filename.endswith(".py"):
            skills.append({
                "name": filename,
                "type": "python",
                "size": os.path.getsize(filepath)
            })
        elif filename.endswith(".md"):
            skills.append({
                "name": filename,
                "type": "workflow",
                "size": os.path.getsize(filepath)
            })
    return {"skills": skills}

@router.post("/skills/upload")
async def upload_skill(
    file: UploadFile = File(...),
    _user: dict = Depends(get_current_user),
):
    """上传并热加载技能脚本（admin only）

    SECURITY: 写入 .py 后会被 importlib.exec_module 执行 → 等同于 RCE。
    严格鉴权 + 文件名清洗 + 扩展名白名单 + AST 校验。
    """
    skills_dir = "app/skills"
    if not os.path.exists(skills_dir):
        os.makedirs(skills_dir)

    # —— 文件名清洗：剥离任何路径分隔符与 .. ——
    raw_name = file.filename or ""
    safe_name = Path(raw_name).name  # 取最末段
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="非法文件名")

    ext = Path(safe_name).suffix.lower()
    if ext not in _ALLOWED_SKILL_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"仅允许 {sorted(_ALLOWED_SKILL_EXTS)} 扩展名",
        )

    content = await file.read()

    # 解析 skills.md (如果是 MD 文件)
    if ext == ".md":
        text = content.decode("utf-8")
        import re
        code_blocks = re.findall(r"```python\n([\s\S]*?)```", text)
        if not code_blocks:
            raise HTTPException(status_code=400, detail="No python code block found in MD file")
        # 仅取第一个代码块生成 .py
        py_filename = Path(safe_name).stem + ".py"
        code_to_write = code_blocks[0]
        file_path = os.path.join(skills_dir, py_filename)
    else:
        code_to_write = content.decode("utf-8", errors="replace")
        file_path = os.path.join(skills_dir, safe_name)

    # —— AST 沙箱校验：复用 create_new_skill 的 deny-list ——
    _validate_or_reject_skill_code(code_to_write)

    # 最终路径必须落在 skills_dir 之内（防御 symlink/绝对路径残留）
    resolved = Path(file_path).resolve()
    skills_root = Path(skills_dir).resolve()
    if skills_root not in resolved.parents and resolved != skills_root:
        raise HTTPException(status_code=400, detail="路径越界")

    if ext == ".md":
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code_to_write)
    else:
        with open(file_path, "wb") as f:
            f.write(content)

    # 重新加载
    load_skills(get_registry(), skills_dir)
    return {"status": "ok", "filename": os.path.basename(file_path)}

@router.post("/skills/refresh")
async def refresh_skills(_user: dict = Depends(get_current_user)):
    """手动触发技能刷新（admin only）"""
    load_skills(get_registry())
    return {"status": "ok"}
