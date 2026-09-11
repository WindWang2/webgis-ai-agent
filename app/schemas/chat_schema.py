"""对话子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/chat.py` 内联迁出；路由文件只 import。
校验器逻辑原样迁移（行为不变）；_bounded_canvas / _MAX_CANVAS_PX
随模型一并迁入（chat.py 回引）。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_CANVAS_PX = 16384


def _bounded_canvas(raw: Any) -> Optional[dict[str, int]]:
    """canvas 有界校验（V6 W8 证据门）。

    width/height 须为正整数（bool 除外；整数值 float 归一为 int）且
    ≤ _MAX_CANVAS_PX；缺席 / 非 dict / 非数字 / 非正 / 超限 → None
    （调用方省略该键 —— 按「证据缺席」诚实降级，不做像素级判定）。
    只返回 {width, height} 投影（不透传客户端多发键）。
    """
    if not isinstance(raw, dict):
        return None
    vals: list[int] = []
    for key in ("width", "height"):
        value = raw.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            ivalue = value
        elif isinstance(value, float) and value.is_integer():
            ivalue = int(value)
        else:
            return None
        if not 0 < ivalue <= _MAX_CANVAS_PX:
            return None
        vals.append(ivalue)
    return {"width": vals[0], "height": vals[1]}


class ChatRequest(BaseModel):
    """聊天请求。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "对这份数据做缓冲区分析",
                    "session_id": "sess-123",
                    "map_state": None,
                    "skill_name": None,
                    "project_id": None,
                }
            ]
        }
    )

    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None
    map_state: Optional[dict] = Field(None, description="当前的地图状态（视角、图层等）")
    skill_name: Optional[str] = Field(None, description="要激活的技能名称")
    project_id: Optional[str] = Field(
        None,
        description=(
            "Active project workspace id; when set, the chat context "
            "assembler renders the project-summary block (datasets + "
            "workflows) for this project. The session metadata store "
            "does not yet persist project_id, so the request body is "
            "the only way to associate a chat turn with a project."
        ),
    )

    @model_validator(mode="after")
    def _cap_map_state_size(self):
        # #521: map_state is client-controlled and persisted via set_map_state
        # per key; without a bound a multi-MB payload stalls the event loop on
        # every turn start (and is capped nowhere else). Reject truthfully —
        # never silent truncation. NOTE: the bound is measured on the WHOLE
        # serialized request (model_dump_json, mirroring the
        # cartographic-observation DTO), not map_state alone — message is
        # bounded at 5000 chars, so this is the map_state budget with a small
        # constant slack, but the error text must not claim map_state itself
        # exceeded the limit.
        if self.map_state is not None and (
            len(self.model_dump_json().encode("utf-8")) > 256 * 1024
        ):
            raise ValueError("serialized chat request exceeds 256KB (map_state budget)")
        return self


class ChatResponse(BaseModel):
    """聊天响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "sess-123",
                    "content": "分析完成，结果已挂载到地图。",
                    "owner_token": "otok-...",
                }
            ]
        }
    )

    session_id: str
    content: str
    # SEC-08：新建匿名会话时由服务端签发，前端需存储并在后续请求头里回传。
    owner_token: Optional[str] = None


class MapStatePushRequest(BaseModel):
    """POST /chat/sessions/{session_id}/map-state 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "viewport": {"center": [120.1, 30.2], "zoom": 10},
                    "layers": [],
                    "base_layer": "dark",
                    "seq": 7,
                }
            ]
        }
    )

    viewport: Optional[dict] = None
    layers: Optional[list] = Field(default=None, max_length=128)
    base_layer: Optional[str] = Field(default=None, max_length=500)
    # F4: monotonic client seq for the viewport write — an out-of-order older
    # POST landing after the turn-start write is rejected as stale.
    seq: Optional[int] = None

    @model_validator(mode="after")
    def _cap_serialized_size(self):
        # #521: client-controlled body. Viewport hints are persisted; layers
        # are accepted for old clients but not written (#643). Bound at 256KB
        # so a multi-MB push cannot stall the event loop's per-key json.dumps.
        if len(self.model_dump_json().encode("utf-8")) > 256 * 1024:
            raise ValueError("serialized map state push exceeds 256KB")
        return self


