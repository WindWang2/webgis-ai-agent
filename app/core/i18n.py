"""后端 i18n：Accept-Language 协商 + 错误文案 catalog（ADR-0144 / P6）。

双轨文案职责（ADR-0144 决策）：错误 **code/category 为准，message 为辅助** ——
结构化字段（category/retryable/code）永不本地化、永不改动；本地化只作用于
``message`` 这类展示性文案。

组成：
- :func:`parse_accept_language`：RFC 7231 宽松解析（q 值、通配符、别名归一）。
- :data:`current_locale` contextvar：中间件在请求入口写入，同一任务上下文内
  （exception handler 等）可读。
- :class:`AcceptLanguageMiddleware`：请求侧协商 locale；响应侧对「错误信封」
  （JSON 且 ``success: false`` 且带 ``category``）按 catalog 本地化 ``message``。
  结构化字段原样透传，非错误 JSON / 流式响应零改动。
- 回退链：locale 键缺失 → ``zh_CN`` → ``CATEGORY_DEFAULTS`` 的固定短语
  （分类学红线：永不内插异常原文，回退也不破坏）。

目录契约：``app/locales/{zh_CN,en_US}.json`` 顶层键 = ``ErrorCategory`` 枚举值
（append-only：新类目两份 catalog 同批登记，缺键由回退链兜底）。
"""
from __future__ import annotations

import contextvars
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.errors import ErrorCategory, category_defaults

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("zh_CN", "en_US")
DEFAULT_LOCALE = "zh_CN"

_LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"

#: 请求级 locale（中间件写入；exception handler 同任务上下文可读）。
current_locale: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_locale", default=DEFAULT_LOCALE
)

_catalog_cache: Dict[str, Dict[str, str]] = {}


def _load_catalog(locale: str) -> Dict[str, str]:
    """读 locale catalog（进程内缓存；文件缺失 = 空表，回退链兜底）。"""
    if locale in _catalog_cache:
        return _catalog_cache[locale]
    path = _LOCALE_DIR / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        catalog = {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        logger.warning("i18n catalog missing: %s", path)
        catalog = {}
    except Exception:  # noqa: BLE001 — i18n 面绝不抛（响应面不因文案失败）
        logger.debug("i18n catalog unreadable: %s", path, exc_info=True)
        catalog = {}
    _catalog_cache[locale] = catalog
    return catalog


def normalize_locale(tag: str | None) -> str:
    """BCP-47 宽松归一：zh* → zh_CN，en* → en_US，其余回落默认。"""
    if not tag:
        return DEFAULT_LOCALE
    lower = tag.strip().lower()
    if lower.startswith("zh"):
        return "zh_CN"
    if lower.startswith("en"):
        return "en_US"
    return DEFAULT_LOCALE


def parse_accept_language(header: str | None) -> str:
    """解析 Accept-Language，返回受支持 locale。

    q 值降序取第一个可归一的 tag；``*`` 与不可解析输入回落默认语言。
    永不抛（头是外部输入，解析失败 = 默认语言）。
    """
    if not header:
        return DEFAULT_LOCALE
    candidates: list[tuple[float, str]] = []
    for part in header.split(","):
        pieces = part.strip().split(";")
        tag = pieces[0].strip()
        if not tag:
            continue
        q = 1.0
        for param in pieces[1:]:
            param = param.strip()
            if param.startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        # RFC 7231：q=0 = 不可接受，直接排除（避免末尾选到明确排除的 tag）
        try:
            if q > 0:
                candidates.append((float(q), tag))
        except Exception:  # noqa: BLE001 — 解析失败忽略该段
            continue
    candidates.sort(key=lambda item: item[0], reverse=True)
    for _q, tag in candidates:
        if tag == "*":
            return DEFAULT_LOCALE
        # 只接受主子标签可归一的 tag（zh*/en*）；不支持的 tag 跳过并继续
        # 找下一个 —— fr-FR;q=1,en;q=0.3 的正确结果 en 而非默认兜底。
        lower = tag.lower()
        if lower.startswith("zh") or lower.startswith("en"):
            return normalize_locale(lower)
    return DEFAULT_LOCALE


def user_message_for(category: ErrorCategory, locale: Optional[str] = None) -> str:
    """类目 → 本地化用户文案（回退链：locale → zh_CN → 分类学默认短语）。"""
    loc = locale or current_locale.get()
    message = _load_catalog(loc).get(category.value)
    if message is None and loc != DEFAULT_LOCALE:
        message = _load_catalog(DEFAULT_LOCALE).get(category.value)
    if message is None:
        message = category_defaults(category).user_message
    return message


def localized_user_message(
    classification_or_category: Any, locale: Optional[str] = None
) -> str:
    """输出层入口：ErrorClassification 或 ErrorCategory → 本地化文案。

    分类失败时由调用方兜底（本函数对未知输入诚实回落 permanent 短语，
    与分类学的 permanent 兜底纪律一致）。
    """
    if isinstance(classification_or_category, ErrorCategory):
        return user_message_for(classification_or_category, locale)
    category = getattr(classification_or_category, "category", None)
    if isinstance(category, ErrorCategory):
        return user_message_for(category, locale)
    return user_message_for(ErrorCategory.PERMANENT, locale)


class AcceptLanguageMiddleware(BaseHTTPMiddleware):
    """请求侧协商 locale；响应侧本地化错误信封的 ``message``。

    响应面纪律：只重写「JSON + success:false + 带 category」的错误信封的
    ``message`` 字段；结构化字段（category/retryable/code/degraded）与
    成功响应、流式响应零触碰。
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        locale = parse_accept_language(request.headers.get("accept-language"))
        current_locale.set(locale)
        # 有意不做 reset：ASGI 每请求独立 context，值不会跨请求泄漏；
        # 而未处理异常会穿过本中间件到达外层 ServerErrorMiddleware 的
        # 全局 handler —— 那里仍需读到请求协商出的 locale（响应侧本地化）。
        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
        # 只处理错误面（4xx/5xx）：成功响应与 3xx 重定向零缓冲、零改动，
        # 大载荷与流式语义不受中间件影响。
        if response.status_code < 400:
            return response

        locale = current_locale.get()
        if locale == DEFAULT_LOCALE:
            return response  # 默认语言：信封原样（zh 短语即现值）

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001 — 非 JSON 体原样返回
            data = None
        if not isinstance(data, dict) or data.get("success") is not False:
            return self._reissue(response, body)

        category_value = data.get("category")
        try:
            category = ErrorCategory(category_value)
        except ValueError:
            category = None
        if category is None:
            return self._reissue(response, body)

        data["message"] = user_message_for(category, locale)
        return self._reissue(response, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def _reissue(response, body: bytes):
        """按原状态/头重发响应体（content-length 按新体重算）。"""
        from starlette.responses import Response as StarletteResponse

        headers = dict(response.headers)
        headers.pop("content-length", None)
        return StarletteResponse(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.headers.get("content-type"),
        )


__all__ = [
    "SUPPORTED_LOCALES",
    "DEFAULT_LOCALE",
    "current_locale",
    "AcceptLanguageMiddleware",
    "normalize_locale",
    "parse_accept_language",
    "user_message_for",
    "localized_user_message",
]
