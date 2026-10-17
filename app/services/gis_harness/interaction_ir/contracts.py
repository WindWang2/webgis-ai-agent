"""Interaction IR V1 契约常量与词表（Goal 02 / ADR-0224）。

本模块是交互事件分类学的**单一权威**：

- ``SCHEMA_VERSION`` —— 事件信封版本（``interaction_ir.v1``）；
- ``INTERACTION_KINDS`` —— 封闭 kind 词表 + 每 kind 元数据
  （payload 白名单 / 合并策略 / 敏感级 / authority 归属）；
- :func:`normalize_payload` —— 服务端有界化（键白名单 + 值长 + 总字节），
  与 ``gis_situation/observation.py`` 同纪律，但**任何 kind 都不允许原始
  feature properties 进入 ledger**（隐私最小化，ADR-0224 H）。

前端镜像常量在 ``frontend/lib/interaction/contract-manifest.ts``，两侧由
``tests/unit/gis_harness/interaction_ir/contract_manifest.json`` 双向漂移闸
锁定（carto_ir manifest 模式）。

纪律（ADR-0224）：
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
#: 评估后**未收录**的候选见 ADR-0224「词表边界」：temporal scrub / feature
#: inspect / measurement clear —— 前端无对应交互钩子，不为词表完整伪造）。
INTERACTION_KINDS: Dict[str, KindSpec] = dict([
    # ── 观察型：viewport（settle 后上报，replace_pending 合并）──────────
    _k(
        "viewport_settled",
        ("center", "zoom", "bearing", "pitch", "bounds"),
        coalesce=COALESCE_REPLACE_PENDING,
    ),
    # ── 观察型：要素选择/取消（有限载荷：引用 + 字段名 + 几何摘要）──────
    _k(
        "feature_selected",
        ("layer_id", "feature_id", "field_names", "geometry_digest", "dataset_ref"),
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
#: payload 字符串值长度上限。
MAX_STR_VALUE_CHARS = 128
#: payload 嵌套键数上限。
_MAX_NESTED_KEYS = 8
#: kind 字符串上限（防御性）。
_MAX_KIND_CHARS = 48


def spec_for(kind: str) -> KindSpec | None:
    return INTERACTION_KINDS.get(kind)


def normalize_payload(kind: str, payload: Any) -> Dict[str, Any] | None:
    """白名单键规范化 + 有界化；非法 → None（调用方按 rejected_payload 拒收）。

    与 observation.py 的差异：空 dict 只有在 kind 显式 ``allow_empty_payload``
    时才合法（信号型事件）；白名单内一律不含原始 feature properties。
    """
    if not isinstance(payload, dict):
        return None
    spec = INTERACTION_KINDS.get(str(kind)[:_MAX_KIND_CHARS])
    if spec is None:
        return None
    out: Dict[str, Any] = {}
    for key in sorted(payload):
        if key not in spec.payload_keys:
            continue
        value = payload[key]
        if isinstance(value, dict):
            out[key] = {
                str(k)[:32]: _clip(v) for k, v in sorted(value.items())[:_MAX_NESTED_KEYS]
            }
        elif isinstance(value, list):
            out[key] = [_clip(v) for v in value[:8]]
        else:
            out[key] = _clip(value)
    if not out and not spec.allow_empty_payload:
        return None
    blob = json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(blob.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return None
    return out


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
    指纹只由协议语义字段构成，重放与时钟噪声不得破坏（ADR-0224 D5）。
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
    :func:`ingest.apply_event` 保证确定性（ADR-0224 D4）。
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