class CartographicRuntimeObservationRequest(BaseModel):
    """Bounded actual MapLibre evidence for one MapSpec generation."""

    # Client-minted wall-clock/monotonic hybrid. Arrival order is not state
    # order: a slow stale POST must not overwrite a newer live observation.
    client_generation: int = Field(ge=1, le=9_007_199_254_740_991)
    mapspec_fingerprint: str = Field(min_length=16, max_length=96)
    layers: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    viewport: dict[str, Any] = Field(default_factory=dict)
    style_loaded: bool
    reconcile_error: str = Field(default="", max_length=500)
    # P9 render observation（增维不换通道，全部 optional 向后兼容）：
    # mapspec_revision 是客户端诊断值 —— 守卫语义由服务端在接受门通过后
    # 盖章当前 _cartographic_mutation_revision（信任边界在服务端）。
    mapspec_revision: Optional[int] = Field(default=None, ge=0, le=9_007_199_254_740_991)
    # chrome 组件观察（resolveMapComponents 派生：id/type/enabled/mounted/
    # anchor/floating/collapsed/rect —— ID 与布尔，无载荷体）。
    components: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    # 有界 runtime error 环（dedup 后 ≤8 条：message≤160 + target）。
    runtime_errors: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    # V5 W5：chart_panel 渲染 telemetry（id/rendered/data_points —— ID、
    # 布尔与计数，无载荷体）。None = 客户端未上报（旧构建）—— 持久层
    # 省略该键，服务端按「telemetry 缺席」诚实披露而非误读为空集。
    charts: Optional[list[dict[str, Any]]] = Field(default=None, max_length=32)
    # bounded settle 结果（map 'idle' race 超时）。
    map_idle: Optional[bool] = None
    observed_at: Optional[int] = Field(default=None, ge=0, le=9_007_199_254_740_991)
    # V6 W8：地图容器像素尺寸（floating 组件 rect 的参照系 —— 确定性
    # offscreen/重叠检查的坐标基准）。None = 客户端未上报（旧构建），
    # 服务端按「证据缺席」降级：不做像素级判定，不产生误伤 finding。
    canvas: Optional[dict[str, Any]] = None

    @field_validator("canvas", mode="before")
    @classmethod
    def _normalize_canvas(cls, value: Any) -> Any:
        # 非法 canvas 不 422 —— 按证据缺席省略（旧客户端零新 finding
        # 语义）；只保留 {width, height} 投影（多发键不透传）。
        return _bounded_canvas(value)


class MapActionAck(BaseModel):
    """单条地图动作终态 ACK（V3 闭环：前端上报命令执行终态）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "action_id": "act-1",
                    "command": "zoomTo",
                    "status": "succeeded",
                    "error": "",
                    "started_at": "",
                    "finished_at": "",
                    "duration_ms": 42.0,
                }
            ]
        }
    )

    action_id: str = Field(min_length=1, max_length=64)
    command: str = Field(max_length=64)
    status: Literal["succeeded", "failed", "cancelled", "superseded"]
    error: str = Field(default="", max_length=500)
    started_at: str = Field(default="", max_length=64)
    finished_at: str = Field(default="", max_length=64)
    # le=1e15 拒绝 Infinity/超大值（json.loads("1e999") 会解析成 inf，存进
    # duration_ms 会在序列化/聚合时产生非有限值）。
    duration_ms: Optional[float] = Field(default=None, ge=0, le=1e15)
    correlation: Optional[dict] = None
    requested: Optional[dict] = None
    actual: Optional[dict] = None

    @model_validator(mode="after")
    def _cap_serialized_size(self):
        # 单条 ACK 序列化上限 16KB —— requested/actual 是无界 dict，防超大上报
        # 撑爆会话存储。超限抛 ValueError -> FastAPI 返回 422。
        if len(self.model_dump_json().encode("utf-8")) > 16 * 1024:
            raise ValueError("serialized ack exceeds 16KB")
        return self


class MapActionAckRequest(BaseModel):
    """地图动作 ACK 批量上报体（V3 闭环），单批 ≤50 条。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "acks": [
                        {
                            "action_id": "act-1",
                            "command": "zoomTo",
                            "status": "succeeded",
                        }
                    ]
                }
            ]
        }
    )

    acks: list[MapActionAck] = Field(max_length=50)


class ToolExecuteRequest(BaseModel):
    """POST /chat/tools/execute 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "tool": "buffer_analysis",
                    "arguments": {"distance_km": 5},
                    "session_id": None,
                    "confirm_destructive": False,
                }
            ]
        }
    )

    tool: str
    arguments: dict = {}
    session_id: Optional[str] = None
    # tier-3 工具（如 create_new_skill —— 写盘 + importlib.exec_module 等同 RCE）
    # 必须显式确认才执行（审计 S30）。
    confirm_destructive: bool = False


# ── 响应模型（P2 新增）────────────────────────────────────────────────


class SessionSummary(BaseModel):
    """会话列表条目。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "sess-123",
                    "title": "缓冲区分析",
                    "createdAt": 1757548800000.0,
                    "updatedAt": 1757548900000.0,
                }
            ]
        }
    )

    id: str
    title: Optional[str] = None
    createdAt: Optional[float] = None
    updatedAt: Optional[float] = None


