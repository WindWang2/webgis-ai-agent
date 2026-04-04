"""
统一异常处理模块
提供基于环境的全局异常处理器，开发环境返回详细错误，生产环境返回安全错误
"""
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Dict
from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.config import settings

logger = logging.getLogger(__name__)

PRODUCTION_ERROR_MESSAGE = "服务器内部错误，请稍后重试"

# 项目根目录用于清理敏感信息
PROJECT_ROOT = str(Path(__file__).parent.parent.parent)


def sanitize_traceback(tb_str: str) -> str:
    """
    清理traceback中的敏感信息，包括项目路径、文件路径、行号
    
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
    cleaned_lines = []
    for line in lines:
        if ', line' in line:
            # 找到 ", line" 的位置并定位前面的文件路径
            idx = line.find(', line')
            # 找到前面最近的 File " 开始位置
            quote_start = line.rfind('"', 0, idx)
            file_keyword = line.rfind('File ', 0, max(0, quote_start))
            if file_keyword >= 0 and quote_start > file_keyword:
                line = line[:file_keyword + 5] + '<REDACTED_PATH>' + line[idx:]
        cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines)


def format_error_response(
    exc: Exception,
    request: Request,
    include_details: bool = False,
) -> Dict[str, Any]:
    """
    格式化错误响应根据include_detail决定返回的信息详细程度
    
    Args:
        exc: 异常对象
        request: FastAPI请求对象
        include_detail: 是否包含详细信息
        
    Returns:
        标准化的错误响应字典
    """
    error_type = type(exc).__name__
    error_message = str(exc)[:200]
    
    response_data = {
        "code": "SERVER_ERROR",
        "success": False,
        "message": PRODUCTION_ERROR_MESSAGE,
        "data": None,
    }
    
    # 开发环境返回详细错误信息
    if include_details:
        exc_info = sys.exc_info()
        tb_stack = traceback.format_exception(*exc_info)
        tb_str = "".join(tb_stack)
        tb_str = sanitize_traceback(tb_str)
        
        response_data.update({
            "error_type": error_type,
            "error_detail": error_message,
            "traceback": tb_str,
            "path": str(request.url.path),
            "method": request.method,
        })
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
    include_detail = not settings.is_production()
    
    # 无论哪种环境都记录完整日志便于服务端调试
    logger.error(
        "[{0}] [{1}] {2} - {3}: {4}".format(
            settings.ENV, request.method, request.url.path,
            type(exc).__name__, str(exc)
        ),
        exc_info=True
    )
    
    # 格式化响应
    response_data = format_error_response(
        exc=exc,
        request=request,
        include_detail=include_detail,
    )
    
    # 确定HTTP状态码：HTTPException使用其自带的状态码
    status_code = 500
    if hasattr(exc, "status_code"):
        status_code = getattr(exc, "status_code", 500)
    
    return JSONResponse(
        status_code=status_code,
        content=response_data,
    )


__all__ = [
    "global_exception_handler",
    "format_error_response",
    "sanitize_traceback",
    "PRODUCTION_ERROR_MESSAGE",
]