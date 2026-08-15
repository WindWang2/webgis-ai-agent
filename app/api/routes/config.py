"""System Config API - 管理 LLM 和 Skills 的运行时配置

⚠️ 安全：本路由的所有端点要求 admin 角色（Depends(require_admin)）。
审计 S29：之前仅 Depends(get_current_user) —— 这只验证 JWT 合法性，
不验证角色；任何登录用户（包括通过公开注册的）都能改 LLM 配置（把
流量重定向到攻击者日志端点窃取所有 prompt）或上传 Python 技能文件
（写盘 + importlib.exec_module 等同 RCE）。
"""
import logging
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional

import httpx

from app.api.routes.chat import get_engine, get_registry
from app.tools.skills import load_skills
from app.core.auth import require_admin
from app.core.config import settings

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


class LLMTestRequest(BaseModel):
    """连通性测试参数（#390）。字段全可选：缺省时使用引擎当前生效的配置。"""
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class RagTestRequest(BaseModel):
    """知识库连通性测试参数（#390）。当前后端使用内置本地向量库，
    address/collection 仅作展示用途，不影响后端行为。"""
    address: Optional[str] = None
    collection: Optional[str] = None

@router.get("/llm")
async def get_llm_config(_user: dict = Depends(require_admin)):
    """获取当前 LLM 配置（admin only）"""
    return get_engine().get_config()

@router.post("/llm")
async def update_llm_config(
    req: LLMConfigRequest,
    _user: dict = Depends(require_admin),
):
    """更新 LLM 配置（admin only）"""
    # SEC-04: 启动时的 _validate_no_ssrf 只在进程启动跑一次；运行时改 base_url
    # 时必须重新校验，否则 admin (或被接管账号) 可把 LLM 流量重定向到内网 /
    # 云元数据端点 (SSRF)。复用 config.py 的同一份校验逻辑。
    if req.base_url:
        try:
            settings._validate_no_ssrf(req.base_url, field="base_url")
        except ValueError as e:
            logger.warning(f"base_url SSRF validation failed: {e}")
            raise HTTPException(status_code=400, detail="base_url 校验失败: 不允许使用该 URL")
    get_engine().update_config(
        base_url=req.base_url,
        model=req.model,
        api_key=req.api_key,
        use_prompt_caching=req.use_prompt_caching
    )
    return {"status": "ok", "config": get_engine().get_config()}


def _provider_error_detail(err: httpx.HTTPError) -> str:
    """从上游 HTTP/传输错误里提取对用户可读的错误详情。

    HTTPStatusError 优先取响应体的 ``error.message``（OpenAI 兼容错误
    形状），取不到再退回状态文本；ConnectError 等传输错误直接转 str。
    """
    if isinstance(err, httpx.HTTPStatusError):
        try:
            body = err.response.json()
            message = body.get("error", {}).get("message")
            if isinstance(message, str) and message:
                return message
        except Exception:
            pass
        return f"上游返回 HTTP {err.response.status_code}"
    return str(err) or "未知网络错误"


@router.post("/llm/test")
async def test_llm_config(
    req: LLMTestRequest,
    _user: dict = Depends(require_admin),
):
    """LLM 连通性测试（admin only，#390）。

    向 provider 发一次最小补全请求，验证 base_url + api_key + model
    组合真实可用。字段缺省时回退到引擎当前生效的配置（前端测试按钮
    不携带 apiKey —— 真实 key 由服务端持有）。本端点不持久化任何配置。

    失败返回 502 + 错误详情（不再有"永远成功"的假测试）；base_url
    先过 SSRF 校验（与 POST /llm 同一套逻辑）。
    """
    engine = get_engine()
    base_url = (req.base_url or engine.base_url or "").rstrip("/")
    model = req.model or engine.model
    api_key = req.api_key if req.api_key is not None else engine.api_key

    if not base_url or not model:
        raise HTTPException(status_code=400, detail="base_url 与 model 不能为空")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key 未配置，无法测试连通性")
    try:
        settings._validate_no_ssrf(base_url, field="base_url")
    except ValueError as e:
        logger.warning(f"base_url SSRF validation failed: {e}")
        raise HTTPException(status_code=400, detail="base_url 校验失败: 不允许使用该 URL")

    try:
        from app.services.chat.llm_client import LLMConfig, test_llm_connection
        cfg = LLMConfig(base_url=base_url, model=model, api_key=api_key)
        await test_llm_connection(cfg)
    except httpx.HTTPError as e:
        logger.warning(f"LLM connectivity test failed: {e}")
        raise HTTPException(status_code=502, detail=f"连接失败: {_provider_error_detail(e)}")
    except Exception as e:
        logger.exception("LLM connectivity test failed unexpectedly")
        raise HTTPException(status_code=502, detail=f"连接测试失败: {e}")

    return {"status": "ok", "detail": f"连接成功: {model}"}


async def _check_rag_store() -> str:
    """校验后端实际使用的知识库存储（当前为内置本地 FAISS 向量库）。

    返回就绪描述；任何一步失败抛异常，由调用方转成 502 + 错误详情。
    """
    from app.services import rag_service
    from app.services.rag.faiss_store import INDEX_DIR

    os.makedirs(INDEX_DIR, exist_ok=True)
    if not os.access(INDEX_DIR, os.W_OK):
        raise RuntimeError(f"向量库目录不可写: {INDEX_DIR}")
    metadata = rag_service._default_store.load_metadata()
    chunks = len(metadata.get("chunks", [])) if isinstance(metadata, dict) else 0
    return f"内置本地向量库（FAISS）就绪，已索引 {chunks} 个分块"


@router.post("/rag/test")
async def test_rag_config(
    req: RagTestRequest,
    _user: dict = Depends(require_admin),
):
    """知识库连通性测试（admin only，#390）。

    当前后端检索使用内置本地 FAISS 向量库（无外部向量数据库依赖），
    因此本端点校验该存储的真实健康状态（目录可写 + 元数据可读）。
    请求中的 address/collection 仅作展示，后端当前不使用；响应返回
    store 类型供前端如实展示。
    """
    try:
        detail = await _check_rag_store()
    except Exception as e:
        logger.warning(f"RAG store health check failed: {e}")
        raise HTTPException(status_code=502, detail=f"知识库不可用: {e}")
    return {"status": "ok", "store": "local-faiss", "detail": detail}

@router.get("/skills")
async def list_skills(_user: dict = Depends(require_admin)):
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
    _user: dict = Depends(require_admin),
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
    # Issue #399: 显式 RCE 警示 —— 该文件将在主进程内被 importlib.exec_module
    # 执行（等同 RCE）。AST deny-list 只是防御纵深，不是安全边界；记录上传者
    # 便于审计追溯。
    logger.warning(
        "Skill uploaded by %s: %s — executes in-process via exec_module "
        "(RCE-equivalent if malicious; deny-list is defense-in-depth only)",
        _user.get("username") or _user.get("sub", "unknown"),
        os.path.basename(file_path),
    )
    return {
        "status": "ok",
        "filename": os.path.basename(file_path),
        "security": "warning: skill code executes in-process via importlib.exec_module "
        "(RCE-equivalent if malicious); AST deny-list is defense-in-depth, "
        "not a sandbox",
    }

@router.post("/skills/refresh")
async def refresh_skills(_user: dict = Depends(require_admin)):
    """手动触发技能刷新（admin only）"""
    load_skills(get_registry())
    return {"status": "ok"}
