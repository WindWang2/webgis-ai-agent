"""Scenario Harness —— 情境化多方案候选（G05 契约面，ADR-0193 之上的编排层）。

职责边界（防重复施工）：

- **隔离机制只有一套**：MapSpec 事务分支（``simulation.whatif.branch_manager``，
  派生会话 ``{parent}__wif_{branch}``，ADR-0257 机器全量继承）。本包不造第二套
  分支/隔离/事务实现，只在分支之上补齐：共享基准绑定、候选生命周期、
  基准漂移失效、选择性采纳事务、用户锁定与情境投影。
- **权威状态只有一份**：父会话 MapSpec（desired）。候选只是"可重放提案 +
  隔离分支产物 refs"，采纳前永远不影响父会话；采纳经既有
  ``apply_gis_mutation*``（CAS + user-wins + provenance）落账。
- **payload 红线**：注册表条目只存 ref/摘要/digest/有界 delta 描述（单条
  ≤32KB、整笔 ≤128KB 的最小可重放载荷内联）；候选数据体留在分支会话与
  会话 refs，不在父会话 map_state 复制数据面。

状态词表（WP2）：``draft → running → comparable → adopted``；旁路
``stale``（基准漂移，可恢复）/ ``failed`` / ``cancelled`` / ``archived``。
``stale`` 不是终态：局部重算（在分支上补干预）后回 ``comparable``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ── 注册表键（父会话 map_state；与 whatif 的 _whatif_branches 分键共存）──

REGISTRY_CANDIDATES = "_scenario_candidates"
REGISTRY_BASELINES = "_scenario_baselines"
REGISTRY_LOCKS = "_scenario_locks"
REGISTRY_ADOPTIONS = "_scenario_adoptions"

# ── 有界上限（超限整笔拒绝并披露，绝不静默截断）──

MAX_CANDIDATES = 5  # 与 whatif MAX_ACTIVE_BRANCHES 同量级（分键不分权）
MAX_BASELINES = 12  # 基准环形档案（含历史，供跨候选审计）
MAX_LOCKS = 16  # 用户锁定环
MAX_ADOPTIONS = 16  # 采纳回执环（幂等重放查这里）
MAX_DELTA_INTENTS = 24  # 单候选干预描述上限
MAX_DELTA_ITEM_BYTES = 32 * 1024
MAX_DELTA_TOTAL_BYTES = 128 * 1024
MAX_TITLE = 80
MAX_HYPOTHESIS = 200
MAX_REASON = 160
MAX_TARGET_ID = 120
MAX_FIELD_PATHS = 16
MAX_COMPARABLE_CANDIDATES = 5  # 单次 compare 上限

#: 候选分支 id 前缀：与用户直连 whatif 分支 id 隔离命名空间（共用
#: branch_session_id 派生函数，故必须保证跨注册表不撞车）。
CANDIDATE_BRANCH_PREFIX = "sc"

CandidateState = Literal[
    "draft",
    "running",
    "comparable",
    "adopted",
    "stale",
    "failed",
    "cancelled",
    "archived",
]

#: 干预语义分组（选择性采纳的选择面；由 intent kind 确定性推导）。
DeltaGroup = Literal["analysis", "style", "layout", "component", "data"]

_GROUP_OF_KIND: Dict[str, DeltaGroup] = {
    "UpsertLayerIntent": "analysis",
    "PatchLayerPresentationIntent": "style",
    "UpsertSourceIntent": "data",
    "PatchLayerStyleIntent": "style",
    "PatchLayerLabelAuthoringIntent": "style",
    "RestoreStyleIntent": "style",
    "SetLayoutIntent": "layout",
    "PatchComponentIntent": "component",
    "RemoveComponentIntent": "component",
    "DuplicateComponentIntent": "component",
    "RebindComponentIntent": "component",
    "SetBasemapIntent": "layout",
    "SetTimeIntent": "analysis",
    "SetTemporalViewIntent": "analysis",
    "SetScenarioModeIntent": "layout",
    "PatchWorkbenchDeltaIntent": "layout",
    "SetFrameProductIntent": "layout",
}


def group_of_kind(kind: str) -> DeltaGroup:
    """intent kind → 语义分组（确定性映射；未知 kind 归 layout 披露）。"""
    return _GROUP_OF_KIND.get(str(kind or ""), "layout")


# ── 共享基准（WP2）───────────────────────────────────────────────────


class BaselineDatasetAnchor(BaseModel):
    """基准绑定的数据版本锚（ref → 观察到的 content revision）。"""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    content_revision: str = ""
    version_fingerprint: str = ""


class ScenarioBaseline(BaseModel):
    """共享基准绑定：候选的公共出发点（一次捕获，多候选引用）。

    绑定的是**事实**（spec 修订/指纹、数据版本、目标摘要），不是聊天文本。
    ``goal_digest`` 取 working context basis 的确定性摘要 —— 目标条件变化
    （分母/区域/时间窗）通过它驱动候选失效（WP3），不解析自然语言。
    """

    model_config = ConfigDict(extra="forbid")

    baseline_id: str
    session_id: str
    mapspec_revision: int = 0
    mapspec_fingerprint: str = ""
    datasets: List[BaselineDatasetAnchor] = Field(default_factory=list, max_length=12)
    goal_digest: str = ""
    selection_digest: str = ""
    captured_at: str = ""
    turn_id: str = ""
    note: str = Field(default="", max_length=MAX_REASON)

    def dataset(self, ref_id: str) -> Optional[BaselineDatasetAnchor]:
        rid = str(ref_id or "")
        for ds in self.datasets:
            if ds.ref_id == rid:
                return ds
        return None


# ── 候选干预描述（可重放提案；payload 有界内联）──────────────────────


class DeltaIntent(BaseModel):
    """一条候选干预的结构化描述（序列化意图，采纳时确定性重放）。

    ``payload`` 是该意图的最小可重放载荷（如 paint 补丁 dict）；
    超过 ``MAX_DELTA_ITEM_BYTES`` 的提案整笔拒绝 —— 大数据不进注册表，
    候选引用分支会话的产物，不在父会话存副本。
    """

    model_config = ConfigDict(extra="forbid")

    seq: int  # 提案内单调序号（1 起；选择性采纳的选择键）
    kind: str  # MutationIntent 类名（应用侧白名单校验）
    group: DeltaGroup = "layout"
    target_id: str = Field(
        default="", max_length=MAX_TARGET_ID
    )  # layer/component/source id
    field_paths: List[str] = Field(default_factory=list, max_length=MAX_FIELD_PATHS)
    summary: str = Field(default="", max_length=MAX_REASON)
    payload: Dict[str, Any] = Field(default_factory=dict)
    #: 提案时观察到的父会话基准值（三方合并的 base 边）：小值内联
    #: （paint 键/标量）；大对象只存 ``<path>.digest``（如整层快照）。
    #: 缺席 = 未观察（采纳时按"不可判定基线"保守处理）。
    base_values: Dict[str, Any] = Field(default_factory=dict)


# ── 候选（注册表条目；引用 whatif 分支，不复制其元数据）──────────────


class ScenarioCandidate(BaseModel):
    """候选方案注册表条目（父会话 map_state；无任何数据 payload）。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    title: str = Field(default="", max_length=MAX_TITLE)
    hypothesis: str = Field(default="", max_length=MAX_HYPOTHESIS)
    session_id: str  # 父会话（权威态所属）
    branch_session_id: str  # 隔离分支会话（whatif branch_session_id 派生）
    branch_id: str  # whatif 注册表键（sc 前缀）
    baseline_id: str  # 共享基准绑定
    fork_revision: int = 0  # 分叉时父会话 mutation revision（= 基准 revision）
    state: CandidateState = "draft"
    delta: List[DeltaIntent] = Field(default_factory=list, max_length=MAX_DELTA_INTENTS)
    applied_seqs: List[int] = Field(default_factory=list, max_length=MAX_DELTA_INTENTS)
    #: 被 user-wins 守卫拦截的干预 seq（用户所有权字段，agent 不可试验；
    #: 采纳时同条会降级 user_wins_skip 逐项披露）。
    blocked_seqs: List[int] = Field(default_factory=list, max_length=MAX_DELTA_INTENTS)
    created_at: str = ""
    updated_at: str = ""
    created_by: Literal["agent", "user"] = "agent"
    turn_id: str = ""
    #: 采纳回执 id（state=adopted 时指向 REGISTRY_ADOPTIONS）。
    adoption_id: str = ""
    #: 最近一次失效归因（WP3；恢复时清空）。
    stale_reasons: List[str] = Field(default_factory=list, max_length=8)
    #: 对比摘要 digest（comparable 时由 compare 写入；不存大结果）。
    compare_digest: str = ""


