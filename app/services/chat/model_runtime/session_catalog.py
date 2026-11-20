"""G37：管理员允许的会话模型目录（catalog）。

原则（任务书 WP02/WP04）：
- **descriptor ≠ 授权**：``ModelDescriptorRegistry`` 只提供能力/限制元数据
  （保守 unknown），不因注册而获得使用授权。目录项来源只有两个：
  (a) 基础模型 —— ``model_config.resolve_llm_config()`` 解析出的主模型
  （Settings ⊕ 运行时 override 的单一解析点），恒在目录内；
  (b) ``settings.SESSION_MODEL_CATALOG_EXTRA`` —— 管理员（部署配置）在
  **同一已授权端点**上额外放开的模型 id。全部共用服务器端解析的凭证，
  绝不从客户端字符串派生 endpoint/key。
- **不编造**：能力字段透传描述符（unknown 保持 unknown）；不可用项给出
  具体原因（credential_missing / invalid_id），不静默隐藏也不假成功。
- **目录代际**：``catalog_generation()`` 是目录内容的不可逆短指纹，供
  preference 重验证与 receipt 对账（区分「配置代」与「目录代」）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.config import settings

# Pi 侧目录项 id 语法：非空、无路径分隔/空白/控制字符（防注入 models.json
# 结构）。宽松白名单（字母数字 - _ . : /）覆盖常见 vendor id；其余拒绝。
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")

# 与 spawn 侧 provider 常量一致（pi_rpc_client.BUNDLED_PI_PROVIDER）——
# lazy import 避免循环依赖。
BUNDLED_PROVIDER = "webgis"

#: 目录来源版本（进 receipt / catalog_generation）。
CATALOG_SOURCE_VERSION = "session_catalog.v1"


class CatalogItemIdError(ValueError):
    """非法目录项 id（含路径分隔/空白/超长等）。"""


@dataclass(frozen=True)
class CatalogItem:
    """一个管理员允许的会话可选模型。"""

    item_id: str                 # 目录内稳定 id（= Pi modelId）
    provider_id: str             # 恒为 bundled provider（当前唯一 transport）
    is_default: bool             # 是否基础模型（继承默认的落点）
    available: bool
    unavailable_reason: str = ""  # credential_missing | invalid_id | ""
    # —— 能力面（全部透传描述符；None = 未知，绝不硬填厂商值）——
    context_window: Optional[int] = None
    max_output: Optional[int] = None
    tool_calling: Optional[bool] = None
    reasoning: Optional[bool] = None
    vision: Optional[bool] = None
    cost_class: str = "unknown"
    capability_source: str = "defaults"  # defaults | env | file | override

    def public_payload(self) -> Dict[str, Any]:
        """面向 UI/API 的目录项投影（无凭证信息）。"""
        return {
            "id": self.item_id,
            "provider": self.provider_id,
            "is_default": self.is_default,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason or None,
            "capabilities": {
                "context_window": self.context_window,
                "max_output": self.max_output,
                "tool_calling": self.tool_calling,
                "reasoning": self.reasoning,
                "vision": self.vision,
                "cost_class": self.cost_class,
                "source": self.capability_source,
            },
        }


def _extra_model_ids() -> List[str]:
    """解析管理员额外放开的模型 id（保序去重，剔除非法项并如实返回）。"""
    raw = (settings.SESSION_MODEL_CATALOG_EXTRA or "").strip()
    ids: List[str] = []
    seen = set()
    for token in raw.split(","):
        mid = token.strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        ids.append(mid)
    return ids


def _credential_present() -> bool:
    """服务器端凭证是否已解析（不回显值，只回显在场位）。"""
    from app.services.chat.model_config import resolve_llm_config

    try:
        key = str(resolve_llm_config().api_key or "")
    except Exception:  # noqa: BLE001 — 解析失败按无凭证（诚实不可用）
        return False
    return bool(key) and "your-api-key" not in key


def build_catalog() -> List[CatalogItem]:
    """构建当前管理员允许目录（每次调用现算 —— 配置/override 变化即时反映）。

    基础模型恒在场；extra 项 id 非法 → 标记 invalid_id 不可选（不静默丢，
    诊断可见）。凭证缺失 → 全部标 credential_missing（目录仍可见、不可选）。
    """
    from app.services.chat.model_config import resolve_llm_config
    from app.services.chat.model_runtime.descriptors import (
        get_model_descriptor_registry,
    )

    try:
        base_model = str(resolve_llm_config().model or "")
    except Exception:  # noqa: BLE001
        base_model = ""
    cred_ok = _credential_present()
    registry = get_model_descriptor_registry()

    items: List[CatalogItem] = []

    def _push(model_id: str, *, is_default: bool) -> None:
        reason = ""
        if not model_id:
            reason = "invalid_id"
        elif not _MODEL_ID_RE.match(model_id):
            reason = "invalid_id"
        elif not cred_ok:
            reason = "credential_missing"
        desc = registry.resolve(model_id, provider_id=BUNDLED_PROVIDER)
        items.append(CatalogItem(
            item_id=model_id,
            provider_id=BUNDLED_PROVIDER,
            is_default=is_default,
            available=not reason,
            unavailable_reason=reason,
            context_window=desc.context_window,
            max_output=desc.max_output,
            tool_calling=desc.tool_calling,
            reasoning=desc.reasoning_content,
            vision=desc.vision,
            cost_class=desc.cost_class,
            capability_source=desc.source,
        ))

    _push(base_model, is_default=True)
    for mid in _extra_model_ids():
        if mid != base_model:
            _push(mid, is_default=False)
    return items


def default_item(catalog: Optional[List[CatalogItem]] = None) -> Optional[CatalogItem]:
    """目录中的基础模型项（继承默认的落点）。"""
    for item in (catalog if catalog is not None else build_catalog()):
        if item.is_default:
            return item
    return None


def find_item(item_id: str, catalog: Optional[List[CatalogItem]] = None) -> Optional[CatalogItem]:
    for item in (catalog if catalog is not None else build_catalog()):
        if item.item_id == item_id:
            return item
    return None


def catalog_generation() -> str:
    """目录内容指纹（sha256[:16]）：base_url + 凭证在场位 + 排序后目录 id 集。

    与 ``model_config.config_generation``（基础三元组代际）互补：目录代际
    覆盖「目录里有哪些模型」，配置代际覆盖「端点/凭证/基础模型换没换」。
    两者都不含 key 明文。
    """
    from app.services.chat.model_config import resolve_llm_config

    try:
        base_url = str(resolve_llm_config().base_url or "").rstrip("/")
        base_model = str(resolve_llm_config().model or "")
    except Exception:  # noqa: BLE001
        base_url, base_model = "", ""
    ids = sorted({base_model, *_extra_model_ids()} - {""})
    payload = "\n".join([base_url, "1" if _credential_present() else "0", *ids])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def validate_model_id(model_id: str) -> str:
    """校验外部输入的 model id（会话 preference 写入前）。"""
    if not isinstance(model_id, str) or not _MODEL_ID_RE.match(model_id):
        raise CatalogItemIdError(f"invalid model id: {model_id!r}")
    return model_id


__all__ = [
    "BUNDLED_PROVIDER",
    "CATALOG_SOURCE_VERSION",
    "CatalogItem",
    "CatalogItemIdError",
    "build_catalog",
    "catalog_generation",
    "default_item",
    "find_item",
    "validate_model_id",
]
