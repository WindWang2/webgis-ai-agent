"""无进展 / 重复调用检测 V2（ADR-0101 Wave 6, §29）。

现状：legacy 引擎有连败计数（is_suspicious_result 过滤 + 阈值 3）+ 去重
哨兵（(normalize_tool_name(tool), 原始 args 串)）。两个盲区：

1. **别名振荡**：LLM 第 1 轮调 ``buffer``、第 2 轮调 ``buffer_layer`` ——
   名字不同 → 去重哨兵不拦；canonical 名相同 → 实为同一调用。
2. **同参重复失败**：去重哨兵只拦**同进程同轮**的重复；跨轮/失败后的
   同参重试没有模式级判定（连败计数只看结果不看形态）。

本模块提供确定性模式判定（纯函数 + 有界 tracker）：

- ``canonical_call_signature(name, args)``：canonical 工具名（别名折叠）+
  canonical JSON 参数（ref 游标保序抽取，大数据载荷以 (type, len) 摘要
  替代）—— 同一语义调用必得同一签名，且与 replay/trace 词汇一致；
- ``CallPatternTracker.record(...)``：返回 reason codes
 （exact_repeat_failure / alias_oscillation / repeated_read / repeated_mutation），
  上游真实状态变化时防误报（状态代数由调用方传入 ``state_epoch``）。

与既有机制的关系：**additive** —— 连败阈值语义不动；tracker 只补充形态
级 reason codes 供引擎失败详情与 trace 使用。
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _summarize_value(val: Any, depth: int = 0) -> Any:
    """大载荷的确定性摘要：list/dict 取 (type, len) + 前几项，深度受限。"""
    if depth > 3:
        return "…"
    if isinstance(val, dict):
        if len(val) > 8:
            return {"__dict__": len(val)}
        return {k: _summarize_value(v, depth + 1) for k, v in sorted(val.items())}
    if isinstance(val, (list, tuple)):
        if len(val) > 4:
            return {"__list__": len(val), "head": [_summarize_value(v, depth + 1) for v in val[:2]]}
        return [_summarize_value(v, depth + 1) for v in val]
    if isinstance(val, str) and len(val) > 120:
        return {"__str__": len(val), "head": val[:32]}
    if isinstance(val, float):
        return round(val, 6)
    return val


def canonical_call_signature(tool_name: str, arguments: Any) -> str:
    """稳定调用签名：canonical 名 + canonical 化参数（§29）。

    - 工具名经别名折叠（registry._TOOL_NAME_ALIASES 同源）；
    - 参数键排序、大载荷摘要化 —— 100k 要素 GeoJSON 与其 ref 游标版本
      若 ref 相同则签名一致（同一份输入数据）；
    - 输出为 16-hex 摘要 + 可读前缀，跨进程确定。
    """
    from app.tools.argument_normalization import resolve_tool_name

    canonical = resolve_tool_name(tool_name or "")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            arguments = {"__raw__": arguments[:120]}
    if not isinstance(arguments, dict):
        arguments = {"__value__": _summarize_value(arguments)}
    summarized = {k: _summarize_value(v) for k, v in sorted(arguments.items())}
    blob = json.dumps(summarized, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)
    import hashlib

    return f"{canonical}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]}"


@dataclass
class _CallRecord:
    signature: str
    outcome: str                      # ok | error | timeout | cancelled
    state_epoch: int                  # 调用方声明的上游状态代（SessionPlan revision 等）


@dataclass
class CallPatternTracker:
    """单轮（turn 级）调用模式 tracker。有界、确定性、无内容存储。"""

    max_records: int = 64
    repeated_read_threshold: int = 3
    records: List[_CallRecord] = field(default_factory=list)
    _by_signature: Dict[str, _CallRecord] = field(default_factory=OrderedDict)
    _name_variants: Dict[str, set] = field(default_factory=dict)

    def record(
        self,
        tool_name: str,
        arguments: Any,
        outcome: str,
        *,
        state_epoch: int = 0,
        is_read_only: bool = False,
        is_mutation: bool = False,
    ) -> List[str]:
        """记录一次调用，返回触发的原因码（可能为空）。"""
        signature = canonical_call_signature(tool_name, arguments)
        reasons: List[str] = []
        prev = self._by_signature.get(signature)

        if prev is not None:
            if prev.outcome != "ok" and outcome != "ok" and prev.state_epoch == state_epoch:
                reasons.append("exact_repeat_failure")
            if outcome == "ok" and is_read_only and prev.state_epoch == state_epoch:
                # 同签名只读调用 + 状态未变 → 反复读
                count = sum(
                    1 for r in self.records
                    if r.signature == signature and r.state_epoch == state_epoch
                )
                if count + 1 >= self.repeated_read_threshold:
                    reasons.append(f"repeated_read:{count + 1}")
            if outcome == "ok" and is_mutation and prev.state_epoch == state_epoch:
                reasons.append("repeated_mutation_no_state_change")
            # 别名振荡：同一签名以不同原始名调用
            names = self._name_variants.setdefault(signature, set())
            names.add((tool_name or "").strip())
            if len(names) > 1:
                reasons.append("alias_oscillation")

        rec = _CallRecord(signature=signature, outcome=outcome, state_epoch=state_epoch)
        self.records.append(rec)
        self._by_signature[signature] = rec
        self._name_variants.setdefault(signature, set()).add((tool_name or "").strip())
        # 有界（review R1 minor：_name_variants 与 records 同步修剪 ——
        # 此前变体表无界增长，「有界 tracker」名不副实；别名振荡检测只需要
        # 环内最近记录的签名）
        if len(self.records) > self.max_records:
            drop = self.records[: len(self.records) - self.max_records]
            self.records = self.records[len(self.records) - self.max_records:]
            kept_sigs = {r.signature for r in self.records}
            for d in drop:
                if self._by_signature.get(d.signature) is d and d.signature not in kept_sigs:
                    self._by_signature.pop(d.signature, None)
            for sig in list(self._name_variants):
                if sig not in kept_sigs:
                    self._name_variants.pop(sig, None)
        return reasons

    def reason_summary(self) -> str:
        """诊断摘要（引擎失败详情/trace 用，有界）。"""
        if not self.records:
            return ""
        from collections import Counter

        outcomes = Counter(r.outcome for r in self.records)
        return f"calls={len(self.records)} outcomes={dict(outcomes)}"


# ---------------------------------------------------------------------------
# ADR-0103（§九）：GIS-aware 无进展诊断。
#
# 在形态级 reason codes 之上叠加两类**真实状态停滞**观测：
# - unchanged_map:N   连续 N 次成功调用后 mapspec 指纹纹丝不动（地图没变，
#   模型还在反复分析/反复要求展示）；
# - unchanged_workflow:N 连续 N 次成功调用后 SessionPlan capability 进度
#   停滞（工作流没推进）；
# - repeated_planning:N 规划签名重复出现（同样的计划被一再重发）。
# 达到阈值的 reason codes 由调用方用于切换 fallback/repair，而不是继续
# 烧 tokens。tracker 只存代数与签名，不存内容；同输入必同输出。
# ---------------------------------------------------------------------------

_PLANNING_HISTORY_MAX = 16


@dataclass
class GisProgressTracker:
    """map/workflow 代数停滞 + 规划重复的确定性诊断器。"""

    map_stale_threshold: int = 4
    workflow_stale_threshold: int = 6
    planning_repeat_threshold: int = 2
    pattern: CallPatternTracker = field(default_factory=CallPatternTracker)

    _last_map_epoch: Optional[str] = None
    _map_stale_streak: int = 0
    _last_workflow_epoch: Optional[str] = None
    _workflow_stale_streak: int = 0
    _planning_counts: Dict[str, int] = field(default_factory=OrderedDict)

    def record_call(
        self,
        tool_name: str,
        arguments: Any,
        outcome: str,
        *,
        state_epoch: int = 0,
        map_epoch: str = "",
        workflow_epoch: str = "",
        is_read_only: bool = False,
        is_mutation: bool = False,
    ) -> List[str]:
        reasons = list(self.pattern.record(
            tool_name, arguments, outcome,
            state_epoch=state_epoch,
            is_read_only=is_read_only,
            is_mutation=is_mutation,
        ))
        if outcome != "ok":
            # 失败调用不推进停滞计数（失败另有 exact_repeat_failure 管辖）
            return reasons

        if map_epoch:
            if self._last_map_epoch is None:
                # 首次观测：基线调用计入停滞窗口（窗口内地图始终未变；
                # 真正改图的调用必然带来不同指纹 → 立即清零）。
                self._map_stale_streak = 1
            elif map_epoch == self._last_map_epoch:
                self._map_stale_streak += 1
            else:
                self._map_stale_streak = 0
            if self._map_stale_streak >= self.map_stale_threshold:
                reasons.append(f"unchanged_map:{self._map_stale_streak}")
            self._last_map_epoch = map_epoch

        if workflow_epoch:
            if self._last_workflow_epoch is None:
                self._workflow_stale_streak = 1
            elif workflow_epoch == self._last_workflow_epoch:
                self._workflow_stale_streak += 1
            else:
                self._workflow_stale_streak = 0
            if self._workflow_stale_streak >= self.workflow_stale_threshold:
                reasons.append(f"unchanged_workflow:{self._workflow_stale_streak}")
            self._last_workflow_epoch = workflow_epoch
        return reasons

    def record_planning(self, signature: str) -> List[str]:
        """记录一次规划产出的签名；重复出现达阈值 → repeated_planning:N。"""
        reasons: List[str] = []
        count = self._planning_counts.get(signature, 0) + 1
        self._planning_counts[signature] = count
        if len(self._planning_counts) > _PLANNING_HISTORY_MAX:
            # 淘汰最旧（OrderedDict 头部）
            for key in list(self._planning_counts):
                self._planning_counts.pop(key, None)
                if len(self._planning_counts) <= _PLANNING_HISTORY_MAX:
                    break
        if count >= self.planning_repeat_threshold:
            reasons.append(f"repeated_planning:{count}")
        return reasons

    def diagnose(self) -> str:
        """一次性诊断摘要（fallback/repair 决策与 trace 用；有界）。"""
        parts = [self.pattern.reason_summary()]
        if self._map_stale_streak:
            parts.append(f"map_stale={self._map_stale_streak}")
        if self._workflow_stale_streak:
            parts.append(f"workflow_stale={self._workflow_stale_streak}")
        return "; ".join(p for p in parts if p)
