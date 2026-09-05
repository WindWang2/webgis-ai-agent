"""有界上下文投影（ADR-0101 Wave 5, §23/§24）。

原则：大真相对象（SessionPlan/AnalysisGraph/地图状态/图层清单/ref 清单）
**永不直接序列化**进 prompt —— 经本模块的投影函数输出有界、确定性、独立
可测的摘要。投影不落盘、不建平行状态（不变式 #5）。

复用：SessionPlan 已有 ``format_session_plan_projection``（ADR-0085/0087/
0088，有界单块），地图状态已有 ``build_map_state_summary`` —— 本模块不重写
它们，只补齐通用投影原语 + 指纹缓存：

- ``bound_lines`` / ``bound_text``：行级/字符级有界化（确定性截断点）；
- ``project_layer_inventory``：图层清单投影（数量上限 + 尺寸上限）；
- ``project_ref_list``：ref 清单投影；
- ``ProjectionCache``：按调用方权威指纹键控的有界 LRU（§24「未变不重建」
  —— 缓存值是投影**文本**，真相永远在权威源）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# 有界化原语（纯函数）
# ---------------------------------------------------------------------------

def bound_text(text: str, max_chars: int, *, suffix: str = "…(truncated)") -> str:
    """字符级有界化；超长时保头 + 明确截断标记（模型可感知被裁）。"""
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(suffix))
    return text[:keep].rstrip() + suffix


def bound_lines(
    lines: Sequence[str],
    max_lines: int,
    max_chars_per_line: int = 200,
    *,
    omitted_note: bool = True,
) -> str:
    """行级有界化：每行限宽 + 行数上限 + 省略行数披露。确定性。"""
    out: List[str] = []
    for line in lines[:max_lines]:
        out.append(bound_text(line, max_chars_per_line))
    dropped = len(lines) - len(out)
    if dropped > 0 and omitted_note:
        out.append(f"…({dropped} more omitted)")
    return "\n".join(out)


def content_fingerprint(obj: Any) -> str:
    """权威内容的稳定指纹（缓存键的「权威半边」—— 由调用方传入真相）。"""
    try:
        blob = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, default=str)
    except Exception:  # noqa: BLE001 — 不可序列化退化为字符串
        blob = str(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 结构化投影
# ---------------------------------------------------------------------------

def project_layer_inventory(
    layers: Dict[str, Any],
    max_layers: int = 20,
    max_chars_per_line: int = 160,
) -> str:
    """地图图层清单投影：名称 + 类型 + 可见性，一行一层，有界。"""
    if not isinstance(layers, dict):
        return ""
    lines: List[str] = []
    for name, meta in list(layers.items())[: max_layers * 2]:
        if isinstance(meta, dict):
            ltype = str(meta.get("type") or meta.get("layer_type") or "?")
            visible = meta.get("visible", meta.get("isVisible", "?"))
            lines.append(f"- {name} [{ltype}] visible={visible}")
        else:
            lines.append(f"- {name}")
    return bound_lines(lines, max_layers, max_chars_per_line)


def project_ref_list(
    refs: Dict[str, str],
    max_refs: int = 30,
    max_chars_per_line: int = 160,
) -> str:
    """数据 ref 清单投影：ref_id → 别名/摘要，一行一条，有界。"""
    if not isinstance(refs, dict):
        return ""
    lines = [
        f"- {rid}: {alias}" if alias else f"- {rid}"
        for rid, alias in list(refs.items())
    ]
    return bound_lines(lines, max_refs, max_chars_per_line)


def project_tool_availability(
    names: Sequence[str],
    max_tools: int = 40,
) -> str:
    """工具可用性投影（长尾清单的一行式摘要，配合 schema 面使用）。"""
    lines = [f"- {n}" for n in names]
    return bound_lines(lines, max_tools, 120)


# ---------------------------------------------------------------------------
# 指纹缓存
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: str
    expires_at: float


class ProjectionCache:
    """(namespace, key, fingerprint) → 投影文本 的有界进程内缓存。

    §24 复用语义：真相未变（指纹不变）→ 投影直接复用；真相变了 → 仅相关
    键失效（指纹变化即 miss）。无 TTL 也安全（指纹是内容指纹），但提供 TTL
    兜底防御调用方传伪指纹（如常量）。线程安全；逐出策略 = 过期优先 →
    字典序首个（review R1 minor：非 LRU，按文档如实标注）。
    """

    def __init__(self, max_entries: int = 256, default_ttl_s: float = 300.0) -> None:
        self._max = max_entries
        self._ttl = default_ttl_s
        self._lock = threading.Lock()
        self._store: Dict[Tuple[str, str], _CacheEntry] = {}
        self._fps: Dict[Tuple[str, str], str] = {}
        self.hits = 0
        self.misses = 0

    def get_or_build(
        self,
        namespace: str,
        key: str,
        fingerprint: str,
        builder: Callable[[], str],
    ) -> Tuple[str, bool]:
        """返回 (投影文本, cache_hit)。builder 异常向上传播（不缓存失败）。"""
        cache_key = (namespace, key)
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(cache_key)
            if (
                entry is not None
                and entry.expires_at > now
                and self._fingerprint_matches(namespace, key, fingerprint)
            ):
                self.hits += 1
                return entry.value, True
            self.misses += 1
        value = builder()
        # review R1 minor：TTL 以 builder 完成时刻起算（此前用构建前的 now，
        # 构建耗时被无谓吃掉）。
        built_at = time.monotonic()
        with self._lock:
            if len(self._store) >= self._max:
                # 确定性逐出：过期优先，其次字典序首个（进程内缓存无需花哨 LRU）
                expired = [k for k, v in self._store.items() if v.expires_at <= built_at]
                evict = expired[0] if expired else sorted(self._store.keys())[0]
                self._store.pop(evict, None)
            self._store[cache_key] = _CacheEntry(value, built_at + self._ttl)
            self._fps[(namespace, key)] = fingerprint
        return value, False

    def _fingerprint_matches(self, namespace: str, key: str, fingerprint: str) -> bool:
        return self._fps.get((namespace, key)) == fingerprint

    def invalidate(self, namespace: Optional[str] = None) -> None:
        with self._lock:
            if namespace is None:
                self._store.clear()
                self._fps.clear()
            else:
                for k in [k for k in self._store if k[0] == namespace]:
                    self._store.pop(k, None)
                for k in [k for k in self._fps if k[0] == namespace]:
                    self._fps.pop(k, None)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"entries": len(self._store), "hits": self.hits, "misses": self.misses}


_cache = ProjectionCache()


def get_projection_cache() -> ProjectionCache:
    return _cache
