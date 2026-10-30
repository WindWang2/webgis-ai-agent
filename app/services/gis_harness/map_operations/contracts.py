"""Map Operation Transactions —— 地图表现层操作事务契约（map_operation.v1）。

Track A（情境化地图交互 · 操作事务 · 可验证终验）的表现层专用操作面：

- ``MapOperationRequest``：一次受控操作的输入（有界效果列表 + 操作级
  幂等键 + CAS 基线）。**不是第二份地图状态** —— 效果最终编译为既有
  mutation intent（PatchLayerPresentation / PatchLayerStyle /
  ReorderLayers / SetView / PatchComponent / RemoveLayer / UpsertLayer），
  经 ``apply_gis_mutation``/``apply_gis_mutation_batch`` 公共门面提交，
  MapSpec 仍是唯一 desired state。
- ``MapOperationPreview``：零突变的 dry-run 投影（逐效果 before/after、
  适用性、风险分类、歧义候选）。
- ``MapOperationReceipt``：操作回执（逐效果终态 + reason code + revision
  链 + 前端命令），持久化于 map_state 有界环，支持跨刷新重放对账。

红线（与 BASELINE §D 对齐）：

1. 显隐/透明度只走 PatchLayerPresentation（user-wins 守卫面）——绝不经
   UpsertLayer 洗白绕过守卫；样式（symbol 尺寸等非守卫面）才允许
   UpsertLayer 读改写。
2. 不虚构原子性：presentation 组单事务（既有 batch），其余 intent 顺序
   提交、逐效果披露（局部成功语义，见 ADR-0278 D3）。
3. 所有载荷有界；文本解释由服务端生成，客户端/LLM 提供的自由文本只
   作为 provenance 摘要（长度截断）透传，不进任何执行面。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "map_operation.v1"

# ── 效果种类（封闭词表）────────────────────────────────────────────────

KIND_SET_LAYER_VISIBILITY = "set_layer_visibility"
KIND_SET_LAYER_OPACITY = "set_layer_opacity"
KIND_SET_LAYER_PAINT = "set_layer_paint"
KIND_SET_LAYER_SYMBOL = "set_layer_symbol"
KIND_MOVE_LAYER = "move_layer"
KIND_SET_VIEWPORT = "set_viewport"
KIND_SET_COMPONENT = "set_component"
KIND_REMOVE_LAYER = "remove_layer"

EFFECT_KINDS: tuple[str, ...] = (
    KIND_SET_LAYER_VISIBILITY,
    KIND_SET_LAYER_OPACITY,
    KIND_SET_LAYER_PAINT,
    KIND_SET_LAYER_SYMBOL,
    KIND_MOVE_LAYER,
    KIND_SET_VIEWPORT,
    KIND_SET_COMPONENT,
    KIND_REMOVE_LAYER,
)

#: 敏感（不可逆/破坏性）效果 —— agent 工具不直接提交，须 owner 经
#: ``POST /chat/sessions/{sid}/map-operations/{operation_id}/commit`` 显式
#: 批准（镜像 visual-repairs plan→approve 先例）。
SENSITIVE_KINDS: frozenset[str] = frozenset({KIND_REMOVE_LAYER})

#: 目标指代种类（M3 情境绑定）
TARGET_KIND_LAYER_REF = "layer_ref"
TARGET_KIND_FOCUSED_LAYER = "focused_layer"
TARGET_KIND_LAST_RESULT = "last_result"
TARGET_KIND_COMPONENT_ID = "component_id"

TARGET_KINDS: tuple[str, ...] = (
    TARGET_KIND_LAYER_REF,
    TARGET_KIND_FOCUSED_LAYER,
    TARGET_KIND_LAST_RESULT,
    TARGET_KIND_COMPONENT_ID,
)

#: PatchLayerStyleIntent 的持久 paint 白名单（mutation_registry 单一登记
#: 的 MapSpecPaintPayload 键集 —— 透明度族；其余 paint 属性归 symbology
#: 管线，不经该 intent）。
PAINT_KEYS: frozenset[str] = frozenset({
    "opacity",
    "circle-opacity",
    "fill-opacity",
    "line-opacity",
    "raster-opacity",
    "heatmap-opacity",
    "fill-extrusion-opacity",
    "icon-opacity",
})

#: symbol 尺寸面（UpsertLayer 读改写的属性白名单 —— 按图层类型对应的
#: MapLibre paint/layout 键；「放大一点」类请求的持久落点）。
SYMBOL_PROPERTIES: Dict[str, str] = {
    "point_size": "circle-radius",
    "line_width": "line-width",
    "label_size": "text-size",
    "icon_size": "icon-size",
}

MOVE_POSITIONS: tuple[str, ...] = ("top", "bottom", "up", "down")

# ── reason codes（封闭词表；receipt/preview 单码语义）───────────────────

RC_APPLIED = "applied"
RC_NOOP = "noop"                          # before == after，未产生 mutation
RC_USER_WINS = "user_wins"                # 用户显式决策/锁守卫拒绝 agent 覆盖
RC_LAYER_NOT_FOUND = "layer_not_found"
RC_COMPONENT_NOT_FOUND = "component_not_found"
RC_SUPERSEDED = "superseded"              # CAS 失败（revision 已被推进）
RC_VALIDATION_REJECTED = "validation_rejected"
RC_PAINT_KEY_UNSUPPORTED = "paint_key_unsupported"
RC_SYMBOL_PROPERTY_UNSUPPORTED = "symbol_property_unsupported"
RC_CLARIFICATION_NEEDED = "clarification_needed"
RC_CONFIRMATION_REQUIRED = "confirmation_required"
RC_CHAIN_ABORTED = "chain_aborted"        # 前序效果失败，本效果未尝试
RC_INTERNAL_ERROR = "internal_error"

REASON_CODES: frozenset[str] = frozenset({
    RC_APPLIED, RC_NOOP, RC_USER_WINS, RC_LAYER_NOT_FOUND,
    RC_COMPONENT_NOT_FOUND, RC_SUPERSEDED, RC_VALIDATION_REJECTED,
    RC_PAINT_KEY_UNSUPPORTED, RC_SYMBOL_PROPERTY_UNSUPPORTED,
    RC_CLARIFICATION_NEEDED, RC_CONFIRMATION_REQUIRED,
    RC_CHAIN_ABORTED, RC_INTERNAL_ERROR,
})

# ── 有界常量 ────────────────────────────────────────────────────────────

MAX_EFFECTS = 8
MAX_OPERATION_ID_CHARS = 64
MAX_ACTOR_CHARS = 48
MAX_PROVENANCE_CHARS = 160
MAX_RECEIPT_RING = 16
MAX_CLARIFICATION_CANDIDATES = 6
MAX_PLACEMENT_KEYS = 16

#: map_state 回执环键（additive 单键；白名单纪律同 _final_display_ack）。
RECEIPTS_KEY = "_map_operation_receipts"


class MapOperationEffect(BaseModel):
    """单个表现层效果（闭合词表 kind + per-kind 载荷；越权字段拒绝）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., description="效果种类（EFFECT_KINDS 词表）")
    target_kind: str = Field(
        TARGET_KIND_LAYER_REF,
        description="目标指代：layer_ref / focused_layer / last_result / component_id",
    )
    target: Optional[str] = Field(
        None, max_length=128, description="目标引用（layer_ref/component_id 必填）",
    )
    # set_layer_visibility
    visible: Optional[bool] = None
    # set_layer_opacity
    opacity: Optional[float] = Field(None, ge=0.0, le=1.0)
    # set_layer_paint（透明度族键）
    paint: Optional[Dict[str, float]] = Field(
        None, description="持久 paint 补丁（PAINT_KEYS 白名单键，0~1）",
    )
    # set_layer_symbol
    symbol_property: Optional[str] = Field(
        None, description="符号尺寸属性（SYMBOL_PROPERTIES 键）",
    )
    symbol_value: Optional[float] = Field(None, ge=0.0)
    symbol_scale: Optional[float] = Field(None, gt=0.0, le=100.0,
                                          description="相对缩放（1.5=放大一半）")
    # move_layer
    position: Optional[str] = Field(None, description="top/bottom/up/down")
    # set_viewport
    center: Optional[List[float]] = Field(None, min_length=2, max_length=2)
    zoom: Optional[float] = Field(None, ge=0.0, le=22.0)
    bearing: Optional[float] = Field(None, ge=0.0, le=360.0)
    pitch: Optional[float] = Field(None, ge=0.0, le=85.0)
    bbox: Optional[List[float]] = Field(None, min_length=4, max_length=4)
    # set_component
    component_patch: Optional[Dict[str, Any]] = Field(
        None, description="组件补丁（position/placement/enabled/variant 有界）",
    )

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v not in EFFECT_KINDS:
            raise ValueError(f"unknown effect kind {v!r}; 词表: {sorted(EFFECT_KINDS)}")
        return v

    @field_validator("target_kind")
    @classmethod
    def _check_target_kind(cls, v: str) -> str:
        if v not in TARGET_KINDS:
            raise ValueError(f"unknown target_kind {v!r}; 词表: {sorted(TARGET_KINDS)}")
        return v

    @field_validator("paint")
    @classmethod
    def _check_paint(cls, v: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if v is None:
            return v
        if not v:
            raise ValueError("paint 补丁不能为空")
        bad = sorted(set(v) - PAINT_KEYS)
        if bad:
            raise ValueError(
                f"paint 键 {bad} 不在持久白名单 {sorted(PAINT_KEYS)}；"
                "尺寸/颜色类样式请用 set_layer_symbol"
            )
        for key, val in v.items():
            if not 0.0 <= float(val) <= 1.0:
                raise ValueError(f"paint[{key}]={val} 越界（0~1）")
        return {k: float(x) for k, x in v.items()}

    @field_validator("symbol_property")
    @classmethod
    def _check_symbol_property(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in SYMBOL_PROPERTIES:
            raise ValueError(
                f"symbol_property {v!r} 不支持；词表 {sorted(SYMBOL_PROPERTIES)}"
            )
        return v

    @field_validator("component_patch")
    @classmethod
    def _check_component_patch(
        cls, v: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if v is None:
            return v
        allowed = {"position", "placement", "enabled", "variant"}
        bad = sorted(set(v) - allowed)
        if bad:
            raise ValueError(f"component_patch 键 {bad} 越权（允许 {sorted(allowed)}）")
        if not v:
            raise ValueError("component_patch 不能为空")
        if "placement" in v and (
            not isinstance(v["placement"], dict) or len(v["placement"]) > MAX_PLACEMENT_KEYS
        ):
            raise ValueError("placement 必须是有界对象（≤16 键）")
        if "enabled" in v and not isinstance(v["enabled"], bool):
            raise ValueError("enabled 必须是布尔")
        return v


class MapOperationRequest(BaseModel):
    """一次地图表现层操作（preview 或 commit）。

    session_id 永远由服务端注入（工具层从 dispatch 上下文、路由层从
    路径参数），客户端/LLM 传入的 session_id 一律忽略 —— 跨会话操作
    fail-closed。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    operation_id: str = Field(
        ..., min_length=8, max_length=MAX_OPERATION_ID_CHARS,
        description="操作幂等键（UUID；重放返回存证回执）",
    )
    mode: str = Field("preview", description="preview（零突变）/ commit")
    effects: List[MapOperationEffect] = Field(
        ..., min_length=1, max_length=MAX_EFFECTS,
    )
    expected_revision: Optional[int] = Field(
        None, ge=0, description="CAS 基线（可选；agent 缺省=当前，user 必填由引擎强制）",
    )
    actor: str = Field("pi_agent", max_length=MAX_ACTOR_CHARS)
    user_ack: bool = Field(
        False, description="owner 显式批准（仅受控提交路由可置 true）",
    )
    turn_id: Optional[str] = Field(None, max_length=64)
    note: Optional[str] = Field(
        None, max_length=MAX_PROVENANCE_CHARS,
        description="归因摘要（仅入 provenance，不进执行面）",
    )

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version 必须 {SCHEMA_VERSION}")
        return v

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in ("preview", "commit"):
            raise ValueError("mode ∈ {preview, commit}")
        return v


def sensitive_request(effects: List[MapOperationEffect]) -> bool:
    """请求是否含敏感效果（须 owner 显式批准方可 commit）。"""
    return any(e.kind in SENSITIVE_KINDS for e in effects)


__all__ = [
    "SCHEMA_VERSION",
    "EFFECT_KINDS",
    "SENSITIVE_KINDS",
    "TARGET_KINDS",
    "TARGET_KIND_LAYER_REF",
    "TARGET_KIND_FOCUSED_LAYER",
    "TARGET_KIND_LAST_RESULT",
    "TARGET_KIND_COMPONENT_ID",
    "PAINT_KEYS",
    "SYMBOL_PROPERTIES",
    "MOVE_POSITIONS",
    "REASON_CODES",
    "MAX_EFFECTS",
    "MAX_RECEIPT_RING",
    "MAX_CLARIFICATION_CANDIDATES",
    "RECEIPTS_KEY",
    "RC_APPLIED",
    "RC_NOOP",
    "RC_USER_WINS",
    "RC_LAYER_NOT_FOUND",
    "RC_COMPONENT_NOT_FOUND",
    "RC_SUPERSEDED",
    "RC_VALIDATION_REJECTED",
    "RC_PAINT_KEY_UNSUPPORTED",
    "RC_SYMBOL_PROPERTY_UNSUPPORTED",
    "RC_CLARIFICATION_NEEDED",
    "RC_CONFIRMATION_REQUIRED",
    "RC_CHAIN_ABORTED",
    "RC_INTERNAL_ERROR",
    "MapOperationEffect",
    "MapOperationRequest",
    "sensitive_request",
]
