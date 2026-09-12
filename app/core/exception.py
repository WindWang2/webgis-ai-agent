"""
统一异常处理模块
提供基于环境的全局异常处理器，开发环境返回详细错误生产环境返回安全错误
"""
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.errors import classify_exception, localized_user_message

logger = logging.getLogger(__name__)

PRODUCTION_ERROR_MESSAGE = "服务器内部错误，请稍后重试"
# 项目根目录用于清理敏感信息
PROJECT_ROOT = str(Path(__file__).parent.parent.parent)

# 审计 M5：HTTP 状态码 → 业务 code 字符串映射。之前 format_error_response
# 永远返回 code=SERVER_ERROR，导致前端无法按 code 区分 404/401/403 等。
_STATUS_CODE_TO_CODE: Dict[int, str] = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    # V9（ADR-0138）：503 = 服务/能力显式不可用（如公开注册关闭），
    # 不是服务器内部错误 —— code 不再误标 SERVER_ERROR。
    503: "SERVICE_UNAVAILABLE",
    502: "UPSTREAM_ERROR",
    504: "TIMEOUT",
}

def sanitize_traceback(tb_str: str) -> str:
    """
    清理traceback中的敏感信息包括项目路径、文件路径、行号
    
    Args:
        tb_str: 原始traceback字符串
        
    Returns:
        清理后的traceback字符串
    """
    # 第1步：清理项目路径
    sanitized = tb_str.replace(PROJECT_ROOT, "<REDACTED>")
    
    # 第2步：清理标准Python traceback格式的文件路径和行号
    # 匹配模式：File "path/to/file.py", line N
    lines = sanitized.split('\n')
    cleaned_line = []
    for line in lines:
        if ', line' in line:
            # 找到 ", line" 的位置并定位前面的文件路径
            idx = line.find(', line')
            # 找到前面最近的 File " 开始位置
            quote_start = line.rfind('"', 0, idx)
            file_keyword = line.rfind('File ', 0, max(0, quote_start))
            if file_keyword >= 0 and quote_start > file_keyword:
                line = line[:file_keyword + 5] + '<REDACTED_PATH>' + line[idx:]
        cleaned_line.append(line)
    
    return '\n'.join(cleaned_line)

def format_error_response(
    exc: Exception,
    request: Request,
    include_details: bool = False,
) -> Dict[str, Any]:
    """
    格式化错误响应根据include_details决定返回的信息详细程度
    
    Args:
        exc: 异常对象
        request: FastAPI请求对象
        include_details: 是否包含详细信息
        
    Returns:
        标准化的错误响应字典
    """
    error_type = type(exc).__name__
    error_message = str(exc)[:200]

    # 审计 M5：按 HTTP 状态码映射 code —— 之前永远是 SERVER_ERROR，前端无法
    # 区分 404 vs 401 vs 403。HTTPException 自带 status_code；其他异常保持
    # SERVER_ERROR 兜底（global_exception_handler 默认 500）。
    code = "SERVER_ERROR"
    if hasattr(exc, "status_code"):
        code = _STATUS_CODE_TO_CODE.get(getattr(exc, "status_code"), "SERVER_ERROR")

    response_data = {
        "code": code,
        "success": False,
        "message": PRODUCTION_ERROR_MESSAGE,
        "data": None,
    }

    # Platform V4（ADR-0131 D3）：additive 结构化分类字段。分类永不抛、
    # 永不改既有字段——老客户端多收到两个字段，无破坏。
    try:
        _cls = classify_exception(exc)
        response_data["category"] = _cls.category.value
        response_data["retryable"] = _cls.retryable
        if _cls.degraded:
            response_data["degraded"] = _cls.degraded
        # ADR-0144 P6：message 按 Accept-Language 本地化（contextvar 由
        # AcceptLanguageMiddleware 写入；缺省 zh 与既有行为等价）。
        # 开发环境的 detailed message 在其后覆盖 —— 内部诊断优先，不受影响。
        response_data["message"] = localized_user_message(_cls)
    except Exception:  # noqa: BLE001 — 分类失败不改变既有响应形状
        logger.debug("error classification failed", exc_info=True)
    
    # 开发环境返回详细错误信息
    if include_details:
        response_data.update({
            "error_type": error_type,
            "error_detail": error_message,
            "path": str(request.url.path),
            "method": request.method,
        })
        exc_info = sys.exc_info()
        tb_stack = traceback.format_exception(*exc_info)
        tb_str = "".join(tb_stack)
        tb_str = sanitize_traceback(tb_str)
        response_data["traceback"] = tb_str
        # 开发环境可以使用较详细的message
        response_data["message"] = "{0}: {1}".format(error_type, error_message)
    
    return response_data

