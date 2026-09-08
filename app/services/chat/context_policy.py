"""Context Policy — 六操作的**确定性执行器**（ADR-0104 决策 #6, Wave 5）。

现状（audit 05）：预算侧只有 measure-and-advise —— ``GisBudgetAdvisor`` 给出
keep/condense/offload_ref/drop_oldest/compress 建议，但除 legacy 引擎的
DROP_OLDEST 与派发期卸载外，没有任何组件真正执行建议。本模块是建议的
**执行器，不是第二个预算模型**：预算判定的唯一来源仍是
``app/services/chat/context_budget.GisBudgetAdvisor``；本模块消费其
``GisBudgetAdvice.actions`` 并落刀。

六操作（``ContextOp``）：

- ``KEEP``       安全相关消息（Tier-3 确认回执 / 自愈错误指引 / 制图裁决）
  在历史截断与轮内折叠中永不丢失 —— 廉价 marker 探测
  （``history_compression.find_safety_pinned_indexes``，无 LLM，有界
  ``MAX_PINNED_TURNS``）。
- ``CONDENSE``   对 advisor 判为超分区的 head 块（TRACE_SUMMARIES /
  DATA_PROFILE / ALGORITHM_METADATA / CARTOGRAPHY_METADATA / SESSION_PLAN /
  MAP_STATE）套用既有有界投影原语（context_projections.bound_text）真实压缩，
  而不是只在日志里建议。
- ``OFFLOAD_REF`` 组装后仍超预算时，把历史里超大的内联 tool 结果消息有界地
  转 ref+摘要（复用 llm_result_formatter 的硬上限提取 helper）；已有 ref 游标
  永不丢失（新 ref 与旧游标同留在消息里，可解析性只增不减）。
- ``SUMMARIZE``  被丢弃轮次范围的指纹化确定性摘要（canonical content hash 复用
  ``content_fingerprint``，按指纹缓存；纯抽取，无 LLM —— LLM hook 只是未来
  可选接口位）。
- ``DROP_OLDEST`` 既有 ``truncate_history_by_budget`` 的 pin 感知执行。
- ``RELOAD_REF`` tombstone：组装产物引用了已逐出 ref 时追加有界诚实提示
  （可从持久副本重载 vs 已失效），而非沉默（持久半边在
  ``session_data.ref_spill_store`` + ``ref_resolver`` 的 ref→durable 回退）。

开关：``GIS_CONTEXT_POLICY=0`` 一键回到 advice-only（所有执行分支短路，
历史压缩走无 pin 旧路径，byte-identical）。默认开。

溢出恢复：本模块向 ``llm_client`` 注册确定性重裁 hook
（``retrim_messages_for_retry``）—— provider 判定 CONTEXT_TOO_LARGE 时收紧
历史预算重跑折叠+DROP_OLDEST，恰好一次（阶梯在 llm_client 侧）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.chat.context.history_compression import (
    HISTORY_MIN_TURNS,
    HISTORY_TOKEN_BUDGET,
    SAFETY_PIN_MARKERS,
    _build_truncation_notice,
    _estimate_tokens,
    _group_into_turns,
    find_safety_pinned_indexes,
    fold_intra_turn_tool_results,
    truncate_history_by_budget,
)
from app.services.chat.context_projections import (
    ProjectionCache,
    bound_text,
    content_fingerprint,
)
from app.services.llm_result_formatter import MSG_MAX_CHARS, slim_tool_result

logger = logging.getLogger(__name__)


class ContextOp(str, Enum):
    """六操作的执行词汇（ADR-0104 决策 #6）。"""

    KEEP = "keep"
    CONDENSE = "condense"
    OFFLOAD_REF = "offload_ref"
    SUMMARIZE = "summarize"
    DROP_OLDEST = "drop_oldest"
    RELOAD_REF = "reload_ref"


_POLICY_FALSE_VALUES = {"0", "false", "no", "off"}


