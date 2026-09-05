"""ModelDescriptor —— 模型/Provider 描述符（ADR-0102 Wave 4, §16）。

原则：
- **配置驱动，不编造**：描述符只来自 (a) 保守默认推导、(b) 环境变量、
  (c) 运维覆盖文件 —— 绝不把厂商营销参数硬编码为事实。未知字段保守为
  None/unknown，能力判定处按「最坏情况」处理。
- 与既有 LLM 配置体系（model_config.resolve_llm_config，audit4 #997 单一
  解析点）同源：主三元组（base_url/model/api_key）仍由它解析，本层只加
  **能力/限制/分类**维度 —— 不引入第二配置真相。
- OpenAI 兼容 transport（llm_client 的 httpx 通道）保持不变；endpoint_profile
  是描述符字段而非新协议。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelDescriptor:
    """单个 (provider, model) 的能力/限制描述。"""

    provider_id: str                     # 逻辑 provider 名（默认 "webgis"）
    model_id: str
    endpoint_profile: str = "openai-completions"   # 现存唯一 transport
    context_window: Optional[int] = None           # tokens；None = 未知
    max_output: Optional[int] = None
    tool_calling: Optional[bool] = None            # None = 未知（按配置主模型同兼容处理）
    streaming: bool = True
    reasoning_content: Optional[bool] = None
    json_mode: Optional[bool] = None
    vision: Optional[bool] = None
    prompt_cache: Optional[bool] = None
    temperature_support: bool = True
    system_role_support: bool = True
    rate_limit_class: str = "unknown"
    timeout_class: str = "default"
    cost_class: str = "unknown"          # 相对成本档 free/low/medium/high/unknown
    latency_class: str = "unknown"       # fast/medium/slow/unknown
    local: bool = False                  # 本地推理（ollama/vLLM 本机）
    fallback_group: str = "default"      # 同组内模型视为可互换的降级候选
    role_suitability: Tuple[str, ...] = ()  # 适合的角色（空 = 无约束）
    source: str = "defaults"             # defaults | env | file | override

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    def supports_tools(self, assume_for_unknown: bool = True) -> bool:
        if self.tool_calling is None:
            return assume_for_unknown
        return self.tool_calling

    def contract_payload(self) -> Dict[str, Any]:
        """指纹载荷（观测/评测比较用；不含健康状态这类易变维度）。"""
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "endpoint_profile": self.endpoint_profile,
            "context_window": self.context_window,
            "max_output": self.max_output,
            "tool_calling": self.tool_calling,
            "streaming": self.streaming,
            "reasoning_content": self.reasoning_content,
            "json_mode": self.json_mode,
            "vision": self.vision,
            "fallback_group": self.fallback_group,
            "local": self.local,
            "source": self.source,
        }


def _descriptor_from_dict(d: Dict[str, Any], source: str) -> ModelDescriptor:
    known = {f for f in ModelDescriptor.__dataclass_fields__}
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"未知 ModelDescriptor 字段: {sorted(unknown)}")
    return ModelDescriptor(source=source, **d)


class ModelDescriptorRegistry:
    """进程级模型描述符注册表（线程安全；来源优先级 override > env/file > defaults）。

    构建来源：
    1. 保守默认：settings 的主三元组 → 一个 default 描述符（能力未知，与
       既有系统假设一致）。
    2. 文件：``MODEL_DESCRIPTORS_FILE`` 指向 JSON 数组（运维预置能力表）。
    3. 内联：``MODEL_DESCRIPTOR_OVERRIDES`` 环境变量 JSON 数组。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._descriptors: Dict[str, ModelDescriptor] = {}
        self._built = False

    def _ensure_built(self) -> None:
        if self._built:
            return
        with self._lock:
            if self._built:
                return
            from app.core.config import settings

            defaults = [
                ModelDescriptor(
                    provider_id="webgis",
                    model_id=settings.LLM_MODEL,
                    max_output=settings.LLM_MAX_TOKENS,
                    # review R1 minor：与 context_budget 共用一个旋钮 ——
                    # 运维声明的窗口必须同时驱动路由 context 护栏。
                    context_window=getattr(settings, "LLM_CONTEXT_WINDOW", None) or None,
                    source="defaults",
                ),
            ]
            loaded: Dict[str, ModelDescriptor] = {}
            file_path = os.getenv("MODEL_DESCRIPTORS_FILE")
            if file_path:
                try:
                    with open(file_path, "r", encoding="utf-8") as fh:
                        entries = json.load(fh)
                    for entry in entries:
                        d = _descriptor_from_dict(entry, source="file")
                        loaded[d.key] = d
                except Exception:  # noqa: BLE001 — 配置损坏降级为默认，不阻断启动
                    logger.exception("MODEL_DESCRIPTORS_FILE 解析失败，忽略")
            inline = os.getenv("MODEL_DESCRIPTOR_OVERRIDES")
            if inline:
                try:
                    for entry in json.loads(inline):
                        d = _descriptor_from_dict(entry, source="override")
                        loaded[d.key] = d
                except Exception:  # noqa: BLE001
                    logger.exception("MODEL_DESCRIPTOR_OVERRIDES 解析失败，忽略")
            merged = {d.key: d for d in defaults}
            merged.update(loaded)
            self._descriptors = merged
            self._built = True

    def invalidate(self) -> None:
        """运维改配后强制重建（测试/管理面用）。"""
        with self._lock:
            self._built = False
            self._descriptors = {}

    def get(self, model_id: str, provider_id: str = "webgis") -> Optional[ModelDescriptor]:
        self._ensure_built()
        return self._descriptors.get(f"{provider_id}/{model_id}")

    def resolve(self, model_id: str, provider_id: str = "webgis") -> ModelDescriptor:
        """取描述符；未知模型回退到保守默认（能力未知、不编造）。"""
        self._ensure_built()
        desc = self._descriptors.get(f"{provider_id}/{model_id}")
        if desc is not None:
            return desc
        return ModelDescriptor(provider_id=provider_id, model_id=model_id)

    def all(self) -> Dict[str, ModelDescriptor]:
        self._ensure_built()
        return dict(self._descriptors)

    def by_group(self, group: str) -> Tuple[ModelDescriptor, ...]:
        self._ensure_built()
        return tuple(d for d in self._descriptors.values() if d.fallback_group == group)

    def upsert_override(self, descriptor: ModelDescriptor) -> None:
        """运行时运维覆盖（管理面写入；进程内有效）。"""
        with self._lock:
            self._descriptors[descriptor.key] = replace(descriptor, source="override")
            self._built = True


_registry = ModelDescriptorRegistry()


def get_model_descriptor_registry() -> ModelDescriptorRegistry:
    return _registry
