"""重放确定性原语：规范化 JSON、行为摘要、注入式时钟与种子化 id。

重放回归的前提是「同输入 → 同摘要」。生产 trace 里天然带墙钟时间与
随机 id（turn-<uuid>），digest 前必须剥除或归一。本模块提供：

- :func:`canonical_json`  排序键、紧凑分隔、显式拒绝非 JSON 原生类型；
- :func:`sha256_of`       规范化字节摘要；
- :func:`behavior_digest` 语义投影 → 摘要（重放比对的唯一权威指纹）；
- :class:`DeterministicClock` 注入式单调时钟（turn 相对毫秒）；
- :func:`seeded_id`       种子化短 id（同 seed 同输出，替代 uuid4）。

纪律：**任何进入 digest 的字段都不允许是墙钟/随机值**；新增 trace 字段时
必须在 :func:`volatile_fields` 登记其波动维度，否则会造成无解释漂移。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, FrozenSet

REPLAY_SEED_DEFAULT = 0

#: digest 前从 trace 顶层剥除的波动字段（墙钟 / 环境相关，语义无关）。
VOLATILE_TOP_FIELDS: FrozenSet[str] = frozenset({
    "created_at_epoch",
    "env",
    "recording",
    "nondeterministic",
})

#: 链记录内的波动键（ts 是墙钟秒）。
VOLATILE_RECORD_FIELDS: FrozenSet[str] = frozenset({"ts"})

#: 摘要内的计时/用量在毫秒级天然抖动 —— digest 只保留**存在性**（0/正值），
#: 精度回归完全交给 tolerant 指标（ratchet 行 replay.duration_ms 等），
#: 绝不进 digest（数量级分桶在 1.0 等边界处不稳定，已否决）。
_QUANTIZED_FIELDS: FrozenSet[str] = frozenset({
    "timing_ms", "llm_usage",
})


def canonical_json(obj: Any) -> str:
    """排序键紧凑 JSON；float 归一（-0.0/NaN 抖动防护）。"""
    def _clean(value: Any) -> Any:
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return str(value)
            if value == 0.0:
                return 0.0
            return round(value, 6)
        if isinstance(value, dict):
            return {str(k): _clean(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        if isinstance(value, (list, tuple)):
            return [_clean(v) for v in value]
        if value is None or isinstance(value, (str, bool, int)):
            return value
        return str(value)  # 枚举/对象 → 稳定字符串（不允许任意对象进 trace）

    return json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _quantize(value: Any) -> Any:
    """计时/用量 → 存在性（None=缺席，其余=present）。

    0 与正数同态：近零计时在 round 边界上会翻转 0↔正值，那正是 digest
    必须消灭的抖动类；精度回归完全走 tolerant ratchet 行。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _quantize(v) for k, v in sorted(value.items())}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "present"
    return "present"


def behavior_digest(trace: Dict[str, Any]) -> str:
    """语义投影 → sha256。重放 A/B 比对的权威指纹。

    剥离：波动字段、关联 id（session/turn/run/request —— 重放侧必然换号）、
    计时与 token 用量精度（只留数量级）。保留：链阶段与载荷、工具调用行为、
    verdict、outcome、warning 码。
    """
    projected: Dict[str, Any] = {}
    for key, value in trace.items():
        if key in VOLATILE_TOP_FIELDS or key == "behavior_digest":
            continue
        if key in ("session_id", "turn_id", "request_id", "run_id"):
            continue
        if key in _QUANTIZED_FIELDS:
            projected[key] = _quantize(value)
            continue
        if key == "chain" and isinstance(value, dict):
            projected["chain"] = _project_chain(value)
            continue
        if key == "tool_calls" and isinstance(value, list):
            projected["tool_calls"] = [
                {k: v for k, v in call.items() if k != "tool_call_id"}
                for call in value
                if isinstance(call, dict)
            ]
            continue
        if key in ("user_input", "final_text"):
            # 自然语言输入/输出是 nondeterministic_text 比对类，不进 digest；
            # 只留长度带（存在性回归仍可被 exact 白名单场景断言）。
            length = len(value) if isinstance(value, str) else 0
            projected[key] = f"len_bucket:{_len_bucket(length)}"
            continue
        projected[key] = value
    return sha256_of(projected)


def _len_bucket(length: int) -> str:
    if length == 0:
        return "0"
    if length <= 64:
        return "xs"
    if length <= 512:
        return "s"
    if length <= 4096:
        return "m"
    return "l"


def _project_chain(chain: Dict[str, Any]) -> Dict[str, Any]:
    stages = []
    for record in chain.get("stages") or []:
        if not isinstance(record, dict):
            continue
        cleaned = {
            k: v for k, v in record.items() if k not in VOLATILE_RECORD_FIELDS
        }
        stages.append(cleaned)
    return {
        "stages": stages,
        "total_records": chain.get("total_records"),
        "completeness": chain.get("completeness"),
        "covered_stages": chain.get("covered_stages"),
    }


class DeterministicClock:
    """注入式时钟：turn 相对单调毫秒 + 固定 epoch（重放期间禁墙钟）。"""

    def __init__(self, epoch: float = 0.0) -> None:
        self.epoch = epoch
        self._mono_origin: float | None = None

    def now_ms(self) -> float:
        import time as _time

        if self._mono_origin is None:
            self._mono_origin = _time.monotonic()
        return (_time.monotonic() - self._mono_origin) * 1000.0


def seeded_id(prefix: str, seed: int, *parts: Any) -> str:
    """种子化确定性短 id（替代 uuid4；同 seed 同输出）。"""
    material = canonical_json([prefix, seed, [str(p) for p in parts]])
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"