def policy_enabled() -> bool:
    """Kill switch：GIS_CONTEXT_POLICY=0 → advice-only（默认开）。"""
    return os.getenv("GIS_CONTEXT_POLICY", "1").strip().lower() not in _POLICY_FALSE_VALUES


# 与评测 harness（pi_agent_harness.REF_CURSOR_PATTERN）同一份 ref 词表；
# 不直接 import 评测模块（运行时不应依赖评测面）。
REF_CURSOR_RE = re.compile(r"ref:(?:geojson|raster|table|data|heatmap)-[a-zA-Z0-9_-]+")


def _ordered_unique_refs(text: str, cap: int = 32) -> List[str]:
    seen: Dict[str, None] = {}
    for ref in REF_CURSOR_RE.findall(text):
        if ref not in seen:
            seen[ref] = None
            if len(seen) >= cap:
                break
    return list(seen.keys())


# ---------------------------------------------------------------------------
# DROP_OLDEST + SUMMARIZE：pin 感知历史管线（含指纹化摘要）
# ---------------------------------------------------------------------------

#: 逐轮摘要上限（有界披露：更早轮次只报数量，不假装全知）。
_SUMMARY_MAX_TURNS = 8
_SUMMARY_LINE_MAX_CHARS = 220
_SUMMARY_TOTAL_MAX_CHARS = 1200

#: 摘要缓存：指纹 → 摘要文本（同输入必同摘要；缓存只是省重算）。
_turn_summary_cache = ProjectionCache(max_entries=512, default_ttl_s=600.0)


def _canonical_turn_content(msg: dict) -> Any:
    """摘要指纹的规范形（与库内消息解耦：只取稳定字段）。"""
    tool_calls = msg.get("tool_calls") or []
    try:
        calls = [
            {"name": tc.get("function", {}).get("name"), "args": tc.get("function", {}).get("arguments")}
            for tc in tool_calls
            if isinstance(tc, dict)
        ]
    except AttributeError:
        calls = []
    return {"role": msg.get("role"), "content": msg.get("content"), "tool_calls": calls}


def _summarize_one_turn(turn: Sequence[dict]) -> str:
    """单轮 → 一行有界确定性摘要（用户意图 + 工具名 + ref 游标 + 安全标记）。"""
    user_head = ""
    tool_names: List[str] = []
    refs: List[str] = []
    markers: List[str] = []
    for msg in turn:
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        if msg.get("role") == "user" and not user_head and text:
            user_head = " ".join(text.split())[:100]
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                try:
                    name = tc.get("function", {}).get("name")
                except AttributeError:
                    name = None
                if name and name not in tool_names:
                    tool_names.append(name)
        for ref in REF_CURSOR_RE.findall(text):
            if ref not in refs:
                refs.append(ref)
        for marker in SAFETY_PIN_MARKERS:
            if marker in text and marker not in markers:
                markers.append(marker)
    parts: List[str] = []
    if user_head:
        parts.append(f"用户: {user_head}")
    if tool_names:
        parts.append("工具: " + ",".join(tool_names[:4]) + ("…" if len(tool_names) > 4 else ""))
    if refs:
        parts.append("引用: " + " ".join(refs[:3]) + ("…" if len(refs) > 3 else ""))
    if markers:
        parts.append("含安全标记: " + ",".join(markers))
    line = " ｜ ".join(parts) if parts else "（空轮次）"
    return bound_text(line, _SUMMARY_LINE_MAX_CHARS)