async def global_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """
    全局异常处理器
    
    根据环境配置决定返回的错误级别：
    - 开发环境(development): 返回完整错误信息堆栈跟踪请求路径等
    - 生产环境(production): 返回通用错误提示不泄露任何内部信息
    
    Args:
        request: FastAPI请求对象
        exc: 捕获的异常对象
        
    Returns:
        JSON格式的错误响应
    """
    # 判断是否显示详细信息：非生产环境都显示
    include_details = not settings.is_production()

    
    # 无论哪种环境都记录完整日志便于服务端调试；category 供日志侧按类型
    # 聚合（告警/回归面直接用结构化字段，不再 grep 消息文本）。
    try:
        _category = classify_exception(exc).category.value
    except Exception:  # noqa: BLE001
        _category = "unknown"
    logger.error(
        "[{0}] [{1}] {2} - {3}: {4} category={5}".format(
            settings.ENV, request.method, request.url.path,
            type(exc).__name__, str(exc), _category
        ),
        exc_info=True
    )
    
    # 格式化响应
    response_data = format_error_response(
        exc=exc,
        request=request,
        include_details=include_details,

    )
    
    # 确定HTTP状态码：HTTPException使用其自带的状态码
    status_code = 500
    if hasattr(exc, "status_code"):
        status_code = getattr(exc, "status_code", 500)

    return JSONResponse(
        status_code=status_code,
        content=response_data,
    )


# ── 统一错误信封（V9 契约基石，ADR-0138）────────────────────────────────
#
# 双信封根治：HTTPException / RequestValidationError 原本走 FastAPI 默认
# 处理器返回 `{"detail": ...}`，与全局 handler 的 ApiResponse 形状并存
# （docs/api-docs.md 曾同时记载两套）。本段把前两者接入同一信封：
# {code, success: false, message, data: null} + errors 分类学附加字段。
#
# 过渡策略：
# - settings.LEGACY_DETAIL_ENVELOPE=true → 全局回退旧体 {"detail"}；
# - 请求头 X-Error-Envelope: detail → 按请求回退旧体（未迁移 v1 客户端灰度）。

LEGACY_ENVELOPE_HEADER = "x-error-envelope"


def wants_legacy_envelope(request: Request) -> bool:
    """legacy 信封判定：settings 总开关 OR 请求头按请求覆盖。

    v2 面不做 legacy 降级（ADR-0138 D5：v2 默认且仅新信封）。
    """
    if request.url.path.startswith("/api/v2"):
        return False
    if getattr(settings, "LEGACY_DETAIL_ENVELOPE", False):
        return True
    return request.headers.get(LEGACY_ENVELOPE_HEADER, "").strip().lower() == "detail"


def unified_error_envelope(
    status_code: int,
    message: str,
    *,
    request: Optional[Request] = None,
    exc: Optional[BaseException] = None,
    include_details: bool = False,
) -> Dict[str, Any]:
    """按状态码 + message 构造统一错误体（与 format_error_response 同形状）。"""
    code = _STATUS_CODE_TO_CODE.get(status_code, "SERVER_ERROR")
    envelope: Dict[str, Any] = {
        "code": code,
        "success": False,
        "message": message,
        "data": None,
    }
    if exc is not None:
        try:
            _cls = classify_exception(exc)
            envelope["category"] = _cls.category.value
            envelope["retryable"] = _cls.retryable
            if _cls.degraded:
                envelope["degraded"] = _cls.degraded
        except Exception:  # noqa: BLE001 — 分类失败不改变既有响应形状
            logger.debug("error classification failed", exc_info=True)
    if include_details and request is not None:
        envelope["path"] = str(request.url.path)
        envelope["method"] = request.method
    return envelope


_STATUS_DEFAULT_MESSAGE: Dict[int, str] = {
    400: "请求无法处理",
    401: "未认证或凭证失效",
    403: "无权访问该资源",
    404: "资源不存在",
    405: "方法不被允许",
    409: "资源状态冲突",
    413: "请求体过大",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后再试",
    503: "服务暂不可用",
}


async def unified_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """HTTPException（含路由 raise HTTPException）→ 统一信封 / legacy detail。

    detail 为 dict 时（既有路由用 ``detail={"code": ...}`` 传结构化错误码），
    结构化载荷移入 ``data``，message 取状态码缺省文案 —— 结构化信息不再被
    吞成通用 5xx 文案（schemathesis/分片测试发现的保真缺陷）。
    """
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        message = detail
        data = None
    elif isinstance(detail, dict):
        message = _STATUS_DEFAULT_MESSAGE.get(exc.status_code, PRODUCTION_ERROR_MESSAGE)
        data = detail
    else:
        message = _STATUS_DEFAULT_MESSAGE.get(exc.status_code, PRODUCTION_ERROR_MESSAGE)
        data = None
    if wants_legacy_envelope(request):
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})
    envelope = unified_error_envelope(
        exc.status_code,
        message,
        request=request,
        exc=exc,
    )
    envelope["data"] = data
    return JSONResponse(status_code=exc.status_code, content=envelope)


async def unified_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """请求体/参数校验失败（422）→ 统一信封 / legacy detail。"""
    if wants_legacy_envelope(request):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    errors = [
        {
            "loc": list(map(str, e.get("loc", []))),
            "msg": str(e.get("msg", "")),
            "type": str(e.get("type", "")),
        }
        for e in list(exc.errors())[:20]
    ]
    envelope = unified_error_envelope(422, "请求参数校验失败", request=request)
    if not settings.is_production():
        envelope["errors"] = errors
    return JSONResponse(status_code=422, content=envelope)


__all__ = [
    "global_exception_handler",
    "format_error_response",
    "sanitize_traceback",
    "PRODUCTION_ERROR_MESSAGE",
    "unified_http_exception_handler",
    "unified_validation_exception_handler",
    "unified_error_envelope",
    "wants_legacy_envelope",
    "LEGACY_ENVELOPE_HEADER",
]