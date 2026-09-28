"""MapSpec mutation 纯原语（H02 解巨石 — 单一事实源）。

锁谓词与守卫分区（W15 锁下沉唯一事实源）、状态三分类/override 词表、
presentation 补丁与 durable 继承、mutation_id 幂等去重索引、workbench
doc 校验 —— 全部无 IO（除 dedup strip 的 best-effort 状态写）的确定性
原语。lifecycle_engine 原样 re-export，既有 import 面零破坏。

新增 intent 的 lock targets / effect 分类 / 锁守卫语义在此登记
（intent_lock_targets / _PRESENTATION_INTENT_TYPES），与
mutation_registry 的 descriptor 保持一致（一致性 gate 断言）。
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from app.services.session_data import session_data_manager
from app.services.mapspec.mutation_contracts import (
    MapSpecResult,
    MutationOrigin,
)
from app.services.mapspec.intents import (
    ApplyVisualHealPatchIntent,
    DuplicateComponentIntent,
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    PatchLayerStyleIntent,
    RebindComponentIntent,
    RemoveComponentIntent,
    RemoveLayerIntent,
    ReorderLayersIntent,
    RestoreStyleIntent,
    SetLayoutIntent,
    SetSceneIntent,
    MutationIntent,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
)

logger = logging.getLogger(__name__)



# Blocking 校验错误码：这些代表 mutation 引入的真实语义缺陷（引用不存在的
# source、stops 不足/非单调）。引入此类错误的 mutation 必须被拒绝。
# MISSING_SOURCES 不在其中：空 source 集合是"项目尚未成熟"的基线状态（如
# InitProject / 仅有 view 的会话），不是某次 mutation 的缺陷，保留为 warning。
BLOCKING_VALIDATION_CODES = {
    "INVALID_SOURCE_REF",
    "INVALID_STOPS_COUNT",
    "NON_INCREASING_STOPS",
    # ADR-0199：场景地形源悬空/类型错误 —— 与图层 INVALID_SOURCE_REF 同为
    # "引用不存在的数据面" 缺陷；声明了地形却指不到 raster-dem 源 = 想象的
    # 垂直证据，引入此类错误的 mutation 必须被拒绝（review P1-1 修复）。
    "SCENE_TERRAIN_SOURCE_REF",
    "SCENE_TERRAIN_SOURCE_TYPE",
}


# ─── 方向 8（ADR-0183）：mutation_id 幂等去重索引 ─────────────────────────────
#
# 键 `_mutation_dedup` 存 map_state（cache 层，随 commit 单事务落地）；值：
# mutation_id → {"revision", "ts", "origin", "committed": True}，FIFO 上限
# _MUTATION_DEDUP_LIMIT。语义（D-03）：
# - 命中 → 幂等重放：返回存证 revision + 当前权威 spec，**不重复执行操作、
#   不递增 revision**（at-most-once 效果）；检查在 CAS 之前 —— 响应丢失后的
#   重试此刻 expected_revision 已落后，幂等命中必须优先于 superseded。
# - 索引随 cache 过期丢失（诚实边界）：此时 CAS 仍是正确性地板（revision 已
#   推进 → superseded），仅「重启后同 id 重放」退化为 at-least-once。
_MUTATION_DEDUP_KEY = "_mutation_dedup"
_MUTATION_DEDUP_LIMIT = 64


def _dedup_index_of(pre_state: Dict[str, Any]) -> Dict[str, Any]:
    raw = pre_state.get(_MUTATION_DEDUP_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _dedup_hit(pre_state: Dict[str, Any], mutation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """已提交的同 id 存证（committed 条目）——失败/回滚尝试不留存证，可重试。"""
    if not mutation_id:
        return None
    hit = _dedup_index_of(pre_state).get(mutation_id)
    if isinstance(hit, dict) and hit.get("committed") is True:
        return hit
    return None


def _dedup_commit_fields(
    pre_state: Dict[str, Any],
    mutation_id: Optional[str],
    mutation_revision: int,
    origin: MutationOrigin,
) -> Optional[Dict[str, Any]]:
    """提交侧 extra_fields：新存证并入索引（FIFO 裁剪），随 save 单事务落地。"""
    if not mutation_id:
        return None
    index = _dedup_index_of(pre_state)
    index.pop(mutation_id, None)
    index[mutation_id] = {
        "revision": int(mutation_revision),
        "ts": time.time(),
        "origin": str(origin),
        "committed": True,
    }
    while len(index) > _MUTATION_DEDUP_LIMIT:
        index.pop(next(iter(index)))
    return {_MUTATION_DEDUP_KEY: index}


async def _dedup_strip(session_id: str, mutation_id: Optional[str]) -> None:
    """回滚侧兜底：剥除本 id 的存证（提交失败后同 id 重试必须可重新执行）。

    best-effort：失败只记日志（回滚主语义已由 _rollback_to_snapshot 承担）。
    review C1：读**当前** map_state 的索引而非锁起点快照 —— 本尝试的提交
    （save 成功）已把存证写进 live state，快照里没有它；读快照会让 strip
    空转，留下指向已回滚世代的孤儿 committed 条目（同 id 重试被静默吞掉，
    直到 FIFO 淘汰）。
    """
    if not mutation_id:
        return
    try:
        get_field = getattr(session_data_manager, "get_state_field", None)
        raw = (
            await get_field(session_id, _MUTATION_DEDUP_KEY)
            if callable(get_field)
            else None
        )
        index = dict(raw) if isinstance(raw, dict) else {}
        if mutation_id not in index:
            return
        index.pop(mutation_id)
        await session_data_manager.set_map_state(
            session_id, _MUTATION_DEDUP_KEY, index
        )
    except Exception:  # noqa: BLE001 — 兜底清理绝不掩盖主回滚结果
        logger.warning(
            "[mapspec] dedup index strip failed for session %s id=%s",
            session_id, mutation_id, exc_info=True,
        )



# workbench doc 载荷上限（组织态不携带数据 —— 大载荷属 layers/sources/ref）。
# 256KB 与前端预检同值（R2-C1：10k 图层全量 membership ~250-300KB，64KB
# 会让旗舰场景持久化停摆）。口径为真实 UTF-8 字节 —— 前端 TextEncoder 同
# 款精确口径（M-1：estimate_json_bytes 是码点近似、按其自述仅用于 metrics，
# 不作正确性闸）。
_MAX_WORKBENCH_DOC_BYTES = 256 * 1024
_WORKBENCH_GROUP_MAX_DEPTH = 4
_WORKBENCH_MODES = {"explore", "analyze", "compose"}


def _workbench_doc_error(doc: Any) -> Optional[str]:
    """校验 workbench doc；非法返回错误消息，合法返回 None。"""
    if not isinstance(doc, dict):
        return "workbench doc must be an object."
    if doc.get("version") != 5:
        return "workbench doc requires version == 5."
    # 真实字节口径（与前端 TextEncoder 一致）；doc 小，全量序列化成本可忽略。
    try:
        actual_bytes = len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    except Exception:  # noqa: BLE001 — 不可序列化载荷直接拒绝
        return "workbench doc is not JSON-serializable."
    if actual_bytes > _MAX_WORKBENCH_DOC_BYTES:
        return (
            "workbench doc exceeds "
            f"{_MAX_WORKBENCH_DOC_BYTES // 1024}KB — organization state "
            "does not carry data payloads."
        )

    groups = doc.get("groups")
    if not isinstance(groups, list):
        return "workbench doc.groups must be a list."
    ids: List[str] = []
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str) or not g.get("id"):
            return "workbench doc.groups entries require non-empty string id."
        ids.append(str(g["id"]))
    if len(ids) != len(set(ids)):
        return "workbench doc.groups ids must be unique."
    id_set = set(ids)
    for g in groups:
        parent = g.get("parentId")
        if parent is None:
            continue
        if not isinstance(parent, str) or parent not in id_set:
            return (
                f"workbench group {g.get('id')!r} parentId must reference "
                "an existing group (null for root)."
            )
    # 环与深度：逐组上溯；步数超组数即有环，深度超上限即拒绝。
    by_id = {str(g["id"]): g for g in groups}
    for gid in ids:
        depth = 1
        cur: Optional[dict] = by_id[gid]
        steps = 0
        while cur is not None and cur.get("parentId") is not None:
            parent = by_id.get(str(cur["parentId"]))
            if parent is None:
                break
            depth += 1
            steps += 1
            if steps > len(ids):
                return f"workbench group tree has a cycle at {gid!r}."
            cur = parent
        if depth > _WORKBENCH_GROUP_MAX_DEPTH:
            return (
                f"workbench group {gid!r} depth {depth} exceeds "
                f"{_WORKBENCH_GROUP_MAX_DEPTH}."
            )
    membership = doc.get("membership", {})
    if not isinstance(membership, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in membership.items()
    ):
        return "workbench doc.membership must map layerId (str) -> groupId (str)."
    locked = doc.get("lockedLayerIds", [])
    if not isinstance(locked, list) or not all(isinstance(x, str) for x in locked):
        return "workbench doc.lockedLayerIds must be a list of strings."
    # W15：component 级锁（version 兼容：缺席=空；非法类型与 lockedLayerIds
    # 同门校验并披露）。
    locked_components = doc.get("lockedComponentIds", [])
    if not isinstance(locked_components, list) or not all(
        isinstance(x, str) for x in locked_components
    ):
        return "workbench doc.lockedComponentIds must be a list of strings."
    if doc.get("mode") not in _WORKBENCH_MODES:
        return "workbench doc.mode must be one of explore|analyze|compose."
    return None


# ─── W15：Human-Agent 状态收敛 —— 锁下沉 + 状态三分类 + override 分类 ───
#（Contextual Cartographic Harness V6，见 07-context-state.md W15 节）
#
# 状态三分类边界（§32）：
# - semantic（语义态）：进 MapSpec/workflow 持久 —— 数据源、图层数据与
#   分类、提交姿态（view/basemap/time/组织态）。改语义走正常变更分类。
# - presentation（呈现态）：可见性/透明度/色板/图例位置 —— durable 呈现
#   决策持久（_preserve_durable_presentation / presentation_owner 印记），
#   但不污染科学语义。
# - transient（瞬态交互态）：pan/zoom 过程增量、hover、selection —— 只活
#   在前端，后端权威状态绝不持久化（strip_transient_state 在提交边界剥离，
#   锁内单事务、幂等、无行为变化）。已提交的 framed view（SetView 落账的
#   center/zoom）是 semantic 姿态，不是瞬态增量 —— 两者以「是否进持久化」
#   为界，不以字段名为界。

STATE_SEMANTIC = "semantic"
STATE_PRESENTATION = "presentation"
STATE_TRANSIENT = "transient"

# 瞬态交互键（顶层）：即使随载荷到达也绝不进持久化。
TRANSIENT_INTERACTION_KEYS = frozenset({"pan", "zoom", "hover", "selection"})

# override 三分类（§33/User override）：
# - semantic override：改 spec 语义 → 走正常变更分类与校验；
# - presentation override：仅呈现 → 不污染科学语义；
# - temporary UI override：纯前端瞬态（pan/zoom/hover/selection）→ 不构造
#   意图、不进引擎、不进 provenance（strip_transient_state 兜底）。
# override 记录来源 user/agent（provenance 的 origin/actor 既有模式 +
# detail.override_kind），支撑 user-wins 判定。最小可用：只分类 + 记录，
# 不扩展 planner 语义。
OVERRIDE_SEMANTIC = "semantic"
OVERRIDE_PRESENTATION = "presentation"
OVERRIDE_TEMPORARY_UI = "temporary_ui"
OVERRIDE_SOURCES = ("user", "agent", "system")

# 锁冲突披露词：对齐前端 failed/layer_locked（前端 LOCK_CONFLICT_ERROR =
# 'layer_locked'，ack.error 机器可读；后端权威拒绝必须携带同一 token）。
# 单码契约（B/Q1 结论）：前端无 component_locked 消费端（仅识别
# layer_locked），组件锁拒绝复用 LOCK_CONFLICT_CODE，载荷
# locked_component_ids 指明被锁组件 —— 不引入前端无法识别的第二码。
LOCK_CONFLICT_CODE = "layer_locked"
# 锁集读取上界（与 repair_planner 既有 [:64] 口径一致，有界披露）。
_MAX_LOCK_IDS = 64


def locked_layer_ids_of(mapspec: Optional[Dict[str, Any]]) -> List[str]:
    """workbench doc lockedLayerIds 读取（缺席/非法 → 空，不过度承诺）。"""
    if not isinstance(mapspec, dict):
        return []
    wb = mapspec.get("workbench")
    if not isinstance(wb, dict):
        return []
    locked = wb.get("lockedLayerIds", [])
    if not isinstance(locked, list):
        return []
    return [x for x in locked[:_MAX_LOCK_IDS] if isinstance(x, str) and x]


def locked_component_ids_of(mapspec: Optional[Dict[str, Any]]) -> List[str]:
    """workbench doc lockedComponentIds 读取（缺席=空，版本兼容）。"""
    if not isinstance(mapspec, dict):
        return []
    wb = mapspec.get("workbench")
    if not isinstance(wb, dict):
        return []
    locked = wb.get("lockedComponentIds", [])
    if not isinstance(locked, list):
        return []
    return [x for x in locked[:_MAX_LOCK_IDS] if isinstance(x, str) and x]


def _lock_matches(locked_id: str, target_id: str) -> bool:
    """锁命中判定（含层族语义，与 _should_remove_layer 谓词一致）。

    精确命中 + 双向族前缀（locked 存逻辑层、目标是物理层，或反之）——
    任一方向命中即拒绝，不留「换个 id 拼法绕过用户锁」的缺口。
    """
    if not isinstance(locked_id, str) or not target_id:
        return False
    if locked_id == target_id:
        return True
    for sep in ("-", "__"):
        if target_id.startswith(f"{locked_id}{sep}"):
            return True
        if locked_id.startswith(f"{target_id}{sep}"):
            return True
    return False


def is_entity_locked(entity: str, locked_entities: FrozenSet[str]) -> bool:
    """共享锁命中谓词（repair planner 复用，不各自手写锁判断）。"""
    if not entity:
        return False
    return any(_lock_matches(locked, entity) for locked in locked_entities)


@dataclass
class LockGuardResult:
    """guard_locked_partitions 的分区结果（locked/unlocked + 机器可读披露）。"""

    allowed_layer_ids: List[str] = field(default_factory=list)
    locked_layer_ids: List[str] = field(default_factory=list)
    allowed_component_ids: List[str] = field(default_factory=list)
    locked_component_ids: List[str] = field(default_factory=list)

    @property
    def has_locked(self) -> bool:
        return bool(self.locked_layer_ids or self.locked_component_ids)

    def disclosure(self) -> Dict[str, Any]:
        """机器可读锁披露（单码契约：图层/组件拒绝码均为 layer_locked）。

        码唯一（前端只识别 layer_locked），被锁目标由 locked_layer_ids /
        locked_component_ids 载荷区分 —— 组件拒绝不再使用第二码。
        """
        if self.locked_layer_ids:
            code = LOCK_CONFLICT_CODE
            message = (
                f"[{LOCK_CONFLICT_CODE}] 图层 {sorted(set(self.locked_layer_ids))} "
                "被用户锁定，agent 突变已拒绝（用户解锁是唯一 override）。"
            )
        elif self.locked_component_ids:
            code = LOCK_CONFLICT_CODE
            message = (
                f"[{LOCK_CONFLICT_CODE}] 组件 "
                f"{sorted(set(self.locked_component_ids))} 被用户锁定，"
                "agent 突变已拒绝（单码契约：与图层锁同码，载荷区分，"
                "用户解锁是唯一 override）。"
            )
        else:
            code = "unlocked"
            message = "无锁冲突。"
        return {
            "code": code,
            "locked_layer_ids": sorted(set(self.locked_layer_ids)),
            "locked_component_ids": sorted(set(self.locked_component_ids)),
            "message": message,
            "correction_hint": (
                "保留被锁目标的用户状态继续成图；如确需修改，请先由用户"
                "在工作台解锁后重试。"
            ),
        }


def guard_locked_partitions(
    mapspec: Optional[Dict[str, Any]],
    layer_ids: Iterable[str] = (),
    component_ids: Iterable[str] = (),
) -> LockGuardResult:
    """W15 统一锁 guard（§33 锁下沉的唯一事实源）。

    任何来源的 mutation（tool 直调 / command / mapspec mutation / batch /
    repair planner / quality_loop）经它把目标分区为 locked/unlocked：
    locked 部分拒绝 + 机器可读披露，unlocked 部分照常执行。确定性：
    同输入同分区（输入序去重，披露排序）。
    """
    locked_layers = locked_layer_ids_of(mapspec)
    locked_components = locked_component_ids_of(mapspec)
    result = LockGuardResult()
    for target in dict.fromkeys(layer_ids):
        if not isinstance(target, str) or not target:
            continue
        hits = [lid for lid in locked_layers if _lock_matches(lid, target)]
        if hits:
            for h in hits:
                if h not in result.locked_layer_ids:
                    result.locked_layer_ids.append(h)
        elif target not in result.allowed_layer_ids:
            result.allowed_layer_ids.append(target)
    for target in dict.fromkeys(component_ids):
        if not isinstance(target, str) or not target:
            continue
        hits = [c for c in locked_components if _lock_matches(c, target)]
        if hits:
            for h in hits:
                if h not in result.locked_component_ids:
                    result.locked_component_ids.append(h)
        elif target not in result.allowed_component_ids:
            result.allowed_component_ids.append(target)
    return result


def intent_lock_targets(intent: "MutationIntent") -> Tuple[List[str], List[str]]:
    """mutation 意图 → （目标图层 ids，目标组件 ids）。"""
    if isinstance(intent, (PatchLayerPresentationIntent, PatchLayerStyleIntent)):
        return ([intent.layer_id], [])
    if isinstance(intent, UpsertLayerIntent):
        layer = intent.layer if isinstance(intent.layer, dict) else {}
        lid = layer.get("id")
        return ([str(lid)] if isinstance(lid, str) and lid else [], [])
    if isinstance(intent, RemoveLayerIntent):
        return ([intent.layer_id], [])
    if isinstance(intent, ReorderLayersIntent):
        return ([lid for lid in intent.layer_ids if isinstance(lid, str)], [])
    if isinstance(intent, ApplyVisualHealPatchIntent):
        # ADR-0186：锁面 = 全部缺陷靶图层 + 遮挡者（遮挡者会被重排/压透明度）
        targets: List[str] = []
        for defect in intent.defects:
            for lid in defect.layer_ids:
                if isinstance(lid, str) and lid:
                    targets.append(lid)
            if isinstance(defect.occluder_layer_id, str) and defect.occluder_layer_id:
                targets.append(defect.occluder_layer_id)
        return (targets, [])
    if isinstance(
        intent,
        (
            PatchComponentIntent,
            RemoveComponentIntent,
            DuplicateComponentIntent,
            RebindComponentIntent,
        ),
    ):
        return ([], [intent.component_id])
    if isinstance(intent, SetLayoutIntent):
        ids = [
            str(c.get("id"))
            for c in (intent.components or [])
            if isinstance(c, dict) and isinstance(c.get("id"), str)
        ]
        return ([], ids)
    if isinstance(intent, RestoreStyleIntent):
        snap = intent.snapshot if isinstance(intent.snapshot, dict) else {}
        layer_ids = [
            str(lay.get("id"))
            for lay in (snap.get("layers") or [])
            if isinstance(lay, dict) and isinstance(lay.get("id"), str)
        ]
        layout = snap.get("layout") if isinstance(snap.get("layout"), dict) else {}
        comp_ids = [
            str(c.get("id"))
            for c in (layout.get("components") or [])
            if isinstance(c, dict) and isinstance(c.get("id"), str)
        ]
        return (layer_ids, comp_ids)
    return ([], [])


def guard_intent_locks(
    mapspec: Optional[Dict[str, Any]],
    intent: "MutationIntent",
    *,
    origin: MutationOrigin = "agent",
) -> Optional[MapSpecResult]:
    """单意图锁裁决：命中被锁目标 → 拒绝结果，否则 None（放行）。

    单意图 mutation 是原子事务（不可部分提交）→ 整笔拒绝；batch 逐
    intent 分区（见 apply_presentation_batch）。user 意图不受自有锁约束
    （用户解锁/操作是唯一 override）；agent/system 一律受 guard。
    """
    if origin == "user":
        return None
    layer_ids, component_ids = intent_lock_targets(intent)
    partition = guard_locked_partitions(
        mapspec, layer_ids=layer_ids, component_ids=component_ids
    )
    if not partition.has_locked:
        return None
    disclosure = partition.disclosure()
    return MapSpecResult(
        is_error=True,
        origin=origin,
        error_msg=disclosure["message"],
        correction_hint=disclosure["correction_hint"],
        # 精确码 + 被锁载荷随结果透出（ack/HTTP/tool 组装经 to_dict 原样
        # 携带；单码契约下组件拒绝亦为 layer_locked）。
        error_code=str(disclosure.get("code") or ""),
        locked_layer_ids=list(disclosure.get("locked_layer_ids") or []),
        locked_component_ids=list(disclosure.get("locked_component_ids") or []),
    )


def user_lock_pin_hit(
    mapspec: Optional[Dict[str, Any]],
    intent: "MutationIntent",
) -> bool:
    """方向 8（ADR-0183 D2）：user 意图是否触及 workbench 锁面（USER_PINNED 判据）。

    两类命中：(a) 目标（层/组件族）命中既有锁集 —— 用户对已 pin 资产的
    操作；(b) SetWorkbenchState 写入非空锁集 —— 用户 pin 决策本身。
    仅用于 producer 分类（信封/溯源），不做任何拒绝 —— user 是唯一 override。
    """
    layer_ids, component_ids = intent_lock_targets(intent)
    if layer_ids or component_ids:
        return guard_locked_partitions(
            mapspec, layer_ids=layer_ids, component_ids=component_ids
        ).has_locked
    if isinstance(intent, SetWorkbenchStateIntent):
        doc = intent.doc if isinstance(intent.doc, dict) else {}
        for key in ("lockedLayerIds", "lockedComponentIds"):
            value = doc.get(key)
            if isinstance(value, list) and any(
                isinstance(x, str) and x for x in value
            ):
                return True
    return False


# 呈现态意图类型（仅呈现，不污染科学语义；其余持久意图默认语义类）。
_PRESENTATION_INTENT_TYPES = (
    PatchLayerPresentationIntent,
    PatchLayerStyleIntent,
    SetLayoutIntent,
    PatchComponentIntent,
    RemoveComponentIntent,
    DuplicateComponentIntent,
    RebindComponentIntent,
    ApplyVisualHealPatchIntent,
    # ADR-0199：场景模式切换是 presentation 决策（不触碰数据/分类/图例）。
    SetSceneIntent,
)


def classify_override(
    intent: "MutationIntent", origin: MutationOrigin = "agent"
) -> Dict[str, str]:
    """User override 最小可用分类 + 来源记录（不扩展 planner 语义）。

    kind ∈ {semantic, presentation}；temporary_ui 意图永不构造（纯前端
    瞬态，不进引擎 —— 见 strip_transient_state）。source ∈ user/agent/
    system，原样记录（provenance origin/actor 既有模式）。
    """
    kind = (
        OVERRIDE_PRESENTATION
        if isinstance(intent, _PRESENTATION_INTENT_TYPES)
        else OVERRIDE_SEMANTIC
    )
    source = origin if origin in OVERRIDE_SOURCES else "agent"
    return {"kind": kind, "source": source}


def strip_transient_state(mapspec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """提交边界剥离瞬态交互态（transient 永不持久）。

    纯函数、幂等：无瞬态键时返回同一对象（零拷贝）；命中时顶层浅拷贝去键。
    """
    if not isinstance(mapspec, dict):
        return mapspec
    if not any(k in mapspec for k in TRANSIENT_INTERACTION_KEYS):
        return mapspec
    return {k: v for k, v in mapspec.items() if k not in TRANSIENT_INTERACTION_KEYS}

# #1220（audit3 C-7）：_locked_family_hit / _agent_locked_layer_guard 已删除
_OPACITY_PAINT_KEYS = {
    "circle": "circle-opacity",
    "fill": "fill-opacity",
    "line": "line-opacity",
    "raster": "raster-opacity",
    "heatmap": "heatmap-opacity",
    "fill-extrusion": "fill-extrusion-opacity",
    "symbol": "icon-opacity",
}


# layout.components 单条目载荷上限（QA-2026-08-26：LLM 直塞 FeatureCollection）
_MAX_COMPONENT_BYTES = 96 * 1024


def _estimate_component_bytes(component: Dict[str, Any]) -> int:
    """组件条目序列化尺寸估算（失败 → 超限，宁可拒绝不放大）。"""
    try:
        from app.lib.json_size import estimate_json_bytes
        return estimate_json_bytes(component)
    except Exception:  # noqa: BLE001
        return _MAX_COMPONENT_BYTES + 1


def _patch_layer_presentation(
    layer: Dict[str, Any],
    visible: Optional[bool],
    opacity: Optional[float],
    origin: str = "agent",
) -> Dict[str, Any]:
    patched = dict(layer)
    if visible is not None:
        layout = dict(patched.get("layout") or {})
        layout["visibility"] = "visible" if visible else "none"
        patched["layout"] = layout
        # CA-P1-1（意图投影）：presentation 是一次**显式决策**——它改写该层的
        # cartographic_intent.expected_visible，QA（RESULT_VISIBILITY）由此区分
        # "故意隐藏"（用户/agent 收口）与"结果层被误藏"（auto_safe 修复）。
        # 用户隐藏 → expected_visible=False → QA pass 且 user-wins 一致。
        # #1070: presentation_owner 持久落层 —— 用户决策的权威从 64 条环形
        # provenance（一次 finalize 写 30+ 条即驱逐）移到权威 spec 本身。
        intent = dict(patched.get("cartographic_intent") or {})
        same_value = (
            intent.get("presentation_owner") == "user"
            and intent.get("expected_visible") is not None
            and bool(intent.get("expected_visible")) == bool(visible)
        )
        intent["expected_visible"] = bool(visible)
        # v2(review R2-P2-1)：幂等同值重放不改写归属 —— agent finalize 的
        # show/hide 与用户既有决策同值时，owner 保持 user（每次 finalize
        # 把用户决策洗成 agent 会让 spec 印记系统性失真，user-wins 退化到
        # 只剩 64 条 ring 兜底）。值翻转才伴随归属转移。
        intent["presentation_owner"] = "user" if same_value else str(origin)
        patched["cartographic_intent"] = intent
    if opacity is not None:
        paint = dict(patched.get("paint") or {})
        paint["opacity"] = opacity
        type_key = _OPACITY_PAINT_KEYS.get(str(patched.get("type") or ""))
        if type_key:
            paint[type_key] = opacity
        patched["paint"] = paint
    return patched


def _project_cartographic_intent(layer: Dict[str, Any]) -> None:
    """Upsert 落意图（CA-P1-1）：authoring 决策写进 cartographic_intent。

    expected_visible = authoring 时的可见性决策（布局无 none 即默认展示）；
    role 从 context_role/role 透传，不发明。调用方显式给出的
    cartographic_intent 优先（planner 携带 product plan 的角色裁决）。
    """
    if isinstance(layer.get("cartographic_intent"), dict):
        return
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    intent: Dict[str, Any] = {
        "expected_visible": layout.get("visibility", "visible") != "none",
    }
    role = layer.get("context_role") or layer.get("role")
    if isinstance(role, str) and role:
        intent["role"] = role
    layer["cartographic_intent"] = intent


def _preserve_durable_presentation(
    existing: Dict[str, Any],
    incoming: Dict[str, Any],
) -> None:
    """同 id 整层 upsert 替换时的 durable presentation 继承（user wins）。

    用户隐藏/调透明度的层被 agent 重跑查询后整层 upsert：数据、样式与
    分类以 agent 新结果为准，但 durable 的显隐/透明度决策属于用户——
    agent 本次未显式给出时必须保留（否则用户隐藏的层静默回默认可见，
    reload 后用户决策彻底丢失）。图层类型改变时只继承 visibility
    （类型专属 opacity 键对新类型无效）。

    #1070(F-3): 既有层的 cartographic_intent.presentation_owner=="user" 且
    用户决策为隐藏时，agent upsert 显式携带的 layout.visibility 也一并
    剥离（此前只在 incoming 未给 visibility 时保留 —— 显式给出即绕过，
    且 expected_visible 被洗成 True 让下游 AUTO_SAFE 修复无从分辨）。
    持久 owner 印记随之继承，守卫据此长期有效。
    """
    existing_layout = existing.get("layout") if isinstance(existing.get("layout"), dict) else {}
    incoming_layout = incoming.get("layout") if isinstance(incoming.get("layout"), dict) else {}
    existing_intent = (
        existing.get("cartographic_intent")
        if isinstance(existing.get("cartographic_intent"), dict) else {}
    )
    user_owned_hidden = (
        existing_intent.get("presentation_owner") == "user"
        and existing_layout.get("visibility") == "none"
    )
    if user_owned_hidden:
        # agent 的 visibility 覆写被剥离，用户隐藏保留；owner 印记继承。
        merged_layout = {**incoming_layout, "visibility": "none"}
        incoming["layout"] = merged_layout
        incoming_intent = dict(incoming.get("cartographic_intent") or {})
        incoming_intent["expected_visible"] = False
        incoming_intent["presentation_owner"] = "user"
        incoming["cartographic_intent"] = incoming_intent
    elif (
        existing_layout.get("visibility") == "none"
        and incoming_layout.get("visibility") is None
    ):
        incoming["layout"] = {**incoming_layout, "visibility": "none"}

    existing_paint = existing.get("paint") if isinstance(existing.get("paint"), dict) else {}
    incoming_paint = incoming.get("paint") if isinstance(incoming.get("paint"), dict) else {}
    if not existing_paint:
        return
    existing_opacity = existing_paint.get("opacity")
    if (
        existing_opacity is not None
        and incoming_paint.get("opacity") is None
        and str(existing.get("type") or "") == str(incoming.get("type") or "")
    ):
        merged = dict(incoming_paint)
        merged["opacity"] = existing_opacity
        type_key = _OPACITY_PAINT_KEYS.get(str(incoming.get("type") or ""))
        if type_key and type_key not in merged:
            merged[type_key] = existing_opacity
        incoming["paint"] = merged
