"""Selection History —— 多轮指代的选择史投影（ADR-0285 D3）。

IR 投影只保留**当前唯一** selection（deselect 即清空）——「刚才那组学校」
这类历史指代没有句柄。本模块从 ledger 环**纯函数**重建最近 ≤N 个「选择代」
（epoch）：每个 epoch 由一次选择事件开启，被下一次选择替代或显式清除终结。

纪律：零 IO（ledger 列表输入）；序号驱动（server_seq）不读时钟；ids 有界；
绝不猜测（不可解析句柄返回 None，由调用方给最小澄清）。
"""
from __future__ import annotations

from typing import Iterable, List, Mapping, Optional
from typing_extensions import TypedDict

from app.services.gis_situation.analysis_scope import ClaimedScope, record_fields


class SelectionEpoch(TypedDict):
    epoch_seq: int
    kinds: list[str]
    layer_id: str | None
    dataset_ref: str | None
    feature_ids: list[str]
    id_count: int
    truncated: bool
    superseded: bool
    cleared: bool
    # SW-02（#2004）：真实命中数（IR matched_count；缺席=ids 数）——历史
    # 指代的范围披露与当前代同权（"刚才那 23 个"不能变成"刚才那 8 个"）。
    matched_count: int
    # 摄入层截断披露（服务端派生 ids_truncated 的镜像；旧事件 False）。
    ids_truncated: bool

__all__ = [
    "project_selection_history",
    "resolve_selection_ref",
    "MAX_HISTORY_ENTRIES",
    "MAX_HISTORY_IDS",
]

#: 选择史条数上限（有界；更早选择被环淘汰即如实消失）。
MAX_HISTORY_ENTRIES = 6
#: 每代 ids 展示上限（SW-02/#2004：与 IR 摄入 selected_ids 专属上限三方
#: 对齐—— 多选上行不再退化为 ≤8 静默样本）。
MAX_HISTORY_IDS = 50

_SELECTION_OPENERS = frozenset({"feature_selected", "table_selection_changed"})
# table clear 的终结在 opener 分支内联处理（无 id 事件），不在 closer 词表。
_SELECTION_CLOSERS = frozenset({"feature_deselected"})


def _ids_of(payload: Mapping[str, object]) -> List[str]:
    multi = payload.get("selected_ids")
    if isinstance(multi, list) and multi:
        return [str(v)[:128] for v in multi[:MAX_HISTORY_IDS]
                if isinstance(v, (str, int))]
    single = payload.get("feature_id")
    if isinstance(single, (str, int)):
        return [str(single)[:128]]
    return []


