"""Interaction IR V1 契约常量与词表（Goal 02 / ADR-0227）。

本模块是交互事件分类学的**单一权威**：

- ``SCHEMA_VERSION`` —— 事件信封版本（``interaction_ir.v1``）；
- ``INTERACTION_KINDS`` —— 封闭 kind 词表 + 每 kind 元数据
  （payload 白名单 / 合并策略 / 敏感级 / authority 归属）；
- :func:`normalize_payload` —— 服务端有界化（键白名单 + 值长 + 总字节），
  与 ``gis_situation/observation.py`` 同纪律，但**任何 kind 都不允许原始
  feature properties 进入 ledger**（隐私最小化，ADR-0227 H）。

前端镜像常量在 ``frontend/lib/interaction/contract-manifest.ts``，两侧由
``tests/unit/gis_harness/interaction_ir/contract_manifest.json`` 双向漂移闸
锁定（carto_ir manifest 模式）。

纪律（ADR-0227）：
- Interaction IR 是 observation/event layer —— 记录"发生了什么 + 与哪个
  authority revision 关联"；MapSpec / GISWorldState / Requirement IR 仍是
  各自领域的唯一真相；
- kind 词表只收录前端**真实存在**的交互（见 ADR 归纳表），不为词表完整
  伪造事件；
- 高频 kind（viewport / temporal scrub）必须 ``replace_pending`` 合并 ——
  只上报 settle 后的终态，不逐帧持久化。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Tuple

#: 事件信封 schema 版本（版本化协议演进锚点）。
SCHEMA_VERSION = "interaction_ir.v1"

# ── 合并策略 ─────────────────────────────────────────────────────────────
COALESCE_NONE = "none"
#: 队列中同 kind 只保留最新未发条目（viewport / temporal scrub 等 settle 语义）。
COALESCE_REPLACE_PENDING = "replace_pending"
COALESCE_POLICIES = frozenset({COALESCE_NONE, COALESCE_REPLACE_PENDING})

# ── 敏感级 ───────────────────────────────────────────────────────────────
#: 普通内部遥测（无用户数据面）。
SENSITIVITY_INTERNAL = "internal"
#: 含要素引用/字段名/几何摘要 —— 最小化载荷，禁止原始属性值。
SENSITIVITY_LIMITED = "limited"
SENSITIVITY_CLASSES = frozenset({SENSITIVITY_INTERNAL, SENSITIVITY_LIMITED})

# ── authority 归属 ───────────────────────────────────────────────────────
#: 真相在 MapSpec lifecycle（mutation receipt 链接面）。
AUTHORITY_MAPSPEC = "mapspec"
#: 纯观察事实（无持久状态突变；AOI/量测几何本体留在客户端画布层）。
AUTHORITY_OBSERVATION = "observation"

# ── mutation 结局（客户端申报，服务端对 _mutation_dedup 交叉校验）────────
OUTCOME_COMMITTED = "committed"
OUTCOME_SUPERSEDED = "superseded"
OUTCOME_FAILED = "failed"
MUTATION_OUTCOMES = frozenset({OUTCOME_COMMITTED, OUTCOME_SUPERSEDED, OUTCOME_FAILED})


@dataclass(frozen=True)
class KindSpec:
    """一个交互 kind 的封闭元数据（词表登记项）。"""

    kind: str
    #: payload 键白名单（服务端 normalize 丢弃未知键）。
    payload_keys: FrozenSet[str]
    #: 该 kind 是否允许空 payload（信号型事件，如 deselected/cleared）。
    allow_empty_payload: bool = False
    state_changing: bool = False
    authority: str = AUTHORITY_OBSERVATION
    coalesce: str = COALESCE_NONE
    sensitivity: str = SENSITIVITY_INTERNAL


def _k(
    kind: str,
    keys: Tuple[str, ...] = (),
    *,
    allow_empty: bool = False,
    state_changing: bool = False,
    authority: str = AUTHORITY_OBSERVATION,
    coalesce: str = COALESCE_NONE,
    sensitivity: str = SENSITIVITY_INTERNAL,
) -> Tuple[str, KindSpec]:
    return kind, KindSpec(
        kind=kind,
        payload_keys=frozenset(keys),
        allow_empty_payload=allow_empty,
        state_changing=state_changing,
        authority=authority,
        coalesce=coalesce,
        sensitivity=sensitivity,
    )


#: 封闭词表：kind → 元数据。每个 kind 都对应前端真实交互路径（ADR 归纳表；
#: 评估后**未收录**的候选见 ADR-0227「词表边界」：temporal scrub / feature
#: inspect / measurement clear —— 前端无对应交互钩子，不为词表完整伪造）。
INTERACTION_KINDS: Dict[str, KindSpec] = dict([
    # ── 观察型：viewport（settle 后上报，replace_pending 合并）──────────
    _k(
        "viewport_settled",
        ("center", "zoom", "bearing", "pitch", "bounds"),
        coalesce=COALESCE_REPLACE_PENDING,
    ),
    # ── 观察型：要素选择/取消（有限载荷：引用 + 字段名 + 几何摘要）──────
    # /goal 03 additive：selected_ids（多选集，normalize 的 list 面纪律
    # ≤8 标量元素）—— 框选/多选对 Agent 可见；单选时缺席（向后兼容）。
    # SW-02（#2004）additive：matched_count（真实命中数，前端 selection-store
    # 同源披露）+ ids_truncated（服务端派生，客户端申报不可信）—— 框选 >8
    # 要素不再静默截断（selected_ids 专属上限 50，三方对齐：前端
    # selection-store / analysis_scope.MAX_SCOPE_FEATURE_IDS）。
    _k(
        "feature_selected",
        ("layer_id", "feature_id", "field_names", "geometry_digest", "dataset_ref", "selected_ids", "id_field", "matched_count", "ids_truncated", "bbox", "selection_handle"),
        sensitivity=SENSITIVITY_LIMITED,
    ),
    _k(
        "feature_deselected",
        ("layer_id", "feature_id"),
        allow_empty=True,
        sensitivity=SENSITIVITY_LIMITED,
    ),
    # ── 观察型：AOI 绘制/编辑/删除（几何本体留客户端，ledger 只存摘要）──
    _k(
        "aoi_drawn",
        ("geometry_type", "vertex_count", "geometry_digest"),
        sensitivity=SENSITIVITY_LIMITED,
    ),
    _k(
        "aoi_edited",
        ("geometry_type", "vertex_count", "geometry_digest"),
        sensitivity=SENSITIVITY_LIMITED,
    ),
    _k("aoi_deleted", (), allow_empty=True, sensitivity=SENSITIVITY_LIMITED),
    # ── 观察型：量测完成（值与单位；无几何本体）────────────────────────
    _k("measurement_created", ("measure_kind", "value", "unit", "segment_count")),
    # ── mutation 链接型：真相在 MapSpec lifecycle，事件只记录用户意图与结局 ──
    _k(
        "layer_visibility_changed",
        ("layer_id", "visible"),
        state_changing=True,
        authority=AUTHORITY_MAPSPEC,
    ),
    _k(
        "layer_opacity_changed",
        ("layer_id", "opacity"),
        state_changing=True,
        authority=AUTHORITY_MAPSPEC,
    ),
    _k(
        "layer_style_changed",
        ("layer_id", "attribute_names"),
        state_changing=True,
        authority=AUTHORITY_MAPSPEC,
    ),
    _k(
        "layer_order_changed",
        ("layer_id", "from_index", "to_index"),
        state_changing=True,
        authority=AUTHORITY_MAPSPEC,
    ),
    _k(
        "layer_removed",
        ("layer_id",),
        state_changing=True,
        authority=AUTHORITY_MAPSPEC,
    ),
    # ── 观察型：比较模式开关 ────────────────────────────────────────────
    _k("comparison_mode_changed", ("enabled", "mode")),
    # ── 观察型：图表类别选择（ADR-0285 D2；钩子 chart-panel onClick
    #    publishSelection(source:'chart')——类别谓词，非要素 id 集）────
    _k(
        "chart_selection_changed",
        ("layer_id", "field", "categories", "action"),
        sensitivity=SENSITIVITY_LIMITED,
    ),
    # ── 观察型：表格行选择/定位（钩子 table-panel 行点选
    #    publishSelection(source:'table')——本质是要素定位，保留来源身份）──
    _k(
        "table_selection_changed",
        ("layer_id", "feature_id", "selected_ids", "dataset_ref", "action", "id_field", "matched_count", "ids_truncated"),
        sensitivity=SENSITIVITY_LIMITED,
    ),
    # ── mutation 链接型：时间窗 settle（ADR-0285 D2；钩子 temporal
    #    controller settle→commitUserTemporal POST set_temporal_view——
    #    #1877 后时间轴 UI 真实存在，收词有据；高频 scrub 只报 settle 终态）──
    _k(
        "temporal_window_settled",
        ("mode", "field", "extent", "current", "window", "pinned"),
        state_changing=True,
        authority=AUTHORITY_MAPSPEC,
        coalesce=COALESCE_REPLACE_PENDING,
        sensitivity=SENSITIVITY_LIMITED,
    ),
])

ALLOWED_KINDS = frozenset(INTERACTION_KINDS)

#: mutation 链接型 kind（事件必须携带 client_mutation_id 才可信为意图记录）。
MUTATION_LINKED_KINDS = frozenset(
    spec.kind for spec in INTERACTION_KINDS.values() if spec.authority == AUTHORITY_MAPSPEC
)

# ── 有界常量（客户端与服务端共用，manifest 双闸镜像）────────────────────
#: 批量端点单次最多事件数（与 map-action-ack 批上限同族）。
MAX_EVENTS_PER_BATCH = 50
#: ledger 环条数上限（drop 有披露计数，不假装保留全部历史）。
MAX_LEDGER_EVENTS = 128
#: ledger 序列化字节预算（超预算从头部淘汰最旧）。
MAX_LEDGER_BYTES = 96 * 1024
#: per-instance 水印表容量（多标签页/多设备；FIFO 淘汰最久未见者）。
MAX_TRACKED_CLIENTS = 8
#: 单条 payload 规范化 JSON 字节上限（与 observation.py 同纪律）。
MAX_PAYLOAD_BYTES = 512
#: SW-02（#2004）：selection kind 的载荷字节预算 —— 50×32 字符 id 集
#: ≈1.9KB，512B 会把"截断"恶化成"整事件拒绝"（Agent 连样本都看不到）。
#: 预算仍严格有界（4KB），且只对 :data:`WIDE_BUDGET_KINDS` 放宽。
MAX_PAYLOAD_BYTES_SELECTION = 4096
#: 携带要素 id 集的 kind（selected_ids 专属上限 + matched_count/ids_truncated
#: 披露派生面）。
SELECTION_ID_KINDS = frozenset({"feature_selected", "table_selection_changed"})
#: 宽载荷预算适用面（id 集/类别集的有界大载荷 kind：ids 50×32、类别 20×32）。
WIDE_BUDGET_KINDS = frozenset(SELECTION_ID_KINDS | {"chart_selection_changed"})
#: selected_ids 元素上限（三方对齐：前端 selection-store MAX_SELECTED_IDS
#: 与 gis_situation.analysis_scope.MAX_SCOPE_FEATURE_IDS —— 声称面/验证面
#: /摄入面同一数字，跨面漂移由 manifest 闸锁定）。
MAX_SELECTED_IDS_PAYLOAD = 50
#: categories 元素上限（SW-02：与前端 selection-store selected_categories ≤20
#: 对齐——图表类别上行不再退化为 ≤8 静默截断）。
MAX_CATEGORIES_PAYLOAD = 20
#: matched_count 合法域上限（框选命中数披露；超域丢弃该键，不猜默认）。
MAX_MATCHED_COUNT = 10_000_000
#: payload 字符串值长度上限。
MAX_STR_VALUE_CHARS = 128
#: payload 嵌套键数上限。
_MAX_NESTED_KEYS = 8
#: kind 字符串上限（防御性）。
_MAX_KIND_CHARS = 48
#: list 值元素数默认上限（通用纪律，保持不变）。
_MAX_LIST_ITEMS = 8
#: per-key list 上限覆写（selected_ids/categories 专属，其余键仍走默认 8）。
_LIST_ITEM_CAPS: Dict[str, int] = {
    "selected_ids": MAX_SELECTED_IDS_PAYLOAD,
    "categories": MAX_CATEGORIES_PAYLOAD,
}


def spec_for(kind: str) -> KindSpec | None:
    return INTERACTION_KINDS.get(kind)


def normalize_payload(kind: str, payload: Any) -> Dict[str, Any] | None:
    """白名单键规范化 + 有界化；非法 → None（调用方按 rejected_payload 拒收）。

    与 observation.py 的差异：空 dict 只有在 kind 显式 ``allow_empty_payload``
    时才合法（信号型事件）；白名单内一律不含原始 feature properties。

    SW-02（#2004）selection 纪律：``selected_ids`` 按
    :data:`MAX_SELECTED_IDS_PAYLOAD` 上限裁剪并**由服务端派生**
    ``ids_truncated``（客户端申报被覆写 —— 截断判定以服务端实际保留数为
    准）；``matched_count`` 只接受有界整数。缺 ``selected_ids`` 键（单选/
    旧事件）不派生截断标记，向后兼容。
    """
    if not isinstance(payload, dict):
        return None
    spec = INTERACTION_KINDS.get(str(kind)[:_MAX_KIND_CHARS])
    if spec is None:
        return None
    is_selection_kind = kind in SELECTION_ID_KINDS
    raw_ids = payload.get("selected_ids")
    raw_id_count = len(raw_ids) if isinstance(raw_ids, list) else None
    out: Dict[str, Any] = {}
    for key in sorted(payload):
        if key not in spec.payload_keys:
            continue
        if is_selection_kind and key == "ids_truncated":
            continue  # 服务端派生键 —— 客户端申报一律不透传
        value = payload[key]
        if key == "bbox":
            # 复审 P3-1/R2 N-3：bbox 摄入即收敛为 4 元有限数值（宽进严出
            # 对称——畸形 bbox 不进 ledger/投影；非 list 值直接丢键）。
            if not isinstance(value, list):
                continue
            numbers = [
                float(v) for v in value
                if isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(float(v))
            ]
            out[key] = numbers if len(numbers) == 4 and len(value) == 4 else []
            continue
        if key == "matched_count" and is_selection_kind:
            count = _as_bounded_int(value, MAX_MATCHED_COUNT)
            if count is not None:
                out[key] = count
            continue
        if isinstance(value, dict):
            out[key] = {
                str(k)[:32]: _clip(v) for k, v in sorted(value.items())[:_MAX_NESTED_KEYS]
            }
        elif isinstance(value, list):
            # list 元素只接受标量（review P3-6：dict 元素不经嵌套归一，直接
            # 丢弃 —— 白名单内的值同样受控，不留旁缝）。
            cap = _LIST_ITEM_CAPS.get(key, _MAX_LIST_ITEMS)
            out[key] = [_clip(v) for v in value[:cap] if not isinstance(v, dict)]
        else:
            out[key] = _clip(value)
    if is_selection_kind and "selected_ids" in out:
        # 服务端权威派生（两条独立判据，满足其一即截断）：
        # 1. 原始 id 数超上限（服务端裁掉了尾部 —— 显式 False 与缺席语义
        #    不同：缺席=单选/旧事件兼容，在场=多选集有界披露）；
        # 2. matched_count（真实命中数）大于实际携带数 —— 前端 selection-store
        #    在上行前已按同界截断（50 ids + matched_count=80）时，携带集
        #    本身就是样本。仅在 selected_ids 实际保留于载荷时派生
        #    （防"只剩标记"的幽灵载荷）。
        carried = out["selected_ids"]
        matched_val = out.get("matched_count")
        out["ids_truncated"] = (
            (raw_id_count if isinstance(raw_id_count, int) else len(carried))
            > MAX_SELECTED_IDS_PAYLOAD
            or (isinstance(matched_val, int) and matched_val > len(carried))
        )
    # G07 M3（Issue #2004）：selected_ids 被截断曾是静默部分分析的来源
    # —— 服务端计算 selected_ids_omitted=N 诚实披露（非客户端输入：白名单
    # 不含该键，客户端伪造在上面的循环里已被剥离，此处只能由截断事实推出）。
    # 仲裁（merge resolution）：携带上限取 SW-02 的 per-key cap
    # （_LIST_ITEM_CAPS，50/三方对齐）而非本 PR 原稿的硬编码 8 —— omitted
    # 按原始数与实际携带数之差推导；未超上限不加键，输出与既有一致。
    if is_selection_kind and "selected_ids" in out:
        if isinstance(raw_id_count, int) and raw_id_count > len(out["selected_ids"]):
            out["selected_ids_omitted"] = raw_id_count - len(out["selected_ids"])
    if not out and not spec.allow_empty_payload:
        return None
    budget = MAX_PAYLOAD_BYTES_SELECTION if kind in WIDE_BUDGET_KINDS else MAX_PAYLOAD_BYTES
    blob = json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(blob.encode("utf-8")) > budget:
        return None
    return out


def _as_bounded_int(value: Any, upper: int) -> int | None:
    """有界整数收敛：bool 排除、浮点取整、越界/非数值 → None（丢键不猜）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = int(value)
    except (ValueError, OverflowError):
        return None
    if number < 0 or number > upper:
        return None
    return number


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_STR_VALUE_CHARS:
        return value[:MAX_STR_VALUE_CHARS]
    return value