def summarize_dropped_turns(dropped_messages: Sequence[dict]) -> str:
    """被丢弃轮次范围的指纹化确定性摘要（SUMMARIZE；无 LLM）。

    指纹 = 范围内全部消息规范形的 canonical content hash（``content_fingerprint``），
    按指纹缓存于进程内有界 ProjectionCache —— 同输入必同摘要。摘要本身从最新
    被丢弃轮开始逐轮抽取（最近的旧上下文价值最高），总量有界；未逐轮覆盖的
    更早轮次如实披露数量。
    """
    if not dropped_messages:
        return ""
    fingerprint = content_fingerprint([_canonical_turn_content(m) for m in dropped_messages])

    def _build() -> str:
        turns = _group_into_turns(list(dropped_messages))
        lines: List[str] = []
        summarized = 0
        for turn in reversed(turns):  # 最新被丢弃轮优先
            if len(lines) >= _SUMMARY_MAX_TURNS:
                break
            lines.append(f"- {_summarize_one_turn(turn)}")
            summarized += 1
        not_summarized = len(turns) - summarized
        body = "\n".join(lines)
        if not_summarized > 0:
            body += f"\n- …（更早 {not_summarized} 轮未逐轮摘要，全文在数据库）"
        return bound_text(body, _SUMMARY_TOTAL_MAX_CHARS)

    summary, _hit = _turn_summary_cache.get_or_build("ctx_policy", fingerprint, fingerprint, _build)
    return summary


def build_truncation_notice_with_summary(dropped_turns: int, dropped_messages: Sequence[dict]) -> str:
    """DROP_OLDEST 披露 + SUMMARIZE 摘要（既有 notice 文案保持不变，摘要追加其后）。"""
    notice = _build_truncation_notice(dropped_turns)
    summary = summarize_dropped_turns(dropped_messages)
    if summary:
        notice = f"{notice}\n{summary}"
    return notice


def run_history_ops(
    history: List[dict],
    *,
    budget: int = HISTORY_TOKEN_BUDGET,
    min_turns: int = HISTORY_MIN_TURNS,
    fold_keep_recent: Optional[int] = None,
) -> Dict[str, Any]:
    """对历史视图执行 KEEP pin 感知的 fold + DROP_OLDEST + SUMMARIZE。

    返回 dict（决策留痕，供 budget_report.context_policy 消费）：
    - ``kept``：截断后的历史消息视图（原 dict 或折叠浅拷贝，绝不改库）；
    - ``folded_count`` / ``dropped_turns`` / ``dropped_messages``；
    - ``pinned_count``：KEEP pin 命中的消息数；
    - ``notice``：dropped>0 时的披露块（含指纹化摘要）。
    """
    if not policy_enabled():
        # Kill switch：与 legacy fold+truncate 逐字节一致（无 pin、无摘要）。
        folded = fold_intra_turn_tool_results(history)
        kept, dropped = truncate_history_by_budget(folded, budget=budget, min_turns=min_turns)
        return {
            "kept": kept,
            "dropped_turns": dropped,
            "dropped_messages": [],
            "folded_count": 0,
            "pinned_count": 0,
            "notice": _build_truncation_notice(dropped) if dropped > 0 else "",
            "ops": [],
        }
    pinned = find_safety_pinned_indexes(history)
    fold_kwargs: Dict[str, Any] = {"pinned": pinned or None}
    if fold_keep_recent is not None:
        fold_kwargs["keep_recent"] = fold_keep_recent
    folded = fold_intra_turn_tool_results(history, **fold_kwargs)
    folded_count = sum(
        1 for a, b in zip(history, folded) if a is not b and a.get("content") != b.get("content")
    )
    # 折叠保持条数与顺序，pin 下标继续有效。
    kept, dropped_turns = truncate_history_by_budget(folded, budget=budget, min_turns=min_turns, pinned=pinned or None)
    kept_ids = {id(m) for m in kept}
    dropped_messages = [m for m in folded if id(m) not in kept_ids] if dropped_turns > 0 else []
    notice = build_truncation_notice_with_summary(dropped_turns, dropped_messages) if dropped_turns > 0 else ""
    return {
        "kept": kept,
        "dropped_turns": dropped_turns,
        "dropped_messages": dropped_messages,
        "folded_count": folded_count,
        "pinned_count": len(pinned),
        "notice": notice,
        "ops": [ContextOp.KEEP.value] * (1 if pinned else 0)
        + [ContextOp.SUMMARIZE.value] * (1 if dropped_turns > 0 else 0)
        + [ContextOp.DROP_OLDEST.value] * (1 if dropped_turns > 0 else 0),
    }


