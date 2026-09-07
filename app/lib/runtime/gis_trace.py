"""GIS 证据链 V3（ADR-0103 §十）—— Intent → User Output 的统一执行证据。

TurnTrace（app/lib/runtime/trace.py）是 turn 内**事件流**（ring buffer，
17 种闭式事件）；本模块在其上定义**证据链**：18 个规范阶段，每阶段一条
有界记录（timestamp / ids / fingerprints / inputs / outputs / decision /
failure / cost / tokens / latency），形成从用户意图到最终地图修复的
统一可回放证据。

设计约束（与 TurnTrace 同门）：
- 只读投影纪律：链是**记录**，不驱动执行；
- 有界：每阶段最多 ``max_per_stage`` 条记录（默认 8），链总条数有上限；
- 脱脂：payload 过 ``bound_meta`` 同源消毒（敏感键 REDACTED、大值摘要）；
- 确定性可回放：阶段序是规范序，replay 按 stage 对齐比较（A/B、回归）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional

from app.lib.runtime.trace import bound_meta


class Stage(IntEnum):
    """证据链规范阶段（1-18 对齐 ADR-0103 §十）。值即规范顺序。"""

    USER_INTENT = 1            # 用户原始意图
    PARSED_INTENT = 2          # 解析后的意图
    TASK_ONTOLOGY = 3          # 任务本体（task_type / domain 归类）
    DATA_PROFILE = 4           # 数据画像
    CANDIDATE_WORKFLOWS = 5    # 候选工作流
    SELECTED_WORKFLOW = 6      # 选定工作流
    TOOL_SURFACE = 7           # 工具面（动态投影）
    MODEL_ROUTING = 8          # 模型路由决策
    TOOL_CALLS = 9             # 工具调用
    ARGUMENTS = 10             # 参数（归一化后）
    TOOL_RESULTS = 11          # 工具结果
    ARTIFACT_CREATION = 12     # 工件创建
    MAP_MUTATIONS = 13         # 地图变更
    MAP_OBSERVATION = 14       # 地图观测（前端回执/校验）
    VERIFICATION = 15          # 验证（cartography 评估）
    REPAIR = 16                # 修复
    FINAL_VERDICT = 17         # 最终裁决
    USER_OUTPUT = 18           # 用户输出


STAGE_IDS = frozenset(s.value for s in Stage)
ALL_STAGES = tuple(Stage)

_RECORD_MAX_STR = 512


@dataclass
class ChainRecord:
    """单条证据记录（有界、消毒后）。"""

    stage: Stage
    ts: float
    payload: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage.name, "stage_id": int(self.stage),
                "ts": self.ts, **self.payload}


@dataclass
class GisTraceChain:
    """一个 turn 的完整证据链。"""

    turn_id: str
    session_id: str = ""
    max_per_stage: int = 8
    _records: "OrderedDict[int, List[ChainRecord]]" = field(
        default_factory=OrderedDict
    )
    _total: int = 0

    def record(self, stage: Stage, **payload: Any) -> ChainRecord:
        """记录一条证据（超限阶段静默丢弃最新 —— 记录绝不阻断执行）。"""
        rec = ChainRecord(
            stage=stage,
            ts=time.time(),
            payload=bound_meta({k: v for k, v in payload.items() if v is not None}),
        )
        bucket = self._records.get(int(stage))
        if bucket is None:
            bucket = []
            self._records[int(stage)] = bucket
        if len(bucket) >= self.max_per_stage:
            bucket.pop(0)  # 保留最近
        bucket.append(rec)
        self._total += 1
        return rec

    def stage_records(self, stage: Stage) -> List[ChainRecord]:
        return list(self._records.get(int(stage), ()))

    def first(self, stage: Stage) -> Optional[ChainRecord]:
        bucket = self._records.get(int(stage))
        return bucket[0] if bucket else None

    def covered_stages(self) -> List[str]:
        return [Stage(int(k)).name for k in sorted(self._records)]

    def completeness(self) -> float:
        """证据链覆盖度（覆盖阶段数 / 18）——评测指标。"""
        return len(self._records) / len(ALL_STAGES)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "total_records": self._total,
            "completeness": round(self.completeness(), 4),
            "stages": [rec.as_dict() for k in sorted(self._records) for rec in self._records[k]],
        }


class GisTraceRegistry:
    """turn → chain 的 LRU 注册表（进程内；有界）。"""

    def __init__(self, max_chains: int = 128):
        self.max_chains = max_chains
        self._chains: "OrderedDict[str, GisTraceChain]" = OrderedDict()
        # RLock：record() 持锁调用 start()（同线程重入），必须可重入锁。
        self._lock = threading.RLock()

    def start(self, turn_id: str, session_id: str = "") -> GisTraceChain:
        chain = GisTraceChain(turn_id=turn_id, session_id=session_id)
        with self._lock:
            self._chains[turn_id] = chain
            while len(self._chains) > self.max_chains:
                self._chains.popitem(last=False)
        return chain

    def get(self, turn_id: str) -> Optional[GisTraceChain]:
        with self._lock:
            return self._chains.get(turn_id)

    def drop(self, turn_id: str) -> None:
        with self._lock:
            self._chains.pop(turn_id, None)

    def record(self, turn_id: str, stage: Stage, **payload: Any) -> bool:
        """便捷记录：链不存在则惰性创建（保证发射侧零前置依赖）。"""
        with self._lock:
            chain = self._chains.get(turn_id)
            if chain is None:
                if turn_id == "":
                    return False
                chain = self.start(turn_id)
        chain.record(stage, **payload)
        return True


_registry = GisTraceRegistry()


def get_gis_trace_registry() -> GisTraceRegistry:
    return _registry


def record_stage(turn_id: str, stage: Stage, **payload: Any) -> bool:
    """模块级便捷发射（任何失败返回 False，绝不抛出）。"""
    try:
        return _registry.record(turn_id, stage, **payload)
    except Exception:  # noqa: BLE001
        return False