# ── 用户锁定（WP1）───────────────────────────────────────────────────

LockKind = Literal["layer_field", "component_field", "analysis_param"]


class ScenarioLock(BaseModel):
    """用户显式锁定：任务级"这个不许动"的权威记录（user 决策，非 Agent 推断）。

    与 user-wins 守卫的关系：守卫在 mutation 当刻从 provenance 推导 ownership
    （覆盖显隐/透明度族）；本记录是**任务级显式 pin**，覆盖守卫覆盖不到的
    语义（如"配色定死""分母必须用常住人口"）。采纳前与干预前都过锁检查。
    """

    model_config = ConfigDict(extra="forbid")

    lock_id: str
    kind: LockKind
    target_id: str = Field(default="", max_length=MAX_TARGET_ID)
    field_paths: List[str] = Field(default_factory=list, max_length=MAX_FIELD_PATHS)
    value_digest: str = ""  # 锁定时观察值的 digest（漂移可见性）
    reason: str = Field(default="", max_length=MAX_REASON)
    created_at: str = ""
    turn_id: str = ""
    session_id: str = ""
    released_at: str = ""  # 非空 = 已释放（保留审计）
    released_reason: str = Field(default="", max_length=MAX_REASON)

    @property
    def active(self) -> bool:
        return not self.released_at

    def covers(self, target_id: str, field_path: str) -> bool:
        """确定性覆盖判定：target 匹配（空 target = 全局锁）且字段命中。"""
        if not self.active:
            return False
        if self.target_id and self.target_id != str(target_id or ""):
            return False
        if not self.field_paths:
            return True
        fp = str(field_path or "")
        return any(
            fp == p or fp.startswith(p + ".") or p in ("*", "")
            for p in self.field_paths
        )


