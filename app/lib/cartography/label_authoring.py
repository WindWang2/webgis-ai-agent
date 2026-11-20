"""Feature-level label authoring — 纯模型/解析/预览/失效语义（G16）。

要素级标注人工控制的单一事实源：覆写（hide/pin/offset/text/callout/
priority）的**词表、上限、身份解析、reset 语义、失效（stale/orphan）
判定、有界预览**。全部纯函数、无 IO —— UI 手势、REST mutation、Pi
工具、live 投影与导出接线都消费这里，绝不出现第二套覆写语义。

身份契约（与前后端 FEATURE_ID_KEYS 链同源：backend
``app/api/routes/layer.py:_find_feature_by_id`` / ``app/services/feature_edit/
contracts.py`` / frontend ``lib/layers/feature-id.ts``）：

- GeoJSON 顶层 ``feature.id``（str|int）优先，``idKey=None`` 记录之；
- 其次 properties 按 ``FEATURE_ID_KEYS`` 顺序取第一个非空（str|int）；
- 值一律 ``str(value)`` 归一 —— ``1`` 与 ``"1"`` 按既有契约折叠为同一
  目标（显式测试锁定）；
- 已物化 ``_wfid``（G14/ADR-0277）的链值是 WFID 本身，天然兼容；
  物化由 G14 拥有，本模块只消费。
- 裸数据集（无任何 id 键）→ 拒绝持久化覆写（typed finding
  ``feature_identity_missing``），绝不以数组下标冒充稳定身份。

单位契约（canonical = em，无量纲、相对该标签自身字号）：

- live：``text-offset`` 原生 em —— ``px = em × font_px_live``；
- 导出：``px = em × font_px_export``，其中 ``font_px_export = base_size ×
  dpi/72`` —— 偏移与字号同乘 dpi_scale，DPI 变化不改变相对位置语义，
  永不持久化绝对屏幕像素。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "MAX_LABEL_OVERRIDES_PER_LAYER",
    "MAX_AUTHORING_TEXT_CHARS",
    "MAX_AUTHORING_NOTE_CHARS",
    "MAX_LABEL_OFFSET_EM",
    "MAX_AUTHORING_PRIORITY",
    "MAX_AUTHORING_OPS_PER_MUTATION",
    "MAX_LABEL_FORBIDDEN_ZONES",
    "MAX_ZONE_RING_POINTS",
    "FEATURE_ID_KEYS",
    "AUTHORING_FIELD_NAMES",
    "RESET_SCOPES",
    "HIDDEN_REASONS",
    "resolve_feature_identity",
    "find_feature_overrides",
    "override_status",
    "apply_reset_scope",
    "normalize_text_override",
    "normalize_offset_override",
    "validate_override_bounds",
    "preview_authoring_ops",
    "em_to_px",
    "px_to_em",
    "is_override_empty",
    "merge_override_patch",
]

# ── 上限（有界纪律；超限 = typed 拒绝，绝不静默截断用户决定）──────────────
#: 每层覆写条目上限（身份键唯一；超出 → authoring_limit_exceeded）。
MAX_LABEL_OVERRIDES_PER_LAYER = 256
#: 文字覆写长度上限（code point 口径 —— 非 BMP 字符不半截）。
MAX_AUTHORING_TEXT_CHARS = 120
#: 备注长度上限。
MAX_AUTHORING_NOTE_CHARS = 80
#: 手工偏移上限（|dx| / |dy| 各自 ≤ 24em —— 画幅外远拽防呆；更大诉求应
#: 通过 callout + 显式说明表达，而不是无限位移）。
MAX_LABEL_OFFSET_EM = 24.0
#: 手工优先级幅度（|priority| ≤ 1000 —— 只需能压过自动名次，不需要更多）。
MAX_AUTHORING_PRIORITY = 1000
#: 单次 mutation 操作数上限（批量编辑的确定性边界）。
MAX_AUTHORING_OPS_PER_MUTATION = 200
#: 显式禁入面数量上限。
MAX_LABEL_FORBIDDEN_ZONES = 8
#: 单个禁入面外环点数上限（防病理多边形）。
MAX_ZONE_RING_POINTS = 128

#: 与前后端既有链同源（见模块 docstring）；本模块是 lib 层，不得 import
#: services —— 与 feature_edit/contracts.py 的重复是登记过的同源镜像，
#: 契约测试两侧互锁。
FEATURE_ID_KEYS: Tuple[str, ...] = (
    "id", "OBJECTID", "fid", "FID", "osm_id", "@id", "featureId", "feature_id",
)

#: 覆写可承载的作者决策字段（不含身份/溯源元数据）。
AUTHORING_FIELD_NAMES = (
    "hidden", "pinned", "offset", "text", "callout", "priority",
)

#: reset 作用域 —— visibility 只解除隐藏；position 恢复自动位置（解除
#: pin/offset/callout）；text 恢复字段文字；all 清除全部作者决定。
#: 语义互斥：解除隐藏绝不顺带清掉用户文字或手工位置（反之亦然）。
RESET_SCOPES = ("visibility", "position", "text", "all")

_SCOPE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "visibility": ("hidden",),
    "position": ("pinned", "offset", "callout"),
    "text": ("text",),
    "all": AUTHORING_FIELD_NAMES,
}

#: “为什么这个标签没显示”词表（closed set）。每条理由在消费侧必须携带
#: 来源（authoring_policy / solver_predicted / live_observation）或显式
#: unknown / not_observed / not_evaluated —— 见 WP10 契约。
HIDDEN_REASONS = (
    "hidden_by_user",    # 作者显式隐藏（authoring_policy）
    "below_scale",       # zoom 域外（authoring_policy / solver_predicted）
    "budget",            # 标签预算截断（solver_predicted）
    "collision",         # 标签-标签碰撞抑制（solver_predicted）
    "obstacle",          # 图元墨迹避让抑制（solver_predicted）
    "field_missing",     # 标注字段缺失/为空（data 事实）
    "stale_override",    # 覆写绑定 revision ≠ 当前 revision（identity 事实）
    "unverified",        # 目标身份无法在当前数据复核（identity 事实）
)


# ── 身份解析 ─────────────────────────────────────────────────────────────
def resolve_feature_identity(feature: Any) -> Tuple[Optional[str], Optional[str]]:
    """单要素 → (canonical feature_id, idKey)。

    顶层 ``feature.id`` 优先（idKey=None —— GeoJSON 原生身份）；
    否则按 :data:`FEATURE_ID_KEYS` 扫描 properties（idKey=命中的键）。
    值 ``str()`` 归一（``1`` ≡ ``"1"``，既有契约）。无可用身份 →
    ``(None, None)`` —— 调用方必须拒绝持久化覆写并披露。
    """
    if not isinstance(feature, dict):
        return (None, None)
    top = feature.get("id")
    if isinstance(top, (str, int)) and not isinstance(top, bool):
        return (str(top), None)
    props = feature.get("properties")
    if isinstance(props, dict):
        for key in FEATURE_ID_KEYS:
            value = props.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return (str(value), key)
    return (None, None)


def find_feature_overrides(
    features: Sequence[Any], wanted: Sequence[str]
) -> Dict[str, Tuple[Optional[int], Optional[str]]]:
    """wanted（canonical id 集）→ {id: (features 下标, 实际 idKey)}。

    未命中不出现在结果里。同 id 重复时取第一条（GeoJSON 顺序稳定）。
    消费侧（live/export/校验）用它在**真实数据**上复核覆写目标 ——
    这才是身份的权威判定点，写入侧只做能做的部分。
    """
    index: Dict[str, Tuple[int, Optional[str]]] = {}
    for i, feature in enumerate(features):
        fid, key = resolve_feature_identity(feature)
        if fid is not None and fid not in index:
            index[fid] = (i, key)
    return {str(w): index[str(w)] for w in wanted if str(w) in index}


# ── 单位（canonical em）─────────────────────────────────────────────────
def em_to_px(em: float, font_px: float) -> float:
    """canonical em → 该出口的像素（live px / 导出 SVG user units 同式）。"""
    return float(em) * float(font_px)


def px_to_em(px: float, font_px: float) -> float:
    """拖拽捕获：出口像素 → canonical em（font_px ≤ 0 → 0，防除零）。"""
    if font_px <= 0:
        return 0.0
    return float(px) / float(font_px)


# ── 覆写字段规整/校验 ────────────────────────────────────────────────────
def normalize_text_override(text: Any) -> Tuple[Optional[str], Optional[str]]:
    """文字覆写规整：str 化、去首尾空白；code point 上限；非法 → 拒绝理由。

    返回 (normalized, None) 或 (None, finding_code)。
    """
    if text is None:
        return (None, None)
    if not isinstance(text, str):
        return (None, "invalid_text_type")
    stripped = text.strip()
    if not stripped:
        return (None, "invalid_text_empty")
    if len(stripped) > MAX_AUTHORING_TEXT_CHARS:
        return (None, "text_too_long")
    # 控制字符（除常规空白外）不得进入文字覆写 —— 导出件是 XML/SVG。
    for ch in stripped:
        if ord(ch) < 0x20 and ch not in "\t":
            return (None, "invalid_text_control_char")
    return (stripped, None)


def normalize_offset_override(offset: Any) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    """偏移覆写规整：{dx, dy} 数值、有限、|v| ≤ MAX_LABEL_OFFSET_EM。"""
    if offset is None:
        return (None, None)
    if not isinstance(offset, dict):
        return (None, "invalid_offset_type")
    dx = offset.get("dx")
    dy = offset.get("dy")
    if not isinstance(dx, (int, float)) or not isinstance(dy, (int, float)) or isinstance(dx, bool) or isinstance(dy, bool):
        return (None, "invalid_offset_type")
    if not (math.isfinite(float(dx)) and math.isfinite(float(dy))):
        return (None, "invalid_offset_not_finite")
    if abs(float(dx)) > MAX_LABEL_OFFSET_EM or abs(float(dy)) > MAX_LABEL_OFFSET_EM:
        return (None, "offset_out_of_range")
    return ({"dx": float(dx), "dy": float(dy)}, None)


def validate_override_bounds(override: Dict[str, Any]) -> List[str]:
    """覆写 dict（已过 schema 类型）→ 业务边界 finding 列表（空 = 通过）。"""
    findings: List[str] = []
    text, err = normalize_text_override(override.get("text"))
    if err is not None and override.get("text") is not None:
        findings.append(err)
    if text is not None:
        override["text"] = text
    offset, err = normalize_offset_override(override.get("offset"))
    if err is not None and override.get("offset") is not None:
        findings.append(err)
    if offset is not None:
        override["offset"] = offset
    priority = override.get("priority")
    if priority is not None:
        if not isinstance(priority, int) or isinstance(priority, bool) or abs(priority) > MAX_AUTHORING_PRIORITY:
            findings.append("invalid_priority")
    note = override.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > MAX_AUTHORING_NOTE_CHARS):
        findings.append("invalid_note")
    return findings


def is_override_empty(override: Dict[str, Any]) -> bool:
    """覆写是否已无任何作者决定（reset 后可安全移除条目）。"""
    return not any(override.get(k) is not None for k in AUTHORING_FIELD_NAMES)


def merge_override_patch(
    current: Optional[Dict[str, Any]], patch: Dict[str, Any]
) -> Dict[str, Any]:
    """set 操作语义：字段级合并（显式 None = 清除该字段），身份/溯源元
    数据（featureId/idKey/origin/sourceRevision/frameId）不被 patch 覆写
    —— 身份只能由目标寻址决定，溯源只能由写入路径盖章。"""
    merged: Dict[str, Any] = dict(current) if current else {}
    meta_keys = {"featureId", "idKey", "origin", "sourceRevision", "frameId"}
    for key, value in patch.items():
        if key in meta_keys:
            continue
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def apply_reset_scope(override: Dict[str, Any], scope: str) -> Optional[Dict[str, Any]]:
    """reset 操作语义：只清 scope 内字段；清空后返回 None（移除条目）。"""
    if scope not in RESET_SCOPES:
        raise ValueError(f"unknown reset scope: {scope}")
    cleared = dict(override)
    for key in _SCOPE_FIELDS[scope]:
        cleared.pop(key, None)
    return None if is_override_empty(cleared) else cleared


# ── 失效（stale/orphan/unverified）───────────────────────────────────────
#: 覆写状态（消费侧真实数据复核后的权威判定）。
OVERRIDE_STATUSES = ("active", "stale", "orphan", "field_missing", "unverified")


def override_status(
    override: Dict[str, Any],
    *,
    feature_found: bool,
    current_revision: Optional[float],
    label_field: Optional[str],
    feature_properties: Optional[Dict[str, Any]],
) -> str:
    """消费侧覆写状态判定。

    - orphan：目标要素在当前数据不存在 → 不应用、如实披露（绝不漂移
      到别的要素）；
    - field_missing：要素在，但标注字段缺失 —— text 覆写仍应用（显式
      文字就是文字），无 text 的基础标注披露 field_missing；
    - stale：要素在但 sourceRevision ≠ 当前 content_revision —— 仍然
      应用（身份未断），但标记待复核；
    - unverified：消费侧无法取到特征数据复核（如未水合 ref 源导出）；
    - active：全部复核通过。
    """
    if not feature_found:
        return "orphan"
    if (
        isinstance(feature_properties, dict)
        and isinstance(label_field, str)
        and label_field
        and feature_properties.get(label_field) is None
        and override.get("text") is None
    ):
        return "field_missing"
    rev = override.get("sourceRevision")
    if (
        rev is not None
        and current_revision is not None
        and float(rev) != float(current_revision)
    ):
        return "stale"
    return "active"


# ── 有界预览（UI 确认与 Pi 提案共用同一函数）────────────────────────────
@dataclass
class AuthoringOpReport:
    """单操作预览回执（before/after + 不可执行理由）。"""

    op_index: int
    feature_id: str
    id_key: Optional[str] = None
    action: str = ""                 # set | reset | noop | rejected
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    findings: List[str] = _dc_field(default_factory=list)


@dataclass
class AuthoringPreview:
    """ops 应用于某层 authoring 的有界预览结果。"""

    layer_id: str
    reports: List[AuthoringOpReport] = _dc_field(default_factory=list)
    next_overrides: Optional[List[Dict[str, Any]]] = None  # None = 不变更
    findings: List[str] = _dc_field(default_factory=list)
    #: 预览身份核验（inline 数据在场时）：存在但找不到目标的 op 数。
    unresolved_targets: int = 0

    @property
    def change_count(self) -> int:
        return sum(1 for r in self.reports if r.action in ("set", "reset"))


def _ops_shape_error(ops: Any) -> Optional[str]:
    if not isinstance(ops, list) or not ops:
        return "ops_empty"
    if len(ops) > MAX_AUTHORING_OPS_PER_MUTATION:
        return "op_limit_exceeded"
    return None


def preview_authoring_ops(
    current_overrides: List[Dict[str, Any]],
    ops: Any,
    *,
    layer_id: str,
    known_features: Optional[Sequence[Any]] = None,
) -> AuthoringPreview:
    """纯函数预览：当前覆写表 + 操作列表 → 下一态覆写表 + 逐 op 回执。

    ``known_features`` 在场（inline 源 / 已水合数据）时做身份核验：
    目标找不到 → 该 op 拒绝（``feature_not_found``），不产生部分写入。
    不在场（ref 源运行前）→ 标记 unverified 但不拒绝 —— 消费侧
    （live/export）复核才是权威（见 :func:`override_status`）。
    """
    preview = AuthoringPreview(layer_id=layer_id)
    err = _ops_shape_error(ops)
    if err is not None:
        preview.findings.append(err)
        return preview

    by_id: Dict[str, Dict[str, Any]] = {}
    for ov in current_overrides:
        fid = ov.get("featureId")
        if isinstance(fid, str):
            by_id[fid] = dict(ov)

    known_index: Optional[Dict[str, Tuple[Optional[int], Optional[str]]]] = None
    if known_features is not None:
        wanted = []
        for op in ops:
            if isinstance(op, dict) and isinstance(op.get("featureId"), str):
                wanted.append(op["featureId"])
        known_index = find_feature_overrides(known_features, wanted)

    changed = False
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            preview.findings.append("invalid_op")
            preview.reports.append(AuthoringOpReport(
                op_index=i, feature_id="", action="rejected",
                findings=["invalid_op"]))
            continue
        fid = op.get("featureId")
        if not isinstance(fid, str) or not fid.strip():
            preview.reports.append(AuthoringOpReport(
                op_index=i, feature_id=str(fid or ""), action="rejected",
                findings=["invalid_target"]))
            continue
        fid = fid.strip()
        kind = op.get("op") or op.get("kind") or "set"
        before = by_id.get(fid)
        report = AuthoringOpReport(
            op_index=i, feature_id=fid,
            id_key=(before or {}).get("idKey") if before else None,
            before=dict(before) if before else None,
        )

        if known_index is not None and fid not in known_index:
            # inline 数据在场而目标不存在：拒绝（不产生孤儿写入）。
            report.action = "rejected"
            report.findings.append("feature_not_found")
            preview.unresolved_targets += 1
            preview.reports.append(report)
            continue

        if known_index is not None:
            _, key = known_index[fid]
            report.id_key = key

        if kind == "set":
            patch = op.get("set")
            if not isinstance(patch, dict):
                report.action = "rejected"
                report.findings.append("invalid_patch")
                preview.reports.append(report)
                continue
            patch = dict(patch)
            bounds_findings = validate_override_bounds(patch)
            if bounds_findings:
                report.action = "rejected"
                report.findings.extend(bounds_findings)
                preview.reports.append(report)
                continue
            merged = merge_override_patch(before, patch)
            merged["featureId"] = fid
            if report.id_key is not None:
                merged["idKey"] = report.id_key
            if is_override_empty(merged):
                by_id.pop(fid, None)
                report.action = "set"
                report.after = None
            else:
                if len(by_id) >= MAX_LABEL_OVERRIDES_PER_LAYER and fid not in by_id:
                    report.action = "rejected"
                    report.findings.append("authoring_limit_exceeded")
                    preview.reports.append(report)
                    continue
                by_id[fid] = merged
                report.action = "set"
                report.after = dict(merged)
            changed = True
        elif kind == "reset":
            scope = op.get("scope") or "all"
            if scope not in RESET_SCOPES:
                report.action = "rejected"
                report.findings.append("invalid_scope")
                preview.reports.append(report)
                continue
            if before is None:
                report.action = "noop"
            else:
                after = apply_reset_scope(before, scope)
                if after is None:
                    by_id.pop(fid, None)
                else:
                    by_id[fid] = after
                report.action = "reset"
                report.after = dict(after) if after else None
                changed = True
        else:
            report.action = "rejected"
            report.findings.append("invalid_op")
        preview.reports.append(report)

    preview.next_overrides = list(by_id.values()) if changed else None
    return preview


def sort_overrides_stable(overrides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """确定性序列化序（featureId 字典序）—— 同状态恒同字节。"""
    return sorted(
        (dict(o) for o in overrides),
        key=lambda o: str(o.get("featureId", "")),
    )
