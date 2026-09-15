"""VLM provider 层（ADR-0185 D5）：OpenAI 兼容 / Gemini 双通道结构化视觉请求。

- 单一 schema 事实源 ``CRITIC_OUTPUT_SCHEMA``：两个 provider 方言共享同一个
  Python 常量——schema 漂移不可能发生。
- 限流纪律：单次调用、无重试、显式超时；超时/HTTP 失败/解析失败向上抛
  类型化异常，由 critic_engine 归因为 fail-closed reason（D3 矩阵）。
- key 只进请求头，永不进日志与报告。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Tuple

from app.lib.harness.visual_judge.contracts import VisualDimension

PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_GEMINI = "gemini"
_KNOWN_PROVIDERS = (PROVIDER_OPENAI_COMPATIBLE, PROVIDER_GEMINI)

_PLACEHOLDER_KEYS = {"your-api-key-here", "sk-...", "CHANGE_ME"}
_DEFAULT_TIMEOUT_S = 20.0
_DEFAULT_MAX_TOKENS = 1024
_CONTEXT_BUDGET = 800  # 确定性摘要进 prompt 的字符预算（有界）

_CRITIQUE_ITEM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dimension", "severity", "suggestion", "confidence"],
    "properties": {
        "dimension": {"enum": [d.value for d in VisualDimension]},
        "severity": {"enum": ["info", "warning", "error"]},
        "suggestion": {"type": "string"},
        "evidence": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "bbox": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 4,
            "maxItems": 4,
            "description": (
                "defect location as [ymin, xmin, ymax, xmax] in image "
                "percentage coordinates (0-100, origin top-left)"
            ),
        },
        "defect_type": {"type": "string"},
    },
}

#: VLM 输出 JSON Schema（单一事实源，ADR-0185 D5）。
CRITIC_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["critiques"],
    "properties": {
        "critiques": {
            "type": "array",
            "maxItems": 12,
            "items": _CRITIQUE_ITEM_SCHEMA,
        }
    },
}

CRITIC_SYSTEM_PROMPT = (
    "You are a cartographic visual critic. Look at the rendered map image and "
    "report ONLY the visual defects you can actually see, as strict JSON "
    'matching the required schema: {"critiques": [...]}. '
    f"dimension must be exactly one of {[d.value for d in VisualDimension]}; "
    "severity: error = a reader cannot use the map, warning = clearly "
    "degraded, info = minor. suggestion is one short actionable sentence. "
    "For each defect give bbox as [ymin, xmin, ymax, xmax] percentages of the "
    "image (origin top-left) and a short defect_type slug. Report at most 12 "
    "items; an empty critiques list means the map looks clean. Never invent "
    "layers, data, or defects you cannot see. Never propose data or spec "
    "changes - you judge pixels, you do not edit."
)


@dataclass(frozen=True)
class VLMRequest:
    """一次视觉评审请求（provider 无关的中间表示）。"""

    provider: str
    model: str
    image_data_url: str
    user_context: str = ""
    max_tokens: int = _DEFAULT_MAX_TOKENS
    timeout_s: float = _DEFAULT_TIMEOUT_S


class CriticProviderError(Exception):
    """provider 通信/协议失败（HTTP 非 2xx、响应缺体等）。"""


class CriticTimeout(CriticProviderError):
    """provider 调用超时（单独归因，便于运营区分网络抖动与故障）。"""


class CriticOutputError(Exception):
    """输出不可解析/不合 schema（fail-closed ⇒ invalid_output）。"""


def _user_text(request: VLMRequest) -> str:
    text = "Judge this rendered map image."
    context = request.user_context.strip()
    if context:
        text += "\nDeterministic context (for reference only):\n" + context[:_CONTEXT_BUDGET]
    return text


def build_openai_request(request: VLMRequest) -> Dict[str, Any]:
    """OpenAI 兼容 chat completions payload（json_schema 严格约束）。"""
    return {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "visual_critic",
                "strict": True,
                "schema": CRITIC_OUTPUT_SCHEMA,
            },
        },
        "messages": [
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _user_text(request)},
                    {
                        "type": "image_url",
                        "image_url": {"url": request.image_data_url},
                    },
                ],
            },
        ],
    }


def _split_data_url(data_url: str) -> Tuple[str, str]:
    """data URL → (mime, base64)；非 data URL 视为裸 base64 PNG。"""
    if data_url.startswith("data:"):
        head, _, payload = data_url.partition(",")
        mime = head[5:].split(";", 1)[0] or "image/png"
        return mime, payload
    return "image/png", data_url


def build_gemini_request(request: VLMRequest) -> Dict[str, Any]:
    """Gemini generateContent payload（responseSchema 方言 + inline_data）。"""
    mime, payload = _split_data_url(request.image_data_url)
    return {
        "contents": [
            {
                "parts": [
                    {"text": _user_text(request)},
                    {"inline_data": {"mime_type": mime, "data": payload}},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": request.max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": CRITIC_OUTPUT_SCHEMA,
        },
    }


def gemini_endpoint(base_url: str, model: str) -> str:
    return base_url.rstrip("/") + f"/v1beta/models/{model}:generateContent"


def parse_critic_output(text: str) -> List[Dict[str, Any]]:
    """从模型输出提取 critiques 数组；任何形状不合法抛 ``CriticOutputError``。"""
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z0-9]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise CriticOutputError("no JSON object in critic output")
    try:
        parsed = json.loads(body[start:end + 1])
    except json.JSONDecodeError as exc:
        raise CriticOutputError(f"invalid JSON: {exc}") from exc
    critiques = parsed.get("critiques") if isinstance(parsed, dict) else None
    if not isinstance(critiques, list):
        raise CriticOutputError("critic output missing critiques list")
    return [item for item in critiques if isinstance(item, dict)]


class VLMClient(Protocol):
    """视觉评审客户端协议（生产 provider 与 FakeVLMClient 共同实现）。"""

    provider: str
    model: str

    async def critique(self, request: VLMRequest) -> str:  # pragma: no cover
        """返回模型原始文本；失败抛类型化异常。"""
        ...


class OpenAICompatVLMClient:
    """OpenAI 兼容 chat completions 客户端（httpx 单次调用，无重试）。"""

    provider = PROVIDER_OPENAI_COMPATIBLE

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout_s: float = _DEFAULT_TIMEOUT_S, transport: Any = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.transport = transport  # httpx 传输注入（测试 MockTransport / 代理）

    async def critique(self, request: VLMRequest) -> str:
        import httpx

        payload = build_openai_request(request)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s), transport=self.transport
            ) as client:
                response = await client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            raise CriticTimeout(str(exc)) from exc
        except CriticProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — 归一为 provider 错误（fail-closed）
            raise CriticProviderError(str(exc)) from exc
        content = str(
            ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        if not content.strip():
            raise CriticOutputError("empty completion content")
        return content


class GeminiVLMClient:
    """Gemini generateContent 客户端（key 走 ``x-goog-api-key`` 头）。"""

    provider = PROVIDER_GEMINI

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout_s: float = _DEFAULT_TIMEOUT_S, transport: Any = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.transport = transport

    async def critique(self, request: VLMRequest) -> str:
        import httpx

        payload = build_gemini_request(request)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s), transport=self.transport
            ) as client:
                response = await client.post(
                    gemini_endpoint(self.base_url, request.model or self.model),
                    headers={"x-goog-api-key": self.api_key},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            raise CriticTimeout(str(exc)) from exc
        except CriticProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — 归一为 provider 错误（fail-closed）
            raise CriticProviderError(str(exc)) from exc
        candidates = body.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        content = str(parts[0].get("text") or "") if parts else ""
        if not content.strip():
            raise CriticOutputError("empty gemini candidate text")
        return content


def is_placeholder_key(api_key: str) -> bool:
    return (not api_key) or api_key in _PLACEHOLDER_KEYS


def resolve_provider_config() -> Dict[str, str]:
    """(provider, base_url, api_key, model, timeout_s)——env 优先，settings 回落。

    与 legacy ``visual_evaluator._resolve_vlm_config`` 同源同语义（不新增
    配置家族，ADR-0185 D5）；settings 导入延迟到调用时（保持模块导入轻）。
    """
    def _env(name: str) -> str:
        return os.getenv(name, "").strip()

    provider = _env("CARTO_VISUAL_JUDGE_PROVIDER") or PROVIDER_OPENAI_COMPATIBLE
    base_url = _env("CARTO_VISUAL_JUDGE_BASE_URL")
    api_key = _env("CARTO_VISUAL_JUDGE_API_KEY")
    model = _env("CARTO_VISUAL_JUDGE_MODEL")
    timeout_s = _env("CARTO_VISUAL_JUDGE_TIMEOUT_S") or str(_DEFAULT_TIMEOUT_S)
    if not (base_url and api_key and model):
        try:
            from app.core.config import settings

            base_url = base_url or settings.LLM_BASE_URL
            api_key = api_key or settings.LLM_API_KEY
            model = model or settings.LLM_MODEL
        except Exception:  # noqa: BLE001 — settings 缺席 ⇒ 交由占位符检测 fail-closed
            pass
    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "timeout_s": timeout_s,
    }


__all__ = [
    "CRITIC_OUTPUT_SCHEMA",
    "CRITIC_SYSTEM_PROMPT",
    "CriticOutputError",
    "CriticProviderError",
    "CriticTimeout",
    "GeminiVLMClient",
    "OpenAICompatVLMClient",
    "PROVIDER_GEMINI",
    "PROVIDER_OPENAI_COMPATIBLE",
    "VLMClient",
    "VLMRequest",
    "build_gemini_request",
    "build_openai_request",
    "gemini_endpoint",
    "is_placeholder_key",
    "parse_critic_output",
    "resolve_provider_config",
]
