"""G37：原生 usage 采集与持久宿主账本（WP08/WP09）。

数据源纪律（与 vendor pin b4afdddf 实读对齐）：
- 只消费 ``message_end``（终态 usage / responseModel / stopReason 都在事
  件本身）；``message_update`` 上的累计 usage 只作实时面参考，不入账
  （增量口径会漏缓存/最终值）。
- ``role=toolResult`` 的 ``usage`` 是原生已合计的 nested 模型工作 —— 记
  ``kind=tool_result`` 一次，绝不与父 assistant 调用再相加（M21）。
- ``reasoning ⊂ output``、``cacheWrite1h ⊂ cacheWrite``（vendor 语义，
  原样保留分项，不重复计入合计）。
- 费用：``cost.totalFromProvider=true`` → provider-reported；否则若宿主
  价格表有该模型条目 → estimated（带版本）；否则 unknown（零不当免费，
  M28/M29）。金额一律 micro-USD 整数（1e-6 USD），避免浮点累计。
- ``(turn_id, call_seq)`` 唯一 → 重放/重复回调不重复计费；内容完全相同
  的两次真实调用是两个 call_seq（M24）。

持久化在事件循环外（独立短事务 + ``asyncio.create_task``）：SSE 流绝不
被账本写阻塞；持久失败 → 汇总行缺失/``complete=False`` → 面上如实
incomplete（M32），绝不伪造零值。
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 宿主价格表版本（catalog estimate 的来源标识；provider-reported 不经此表）。
PRICE_TABLE_VERSION = "llm_price.v1"

#: micro-USD 整数转换的防溢出上界（~9e13 USD；超出视为异常数据拒记金额）。
_MAX_COST_MICRO_USD = 10**20

#: price 表：model_id → (input_usd_per_1m, output_usd_per_1m)。空 = 全部
#: unknown。当前仅测试/部署注入（env ``SESSION_MODEL_PRICE_TABLE_JSON``），
#: 缺席时不从 cost_class 推测金额（任务书红线）。
_PRICE_TABLE: Dict[str, tuple[float, float]] = {}


def _load_price_table() -> Dict[str, tuple[float, float]]:
    import json
    import os

    raw = os.getenv("SESSION_MODEL_PRICE_TABLE_JSON", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        table: Dict[str, tuple[float, float]] = {}
        for mid, entry in (data or {}).items():
            in_usd = float(entry.get("input_usd_per_1m", 0) or 0)
            out_usd = float(entry.get("output_usd_per_1m", 0) or 0)
            if in_usd >= 0 and out_usd >= 0:
                table[str(mid)] = (in_usd, out_usd)
        return table
    except Exception:  # noqa: BLE001 — 价格表损坏 = 无价（unknown），不阻断
        logger.warning("[usage-ledger] SESSION_MODEL_PRICE_TABLE_JSON parse failed; prices unknown", exc_info=True)
        return {}


def _price_table() -> Dict[str, tuple[float, float]]:
    global _PRICE_TABLE
    if not _PRICE_TABLE:
        _PRICE_TABLE = _load_price_table()
    return _PRICE_TABLE


def reset_price_table_cache() -> None:
    """测试钩子：清空价格表缓存（改 env 后重读）。"""
    global _PRICE_TABLE
    _PRICE_TABLE = {}


def _to_micro_usd(usd: float) -> Optional[int]:
    if not isinstance(usd, (int, float)) or not math.isfinite(usd) or usd < 0:
        return None
    micro = int(round(float(usd) * 1_000_000))
    if micro > _MAX_COST_MICRO_USD:
        return None
    return micro


def _bounded_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return max(0, int(value))
    return None


@dataclass(frozen=True)
class MessageUsageFact:
    """一条 message_end 提炼出的记账事实（纯函数产物，无 I/O）。"""

    kind: str                       # assistant | tool_result | other
    provider: Optional[str]
    model: Optional[str]
    response_model: Optional[str]
    usage: Dict[str, Optional[int]]
    cost_total_micro_usd: Optional[int]
    cost_from_provider: Optional[bool]
    stop_reason: Optional[str]
    message_ts: Optional[int] = None  # vendor message.timestamp（同 turn 内重复投递去重键）


def extract_usage_fact(event: dict) -> Optional[MessageUsageFact]:
    """从 ``message_end`` 事件提炼记账事实（纯函数）。

    非携带 usage 的消息（user/system）→ None。字段缺席保持 None（诚实未
    知），绝不以 0 冒充「确认为零」。
    """
    if not isinstance(event, dict) or event.get("type") != "message_end":
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    usage = message.get("usage")
    if not isinstance(usage, dict):
        # toolResult 的 usage 可缺席 = 该工具无 nested 模型工作 → 不入账
        # （无中生有的零值行只会污染 coverage 语义）。
        if role == "assistant":
            usage = {}
        else:
            return None

    kind = {"assistant": "assistant", "toolResult": "tool_result"}.get(str(role), "other")
    cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
    cost_total = cost.get("total")
    from_provider = cost.get("totalFromProvider") if isinstance(cost.get("totalFromProvider"), bool) else None

    micro: Optional[int] = None
    if isinstance(cost_total, (int, float)):
        micro = _to_micro_usd(float(cost_total))
        if micro == 0 and from_provider is not True:
            # M29：无价目时 runtime 的默认零不构成「确认为零」—— 归 unknown
            # （缺价），绝不显示成 estimated $0 / 免费。
            micro = None

    return MessageUsageFact(
        kind=kind,
        provider=message.get("provider") if isinstance(message.get("provider"), str) else None,
        model=message.get("model") if isinstance(message.get("model"), str) else None,
        response_model=(
            message.get("responseModel")
            if isinstance(message.get("responseModel"), str) else None
        ),
        usage={
            "input": _bounded_int(usage.get("input")),
            "output": _bounded_int(usage.get("output")),
            "cacheRead": _bounded_int(usage.get("cacheRead")),
            "cacheWrite": _bounded_int(usage.get("cacheWrite")),
            "cacheWrite1h": _bounded_int(usage.get("cacheWrite1h")),
            "reasoning": _bounded_int(usage.get("reasoning")),
            "totalTokens": _bounded_int(usage.get("totalTokens")),
        },
        cost_total_micro_usd=micro,
        cost_from_provider=from_provider,
        stop_reason=(
            message.get("stopReason")
            if isinstance(message.get("stopReason"), str) else None
        ),
        message_ts=(
            int(message["timestamp"])
            if isinstance(message.get("timestamp"), (int, float))
            else None
        ),
    )


def estimate_cost_micro_usd(fact: MessageUsageFact) -> Optional[int]:
    """宿主价格表估算（仅当 provider 未上报金额时）。

    价格表条目按 ``input_usd_per_1m/output_usd_per_1m``；cache 维按 input
    价近似（无独立价时不猜）。无条目 → None（unknown）。
    """
    if fact.cost_total_micro_usd is not None and fact.cost_from_provider:
        return None  # provider-reported 已有，无需估算
    table = _price_table()
    model_id = fact.model or ""
    if model_id not in table:
        return None
    in_usd, out_usd = table[model_id]
    usage = fact.usage
    in_tok = usage.get("input") or 0
    out_tok = usage.get("output") or 0
    cache_read = usage.get("cacheRead") or 0
    if in_tok + out_tok + cache_read == 0:
        return None
    usd = (in_tok + cache_read) * in_usd / 1_000_000 + out_tok * out_usd / 1_000_000
    return _to_micro_usd(usd)


class TurnUsageCollector:
    """单 turn 的内存采集器（事件循环内 O(1) 更新，落盘交给后台任务）。

    ``call_seq`` 只在**真实 message_end 到达**时递增 —— 重放/重复投递由
    ``(turn_id, call_seq)`` 唯一约束在 DB 侧兜底（插入冲突 = 已记，跳过）。
    """

    def __init__(self, session_id: str, turn_id: str, run_id: str = "") -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.run_id = run_id
        self.facts: List[MessageUsageFact] = []
        self.write_failures = 0
        self.expected_records = 0
        # review-P3：同 turn 内同一 message 的重复投递去重（vendor 每条消息
        # 带唯一 timestamp；缺席时不去重 —— DB 唯一约束兜底跨 persist 场景）。
        self._seen_message_ts: set = set()

    def observe(self, event: dict) -> Optional[MessageUsageFact]:
        fact = extract_usage_fact(event)
        if fact is None:
            return None
        if fact.message_ts is not None:
            key = (fact.kind, fact.message_ts)
            if key in self._seen_message_ts:
                return None  # 重复投递：同一消息只记一次
            self._seen_message_ts.add(key)
        self.facts.append(fact)
        self.expected_records += 1
        return fact

    @property
    def totals(self) -> Dict[str, int]:
        """账本聚合口径：input+output+cacheRead+cacheWrite（vendor total
        语义；reasoning ⊂ output、cacheWrite1h ⊂ cacheWrite 不另加）。"""
        t = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}
        for fact in self.facts:
            u = fact.usage
            t["input"] += u.get("input") or 0
            t["output"] += u.get("output") or 0
            t["cacheRead"] += u.get("cacheRead") or 0
            t["cacheWrite"] += u.get("cacheWrite") or 0
        t["total"] = t["input"] + t["output"] + t["cacheRead"] + t["cacheWrite"]
        return t

    @property
    def cost_view(self) -> tuple[Optional[int], str]:
        """(micro_usd 合计或 None, 来源)。来源规则：
        - 全部 provider-reported → provider；
        - 无 provider 值且全部可估算 → estimated；
        - 混合 → mixed（金额只合计已知部分，面上分开披露）；
        - 全未知 → (None, unknown)。"""
        provider_sum: List[int] = []
        estimated_sum: List[int] = []
        unknown = False
        for fact in self.facts:
            if fact.cost_total_micro_usd is not None and fact.cost_from_provider:
                # 供应商上报（totalFromProvider=true）
                provider_sum.append(fact.cost_total_micro_usd)
            elif fact.cost_total_micro_usd is not None:
                # runtime 目录价估算（Pi models.json 价目；非 provider 账单）
                estimated_sum.append(fact.cost_total_micro_usd)
            else:
                est = estimate_cost_micro_usd(fact)
                if est is not None:
                    estimated_sum.append(est)
                else:
                    unknown = True
        if not self.facts:
            return (None, "unknown")
        if unknown:
            if provider_sum or estimated_sum:
                return (sum(provider_sum) + sum(estimated_sum), "mixed")
            return (None, "unknown")
        if provider_sum and estimated_sum:
            return (sum(provider_sum) + sum(estimated_sum), "mixed")
        if provider_sum:
            return (sum(provider_sum), "provider")
        return (sum(estimated_sum), "estimated")


async def persist_turn_records(
    collector: TurnUsageCollector,
    *,
    runtime_epoch: str = "",
    catalog_generation: str = "",
    config_generation: str = "",
    preference_revision: Optional[int] = None,
    mode: str = "inherit",
    requested_model: str = "",
    thinking_requested: Optional[str] = None,
    effective_provider: Optional[str] = None,
    effective_model: Optional[str] = None,
    thinking_effective: Optional[str] = None,
    status: str = "pending",
    failure_reason: Optional[str] = None,
    finalize: bool = False,
) -> bool:
    """把 turn 回执 + usage 明细写入宿主账本（独立短事务）。

    幂等：turn 行 upsert（按 turn_id）；明细行按 ``(turn_id, call_seq)``
    插入，冲突跳过。``finalize=True`` 时结算（complete=records 全部落盘）。
    返回是否全部落盘成功（失败时调用方保持/标记 incomplete）。
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.core.database import async_db_session
    from app.models.db_model import ChatTurnRuntime, ChatUsageRecord

    totals = collector.totals
    cost_micro, cost_source = collector.cost_view
    now = datetime.now(timezone.utc)
    try:
        # 本次持久化成功则清零历史失败计数（失败由下方 except 重新标记）。
        collector.write_failures = 0
        async with async_db_session() as db:
            row = (
                await db.execute(
                    select(ChatTurnRuntime).where(ChatTurnRuntime.turn_id == collector.turn_id)
                )
            ).scalar_one_or_none()
            if row is None:
                row = ChatTurnRuntime(
                    session_id=collector.session_id,
                    turn_id=collector.turn_id,
                    run_id=collector.run_id or None,
                    runtime_epoch=runtime_epoch,
                    catalog_generation=catalog_generation,
                    config_generation=config_generation,
                    preference_revision=preference_revision,
                    mode=mode,
                    requested_model=requested_model,
                    thinking_requested=thinking_requested,
                    applied_at=now,
                )
                db.add(row)
            row.status = status
            row.failure_reason = failure_reason
            row.effective_provider = effective_provider
            row.effective_model = effective_model
            row.thinking_effective = thinking_effective
            row.tokens_input = totals["input"]
            row.tokens_output = totals["output"]
            row.tokens_cache_read = totals["cacheRead"]
            row.tokens_cache_write = totals["cacheWrite"]
            row.tokens_total = totals["total"]
            row.cost_total = cost_micro
            row.cost_source = cost_source
            row.records_expected = collector.expected_records
            row.records_written = len(collector.facts) - collector.write_failures
            if finalize:
                row.settled_at = now
                row.complete = collector.write_failures == 0
            await db.flush()

            for seq, fact in enumerate(collector.facts):
                exists = (
                    await db.execute(
                        select(ChatUsageRecord.id).where(
                            ChatUsageRecord.turn_id == collector.turn_id,
                            ChatUsageRecord.call_seq == seq,
                        )
                    )
                ).scalar_one_or_none()
                if exists is not None:
                    continue
                record_micro = fact.cost_total_micro_usd
                record_from_provider = fact.cost_from_provider
                usage_payload = dict(fact.usage)
                if record_micro is not None and record_from_provider is True:
                    usage_payload["cost_source"] = "provider"
                else:
                    est = estimate_cost_micro_usd(fact)
                    if record_micro is None and est is not None:
                        record_micro, record_from_provider = est, False
                    if record_micro is not None:
                        # review-P3：estimated 随行盖价格来源版本戳（可审计
                        # —— 价格表换版后旧行可对账）。
                        usage_payload["cost_source"] = "estimated"
                        usage_payload["price_table"] = (
                            "pi-model-catalog"
                            if fact.cost_total_micro_usd is not None
                            else PRICE_TABLE_VERSION
                        )
                # response_model 只在 assistant 消息上回填 turn 汇总（物理
                # 响应身份；toolResult 无此概念）。
                db.add(ChatUsageRecord(
                    session_id=collector.session_id,
                    turn_id=collector.turn_id,
                    call_seq=seq,
                    kind=fact.kind,
                    provider=fact.provider,
                    model=fact.model,
                    response_model=fact.response_model,
                    usage_json=usage_payload,
                    cost_total=record_micro,
                    cost_from_provider=record_from_provider,
                    stop_reason=fact.stop_reason,
                ))
                if fact.kind == "assistant" and fact.response_model:
                    row.response_model = fact.response_model
            await db.commit()
        return True
    except Exception:  # noqa: BLE001 — 账本持久失败绝不打断 turn 流
        collector.write_failures = len(collector.facts)
        logger.error(
            "[usage-ledger] persist failed session=%s turn=%s (marked incomplete)",
            collector.session_id, collector.turn_id, exc_info=True,
        )
        return False