# ── 基准漂移与影响分级（WP3）────────────────────────────────────────


class BaselineMove(BaseModel):
    """共享基准自捕获以来的漂移观察（确定性比较，逐锚披露）。"""

    model_config = ConfigDict(extra="forbid")

    baseline_id: str
    moved: bool = False
    revision_from: int = 0
    revision_to: int = 0
    fingerprint_changed: bool = False
    dataset_changes: List[Dict[str, str]] = Field(default_factory=list, max_length=12)
    goal_changed: bool = False
    observed_at: str = ""

    def reasons(self) -> List[str]:
        """失效归因词表（有界、机器可读）。"""
        out: List[str] = []
        if self.fingerprint_changed:
            out.append(f"mapspec.revision:{self.revision_from}->{self.revision_to}")
        for ch in self.dataset_changes:
            rid = ch.get("ref_id", "?")
            out.append(f"dataset.{rid}:{ch.get('from', '?')}->{ch.get('to', '?')}")
        if self.goal_changed:
            out.append("goal.digest_changed")
        return out[:8]


ImpactLevel = Literal["current", "recompute_required", "adopt_blocked", "unresolvable"]


class CandidateImpact(BaseModel):
    """单候选对基准漂移的影响分级（WP3 影响分析）。

    - ``current``：基准未动，候选可直接比较/采纳；
    - ``recompute_required``：漂移字段与候选干预目标相交 → 局部重算
      （在分支上补干预/重跑，不盲目重跑整个任务）；
    - ``adopt_blocked``：父会话用户/Agent 已改候选目标字段 → 采纳前必须
      冲突裁决（用户锁定优先）；
    - ``unresolvable``：基准数据版本推进且候选结果依赖旧版本 → 不可采纳。
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    level: ImpactLevel = "current"
    reasons: List[str] = Field(default_factory=list, max_length=8)


# ── 采纳（WP4）───────────────────────────────────────────────────────

AdoptionItemVerdict = Literal[
    "applied",  # 已应用（无冲突路径）
    "user_wins_skip",  # 用户锁定/用户已改 → 候选项被拒
    "agent_conflict",  # 父会话 Agent 已改同字段 → 需用户仲裁，本轮拒
    "target_missing",  # 目标在父会话已不存在
    "lock_blocked",  # 命中用户显式锁定
    "not_selected",  # 用户未选择该组/项
]


class AdoptionItem(BaseModel):
    """单条干预的采纳裁决（逐条披露，不掩盖部分成功）。"""

    model_config = ConfigDict(extra="forbid")

    seq: int
    kind: str = ""
    group: DeltaGroup = "layout"
    target_id: str = ""
    verdict: AdoptionItemVerdict
    detail: str = Field(default="", max_length=MAX_REASON)


class AdoptionReceipt(BaseModel):
    """采纳回执：幂等键 + 逐项裁决 + 落账 revision（WP4 决议存证）。"""

    model_config = ConfigDict(extra="forbid")

    adoption_id: str
    session_id: str
    candidate_id: str
    base_revision: int = 0  # 采纳时父会话 revision（CAS expected_revision）
    final_revision: int = 0  # 采纳后 revision
    selected_groups: List[str] = Field(default_factory=list, max_length=5)
    items: List[AdoptionItem] = Field(
        default_factory=list, max_length=MAX_DELTA_INTENTS
    )
    applied_count: int = 0
    skipped_count: int = 0
    adopted_at: str = ""
    turn_id: str = ""
    idempotency_key: str = ""
    reverted_by: str = ""  # 回退采纳的 adoption id（审计链）
    #: 采纳前父会话 checkpoint（回退锚；RollbackIntent 消费）。
    pre_adopt_checkpoint_id: str = ""
    #: 回退时被一并丢弃的采纳后 revision 数（review P2-2 披露；0 = 无漂移）。
    discarded_revisions: int = 0

    def find_item(self, seq: int) -> Optional[AdoptionItem]:
        for item in self.items:
            if item.seq == seq:
                return item
        return None


# ── 采纳请求（工具/API 入参契约）────────────────────────────────────


class AdoptSelection(BaseModel):
    """选择性采纳的选择面：按组、按项或"全部无冲突项"。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["all", "groups", "seqs"] = "all"
    groups: List[DeltaGroup] = Field(default_factory=list, max_length=5)
    seqs: List[int] = Field(default_factory=list, max_length=MAX_DELTA_INTENTS)
    #: 乐观并发令牌（缺省 = 服务端取当前 revision；显式提供时强 CAS）。
    expected_revision: Optional[int] = None