# ---------------------------------------------------------------------------
# CONDENSE：head 块的有界投影执行
# ---------------------------------------------------------------------------

def condense_text_to_budget(text: str, cap_tokens: int) -> Tuple[str, bool]:
    """把文本压缩到 est_tokens ≤ cap（确定性收敛；无法收敛时压到最小档并如实返回）。

    est 上界：CJK 1 字 ≈ 1.5 token → 起始按 ``cap*2`` 字符试探，逐轮折半，
    至多 6 轮（有界、同输入必同输出）。``cap_tokens <= 0`` 视为无建议，原样返回。
    """
    if cap_tokens <= 0 or _estimate_tokens(text) <= cap_tokens:
        return text, False
    max_chars = max(64, int(cap_tokens * 2))
    candidate = text
    for _ in range(6):
        candidate = bound_text(text, max_chars)
        if _estimate_tokens(candidate) <= cap_tokens:
            return candidate, True
        if max_chars <= 64:
            return candidate, True
        max_chars //= 2
    return candidate, True


def apply_condense_actions(
    head: List[dict],
    head_meta: Sequence[dict],
    actions: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """执行 advisor 的 condense 动作（仅作用于已登记的有界 head 块）。

    ``head_meta[i]`` = {"name", "category", "base_chars"(可选, sys_msg 的
    可压缩尾部起点)}；head_meta 与 head 按位置一一对应（"index" 键可选覆盖）。
    返回决策留痕（含 before/after token 数）。确定性：
    同 head + 同 actions → 逐字节同输出。
    """
    by_name = {a.get("item"): a for a in actions if isinstance(a, dict)}
    decisions: List[Dict[str, Any]] = []
    for idx_default, meta in enumerate(head_meta):
        action = by_name.get(meta.get("name"))
        if not action or action.get("action") != "condense":
            continue
        idx = meta.get("index", idx_default)
        if not isinstance(idx, int) or idx < 0 or idx >= len(head):
            continue
        msg = head[idx]
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        base_chars = meta.get("base_chars") or 0
        core, tail = content[:base_chars], content[base_chars:]
        condensed, changed = condense_text_to_budget(tail, int(action.get("section_cap") or 0))
        if not changed:
            continue
        before = _estimate_tokens(tail)
        after = _estimate_tokens(condensed)
        head[idx] = {**msg, "content": core + condensed}
        decisions.append({
            "op": ContextOp.CONDENSE.value,
            "target": meta.get("name"),
            "category": meta.get("category"),
            "before_tokens": before,
            "after_tokens": after,
            "section_cap": action.get("section_cap"),
        })
    return decisions


# ---------------------------------------------------------------------------
# OFFLOAD_REF：超预算时的有界再卸载（内联大 tool 结果 → ref+摘要）
# ---------------------------------------------------------------------------

#: 参与再卸载的 tool 结果最小估算 token（≈1KB CJK / 4KB ASCII 以下不动）。
OFFLOAD_MIN_TOOL_TOKENS = 256
#: 单次组装最多卸载条数（有界）。
OFFLOAD_MAX_ITEMS = 8

#: review R3 MAJOR（perf）：卸载内容去重缓存（session × content-hash → ref）。
#: 有界；进程内 —— 跨进程重复铸造只是冗余不是错误（ref 面本身会话作用域）。
_OFFLOAD_DEDUPE_CACHE: Dict[Tuple[str, str], str] = {}
_OFFLOAD_DEDUPE_CACHE_MAX = 256


def _offloaded_view(content: str, new_ref: Optional[str], refs: Sequence[str]) -> str:
    """卸载后的消息视图：卸载披露 + 摘要 + 既有 ref 游标（可解析性不减）。"""
    parts: List[str] = []
    if new_ref:
        parts.append(f"[已卸载] 原始结果（{len(content)} 字符）已转为会话引用 {new_ref}。")
    summary = ""
    try:
        parsed = json.loads(content)
        if isinstance(parsed, (dict, list)):
            summary = slim_tool_result(parsed, content, None)
    except (ValueError, TypeError):
        summary = ""
    if summary:
        parts.append(summary)
    else:
        parts.append(bound_text(content, 1200))
    if refs:
        parts.append("既有引用（仍可解析）: " + " ".join(refs[:5]) + ("…" if len(refs) > 5 else ""))
    return bound_text("\n".join(parts), MSG_MAX_CHARS + 200)


async def offload_pass(
    session_id: str,
    head: List[dict],
    store: Any,
    *,
    over_tokens: int,
    history_start: int,
) -> List[Dict[str, Any]]:
    """把 head 里超大的内联历史 tool 结果有界地转 ref+摘要（OFFLOAD_REF）。

    只替换 LLM 视图（head 中的浅拷贝 dict），绝不改动会话库内消息。最旧优先，
    至多 ``OFFLOAD_MAX_ITEMS`` 条；每条卸载量记账，直到覆盖 over_tokens。
    """
    decisions: List[Dict[str, Any]] = []
    if over_tokens <= 0:
        return decisions
    # review R3 MAJOR（perf）：同一内容在每次超预算装配都会重卸载 —— store
    # 每次铸造新 ref（无去重），长会话上 ref/磁盘无界膨胀。按内容哈希做
    # 进程内复用（有界 LRU）：同内容复用已铸 ref，只替换视图不新铸。
    global _OFFLOAD_DEDUPE_CACHE
    candidates: List[Tuple[int, dict]] = []
    for i in range(history_start, len(head)):
        msg = head[i]
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str) and _estimate_tokens(content) > OFFLOAD_MIN_TOOL_TOKENS:
            candidates.append((i, msg))
    for i, msg in candidates:  # 列表序 = 时间序（最旧优先）
        if over_tokens <= 0 or len(decisions) >= OFFLOAD_MAX_ITEMS:
            break
        content = msg["content"]
        refs = _ordered_unique_refs(content)
        new_ref: Optional[str] = None
        content_hash = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest()
        cached = _OFFLOAD_DEDUPE_CACHE.get((session_id, content_hash))
        if cached is not None:
            new_ref = cached
        else:
            try:
                parsed = json.loads(content)
                if isinstance(parsed, (dict, list)) and store is not None:
                    store_fn = getattr(store, "store", None)
                    if store_fn is not None:
                        new_ref = await store_fn(session_id, parsed, prefix="data")
                        if new_ref:
                            _OFFLOAD_DEDUPE_CACHE[(session_id, content_hash)] = new_ref
                            if len(_OFFLOAD_DEDUPE_CACHE) > _OFFLOAD_DEDUPE_CACHE_MAX:
                                for k in list(_OFFLOAD_DEDUPE_CACHE.keys())[
                                    : -_OFFLOAD_DEDUPE_CACHE_MAX
                                ]:
                                    _OFFLOAD_DEDUPE_CACHE.pop(k, None)
            except (ValueError, TypeError):
                new_ref = None
            except Exception as e:  # noqa: BLE001 — 卸载失败保留原文（诚实不破坏）
                logger.debug("offload store failed for %s: %s", session_id, e)
                new_ref = None
        if new_ref is None and len(content) <= MSG_MAX_CHARS:
            continue  # 无处可卸且不超上限 → 不动
        new_content = _offloaded_view(content, new_ref, refs)
        if new_content == content:
            continue
        saved = _estimate_tokens(content) - _estimate_tokens(new_content)
        head[i] = {**msg, "content": new_content}
        over_tokens -= saved
        decisions.append({
            "op": ContextOp.OFFLOAD_REF.value,
            "message_index": i,
            "ref": new_ref,
            "saved_tokens": max(0, saved),
        })
    return decisions


