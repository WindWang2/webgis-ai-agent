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

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional

from app.lib.runtime.trace import bound_meta

logger = logging.getLogger(__name__)

#: ADR-0204：结构化决策载荷键 —— bound_meta 会把 dict/list repr 化成
#: 字符串（决策 id/alternatives/reason_codes 是可计算的回归证据面，
#: repr 化即销毁），这些键改走 :func:`_bound_structured` 的有界结构化
#: 投影（递归钳制，绝不为 repr 形态）。其余载荷键行为逐位不变。
_STRUCTURED_PAYLOAD_KEYS = frozenset({"decision", "decisions"})

_STRUCT_STR_MAX = 192
_STRUCT_LIST_MAX = 8
_STRUCT_DICT_KEYS_MAX = 32
_STRUCT_DEPTH_MAX = 6

#: 结构化通道的秘密键**精确**名单（归一化后全等匹配）。子串匹配（
#: bound_meta 的 is_sensitive_key）会把情境投影里的领域事实键
#: （``auth_tier`` / ``owner_scope_key``）误 REDACTED —— 那些值是重推导
#: （drift.rederive_capability_decision）的行为输入，丢真值会制造假
#: delta。精确名单仍拦住口令/令牌/凭据类键。
_STRUCT_SENSITIVE_KEYS_EXACT = frozenset({
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "credential", "credentials", "cookie",
    "private_key", "privatekey", "access_key", "signing_key", "passphrase",
    "auth_key", "session_key", "secret_key", "client_secret",
})


def _struct_key_normalized(key: str) -> str:
    return key.replace("-", "_").replace(" ", "_").lower()


def _bound_structured(value: Any, depth: int = 0) -> Any:
    """决策载荷的有界结构化投影（替代 repr 化；保持 dict/list 形态）。"""
    if depth >= _STRUCT_DEPTH_MAX:
        return str(value)[:32]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in list(value.items())[:_STRUCT_DICT_KEYS_MAX]:
            key = str(k)[:48]
            # 秘密键防线：精确名单（见 _STRUCT_SENSITIVE_KEYS_EXACT）。
            if _struct_key_normalized(key) in _STRUCT_SENSITIVE_KEYS_EXACT:
                out[key] = "[REDACTED]"
            else:
                out[key] = _bound_structured(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_bound_structured(v, depth + 1)
                for v in list(value)[:_STRUCT_LIST_MAX]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_STRUCT_STR_MAX]


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

#: 链记录本体 schema 版本（ADR-0204：此前版本只在 trace_v6 manifest 层；
#: additive 字段 —— 演进纪律 = 只增不删不改语义，读取侧未知容忍）。
GIS_TRACE_CHAIN_SCHEMA_VERSION = 1

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
        items = {k: v for k, v in payload.items() if v is not None}
        structured: Dict[str, Any] = {}
        for key in _STRUCTURED_PAYLOAD_KEYS:
            if key in items:
                structured[key] = _bound_structured(items.pop(key))
        rec = ChainRecord(
            stage=stage,
            ts=time.time(),
            payload={**bound_meta(items), **structured},
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
            # ADR-0204：链记录本体版本（此前版本只存在于 trace_v6 manifest
            # 层；additive 字段，读取侧零行为变化）。
            "schema_version": GIS_TRACE_CHAIN_SCHEMA_VERSION,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "total_records": self._total,
            "completeness": round(self.completeness(), 4),
            "stages": [rec.as_dict() for k in sorted(self._records) for rec in self._records[k]],
        }


class GisTraceRegistry:
    """turn → chain 的 LRU 注册表（进程内；有界）。

    V5：pinned 区（start 即 pin、persist 成功 unpin，有界 FIFO）——
    LRU 驱逐不再使 settle 期持久化丢失链（V4 行交错/丢链窗口修复的
    registry 半边；文件锁半边在 trace_store）。pinned 上限超出时丢最旧
    并 debug 记录 —— 与 LRU 同为有界纪律，不构成无界增长。
    """

    def __init__(self, max_chains: int = 128, max_pinned: int = 1024):
        self.max_chains = max_chains
        self.max_pinned = max_pinned
        self._chains: "OrderedDict[str, GisTraceChain]" = OrderedDict()
        self._pinned: "OrderedDict[str, GisTraceChain]" = OrderedDict()
        # RLock：record() 持锁调用 start()（同线程重入），必须可重入锁。
        self._lock = threading.RLock()

    def start(self, turn_id: str, session_id: str = "") -> GisTraceChain:
        chain = GisTraceChain(turn_id=turn_id, session_id=session_id)
        with self._lock:
            self._chains[turn_id] = chain
            while len(self._chains) > self.max_chains:
                self._chains.popitem(last=False)
            self._pinned[turn_id] = chain
            while len(self._pinned) > self.max_pinned:
                dropped_id, _ = self._pinned.popitem(last=False)
                logger.debug(
                    "[GisTrace] pinned overflow drop turn=%s", dropped_id
                )
        return chain

    def get(self, turn_id: str) -> Optional[GisTraceChain]:
        with self._lock:
            chain = self._chains.get(turn_id)
            if chain is None:
                chain = self._pinned.get(turn_id)
            return chain

    def drop(self, turn_id: str) -> None:
        with self._lock:
            self._chains.pop(turn_id, None)
            self._pinned.pop(turn_id, None)

    def unpin(self, turn_id: str) -> None:
        """settle 持久化成功后释放 pin（pinned 区只收留未 settle 的链）。"""
        with self._lock:
            self._pinned.pop(turn_id, None)

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
