"""跨运行视觉 recurrence 账本（F15/ADR-0214 决策五）—— 披露面硬停。

同一 ``recurrence_fingerprint``（UnifiedFinding 既有铸造点，零新哈希）的
视觉缺陷在 ≥ ``MAX_VISUAL_RECURRENCE_RUNS`` 次 finalization 运行中反复
出现 → 移入 ``hard_stopped``：披露面降 info、**不再进入 plan-face**
（不再反复向用户索要同一修复）。与 W11 账本（per-epoch 修复面）和
healer 收敛账本（per-fingerprint 修复面）正交——本账本是观察侧披露面。

状态 ``map_state["_visual_observation_state"]``（``_`` 前缀私有键，旧读者
忽略）：findings ≤16 FIFO、hard_stopped ≤8。任何异常 → 空披露绝不阻断
终验。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

STATE_KEY = "_visual_observation_state"
STATE_VERSION = 1

#: 同一指纹允许的观察轮数：第 3 次仍现 → 硬停（修复请求已两度未生效，
#: 继续索要是对抗）。与 healer MAX_VISUAL_HEAL_ITERATIONS、W11 attempts 上限
#: 量级对齐但语义独立（观察侧）。
MAX_VISUAL_RECURRENCE_RUNS = 3
_MAX_TRACKED = 16
_MAX_HARD_STOPPED = 8


@dataclass
class VisualRecurrenceLedger:
    """账本内存形态（load → record → save 由调用方编排）。"""

    findings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    hard_stopped: List[str] = field(default_factory=list)

    @classmethod
    def from_state(cls, raw: Any) -> "VisualRecurrenceLedger":
        if not isinstance(raw, dict) or int(raw.get("v") or 0) != STATE_VERSION:
            return cls()
        findings: Dict[str, Dict[str, Any]] = {}
        raw_findings = raw.get("findings")
        if isinstance(raw_findings, dict):
            for fp, entry in list(raw_findings.items())[:_MAX_TRACKED * 2]:
                key = str(fp or "")[:64]
                if key and isinstance(entry, dict):
                    findings[key] = entry
        stopped = [
            str(fp)[:64]
            for fp in (raw.get("hard_stopped") or [])
            if isinstance(fp, (str,)) and fp
        ][:_MAX_HARD_STOPPED]
        return cls(findings=findings, hard_stopped=stopped)

    def to_state(self) -> Dict[str, Any]:
        return {
            "v": STATE_VERSION,
            "findings": dict(list(self.findings.items())[:_MAX_TRACKED]),
            "hard_stopped": self.hard_stopped[:_MAX_HARD_STOPPED],
        }


@dataclass(frozen=True)
class RecurrenceReport:
    """一次记账的产出（finalizer 消费的纯披露面）。"""

    ledger: VisualRecurrenceLedger
    recurrent: Tuple[str, ...] = ()      # 本轮出现且 runs ≥2（披露升级）
    newly_hard_stopped: Tuple[str, ...] = ()
    hard_stopped_findings: Tuple[Any, ...] = ()  # 本轮命中硬停的 finding 对象


def observe_visual_findings(
    ledger: VisualRecurrenceLedger,
    visual_findings: List[Any],
    *,
    mapspec_revision: int = 0,
) -> RecurrenceReport:
    """记账一轮视觉 findings（纯函数；返回新披露 + 更新后的账本）。

    - 新指纹 → runs=1；重复 → runs+1；缺席的指纹**保留计数**（观察间歇
      不清零——间歇复现是同因的强信号）；只在账本 FIFO 溢出时淘汰最老。
    - runs ≥ MAX → 首次移入 hard_stopped（newly_hard_stopped 披露）。
    """
    recurrent: List[str] = []
    newly_stopped: List[str] = []
    stopped_findings: List[Any] = []
    seen: set = set()
    for vf in tuple(visual_findings or [])[:_MAX_TRACKED]:
        fp = str(getattr(vf, "recurrence_fingerprint", "") or "")[:64]
        if not fp:
            continue
        seen.add(fp)
        entry = ledger.findings.get(fp) or {
            "runs": 0, "first_revision": int(mapspec_revision),
        }
        entry["runs"] = int(entry.get("runs") or 0) + 1
        entry["last_revision"] = int(mapspec_revision)
        ledger.findings[fp] = entry
        if entry["runs"] >= MAX_VISUAL_RECURRENCE_RUNS:
            if fp not in ledger.hard_stopped:
                ledger.hard_stopped.append(fp)
                newly_stopped.append(fp)
                stopped_findings.append(vf)
            else:
                stopped_findings.append(vf)
        elif entry["runs"] >= 2:
            recurrent.append(fp)
    # FIFO 淘汰（未硬停的最老指纹先出）。
    while len(ledger.findings) > _MAX_TRACKED:
        for fp in list(ledger.findings.keys()):
            if fp not in ledger.hard_stopped:
                ledger.findings.pop(fp)
                break
        else:
            break
    return RecurrenceReport(
        ledger=ledger,
        recurrent=tuple(recurrent),
        newly_hard_stopped=tuple(newly_stopped),
        hard_stopped_findings=tuple(stopped_findings),
    )


def is_hard_stopped(ledger: VisualRecurrenceLedger, fingerprint: str) -> bool:
    return str(fingerprint or "")[:64] in ledger.hard_stopped


async def load_ledger(session_id: str) -> VisualRecurrenceLedger:
    from app.services.session_data import session_data_manager

    try:
        raw = await session_data_manager.get_map_state(session_id)
        return VisualRecurrenceLedger.from_state(
            (raw or {}).get(STATE_KEY))
    except Exception:  # noqa: BLE001 — 账本缺席 = 空账本（诚实缺席）
        return VisualRecurrenceLedger()


async def save_ledger(session_id: str, ledger: VisualRecurrenceLedger) -> bool:
    from app.services.session_data import session_data_manager

    try:
        return bool(await session_data_manager.set_map_state(
            session_id, STATE_KEY, ledger.to_state()))
    except Exception:  # noqa: BLE001 — 披露面绝不阻断终验
        logger.warning("[VisualRecurrence] save failed", exc_info=True)
        return False


def reset_fingerprints(ledger: VisualRecurrenceLedger,
                       fingerprints: List[str]) -> VisualRecurrenceLedger:
    """修复尝试即进展信号：批准的 visual repair 重置相关指纹计数。"""
    for fp in fingerprints or []:
        key = str(fp or "")[:64]
        ledger.findings.pop(key, None)
        if key in ledger.hard_stopped:
            ledger.hard_stopped.remove(key)
    return ledger


__all__ = [
    "STATE_KEY",
    "STATE_VERSION",
    "MAX_VISUAL_RECURRENCE_RUNS",
    "VisualRecurrenceLedger",
    "RecurrenceReport",
    "observe_visual_findings",
    "is_hard_stopped",
    "load_ledger",
    "save_ledger",
    "reset_fingerprints",
]