def canonical_event_blob(event: Dict[str, Any]) -> str:
    """事件的规范化 JSON（排序键、紧凑分隔符）—— fingerprint/replay 基础。"""
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def interaction_event_fingerprint(
    kind: str,
    actor: str,
    client_instance_id: str,
    client_seq: int,
    payload: Dict[str, Any],
    *,
    client_mutation_id: str | None = None,
) -> str:
    """内容指纹（sha256[:16]）。

    **排除 wall-clock 字段**（created_at / observed_at / 服务端接收序）——
    指纹只由协议语义字段构成，重放与时钟噪声不得破坏（ADR-0227 D5）。
    """
    blob = canonical_event_blob({
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "actor": actor,
        "client_instance_id": client_instance_id,
        "client_seq": client_seq,
        "payload": payload,
        "client_mutation_id": client_mutation_id,
    })
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class LedgerState:
    """ingest 纯函数状态机的可序列化状态（replay 与生产同一路径）。

    生产接线：``load_ledger_state`` / ``persist_ledger_state`` 在 session
    锁内读/写 ``map_state``；回放：corpus 直接构造初始态后 fold —— 同一
    :func:`ingest.apply_event` 保证确定性（ADR-0227 D4）。
    """

    events: list = field(default_factory=list)
    #: client_instance_id → {"acked_seq": int, "last_seen": int(server_seq)}
    clients: Dict[str, Dict[str, int]] = field(default_factory=dict)
    server_seq: int = 0
    #: 环淘汰/字节预算丢弃的累计披露计数（只增不减，诚实披露）。
    dropped: int = 0
    #: 环内记录规范化字节账（派生值，不持久化；load/replay 构造时重算 ——
    #: 生产与回放都经 :func:`recalculate_bytes` 对齐，淘汰判定两侧一致）。
    total_bytes: int = field(default=0, repr=False, compare=False)

    def recalculate_bytes(self) -> None:
        self.total_bytes = sum(
            len(canonical_event_blob(rec).encode("utf-8")) for rec in self.events
        )
