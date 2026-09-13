"""GISSituation —— 结构化 GIS 情境契约 v1（方向 2 S1，ADR-0180）。

把"聊天上下文"升级为有来源、有新鲜度、有 revision、有界的会话世界状态。
十一个 context 分区对齐任务书模型；每个分区由命名 SitFact 组成（严格
schema，extra=forbid —— 拼写错误在编译期暴露而非静默丢字段）。

红线（与 V6 三层块 / GISWorldState 同纪律）：
- **绝无 payload**：图层/数据集只携带 ref/类型/计数/摘要（有界）；
- **确定性**：同 store 输入同输出 —— 无 wall-clock（compiled_at 由调用方
  传入）、无随机、无 LLM；全部列表固定排序；
- **显式 unknown**：无证据是 known 语义的一部分，不猜默认值；
- **只读**：编译/投影不写任何权威状态（唯一例外：diff 层的前进快照，
  见 diff.py，单向只进不退）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_situation.facts import SitFact

COMPILER_VERSION = 1

#: 有界上限（编译期裁剪；投影层另有 byte cap）。
MAX_DATASET_FACTS = 24
MAX_LAYER_SUMMARIES = 100
MAX_SOURCE_SUMMARIES = 100
MAX_PROGRESS_ROWS = 16
MAX_INTERACTIONS = 12
MAX_PROVENANCE_EVIDENCE = 8


class SituationRevision(BaseModel):
    """复合 revision（字典序单调）：mutation 权威令牌 × 观察序 × 交互序。

    迟到的观察/交互事件只允许推高对应分量；任何分量倒退即为 regressed
    （diff 层拒收，快照不前进 —— DC-5）。
    """

    model_config = ConfigDict(extra="forbid")

    mutation_revision: int = 0
    observation_sequence: int = 0
    interaction_sequence: int = 0

    def as_tuple(self) -> tuple:
        return (
            self.mutation_revision,
            self.observation_sequence,
            self.interaction_sequence,
        )

    def ge(self, other: "SituationRevision") -> bool:
        return self.as_tuple() >= other.as_tuple()


class SituationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    turn_id: str = ""
    revision: SituationRevision = Field(default_factory=SituationRevision)
    compiled_at: str = ""  # 调用方传入（session 冻结时钟策略）；确定性要求
    compiler_version: int = COMPILER_VERSION


class UserGoalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: SitFact
    plan_id: SitFact
    recipe_id: SitFact
    progress: SitFact  # [{capability,status}] 有界


class GeographicContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewport: SitFact       # 前端 observed（center/zoom/bearing/pitch/bounds）
    framed_view: SitFact    # agent desired（mapspec.view，framed 语义）
    scope_name: SitFact     # 视口区域名（viewport_naming 派生）
    user_location: SitFact
    scale: SitFact          # zoom → 档位（derived.zoom，不是新事实源）
    crs: SitFact            # v1 无权威 CRS 事实 → 显式 unknown


class TemporalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_started_at: SitFact
    requested_period: SitFact   # SessionPlan 章节的时间要求（无则 unknown）
    data_coverage: SitFact      # 描述符时间覆盖摘要（有界）
    active_time_slice: SitFact  # v1 无权威时间片事实 → 显式 unknown


class DataContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasets: SitFact    # [{ref_id,alias,kind?,content_revision?,feature_count?}] 有界
    active_roles: SitFact  # {role: ref_id}（mapspec sources 的 context_role）
    quality: SitFact     # v1 无会话级质量事实 → unknown（ads facts 属 project 级）
    freshness: SitFact   # 观察到的最大 ref content_revision


class MapContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desired_revision: SitFact
    fingerprint: SitFact
    layers: SitFact      # 有界摘要（id/type/visible/role）
    layer_count: SitFact
    sources: SitFact     # 有界摘要
    basemap: SitFact
    observed: SitFact    # runtime 观察阶梯摘要（observation_states 派生）


class AnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_progress: SitFact  # {total,ready,running,complete,failed}
    stale_nodes: SitFact
    artifacts: SitFact      # 进度行 bound_ref 去重有界


class CartographicContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: SitFact       # should_inject_verdict 门后三态（none 即 unknown 语义）
    product_status: SitFact
    render_status: SitFact
    recipe_id: SitFact


class InteractionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_feature: SitFact
    focus_layer_id: SitFact
    user_hidden_layers: SitFact   # durable user 决策（provenance 裁决）
    pending_mutations: SitFact    # 进行中后台任务（event_log 派生）
    recent_interactions: SitFact  # S4 交互环投影（有界）
    display_mode: SitFact         # is_3d


class DeliveryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: SitFact       # v1：unknown（交付意图尚无会话级权威事实）
    display_mode: SitFact
    export_format: SitFact


class ConstraintsContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explicit: SitFact    # 用户显式约束（SessionPlan 章节；无则 unknown）
    budget: SitFact      # v1 unknown
    security: SitFact    # v1 unknown


class SituationEvidence(BaseModel):
    """编译证据面：哪些源在场、哪些失败、哪些被有界裁剪、溯源尾部。"""

    model_config = ConfigDict(extra="forbid")

    sources_ok: List[str] = Field(default_factory=list)
    sources_unavailable: List[str] = Field(default_factory=list)
    omitted: List[str] = Field(default_factory=list)  # 编译期裁剪记录（通道名）
    provenance_tail: List[Dict[str, Any]] = Field(default_factory=list)
    snapshot_advanced: Optional[bool] = None  # diff 层回填：快照是否前进


class GISSituation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: SituationIdentity
    user_goal: UserGoalContext
    geographic: GeographicContext
    temporal: TemporalContext
    data: DataContext
    map: MapContext
    analysis: AnalysisContext
    cartographic: CartographicContext
    interaction: InteractionContext
    delivery: DeliveryContext
    constraints: ConstraintsContext
    evidence: SituationEvidence

    # ── 序列化 ──────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """紧凑 JSON 面（unknown 事实无 value；供快照持久化与 inspector）。"""
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GISSituation":
        return cls.model_validate(data)

    def iter_facts(self):
        """遍历 (context_name, fact_name, fact) —— 查询/diff/inspector 共用。"""
        for ctx_name in (
            "user_goal", "geographic", "temporal", "data", "map",
            "analysis", "cartographic", "interaction", "delivery",
            "constraints",
        ):
            ctx = getattr(self, ctx_name)
            for fact_name in ctx.model_dump():
                yield ctx_name, fact_name, getattr(ctx, fact_name)