# ---------------------------------------------------------------------------
# RELOAD_REF：逐出 ref 的诚实 tombstone（可重载 vs 已失效）
# ---------------------------------------------------------------------------

_TOMBSTONE_MAX_SCAN = 32
_TOMBSTONE_MAX_REFS = 6
_TOMBSTONE_MAX_CHARS = 900


async def build_evicted_refs_tombstone(session_id: str, head: List[dict], store: Any) -> str:
    """组装产物引用了内存中已不存在的 ref 时，返回有界诚实提示（无则空串）。

    - 逐出但持久副本在（ref_spill_store）→ "可自动重载"（ref_resolver 已接
      ref→durable 回退）；
    - 副本也不在 → "已失效"。
    store 无 ref_exists（fake/降级后端）→ 不误报，返回空串。
    """
    if not policy_enabled():
        return ""
    ref_exists_fn = getattr(store, "ref_exists", None)
    if ref_exists_fn is None:
        return ""
    scanned: List[str] = []
    for msg in head:
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        if not text:
            continue
        for ref in REF_CURSOR_RE.findall(text):
            if ref not in scanned:
                scanned.append(ref)
            if len(scanned) >= _TOMBSTONE_MAX_SCAN:
                break
        if len(scanned) >= _TOMBSTONE_MAX_SCAN:
            break
    if not scanned:
        return ""
    lines: List[str] = []
    try:
        from app.services.session_data import ref_spill_store
    except Exception:  # noqa: BLE001 — 持久半边不可用时全部按"已失效"如实报告
        ref_spill_store = None
    for ref in scanned[:_TOMBSTONE_MAX_REFS]:
        try:
            exists = await ref_exists_fn(session_id, ref)
        except Exception:  # noqa: BLE001 — 探测失败不误报
            continue
        if exists:
            continue
        reloadable = False
        if ref_spill_store is not None:
            try:
                reloadable = ref_spill_store.has(session_id, ref)
            except Exception:  # noqa: BLE001
                reloadable = False
        if reloadable:
            lines.append(f"- {ref}: 已从内存缓存逐出，持久副本仍在，工具调用时会自动重载。")
        else:
            lines.append(f"- {ref}: 已失效（无法重载）；如需该数据请重新生成。")
    if not lines:
        return ""
    return bound_text(
        "[引用状态] 历史中的部分数据引用已不在内存缓存：\n" + "\n".join(lines),
        _TOMBSTONE_MAX_CHARS,
    )