class SessionListResponse(BaseModel):
    """GET /chat/sessions 响应（审计 A5 分页）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"total": 1, "limit": 50, "offset": 0, "sessions": []}]
        }
    )

    total: int
    limit: int
    offset: int
    sessions: list[SessionSummary] = []


class SessionMessage(BaseModel):
    """会话消息条目。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "42",
                    "role": "user",
                    "content": "你好",
                    "timestamp": 1757548800000.0,
                }
            ]
        }
    )

    id: str
    role: str
    content: str
    timestamp: float


class SessionDetailResponse(BaseModel):
    """GET /chat/sessions/{session_id} 响应（消息分页，最新端偏移）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "sess-123",
                    "title": "缓冲区分析",
                    "createdAt": 1757548800000.0,
                    "updatedAt": 1757548900000.0,
                    "limit": 200,
                    "offset": 0,
                    "total": 2,
                    "has_more": False,
                    "messages": [],
                }
            ]
        }
    )

    id: str
    title: Optional[str] = None
    createdAt: Optional[float] = None
    updatedAt: Optional[float] = None
    limit: int
    offset: int
    total: int
    has_more: bool
    messages: list[SessionMessage] = []


class SessionMapStateResponse(BaseModel):
    """GET /chat/sessions/{session_id}/map-state 响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"session_id": "sess-123", "map_state": {"viewport": {}}}
            ]
        }
    )

    session_id: str
    map_state: Optional[dict[str, Any]] = None


class ChartArtifactResponse(BaseModel):
    """GET /chat/sessions/{session_id}/chart-artifacts/{ref_id} 响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"chart": {"type": "bar", "data": []}}]
        }
    )

    chart: dict[str, Any]


class TableArtifactResponse(BaseModel):
    """GET /chat/sessions/{session_id}/table-artifacts/{ref_id} 响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"table": {"columns": [], "rows": []}}]
        }
    )

    table: Any


class SessionPlanProgressRow(BaseModel):
    """SessionPlan 进度行投影。"""

    model_config = ConfigDict(extra="allow")

    step: Optional[str] = None
    status: Optional[str] = None


class SessionPlanViewResponse(BaseModel):
    """GET /chat/sessions/{session_id}/plan 响应（无信封时 204）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "sess-123",
                    "envelope_id": "env-1",
                    "user_goal": "缓冲区分析",
                    "query": "缓冲区分析",
                    "plan_id": "plan-1",
                    "recipe_id": None,
                    "progress": [],
                    "replaced": False,
                    "superseded": False,
                    "updated_at": "2026-09-11T00:00:00",
                }
            ]
        }
    )

    session_id: str
    envelope_id: str
    user_goal: Optional[str] = None
    query: Optional[str] = None
    plan_id: Optional[str] = None
    recipe_id: Optional[str] = None
    progress: list[SessionPlanProgressRow] = []
    replaced: bool = False
    superseded: bool = False
    updated_at: Optional[str] = None


class CartographicObservationResponse(BaseModel):
    """POST /chat/sessions/{session_id}/cartographic-observation 响应。

    review 裁决键由服务端评审器派生（findings 等），extra="allow" 透传。
    """

    model_config = ConfigDict(extra="allow")

    observation_sequence: int = 0
    observation_accepted: bool = True
    observation_rejection_reason: Optional[str] = None
    overall_passed: Optional[bool] = None


class MapActionAckResponse(BaseModel):
    """POST /chat/sessions/{session_id}/map-action-ack 响应（V3 闭环回执）。

    字段全部 Optional + 路由侧 exclude_none：内部 result dict 只在
    非零/评审触发时携带对应键，wire 形态保持与历史一致（缺键 = 无）。
    """

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {"accepted": 2, "duplicates": 0}
            ]
        },
    )

    accepted: Optional[int] = None
    duplicates: Optional[int] = None
    dropped: Optional[int] = None
    repair_action: Optional[dict[str, Any]] = None


class SkillsListResponse(BaseModel):
    """GET /chat/skills 响应（MD 技能目录，可选鉴权）。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"skills": []}]}
    )

    skills: list[Any] = []


class ClearSessionResponse(BaseModel):
    """DELETE /chat/sessions/{session_id} 响应。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "ok"}]}
    )

    status: str


class ToolsListResponse(BaseModel):
    """GET /chat/tools 响应（工具 schema 目录，含 tier-3）。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"tools": []}]}
    )

    tools: list[Any] = []


class ToolExecuteResponse(BaseModel):
    """POST /chat/tools/execute 响应（工具 dispatch 结果透传）。

    工具结果形态由具体工具决定（工具层契约自校验），本模型声明为
    任意键对象 —— 例外项记入 PR 附表与 ADR-0138。
    """

    model_config = ConfigDict(extra="allow")
