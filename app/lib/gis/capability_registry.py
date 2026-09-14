"""Capability Registry —— Harness 能力面的正式注册表。

Capability 是「需要什么能力」的稳定词汇（recipe/plan 引用它），不绑定
具体工具实现；capability → algorithm → tool 的解析归 AlgorithmResolver。
本注册表取代 planner.py 里手写的 CAPABILITY_TOOLS 知识。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.gis.artifacts import get_artifact_type_registry

CapabilityStatus = Literal["native", "planned", "unavailable"]

# ── 动态外部扩展挂钩（ADR-0191 D5：数据-only，无代码执行路径）────────────
#: 动态能力 id 必须携带的命名空间前缀（非前缀 id 一律拒绝）。
DYNAMIC_CAPABILITY_PREFIX = "induced."
#: 动态挂钩允许的状态词表：挂钩只能登记"声明"，不能虚构 native 能力。
DYNAMIC_STATUS_ALLOWLIST = ("planned", "unavailable")
#: 单注册表动态能力数量预算（防止外部目录无限膨胀注册表）。
MAX_DYNAMIC_CAPABILITIES = 128
#: 单次 load_dynamic_capabilities 允许扫描的文件数上限。
MAX_DYNAMIC_FILES = 64


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
    # ── Capability Graph V1（ADR-0181，additive 全默认 —— 存量零迁移）────
    # 能力级离线声明：True = 存在纯本地 provider（无网络也可达成）；False =
    # 强依赖远端服务；None（默认）= 未声明，由 provider 面（tool.network 等）
    # 投影推导 —— 声明是 owner 级覆盖，不是第二事实源。
    offline_capable: Optional[bool] = None
    # 能力级不相容（如栅格域 vs 纯点要素能力同计划互斥）。引用必须指向
    # 已注册 capability id（validate 校验）；图上发射 conflicts_with 边。
    incompatible_with: List[str] = Field(default_factory=list)


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
        self._dynamic_count = 0

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._dynamic_count = 0
        for cap in _load_seed_capabilities():
            self.register(cap)

    def register(self, cap: CapabilityDescriptor) -> None:
        if cap.id in self._by_id:
            raise ValueError(f"duplicate capability id: {cap.id}")
        self._by_id[cap.id] = cap

    def register_dynamic(self, cap: CapabilityDescriptor) -> None:
        """动态外部扩展入口（ADR-0191 D5）。

        数据-only 纪律：仅接受命名空间前缀内的**声明型**描述符
        （planned/unavailable），不携带任何可执行载荷；重复 id 与
        native 状态一律 raise（fail-loud，与 register 同一纪律）。
        """
        if not cap.id.startswith(DYNAMIC_CAPABILITY_PREFIX):
            raise ValueError(
                f"dynamic capability {cap.id}: 缺命名空间前缀 "
                f"{DYNAMIC_CAPABILITY_PREFIX}（防核心词汇劫持）")
        if cap.status not in DYNAMIC_STATUS_ALLOWLIST:
            raise ValueError(
                f"dynamic capability {cap.id}: status={cap.status} 不在 "
                f"{DYNAMIC_STATUS_ALLOWLIST}（挂钩不得虚构 native 能力）")
        if cap.purpose_template:
            # purpose_template 会经 purpose_for() 的 .format(subject=...)
            # 渲染——动态面禁止携带（防 format-string 属性穿越注入面）。
            raise ValueError(
                f"dynamic capability {cap.id}: purpose_template 不允许"
                f"经动态挂钩登记")
        if self._dynamic_count >= MAX_DYNAMIC_CAPABILITIES:
            raise ValueError(
                f"dynamic capability budget exhausted "
                f"(>{MAX_DYNAMIC_CAPABILITIES})")
        self.register(cap)
        self._dynamic_count += 1

    @property
    def dynamic_ids(self) -> List[str]:
        return sorted(i for i in self._by_id
                      if i.startswith(DYNAMIC_CAPABILITY_PREFIX))

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
            for inc in cap.incompatible_with:
                if inc not in self._by_id:
                    issues.append(f"capability {cap.id}: incompatible capability {inc} not registered")
                elif inc == cap.id:
                    issues.append(f"capability {cap.id}: incompatible with itself")
        return issues


def load_dynamic_capabilities(
    path, registry: Optional["CapabilityRegistry"] = None,
) -> Tuple[List[str], List[str]]:
    """从外部目录/文件装载数据-only 能力声明（ADR-0191 D5）。

    纪律：``yaml.safe_load``（绝不执行文档内对象）；每条描述符经
    ``CapabilityDescriptor.model_validate`` + 前缀/状态/重复校验，
    非法条目 fail-closed（跳过并记录 violation，绝不部分装载脏数据）；
    文件数受 ``MAX_DYNAMIC_FILES`` 约束。返回 ``(loaded_ids, violations)``。
    """
    import yaml

    reg = registry if registry is not None else get_capability_registry()
    loaded: List[str] = []
    violations: List[str] = []

    def _register_one(payload: Any, source: str) -> None:
        try:
            cap = CapabilityDescriptor.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - 外部数据 fail-closed
            violations.append(f"{source}: invalid descriptor ({exc})")
            return
        if not cap.id.startswith(DYNAMIC_CAPABILITY_PREFIX):
            violations.append(
                f"{source}: id {cap.id} 缺前缀 {DYNAMIC_CAPABILITY_PREFIX}")
            return
        if cap.status not in DYNAMIC_STATUS_ALLOWLIST:
            violations.append(
                f"{source}: status {cap.status} 不允许动态登记")
            return
        if cap.purpose_template:
            violations.append(
                f"{source}: purpose_template 不允许动态登记（format 面）")
            return
        if reg.has(cap.id):
            violations.append(f"{source}: duplicate capability {cap.id}")
            return
        try:
            reg.register_dynamic(cap)
        except ValueError as exc:
            violations.append(f"{source}: {exc}")
            return
        loaded.append(cap.id)

    p = Path(path)
    files = sorted(p.glob("*.yaml")) if p.is_dir() else [p]
    if len(files) > MAX_DYNAMIC_FILES:
        violations.append(
            f"{p}: 文件数 {len(files)} 超预算 {MAX_DYNAMIC_FILES}（拒绝装载）")
        return loaded, violations
    for f in files:
        try:
            with f.open("r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
        except Exception as exc:  # noqa: BLE001 - 坏文件 fail-closed
            violations.append(f"{f}: unreadable yaml ({exc})")
            continue
        entries = doc.get("capabilities") if isinstance(doc, dict) else doc
        if not isinstance(entries, list):
            violations.append(f"{f}: 形态非法（期望 capabilities 列表）")
            continue
        for entry in entries:
            _register_one(entry, str(f))
    return loaded, violations


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