# ---------------------------------------------------------------------------
# advisor 建议 → 执行编排（assembler 单一调用点）
# ---------------------------------------------------------------------------

#: 需要执行 CONDENSE 的 head 块登记名 → ContextOp（执行面词汇）。
_CONDENSABLE_CATEGORIES = {
    "TRACE_SUMMARIES",
    "DATA_PROFILE",
    "ALGORITHM_METADATA",
    "CARTOGRAPHY_METADATA",
    "SESSION_PLAN",
    "MAP_STATE",
}

_MAX_REPORTED_DECISIONS = 32


async def execute_advice(
    head: List[dict],
    head_meta: Sequence[dict],
    *,
    advice_actions: Sequence[Dict[str, Any]],
    window_known: bool,
    usable_tokens: int,
    session_id: str,
    store: Any,
    tools_tokens: int = 0,
    history_start: int = 0,
) -> Dict[str, Any]:
    """执行 advisor（唯一建议源）的动作；返回有界决策留痕。

    - 窗口未知（``window_known=False``）→ 不执行 CONDENSE/OFFLOAD（预算不可
      知时不落刀；KEEP/SUMMARIZE/DROP_OLDEST/tombstone 与窗口无关，已在
      历史管线/组装路径执行），如实报告。
    - CONDENSE：advisor 判超分区的 head 块 → 有界投影真实压缩。
    - OFFLOAD_REF：执行后仍超 usable → 有界再卸载。
    - TOOL_SCHEMAS 的 compress 针对调用方序列化产物，本层诚实跳过（不截断
      JSON schema 串 —— 那会改变工具契约；压缩权在 schema_compression 管线）。
    """
    report: Dict[str, Any] = {
        "enabled": True,
        "window_known": window_known,
    }
    if not window_known:
        report["executed"] = False
        report["reason"] = "window_unknown"
        report["decisions"] = []
        return report

    decisions: List[Dict[str, Any]] = []
    condensable = [
        a for a in advice_actions
        if isinstance(a, dict) and a.get("action") == "condense"
        and a.get("category") in _CONDENSABLE_CATEGORIES
    ]
    if condensable:
        decisions.extend(apply_condense_actions(head, head_meta, condensable))
    for a in advice_actions:
        if isinstance(a, dict) and a.get("action") == "compress":
            decisions.append({
                "op": "compress",
                "target": a.get("item"),
                "executed": False,
                "reason": "serialized_payload_out_of_policy_scope",
            })

    # 执行后仍超预算 → OFFLOAD_REF 有界再卸载。
    total = tools_tokens + sum(
        _estimate_tokens(m.get("content") if isinstance(m.get("content"), str) else "")
        for m in head
    )
    if total > usable_tokens:
        decisions.extend(await offload_pass(
            session_id, head, store,
            over_tokens=total - usable_tokens,
            history_start=history_start,
        ))
    report["executed"] = True
    report["decisions"] = decisions[:_MAX_REPORTED_DECISIONS]
    return report


