"""静态文件路由 — 替代 app.mount(StaticFiles)，关闭审计 A4 公开枚举入口。

设计：
- 唯一入口 GET /static/{file_path:path}
- 路径强校验：
    * 不允许任何形式的 `..`
    * resolve 后必须仍在 settings.DATA_DIR 之下
    * 拒绝以 `.` 开头的文件名
- 访问控制三选一（任一通过即放行）：
    * 持有合法 Bearer JWT
    * 提供合法 `?sig=...&exp=...` 签名 URL
    * 文件位于显式公共子目录 `public/` 下
- 全部访问记日志（含 user_id / anonymous）便于事后审计。

发布签名 URL：见 app/core/signing.py:sign_path()。
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.core.auth import get_current_user_optional
from app.core.config import settings
from app.core.signing import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter()

# 公共白名单子目录：允许匿名直接访问（如分享底图、demo 数据）
# 显式列表，避免无意中放开整个 data/ 的子树
_PUBLIC_PREFIXES = ("public/",)


def _resolve_under_data_dir(file_path: str) -> Path:
    """把请求路径解析为 DATA_DIR 下的绝对路径；越界 / 非法均抛 HTTPException。"""
    if not file_path or file_path.startswith("/") or ".." in file_path.split("/"):
        raise HTTPException(status_code=400, detail="非法路径")

    # 末段以 . 开头的隐藏文件直接拒
    last = Path(file_path).name
    if last.startswith("."):
        raise HTTPException(status_code=400, detail="非法文件名")

    base = Path(settings.DATA_DIR).resolve()
    target = (base / file_path).resolve()

    # 二次防御：解析后必须仍在 base 之下（防御 symlink 跨界）
    if base not in target.parents and target != base:
        raise HTTPException(status_code=403, detail="路径越界")
    return target


@router.get("/static/{file_path:path}", tags=["静态文件"])
async def serve_static(
    file_path: str,
    request: Request,
    sig: Optional[str] = Query(None, description="可选 HMAC 签名"),
    exp: Optional[int] = Query(None, description="签名过期时间（unix ts）"),
    user: dict = Depends(get_current_user_optional),
):
    """带访问控制 + 越界防护的静态文件下发。"""
    target = _resolve_under_data_dir(file_path)

    is_public = file_path.startswith(_PUBLIC_PREFIXES)
    is_authed = user and user.get("user_id") not in (None, "", "anonymous")
    is_signed = bool(sig and exp) and verify_signature(file_path, exp, sig)

    if not (is_public or is_authed or is_signed):
        # 公网枚举的最后一道阻挡：404 不暴露存在性
        raise HTTPException(status_code=404, detail="Not found")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    mime, _ = mimetypes.guess_type(str(target))
    actor = "anon" if not is_authed else user.get("user_id")
    via = "auth" if is_authed else ("sig" if is_signed else "public")
    logger.info(
        "[static] %s %s via=%s actor=%s ip=%s",
        request.method, file_path, via, actor,
        getattr(request.client, "host", "?"),
    )
    return FileResponse(
        path=str(target),
        media_type=mime or "application/octet-stream",
        filename=target.name,
    )
