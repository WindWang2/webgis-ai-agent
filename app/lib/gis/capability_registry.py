"""Capability Registry —— Harness 能力面的正式注册表。

Capability 是「需要什么能力」的稳定词汇（recipe/plan 引用它），不绑定
具体工具实现；capability → algorithm → tool 的解析归 AlgorithmResolver。
本注册表取代 planner.py 里手写的 CAPABILITY_TOOLS 知识。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.lib.gis.artifacts import get_artifact_type_registry

CapabilityStatus = Literal["native", "planned", "unavailable"]


class CapabilityDescriptor(BaseModel):
    """一个 GIS 能力的机器可读描述。"""

    id: str
    name: str
    description: str = ""
    # artifact 语义（引用 ArtifactTypeRegistry）
    input_artifact_types: List[str] = Field(default_factory=list)
    output_artifact_types: List[str] = Field(default_factory=list)
    # 输入约束（数据访问类能力可为空 —— 输入是查询参数而非 artifact）
    geometry_requirements: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    optional_fields: List[str] = Field(default_factory=list)
    domain: str = "general"           # general / network / raster / statistics
    category: str = "analysis"        # data_access / analysis / statistics / density / network / raster
    preferred_execution: str = ""     # 执行偏好提示（local_first / celery / async）
    supports_large_data: bool = True
    deterministic: bool = True
    compatible_map_models: List[str] = Field(default_factory=list)
    fallback_capabilities: List[str] = Field(default_factory=list)
    status: CapabilityStatus = "native"
    version: str = "1.0"
    # plan 里的用途文案（"{subject} 要素获取" 之类；planner 用 subject 格式化）
    purpose_template: str = ""


# 域包架构（ADR-0099 §34）：种子迁至 app/lib/gis/capabilities/ 各域模块。


def _load_seed_capabilities() -> list:
    from app.lib.gis.capabilities import iter_capability_packs

    seeds: list = []
    for pack in iter_capability_packs():
        seeds.extend(pack)
    return seeds


class CapabilityRegistry:
    """capability 目录：O(1) by id、可枚举、可校验、禁止静默重复。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, CapabilityDescriptor] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for cap in _load_seed_capabilities():
            self.register(cap)

    def register(self, cap: CapabilityDescriptor) -> None:
        if cap.id in self._by_id:
            raise ValueError(f"duplicate capability id: {cap.id}")
        self._by_id[cap.id] = cap

    def get(self, capability_id: str) -> Optional[CapabilityDescriptor]:
        return self._by_id.get(capability_id)

    def has(self, capability_id: str) -> bool:
        return capability_id in self._by_id

    def purpose_for(self, capability_id: str, subject: str = "") -> str:
        """plan 用途文案：registry 的 purpose_template 优先，缺省回 id。"""
        cap = self._by_id.get(capability_id)
        if cap is None or not cap.purpose_template:
            return capability_id
        if "{subject}" in cap.purpose_template:
            return cap.purpose_template.format(subject=subject or "主体")
        return cap.purpose_template

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def validate(self) -> List[str]:
        """结构自检（artifact 引用存在性等）。空列表 = 通过。"""
        artifact_types = get_artifact_type_registry()
        issues: List[str] = []
        for cap in self._by_id.values():
            for ref in cap.input_artifact_types + cap.output_artifact_types:
                if not artifact_types.has(ref):
                    issues.append(f"capability {cap.id}: unknown artifact type {ref}")
            for fb in cap.fallback_capabilities:
                if fb not in self._by_id:
                    issues.append(f"capability {cap.id}: fallback capability {fb} not registered")
        return issues


_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        _registry.load_builtins()
    return _registry


def reset_capability_registry() -> None:
    global _registry
    _registry = None