#: fire-and-forget persist 任务的强引用表 —— 只 create_task 不持引用会被
#: GC 回收（coroutine 永不完成，usage 面静默少 turn）。done 回调移除。
_persist_tasks: set[asyncio.Task] = set()


def spawn_persist_task(collector: TurnUsageCollector, **kwargs) -> asyncio.Task:
    """fire-and-forget 落盘（SSE 循环内调用，绝不 await）。"""
    task = asyncio.create_task(persist_turn_records(collector, **kwargs))
    _persist_tasks.add(task)
    task.add_done_callback(_persist_tasks.discard)
    return task


async def session_usage_summary(db, session_id: str, limit_turns: int = 20) -> Dict[str, Any]:
    """会话累计 + 最近 turn 明细（API/工具/预算投影共用口径）。

    coverage 如实分层：assistant/tool_result 为主链已采；compaction 与
    辅助调用不在本账本（面披露），对账走 Pi session_stats 快照。
    """
    from sqlalchemy import func, select

    from app.models.db_model import ChatTurnRuntime

    totals_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(ChatTurnRuntime.tokens_input), 0),
                func.coalesce(func.sum(ChatTurnRuntime.tokens_output), 0),
                func.coalesce(func.sum(ChatTurnRuntime.tokens_cache_read), 0),
                func.coalesce(func.sum(ChatTurnRuntime.tokens_cache_write), 0),
                func.coalesce(func.sum(ChatTurnRuntime.tokens_total), 0),
                func.count(ChatTurnRuntime.id),
                func.sum(func.coalesce(ChatTurnRuntime.cost_total, 0)),
            ).where(ChatTurnRuntime.session_id == session_id)
        )
    ).one()
    incomplete = (
        await db.execute(
            select(func.count(ChatTurnRuntime.id)).where(
                ChatTurnRuntime.session_id == session_id,
                ChatTurnRuntime.complete == False,  # noqa: E712
            )
        )
    ).scalar_one()

    recent_rows = (
        await db.execute(
            select(ChatTurnRuntime)
            .where(ChatTurnRuntime.session_id == session_id)
            .order_by(ChatTurnRuntime.created_at.desc())
            .limit(limit_turns)
        )
    ).scalars().all()

    # review-P3：来源标签对**全部** turn 聚合（SQL 分组），不再用最近 N 行
    # 采样 —— 部分未知的总额不会被采样窗口误标成单一来源。
    cost_source_rows = (
        await db.execute(
            select(ChatTurnRuntime.cost_source, func.count(ChatTurnRuntime.id))
            .where(ChatTurnRuntime.session_id == session_id)
            .group_by(ChatTurnRuntime.cost_source)
        )
    ).all()
    cost_sources = {row[0] for row in cost_source_rows}
    return {
        "session_id": session_id,
        "totals": {
            "input": int(totals_row[0] or 0),
            "output": int(totals_row[1] or 0),
            "cacheRead": int(totals_row[2] or 0),
            "cacheWrite": int(totals_row[3] or 0),
            "total": int(totals_row[4] or 0),
        },
        "turns_recorded": int(totals_row[5] or 0),
        "turns_incomplete": int(incomplete or 0),
        "cost_total_micro_usd": (
            int(totals_row[6]) if totals_row[6] is not None else None
        ),
        "cost_source": (
            "provider" if cost_sources == {"provider"}
            else "estimated" if cost_sources == {"estimated"}
            else "mixed" if cost_sources - {"unknown"}
            else "unknown"
        ),
        "recent_turns": [
            {
                "turn_id": r.turn_id,
                "status": r.status,
                "mode": r.mode,
                "requested_model": r.requested_model,
                "effective_model": r.effective_model,
                "thinking_requested": r.thinking_requested,
                "thinking_effective": r.thinking_effective,
                "response_model": r.response_model,
                "tokens": {
                    "input": r.tokens_input, "output": r.tokens_output,
                    "cacheRead": r.tokens_cache_read, "cacheWrite": r.tokens_cache_write,
                    "total": r.tokens_total,
                },
                "cost_total_micro_usd": r.cost_total,
                "cost_source": r.cost_source,
                "complete": r.complete,
                "records": {"expected": r.records_expected, "written": r.records_written},
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reversed(recent_rows)
        ],
        "coverage": {
            "included": ["pi_main_model_calls", "pi_tool_result_nested_usage"],
            "not_included": [
                "legacy_auxiliary_calls (title/planner/spatial)",
                "compaction_model_work (reconcile via session_stats)",
            ],
        },
    }


__all__ = [
    "PRICE_TABLE_VERSION",
    "MessageUsageFact",
    "TurnUsageCollector",
    "estimate_cost_micro_usd",
    "extract_usage_fact",
    "persist_turn_records",
    "reset_price_table_cache",
    "session_usage_summary",
    "spawn_persist_task",
]