# ── 对比（WP3）───────────────────────────────────────────────────────


class CandidateDiffRow(BaseModel):
    """候选 vs 共享基准（或候选间）的一行结构化差异（有界摘要）。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    layer_adds: List[str] = Field(default_factory=list, max_length=12)
    layer_removes: List[str] = Field(default_factory=list, max_length=12)
    style_fields: List[str] = Field(default_factory=list, max_length=MAX_FIELD_PATHS)
    component_changes: List[str] = Field(default_factory=list, max_length=12)
    metric_deltas: Dict[str, float] = Field(default_factory=dict)
    comparable: bool = True
    incomparable_reason: str = Field(default="", max_length=MAX_REASON)


class ScenarioCompareResult(BaseModel):
    """一次多候选对比的结果面（基线锚 + 逐候选行 + 影响分级）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    baseline_id: str
    baseline_revision: int = 0
    move: Optional[BaselineMove] = None
    rows: List[CandidateDiffRow] = Field(
        default_factory=list, max_length=MAX_COMPARABLE_CANDIDATES
    )
    impacts: List[CandidateImpact] = Field(
        default_factory=list, max_length=MAX_CANDIDATES
    )
    compare_digest: str = ""


__all__ = [
    "REGISTRY_ADOPTIONS",
    "REGISTRY_BASELINES",
    "REGISTRY_CANDIDATES",
    "REGISTRY_LOCKS",
    "CANDIDATE_BRANCH_PREFIX",
    "MAX_ADOPTIONS",
    "MAX_BASELINES",
    "MAX_CANDIDATES",
    "MAX_COMPARABLE_CANDIDATES",
    "MAX_DELTA_INTENTS",
    "MAX_DELTA_ITEM_BYTES",
    "MAX_DELTA_TOTAL_BYTES",
    "MAX_LOCKS",
    "AdoptSelection",
    "AdoptionItem",
    "AdoptionItemVerdict",
    "AdoptionReceipt",
    "BaselineDatasetAnchor",
    "BaselineMove",
    "CandidateDiffRow",
    "CandidateImpact",
    "CandidateState",
    "DeltaGroup",
    "DeltaIntent",
    "ImpactLevel",
    "LockKind",
    "ScenarioBaseline",
    "ScenarioCandidate",
    "ScenarioCompareResult",
    "ScenarioLock",
    "group_of_kind",
]