def project_selection_history(
    ledger_events: Iterable[Mapping[str, object]],
    *,
    max_entries: int = MAX_HISTORY_ENTRIES,
) -> List[SelectionEpoch]:
    """ledger 环 → 选择代列表（新 → 旧输出；纯函数）。

    每代：{epoch_seq, kinds, layer_id, dataset_ref, feature_ids, id_count,
    truncated, superseded, cleared}。当前代 = 列表[0]（若未被清除）；
    superseded = 被后续选择替代；cleared = 被 deselect/清除终结。
    """
    max_entries = max(1, min(int(max_entries), MAX_HISTORY_ENTRIES))
    events = [rec for rec in ledger_events if isinstance(rec, dict)]
    epochs: List[SelectionEpoch] = []
    for record in events:
        kind = str(record.get("kind") or "")
        payload = record.get("payload")
        payload = record_fields(payload)
        seq = record.get("server_seq")
        seq = int(seq) if isinstance(seq, (int, float)) else len(epochs) + 1
        # chart 类别选择替换要素选择（投影面已镜像单选模型）——历史当前代
        # 同步终结，保持两面一致（review P3）。
        if kind == "chart_selection_changed" and payload.get("action") != "clear":
            current = next(
                (e for e in epochs if not e["superseded"] and not e["cleared"]), None)
            if current is not None:
                current["superseded"] = True
        if kind in _SELECTION_OPENERS:
            ids = _ids_of(payload)
            # review P2-2：table clear 事件无 id —— 按 closer 终结当前代
            # （不跳过；否则 previous 句柄错代）。
            if not ids and kind == "table_selection_changed":
                current = next(
                    (e for e in epochs if not e["superseded"] and not e["cleared"]), None)
                if current is not None:
                    current["cleared"] = True
                continue
            if not ids:
                continue
            for prev in epochs:
                if not prev["superseded"] and not prev["cleared"]:
                    prev["superseded"] = True
            raw_multi = payload.get("selected_ids")
            matched = payload.get("matched_count")
            matched_count = (
                int(matched) if isinstance(matched, int) and not isinstance(matched, bool) and matched >= 0
                else len(ids)
            )
            # 摄入层服务端派生的截断披露优先；缺席（旧事件）保持 False。
            # 不用 id_count < matched_count 推断 —— 部分取消是用户显式决策，
            # 不是静默截断（两者必须可区分）。
            # 复审 P3-2：truncated（环内自截标志）在真实链路恒 False——
            # IR 摄入已保证 selected_ids ≤50（=MAX_HISTORY_IDS），本投影层
            # 再截只在防御性场景（手工构造 ledger）触发；消费方应读
            # ids_truncated（摄入层权威披露），不要读 truncated。
            ingested_truncated = payload.get("ids_truncated")
            epochs.append({
                "epoch_seq": seq,
                "kinds": [kind],
                "layer_id": (str(payload.get("layer_id") or "")[:200] or None),
                "dataset_ref": (str(payload.get("dataset_ref") or "")[:200] or None),
                "feature_ids": ids,
                "id_count": len(ids),
                "truncated": (
                    isinstance(raw_multi, list) and len(raw_multi) > MAX_HISTORY_IDS
                ),
                "superseded": False,
                "cleared": False,
                "matched_count": matched_count,
                "ids_truncated": bool(ingested_truncated),
            })
        elif kind in _SELECTION_CLOSERS and epochs:
            payload_ids = _ids_of(payload)
            current = next((e for e in epochs if not e["superseded"] and not e["cleared"]), None)
            if current is None:
                continue
            # 空 payload deselect（map 空点）= 清除当前代；带 id 的部分取消
            # = 从当前代移除该 id（若清空则终结）。
            if not payload_ids:
                current["cleared"] = True
            else:
                removed = set(payload_ids)
                current["feature_ids"] = [
                    fid for fid in current["feature_ids"] if fid not in removed
                ]
                current["id_count"] = len(current["feature_ids"])
                if not current["feature_ids"]:
                    current["cleared"] = True
    live = [e for e in epochs if e["feature_ids"]][-max_entries:]
    return list(reversed(live))


def resolve_selection_ref(
    ledger_events: Iterable[Mapping[str, object]],
    selection_ref: str,
) -> Optional[ClaimedScope]:
    """句柄 → ClaimedScope（与 analysis_scope.derive_claimed_scope 同形状）。

    句柄词表：``previous``（上一个非当前代）/ ``<epoch_seq>``（指定代）。
    不可解析（史空/越界/已清除）→ None（调用方给最小澄清，绝不随机挑）。
    """
    history = project_selection_history(ledger_events)
    ref = str(selection_ref or "").strip()
    entry: Optional[SelectionEpoch] = None
    if ref == "previous":
        # 「刚才那组」= 最近一次**非当前活跃**的选择代：有活跃当前代时取
        # 其后最近代；无活跃代（已 deselect/清除）时最近代即是「刚才」。
        if not history:
            return None
        if history[0].get("cleared") or history[0].get("superseded"):
            entry = history[0] if history[0].get("feature_ids") else None
        else:
            for candidate in history[1:]:
                if candidate.get("feature_ids"):
                    entry = candidate
                    break
    elif len(ref) <= 20 and ref.isascii() and ref.isdigit():
        wanted = int(ref)
        for candidate in history:
            if candidate.get("epoch_seq") == wanted and candidate.get("feature_ids"):
                entry = candidate
                break
    else:
        return None
    if entry is None or not entry.get("feature_ids"):
        return None
    return {
        "kind": "feature_ids" if entry["id_count"] > 1 else "feature_single",
        "layer_id": entry.get("layer_id"),
        "dataset_ref": entry.get("dataset_ref"),
        "feature_ids": list(entry["feature_ids"]),
        "claimed_at_seq": entry.get("epoch_seq"),
        "sources": ["selection_history"],
        "matched_count": entry.get("matched_count", entry["id_count"]),
        "ids_truncated": bool(entry.get("ids_truncated", False)),
    }