def build_policy_items(
    head: Sequence[dict],
    head_meta: Sequence[dict],
    tools_payload: str = "",
    *,
    history_budget_tokens: int = HISTORY_TOKEN_BUDGET,
) -> List[Any]:
    """组装产物 → 细粒度 BudgetItem（供 GisBudgetAdvisor 判定分区超限）。

    与 ``measure_assembled_context`` 的粗分类并行：head 块按 head_meta 登记
    类别（SESSION_PLAN / CARTOGRAPHY_METADATA / DATA_PROFILE / TRACE_SUMMARIES /
    MAP_STATE），未登记的 system 块仍归 SYSTEM_INSTRUCTIONS（不可触碰）。
    返回 BudgetItem 列表（惰性 import 避免环）。
    """
    from app.services.chat.context_budget import BudgetItem, Category

    items: List[Any] = []
    history_tokens = 0
    last_idx = len(head) - 1
    for i, msg in enumerate(head):
        role = msg.get("role")
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        meta = head_meta[i] if i < len(head_meta) else None
        if role == "system":
            category_name = (meta or {}).get("category")
            name = (meta or {}).get("name") or f"system[{i}]"
            if category_name == "MAP_STATE" and (meta or {}).get("base_chars"):
                base = int(meta["base_chars"])
                core, tail = text[:base], text[base:]
                items.append(BudgetItem(
                    category=Category.SYSTEM_INSTRUCTIONS, name="system_core", text=core,
                ))
                items.append(BudgetItem(
                    category=Category.MAP_STATE, name=name, text=tail,
                ))
                continue
            if category_name in _CONDENSABLE_CATEGORIES:
                items.append(BudgetItem(
                    category=Category[category_name], name=name, text=text,
                ))
                continue
            items.append(BudgetItem(
                category=Category.SYSTEM_INSTRUCTIONS, name=name, text=text,
            ))
        elif role == "user" and i == last_idx:
            items.append(BudgetItem(
                category=Category.USER_PROMPT, name="user_final", text=text,
            ))
        else:
            history_tokens += _estimate_tokens(text)
    items.append(BudgetItem(
        category=Category.HISTORY, name="history",
        est_tokens=history_tokens,
        hard_limit_tokens=history_budget_tokens,
    ))
    if tools_payload:
        items.append(BudgetItem(
            category=Category.TOOL_SCHEMAS, name="tool_schemas", text=tools_payload,
        ))
    return items


# ---------------------------------------------------------------------------
# 确定性溢出恢复：llm_client 的重裁 hook（阶梯本身在 llm_client，恰好 1 次）
# ---------------------------------------------------------------------------

_RETRIM_MIN_BUDGET_TOKENS = 1200
_RETRIM_FOLD_KEEP_RECENT = 4


