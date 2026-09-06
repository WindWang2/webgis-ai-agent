"""Fingerprints V3 —— 统一指纹与版本判定（契约层，零 I/O）。

V3 之前指纹机制有四套互不兼容的实现（审计 Agent D）：artifact_cache
（mtime+size 代理）、data_fabric/fingerprint（descriptor+payload）、
provenance/fingerprint（身份证据）、raster 双降采样方案。本模块**不替换**
它们（各自服务既有语义），只提供 V3 契约层的统一原语：

- ``canonical_fingerprint``：排序键 canonical JSON 的 sha256 —— 所有
  V3 键的唯一哈希原语；
- ``FingerprintSet``：content / schema / metadata / crs 四维指纹 ——
  「什么变了」的最小完备证据（§九）；
- ``classify_change``：新旧指纹集 → 变更类别（none/metadata_only/
  schema/content/crs），下游据此判 valid/stale/recompute（§十一）；
- ``compute_reuse_fingerprint``：确定性复用键（§十三）：
  input fingerprints + operation/version + normalized args + crs +
  runtime semver → output fingerprint 前像。

哈希输入全部先 canonical 化（sort_keys、集合排序、NaN/Inf 拒绝）——
同输入必同哈希，异输入碰撞概率即 sha256 碰撞概率。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence


def _reject_non_finite(node: Any) -> None:
    """NaN/Inf 在 canonical JSON 里不可往返（json.dumps 会输出非法字面量）→ 拒绝。"""
    if isinstance(node, float) and not math.isfinite(node):
        raise ValueError("non-finite float is not fingerprintable")
    if isinstance(node, dict):
        for v in node.values():
            _reject_non_finite(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _reject_non_finite(v)


def canonical_dumps(obj: Any) -> str:
    """V3 canonical JSON：sort_keys + 紧凑分隔符 + 集合排序。

    与 provenance/fingerprint.canonical_dumps 同口径（集合作为排序列表
    序列化），保证跨模块一致；新增 NaN/Inf 拒绝。
    """
    def _normalize(node: Any) -> Any:
        if isinstance(node, (set, frozenset)):
            return sorted(_normalize(v) for v in node)
        if isinstance(node, tuple):
            return [_normalize(v) for v in node]
        if isinstance(node, list):
            return [_normalize(v) for v in node]
        if isinstance(node, dict):
            return {str(k): _normalize(v) for k, v in node.items()}
        if isinstance(node, float) and not math.isfinite(node):
            raise ValueError("non-finite float is not fingerprintable")
        return node

    return json.dumps(_normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_fingerprint(obj: Any) -> str:
    """任意可 JSON 化对象 → 确定性 sha256 hex。不可序列化/含非有限数 → ValueError。"""
    return sha256_hex(canonical_dumps(obj))


class FingerprintFormatError(ValueError):
    """指纹输入不可哈希（非 JSON 对象 / NaN / Inf）。"""


def safe_canonical_fingerprint(obj: Any) -> Optional[str]:
    """canonical_fingerprint 的不可哈希 → None 版（调用方决定降级语义）。"""
    try:
        return canonical_fingerprint(obj)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class FingerprintSet:
    """四维指纹（§九）。任何一维为 None = 该维证据缺失（诚实缺省）。"""

    content: Optional[str] = None      # 数据本体（载荷/字节/降采样样本）
    schema: Optional[str] = None       # 字段/波段/类型结构
    metadata: Optional[str] = None     # 描述性元数据（名称/标签/样式提示）
    crs: Optional[str] = None          # 坐标系（CRS 变更独立成类）

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {
            "content": self.content,
            "schema": self.schema,
            "metadata": self.metadata,
            "crs": self.crs,
        }

    @classmethod
    def from_dict(cls, d: Optional[Mapping[str, Any]]) -> "FingerprintSet":
        d = d or {}
        return cls(
            content=d.get("content") or None,
            schema=d.get("schema") or None,
            metadata=d.get("metadata") or None,
            crs=d.get("crs") or None,
        )


class ChangeClass(str, Enum):
    """变更类别（§九：metadata-only / content / schema / crs / none）。"""

    NONE = "none"
    METADATA_ONLY = "metadata_only"
    CONTENT = "content"
    SCHEMA = "schema"
    CRS = "crs"
    UNKNOWN = "unknown"   # 证据缺失无法判定（旧指纹缺维）


def classify_change(old: FingerprintSet, new: FingerprintSet) -> ChangeClass:
    """新旧指纹集 → 变更类别。

    判定优先级：crs > schema > content > metadata。证据缺失（任一方
    相关维为 None 且另一方位有值）→ unknown —— 绝不把「不知道」当成
    「没变」。
    """
    if old.content is None and new.content is None and old.schema is None and new.schema is None:
        # 双方都无实质证据：只有 metadata 可比时才敢下 metadata_only 结论。
        if old.metadata is not None and new.metadata is not None and old.metadata != new.metadata:
            return ChangeClass.METADATA_ONLY
        return ChangeClass.UNKNOWN

    if old.crs is not None and new.crs is not None and old.crs != new.crs:
        return ChangeClass.CRS

    schema_changed = (
        old.schema is not None
        and new.schema is not None
        and old.schema != new.schema
    )
    content_changed = (
        old.content is not None
        and new.content is not None
        and old.content != new.content
    )
    if schema_changed:
        # schema 变更时 content 指纹必然失义（结构不同不可比）。
        return ChangeClass.SCHEMA
    if content_changed:
        return ChangeClass.CONTENT
    if old.metadata is not None and new.metadata is not None and old.metadata != new.metadata:
        return ChangeClass.METADATA_ONLY
    if old == new:
        return ChangeClass.NONE
    return ChangeClass.UNKNOWN


class StalenessVerdict(str, Enum):
    """下游依赖对上游变更的裁决（§十一）。"""

    VALID = "valid"                # 上游未变
    STALE = "stale"                # 上游 metadata 变了，结果仍可用但需提示
    RECOMPUTE = "recompute"        # 上游内容/结构变了，结果需要重算
    INVALID = "invalid"            # 上游不可用（删除/损坏），下游悬挂


def staleness_verdict(change: ChangeClass, upstream_alive: bool = True) -> StalenessVerdict:
    """变更类别 → 下游裁决。"""
    if not upstream_alive:
        return StalenessVerdict.INVALID
    mapping = {
        ChangeClass.NONE: StalenessVerdict.VALID,
        ChangeClass.METADATA_ONLY: StalenessVerdict.STALE,
        ChangeClass.CONTENT: StalenessVerdict.RECOMPUTE,
        ChangeClass.SCHEMA: StalenessVerdict.RECOMPUTE,
        ChangeClass.CRS: StalenessVerdict.RECOMPUTE,
        ChangeClass.UNKNOWN: StalenessVerdict.RECOMPUTE,  # 不可判定按需重算（保守）
    }
    return mapping[change]


# ── 确定性复用键（§十三）────────────────────────────────────────────

REUSE_FINGERPRINT_VERSION = "v3"


def compute_reuse_fingerprint(
    *,
    operation: str,
    operation_version: str,
    input_fingerprints: Mapping[str, str],
    normalized_args: Any = None,
    crs: str = "",
    runtime_semantic_version: str = "",
) -> str:
    """确定性操作的输出指纹前像（§十三公式）。

    input fingerprints + operation/version + normalized args + CRS +
    runtime semver → sha256。调用方保证：
    - operation deterministic（非确定性操作不得调用本函数）；
    - input_fingerprints 是**内容**证据（不是临时指针 id）；
    - normalized_args 已 canonical（无内存地址等非确定成分）。

    返回 ``reuse:v3:<hex>``。输入指纹按 key 排序后拼入 —— 与 key 命名
    无关的注入顺序不影响结果。
    """
    if not operation:
        raise FingerprintFormatError("operation is required")
    payload = {
        "v": REUSE_FINGERPRINT_VERSION,
        "op": str(operation),
        "op_v": str(operation_version or ""),
        "inputs": {str(k): str(v) for k, v in sorted(input_fingerprints.items())},
        "args": canonical_dumps(normalized_args if normalized_args is not None else {}),
        "crs": str(crs or ""),
        "rt": str(runtime_semantic_version or ""),
    }
    return f"reuse:{REUSE_FINGERPRINT_VERSION}:{sha256_hex(canonical_dumps(payload))}"


def fingerprint_sequence(items: Sequence[str]) -> str:
    """有序指纹列表 → 单一指纹（多输入合一时保持顺序敏感）。"""
    return sha256_hex(canonical_dumps([str(i) for i in items]))
