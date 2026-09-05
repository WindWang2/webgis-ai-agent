"""统一模型可见工具面投影（ADR-0101 Wave 3, §12-14）。

ToolSurface V2 是**投影**，不是第二个注册中心（不变式 #3）：唯一输入是
ToolRegistry 真相（descriptor + schema）与既有 ToolCatalog 选择语义
（tier/关键词/sticky/字节预算，ADR-0005 与 audit4 #981 全部保留），输出：

    ToolSurfaceProjection {
        schemas,          # 模型可见 schema（有序、压缩可选）
        reasons,          # name -> 纳入原因（tier1 / domain / surface_preferred /
                          #            catalog / retrieval(score,matched)）
        dropped,          # name -> 排除原因（lifecycle:status / budget）
        bytes_used,       # 压缩后字节
        fingerprint,      # 有序 (name, schema_fp) 指纹 —— 投影缓存失效键
    }

消费方：
- legacy ChatExecutionEngine._select_tools —— 现有 catalog 结果经
  ``augment()`` 后处理（检索补强 + 生命周期过滤 + 原因留痕），零行为回退；
- Pi 适配层 —— ``native_surface_snapshot()`` 用同一投影栈产出原生面 schema
  （仍从活注册表 dump，绝不手写第二清单）；
- 评测（Wave 9 tool-selection benchmark）—— reasons/dropped 是召回率与
  泄漏率的断言面。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from app.tools.descriptor import ToolStatus
from app.tools.registry import ToolRegistry
from app.services.chat.schema_compression import (
    compress_schema,
    schema_bytes,
)
from app.services.chat.tool_retrieval import rank_tools

logger = logging.getLogger(__name__)

#: 检索补强默认开关（additive：只在预算有剩余时补工具；TOOL_SURFACE_RETRIEVAL=0 关闭）
_RETRIEVAL_ENABLED = os.getenv("TOOL_SURFACE_RETRIEVAL", "1") != "0"

#: 模型可见 schema 压缩级别（none 保持注册原样 —— 默认；compact 经投影 API 显式启用）
_DEFAULT_COMPRESS = os.getenv("TOOL_SURFACE_COMPRESS", "none")


@dataclass(frozen=True)
class SurfaceRequest:
    """一次投影请求（全部字段可选 —— 兼容最小调用）。"""

    user_message: str = ""
    session_id: Optional[str] = None
    declared_domains: Optional[Set[str]] = None
    turn_id: Optional[str] = None
    surface: Optional[Any] = None          # gis_harness ToolSurface（Protocol 鸭子类型）
    compress: str = _DEFAULT_COMPRESS
    retrieval: bool = _RETRIEVAL_ENABLED
    retrieval_k: int = 6
    byte_budget: Optional[int] = None      # None = 沿用 ToolCatalog tier-2 预算语义


@dataclass
class ToolSurfaceProjection:
    schemas: List[Dict[str, Any]] = field(default_factory=list)
    reasons: Dict[str, List[str]] = field(default_factory=dict)
    dropped: Dict[str, str] = field(default_factory=dict)
    bytes_used: int = 0
    fingerprint: str = ""
    base_names: List[str] = field(default_factory=list)
    retrieval_added: List[str] = field(default_factory=list)

    def why(self, name: str) -> str:
        return "; ".join(self.reasons.get(name, ("not-included",)))

    def summary_line(self) -> str:
        return (
            f"tools={len(self.schemas)} bytes={self.bytes_used} "
            f"retrieval_added={len(self.retrieval_added)} fp={self.fingerprint[:8]}"
        )


class ToolSurfaceProjector:
    """组合 ToolCatalog 选择语义 + 生命周期 + 检索补强 + 压缩的投影器。"""

    def __init__(self, registry: ToolRegistry, catalog: Optional[Any] = None):
        self.registry = registry
        self.catalog = catalog

    # ------------------------------------------------------------------
    # 主入口：完整投影（内部调用 catalog；catalog 缺席时退化为基础选择）
    # ------------------------------------------------------------------
    def project(self, req: SurfaceRequest) -> ToolSurfaceProjection:
        base = self._base_select(req)
        return self.augment(base, req)

    def _base_select(self, req: SurfaceRequest) -> List[Dict[str, Any]]:
        if self.catalog is not None and hasattr(self.catalog, "select_schemas"):
            try:
                return self.catalog.select_schemas(
                    req.user_message,
                    session_id=req.session_id,
                    declared_domains=req.declared_domains,
                    turn_id=req.turn_id,
                    surface=req.surface,
                ) or []
            except TypeError:
                # catalog 替身（subagent _FrozenCatalog）不接受 surface kwarg
                return self.catalog.select_schemas(
                    req.user_message,
                    session_id=req.session_id,
                    declared_domains=req.declared_domains,
                    turn_id=req.turn_id,
                ) or []
        return self.registry.get_schemas()

    # ------------------------------------------------------------------
    # augment：既有 catalog 结果 → V2 投影（additive，零行为回退）
    # ------------------------------------------------------------------
    def augment(
        self,
        base_schemas: Sequence[Dict[str, Any]],
        req: SurfaceRequest,
    ) -> ToolSurfaceProjection:
        reasons: Dict[str, List[str]] = {}
        dropped: Dict[str, str] = {}

        preferred = frozenset(getattr(req.surface, "preferred_tools", frozenset()) or ())
        active_domains: Set[str] = set(req.declared_domains or ())
        active_domains |= set(getattr(req.surface, "allowed_domains", frozenset()) or ())
        if req.user_message and self.catalog is not None and hasattr(
            self.catalog, "detect_domains"
        ):
            try:
                active_domains |= self.catalog.detect_domains(req.user_message)
            except Exception:  # noqa: BLE001
                pass

        kept: List[Dict[str, Any]] = []
        for s in base_schemas:
            name = s.get("function", {}).get("name", "")
            try:
                desc = self.registry.descriptor(name)
            except KeyError:
                desc = None
            if desc is not None and not desc.model_visible:
                dropped[name] = f"lifecycle:{desc.status.value}"
                continue
            if desc is not None and desc.status is ToolStatus.EXTERNAL_UNAVAILABLE:
                dropped[name] = "lifecycle:external_unavailable"
                continue
            # review R1 MAJOR：tier 闸对**基线来源**同样生效（此前只在检索
            # 分支）—— no-catalog 退化路径（get_schemas() 全量）曾把 tier-3
            # schema 直接漏进投影面。
            if desc is not None and int(desc.tier) >= 3:
                dropped[name] = "tier3"
                continue
            kept.append(s)
            r: List[str] = []
            if desc is None:
                r.append("catalog")
            else:
                if int(desc.tier) <= 1:
                    r.append("tier1")
                if name in preferred:
                    r.append("surface_preferred")
                if desc.domains and set(desc.domains) & active_domains:
                    r.append("domain:" + ",".join(sorted(set(desc.domains) & active_domains)))
                if not r:
                    r.append("catalog")
            reasons[name] = r

        # --- 检索补强：只补空隙，不挤既有（§13） ---
        retrieval_added: List[str] = []
        if req.retrieval and req.user_message:
            try:
                used = sum(
                    self.registry.schema_size(s["function"]["name"])
                    if self.registry.schema_size(s["function"]["name"]) is not None
                    else schema_bytes(s)
                    for s in kept
                )
                hits = rank_tools(
                    self.registry,
                    req.user_message,
                    top_k=req.retrieval_k + len(kept),
                )
                existing = {s["function"]["name"] for s in kept}
                # review R1 MAJOR：检索补强默认继承 ToolCatalog 的 tier-2 字节
                # 预算（此前 None = 无预算，最多 6 个大 schema 每轮无界膨胀）。
                budget = req.byte_budget
                if budget is None:
                    try:
                        from app.services.tool_catalog import (
                            _TIER2_SCHEMA_BUDGET_BYTES as _CATALOG_BUDGET,
                        )

                        budget = _CATALOG_BUDGET
                    except Exception:  # noqa: BLE001
                        budget = 24 * 1024
                for hit in hits:
                    if len(retrieval_added) >= req.retrieval_k:
                        break
                    if hit.name in existing:
                        continue
                    # 安全红线（§38）：检索只是补强通道，绝不做 tier/权限升级 ——
                    # tier-3/destructive 工具只能经既有显式语义（admin 通道 /
                    # confirm_tier3）可达，永不因词法命中被补进模型可见面。
                    try:
                        _desc = self.registry.descriptor(hit.name)
                        if int(_desc.tier) >= 3:
                            continue
                        # review R1 minor：不可用工具不得被检索复活
                        if _desc.status is ToolStatus.EXTERNAL_UNAVAILABLE:
                            continue
                    except KeyError:
                        pass
                    _cached = self.registry.schema_size(hit.name)
                    if _cached is None:
                        _subset = self.registry.get_schemas_subset({hit.name})
                        _cached = schema_bytes(_subset[0]) if _subset else 0
                    size = _cached
                    if budget is not None and used + size > budget:
                        dropped[hit.name] = "budget"
                        continue
                    schemas = self.registry.get_schemas_subset({hit.name})
                    if not schemas:
                        continue
                    kept.append(schemas[0])
                    existing.add(hit.name)
                    used += size
                    retrieval_added.append(hit.name)
                    reasons[hit.name] = [
                        f"retrieval(score={hit.score:.1f},matched={list(hit.matched[:4])})"
                    ]
            except Exception:  # noqa: BLE001 — 检索失败绝不阻断选择
                logger.debug("[ToolSurfaceV2] retrieval augment failed", exc_info=True)

        # --- 压缩 + 度量 + 指纹（保持 registry 序，检索新增按分数序追加） ---
        # PERF（review R1）：compress=none 时不做任何序列化 —— 字节走 registry
        # 注册期缓存（#1062）；压缩档的尺寸按 (name, level, summary) 确定性缓存。
        final: List[Dict[str, Any]] = []
        used_bytes = 0
        compressed_sizes: Dict[str, int] = {}
        for s in kept:
            name = s["function"]["name"]
            if req.compress != "none":
                try:
                    summary = self.registry.descriptor(name).summary or None
                except KeyError:
                    summary = None
                cache_key = f"{name}|{req.compress}|{summary or ''}"
                size = compressed_sizes.get(cache_key)
                if size is None:
                    s = compress_schema(s, level=req.compress, summary=summary)
                    size = schema_bytes(s)
                    compressed_sizes[cache_key] = size
                else:
                    s = compress_schema(s, level=req.compress, summary=summary)
            else:
                size = self.registry.schema_size(name)
                if size is None:
                    size = schema_bytes(s)
            final.append(s)
            used_bytes += size

        from app.tools.descriptor import canonical_json, _short_digest

        fingerprint = _short_digest(canonical_json(
            [
                [s["function"]["name"], self.registry.schema_fingerprint(s["function"]["name"]) or ""]
                for s in final
            ]
        ))
        return ToolSurfaceProjection(
            schemas=final,
            reasons=reasons,
            dropped=dropped,
            bytes_used=used_bytes,
            fingerprint=fingerprint,
            base_names=[s["function"]["name"] for s in base_schemas],
            retrieval_added=retrieval_added,
        )

    # ------------------------------------------------------------------
    # Pi 原生面快照：与 pi_native_surface 同一注册表真相 + V2 投影栈
    # ------------------------------------------------------------------
    def native_surface_snapshot(
        self,
        native_names: Sequence[str],
        compress: str = "compact",
    ) -> List[Dict[str, Any]]:
        req = SurfaceRequest(user_message="", compress=compress, retrieval=False)
        base = self.registry.get_schemas_subset(set(native_names))
        return self.augment(base, req).schemas
