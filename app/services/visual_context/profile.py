"""已配置视觉模型的能力投影与运行时核对（G36 / ADR-0291，WP08）。

职责边界（与 G37 的分界）：
- 本模块**只投影**：管理员维护的 ``ModelDescriptor.vision``（env / file /
  override 三来源）+ 保守 inputLimits → spawn 时 ``models.json`` 的
  ``input``/``inputLimits.images.resize`` 字段。不新增模型选择器、不改
  凭据、不动 provider 路由。
- **不推断**：``vision`` 不是 True（None=未知 / False）→ 不投影 image
  input（Pi 目录省略 input 默认 text-only）→ 图片通道关闭，文本照常
  （V10）。
- **运行时核对**（V11）：管理员声明 vision=True 但旧 worker 的目录里没
  有 image input（描述符换代早于 spawn）→ 送图前拒绝，typed reason；
  ``get_state`` 的当前模型 input 亦须包含 image（目录写对了但 vendor 实
  际加载的模型不匹配 → 同样拒绝）。文本不受影响。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 派生图预算（与 decoder.MAX_DERIVED_* 单一语义源；这里只投影给 Pi 目录）.
RESIZE_MAX_SIDE = 1568
RESIZE_MAX_BYTES = 2048 * 1024        # vendor 语义 = base64 **编码后**上限
                                      # （派生字节 1.5MiB × 4/3 ≈ 2MiB）
JPEG_QUALITY = 82

_ALLOWED_REASONS = (
    "model_not_configured_for_vision",
    "worker_spawned_without_image_input",
    "runtime_model_lacks_image_input",
    "runtime_state_unavailable",
)


def descriptor_vision_confirmed(model_id: str, provider_id: str = "webgis") -> Optional[bool]:
    """描述符确证的 vision（True/False）；无描述符 → None（未知，不开放）。"""
    try:
        from app.services.chat.model_runtime.descriptors import (
            get_model_descriptor_registry,
        )

        desc = get_model_descriptor_registry().resolve(model_id, provider_id)
        vision = getattr(desc, "vision", None)
        return bool(vision) if vision is not None else None
    except Exception:  # noqa: BLE001 — 能力面 fail-closed
        logger.warning("[VisualProfile] descriptor resolve failed model=%s",
                       str(model_id)[:64], exc_info=True)
        return None


def model_catalog_entry(model_id: str, name: str, reasoning: bool) -> Dict[str, Any]:
    """spawn ``models.json`` 的模型条目：vision 确证 → 加性 input/inputLimits。

    与 ``pi_rpc_client`` 现有 ``{"id","name","reasoning"}`` 条目兼容 —— 无
    vision 时返回原样（不写 input，Pi 默认 text-only）。
    """
    entry: Dict[str, Any] = {"id": model_id, "name": name, "reasoning": reasoning}
    if descriptor_vision_confirmed(model_id) is True:
        entry["input"] = ["text", "image"]
        entry["inputLimits"] = {
            "images": {
                "resize": {
                    "maxWidth": RESIZE_MAX_SIDE,
                    "maxHeight": RESIZE_MAX_SIDE,
                    "maxBytes": RESIZE_MAX_BYTES,
                    "jpegQuality": JPEG_QUALITY,
                },
            },
        }
    return entry


def model_entry_declares_image(entry: Dict[str, Any]) -> bool:
    """models.json 条目是否声明了 image input（spawn 锚点记录用）。"""
    try:
        inputs = entry.get("input")
        return isinstance(inputs, list) and "image" in inputs
    except Exception:  # noqa: BLE001
        return False


def state_model_accepts_image(state: Optional[Dict[str, Any]]) -> Optional[bool]:
    """vendor ``get_state`` → 当前模型是否声明 image input。

    None = state 缺席/无模型（无法核对，调用方按拒绝处理）。
    """
    if not isinstance(state, dict):
        return None
    model = state.get("model")
    if not isinstance(model, dict):
        return None
    inputs = model.get("input")
    if not isinstance(inputs, list):
        return False
    return "image" in inputs


class VisionGate:
    """送图前的三重门（描述符 → spawn 锚点 → 运行时 state）。

    spawn 锚点：``spawn_declared_image_input`` 由 ``pi_rpc_client`` 在写
    models.json 时记录；描述符此后换代（管理员改配置）→ 与锚点不一致 →
    拒绝图片直到 worker 按既有边界换代（V11）。
    """

    def __init__(self) -> None:
        self._state_cache: Optional[Dict[str, Any]] = None
        self._state_epoch: Any = None

    def invalidate_runtime_cache(self) -> None:
        self._state_cache = None
        self._state_epoch = None

    async def check(
        self,
        *,
        model_id: str,
        spawn_declared_image_input: bool,
        rpc,
        spawn_epoch: Any = None,
    ) -> tuple[bool, str]:
        """返回 ``(allowed, reason)``；reason ∈ ``_ALLOWED_REASONS`` 或 ""。"""
        if descriptor_vision_confirmed(model_id) is not True:
            return False, "model_not_configured_for_vision"
        if not spawn_declared_image_input:
            return False, "worker_spawned_without_image_input"
        # 运行时核对（按 spawn epoch 缓存；换代失效）。
        if self._state_cache is None or self._state_epoch != spawn_epoch:
            state = None
            try:
                state = await rpc.request("get_state", {})
            except Exception as exc:  # noqa: BLE001 — 核对失败 fail-closed
                logger.warning("[VisualProfile] get_state failed: %s", exc)
                return False, "runtime_state_unavailable"
            self._state_cache = state if isinstance(state, dict) else {}
            self._state_epoch = spawn_epoch
        accepts = state_model_accepts_image(self._state_cache)
        if accepts is not True:
            return False, "runtime_model_lacks_image_input"
        return True, ""