def retrim_messages_for_retry(messages: Sequence[dict]) -> Optional[List[dict]]:
    """CONTEXT_TOO_LARGE 后的确定性重裁：收紧历史预算重跑折叠 + DROP_OLDEST。

    - 前导 system 块（含安全注入）原样保留；
    - 其余历史按「当前估算的一半」（下限 1200 token）重新截断，轮内折叠收紧到
      最近 4 条 tool 结果 —— 轮次粒度保证 tool_call/tool 配对不被拆散；
    - 无可收紧（已是最小形态）→ None（调用方不重试，诚实失败）。
    纯函数、确定性、无 LLM。
    """
    split = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            split = i + 1
        else:
            break
    head = list(messages[:split])
    rest = list(messages[split:])
    if not rest:
        return None
    est = sum(_estimate_tokens(m.get("content")) for m in rest)
    target = max(_RETRIM_MIN_BUDGET_TOKENS, est // 2)
    folded = fold_intra_turn_tool_results(rest, keep_recent=_RETRIM_FOLD_KEEP_RECENT)
    # review R2 MAJOR-9：重裁是**最极端的压力路径** —— KEEP pin 必须同样
    # 生效（Tier-3 确认回执/自愈指引不随重试预算消失）。pin 下标在 fold
    # 之后求值（truncate 的输入坐标系）。
    folded_list = list(folded)
    pinned = find_safety_pinned_indexes(folded_list)
    trimmed, dropped = truncate_history_by_budget(
        folded_list, budget=target, pinned=pinned or None)
    if dropped <= 0 and folded is rest:
        return None
    if len(trimmed) >= len(rest) and dropped <= 0:
        return None
    return head + trimmed


def _policy_retrim_hook(messages: Sequence[dict]) -> Optional[List[dict]]:
    """挂进 llm_client 的安全包装：重裁任何异常都退回"不重试"而非中断调用方。

    review Round-1 minor #8：调用时复查 kill switch —— import 期注册后
    运行中关停（GIS_CONTEXT_POLICY=0）立即失效，不残留恢复面。
    """
    if not policy_enabled():
        return None
    try:
        return retrim_messages_for_retry(messages)
    except Exception as e:  # noqa: BLE001 — 恢复面自身绝不抛
        logger.warning("[CONTEXT-RETRIM] hook failed, giving up ladder: %s", e)
        return None


def register_with_llm_client() -> bool:
    """把确定性重裁 hook 注册进 llm_client（模块导入期调用；失败不阻断）。"""
    try:
        from app.services.chat.llm_client import register_context_retrim_hook
        register_context_retrim_hook(_policy_retrim_hook)
        return True
    except Exception as e:  # noqa: BLE001 — 溢出恢复是增值面
        logger.warning("context_policy: llm_client retrim hook registration failed: %s", e)
        return False


if policy_enabled():
    register_with_llm_client()


# ---------------------------------------------------------------------------
# Pi 路径接缝（audit 05 §g-3）：同一策略作用于 ToolDispatchResult.llm_payload
# ---------------------------------------------------------------------------

_PI_PAYLOAD_MAX_CHARS = MSG_MAX_CHARS
_PI_PAYLOAD_MAX_REFS = 8


def policy_postprocess_llm_payload(payload: str, *, max_chars: int = _PI_PAYLOAD_MAX_CHARS) -> str:
    """Pi 路径 tool→prompt 接缝的有界后处理（纯函数、确定性）。

    legacy 路径的卸载/折叠发生在组装期；Pi 子进程自持历史，唯一可下刀的点是
    交给它的 ``ToolDispatchResult.llm_payload``。超限时保头 + 显式截断标记 +
    ref 游标清单（可解析性只增不减）。与 OFFLOAD_REF 同一有界化纪律。
    """
    if not isinstance(payload, str) or len(payload) <= max_chars:
        return payload or ""
    refs = _ordered_unique_refs(payload, cap=_PI_PAYLOAD_MAX_REFS)
    budget = max_chars
    if refs:
        budget -= len("…(truncated)\n既有引用: " + " ".join(refs))
    kept = bound_text(payload, max(64, budget))
    parts = [kept]
    if refs:
        parts.append("既有引用（仍可解析）: " + " ".join(refs))
    return "\n".join(parts)
