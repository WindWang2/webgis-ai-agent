"""ExecutionCatalog —— tool / algorithm / recipe / capability 的统一只读投影。

F07（docs/dev/f07-execution-catalog-design.md）。各权威 registry
（CapabilityRegistry / AlgorithmRegistry / ToolRegistry / RecipeRegistry）
仍是唯一真相；本模块把它们聚合为一份**versioned、指纹化、有界**的
执行目录，回答三类此前没有统一入口的问题：

1. **对账**：同一事实在 catalog / runtime_manifest / capability graph 三面
   是否一致（``reconcile_with_manifest``）；
2. **解析**：capability → algorithm(priority 序) → 已注册工具候选链
   （``tool_candidates_for_capability``，capability-first，不依赖工具名
   字面量）;
3. **stale 凭证**：per-entry 内容指纹 + generation 指纹，plan 携带快照后
   可精确解释「哪些条目变化、影响哪些消费者」（staleness 模块）。

纪律（与 runtime_manifest / capability_conformance 同门）：
- 编译是**只读投影**，绝不反写 registry；导入失败容错为 fatal 记录；
- 指纹 = canonical-JSON SHA-256（descriptor.canonical_json 同一原语），
  含 ``EXECUTION_CATALOG_VERSION`` 盐 —— 词表/投影演进必然换指纹；
- 同一 registry 内容跨进程指纹稳定（不含 compiled_at / 顺序噪声）；
- 编译一次缓存（``get_execution_catalog``），registry 变化经
  ``refresh_execution_catalog()`` 生效。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: catalog 投影版本（投影结构/语义演进时升版；参与全部指纹）。
EXECUTION_CATALOG_VERSION = 1

#: (kind, id) 二元组键。
CatalogKey = Tuple[str, str]

KIND_CAPABILITY = "capability"
KIND_ALGORITHM = "algorithm"
KIND_TOOL = "tool"
KIND_RECIPE = "recipe"
CATALOG_KINDS = (KIND_CAPABILITY, KIND_ALGORITHM, KIND_TOOL, KIND_RECIPE)

#: provider 归属词表（诚实披露：unknown = 证据缺席，绝不虚构 core）。
PROVIDER_CORE = "core"
PROVIDER_DYNAMIC = "dynamic"
PROVIDER_UNKNOWN = "unknown"

#: 动态 capability 命名空间前缀（引用 capability_registry 的规则，不 import
#: 以免编译期耦合 —— 词表以字符串常量同步，parity 测试锁定）。
_DYNAMIC_CAPABILITY_PREFIX = "induced."

#: 认证 facet 词表（扩展包最小契约；conformance/文档共用）。
CERTIFICATION_CONTRACT_FACETS = (
    "metadata", "schema", "cancellation", "side_effects",
    "security", "resource_estimate", "tests",
)


def canonical_payload_json(obj: Any) -> str:
    """确定性序列化（复用 ToolDescriptor 的 canonical 原语，单一实现）。"""
    from app.tools.descriptor import canonical_json

    return canonical_json(obj)


def _digest(payload: str) -> str:
    return hashlib.sha256(
        payload.encode("utf-8"), usedforsecurity=False,
    ).hexdigest()[:32]


def _sorted_tuple(values: Iterable[Any], limit: int = 16) -> Tuple[str, ...]:
    out: List[str] = []
    for v in values:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s[:128])
        if len(out) >= limit:
            break
    return tuple(sorted(out))


@dataclass(frozen=True)
class CatalogEntry:
    """执行目录条目（kind + id 唯一；字段全部来自权威 registry 事实）。"""

    kind: str
    id: str
    name: str = ""
    version: str = "1.0"
    contract_version: int = 1
    provider: str = PROVIDER_CORE
    status: str = "native"
    #: 归属的能力 id（capability 条目即自身 id）。
    capabilities: Tuple[str, ...] = ()
    #: 语义类型（artifact 词表 / 工具 output_semantic_type）。
    input_semantic_types: Tuple[str, ...] = ()
    output_semantic_types: Tuple[str, ...] = ()
    geometry_requirements: Tuple[str, ...] = ()
    crs_class: str = ""
    crs_semantics: str = ""
    unit_requirements: str = ""
    unit_semantics: str = ""
    #: 时间约束（recipe 资格规则声明的时间面；词表元素 id）。
    temporal_constraints: Tuple[str, ...] = ()
    deterministic: Optional[bool] = None
    random_seed_policy: str = ""
    side_effect: str = ""
    #: 资源分级投影（有界词表：latency/memory/scale/cpu/io）。
    resource_class: Mapping[str, str] = field(default_factory=dict)
    cancellation_profile: str = ""
    #: 声明式资源包络（算法层 ResourceEnvelope 的有界投影）。
    resource_envelope: Mapping[str, Any] = field(default_factory=dict)
    #: 声明的降级/替代目标（同 kind 或跨 kind 的 id 引用）。
    fallback_targets: Tuple[str, ...] = ()
    #: 替代者（deprecated 条目的 canonical 后继）。
    superseded_by: str = ""
    deprecated: bool = False
    #: 认证证据投影（core / extension / unknown；绝不虚构 certified）。
    certification: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 50
    #: 有界补充事实（conformance_tests 数、keywords 计数等；不进指纹正文
    #: 之外的大对象）。
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> CatalogKey:
        return (self.kind, self.id)

    @property
    def is_deprecated(self) -> bool:
        """弃用判定（status 与投影旗标的合取安全面 —— 两处声明任一成立
        即弃用；权威投影恒双写一致）。"""
        return bool(self.deprecated) or self.status == "deprecated"

    @property
    def stable_id(self) -> str:
        """跨 kind 统一稳定标识 ``kind:id@version``。"""
        return f"{self.kind}:{self.id}@{self.version}"

    def fingerprint_payload(self) -> Dict[str, Any]:
        """指纹载荷：全部语义字段（canonical 形态；不含 provider 运行态）。

        ``cert_state`` 参与指纹：认证状态翻转（valid → stale，证据吊销）
        改变 discovery 排序语义 ⇒ 旧快照必须可感知（P2-3）。
        """
        return {
            "catalog_version": EXECUTION_CATALOG_VERSION,
            "kind": self.kind,
            "id": self.id,
            "version": self.version,
            "contract_version": self.contract_version,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "input_semantic_types": list(self.input_semantic_types),
            "output_semantic_types": list(self.output_semantic_types),
            "geometry_requirements": list(self.geometry_requirements),
            "crs_class": self.crs_class,
            "crs_semantics": self.crs_semantics,
            "unit_requirements": self.unit_requirements,
            "unit_semantics": self.unit_semantics,
            "temporal_constraints": list(self.temporal_constraints),
            "deterministic": self.deterministic,
            "random_seed_policy": self.random_seed_policy,
            "side_effect": self.side_effect,
            "resource_class": dict(sorted(self.resource_class.items())),
            "cancellation_profile": self.cancellation_profile,
            "resource_envelope": dict(sorted(self.resource_envelope.items()))
            if self.resource_envelope else {},
            "fallback_targets": list(self.fallback_targets),
            "superseded_by": self.superseded_by,
            "deprecated": self.deprecated,
            "priority": self.priority,
            "cert_state": str(self.certification.get(
                "state", self.certification.get("provider_kind", "")) or ""),
            "detail": dict(sorted(self.detail.items()))
            if self.detail else {},
        }

    @property
    def fingerprint(self) -> str:
        return _digest(canonical_payload_json(self.fingerprint_payload()))


# ── provider 归属（扩展命名空间规则；证据注入，不猜测）──────────────────

#: 核心算法 id 本身含点号（spatial.aggregate.admin …），因此**点号不是**
#: 扩展证据 —— 全部归属只认注入的已知 namespace（host 注册的投影规则）：
#: tool / recipe = ``{ns}_*``（host 的 ns_recipe_prefix 同构）；
#: algorithm = ``{ns}.*``（manifest.namespaced_algorithm_id 同构）。
#: 证据缺席 = core（fail-safe，绝不把核心前缀误判为扩展）。
def split_extension_namespace(
    kind: str, raw_id: str, namespaces: Iterable[str],
) -> Optional[str]:
    """按扩展命名空间规则归位 provider namespace；非扩展条目返回 None。

    ``namespaces`` 必须来自真实证据（扩展 host 认证索引注入）；最长
    namespace 优先（防 ``a`` 遮蔽 ``a_b``）。
    """
    name = str(raw_id or "")
    if not name:
        return None
    known = sorted(set(namespaces), key=len, reverse=True)
    if kind in (KIND_TOOL, KIND_RECIPE):
        for ns in known:
            if name.startswith(f"{ns}_"):
                return ns
        return None
    for ns in known:
        if name.startswith(f"{ns}."):
            return ns
    return None


def build_certification_index_from_host(host: Any) -> Dict[str, Dict[str, Any]]:
    """从 ExtensionHost 构建有界认证索引 ``{namespace: evidence}``（只读）。

    证据来自 pack_catalog.certification_status_for（report 指纹校验，
    不验 HMAC）。host 缺席/异常由调用方处理 —— 本函数不做容错导入。
    """
    from app.extensions_platform.pack_catalog import certification_status_for

    index: Dict[str, Dict[str, Any]] = {}
    for extension_id in host.extension_ids():
        record = host.get_record(extension_id)
        if record is None or getattr(record, "manifest", None) is None:
            continue
        ns = str(record.manifest.namespace)
        status = certification_status_for(record)
        index[ns] = {
            "extension_id": str(extension_id),
            "version": str(getattr(record.manifest, "version", ""))[:32],
            "state": str(status.get("state", "unknown")),
            "certified": bool(status.get("certified", False)),
            "signed": bool(status.get("signed", False)),
        }
    return {ns: index[ns] for ns in sorted(index)}


def _certification_projection(
    namespace: Optional[str],
    index: Mapping[str, Mapping[str, Any]],
    *,
    is_dynamic: bool = False,
    evidence_available: bool = False,
) -> Dict[str, Any]:
    """认证证据投影（诚实三态：True/False/None=证据系统未接入）。

    - 有 namespace 证据 → extension + 证据状态；
    - 无 namespace 且**证据系统已接入**（注入了 index）→ core，certified
      是真判定（核心条目天然认证）；
    - 无 namespace 且证据系统未接入（缺省编译）→ provider 归 core 是
      结构事实（扩展投影必带命名空间前缀），但 ``certified=None`` ——
      绝不虚构 certified=True（f11 扩展注册后未注入证据也不误判）。
    """
    if is_dynamic:
        return {"provider_kind": PROVIDER_DYNAMIC, "certified": False}
    if not namespace:
        return {
            "provider_kind": PROVIDER_CORE,
            "certified": True if evidence_available else None,
        }
    evidence = index.get(namespace)
    if evidence is None:
        # namespace 已知但证据缺席 —— unknown 诚实披露（不虚构 core）。
        return {
            "provider_kind": PROVIDER_UNKNOWN,
            "namespace": namespace[:64],
            "certified": False,
        }
    return {
        "provider_kind": "extension",
        "namespace": namespace[:64],
        "state": str(evidence.get("state", "unknown")),
        "certified": bool(evidence.get("certified", False)),
        "signed": bool(evidence.get("signed", False)),
    }


# ── 各 registry → CatalogEntry 投影（纯函数，逐字读取声明事实）──────────


def _capability_entry(
    cap: Any, index: Mapping[str, Mapping[str, Any]],
    *, evidence_available: bool = False,
) -> CatalogEntry:
    is_dynamic = str(getattr(cap, "id", "")).startswith(_DYNAMIC_CAPABILITY_PREFIX)
    # capability 无扩展注册路径：动态挂钩（induced.*）或 core 二态。
    return CatalogEntry(
        kind=KIND_CAPABILITY,
        id=str(cap.id),
        name=str(getattr(cap, "name", "") or "")[:160],
        version=str(getattr(cap, "version", "1.0") or "1.0"),
        status=str(getattr(cap, "status", "native") or "native"),
        capabilities=(str(cap.id),),
        input_semantic_types=_sorted_tuple(
            getattr(cap, "input_artifact_types", None) or ()),
        output_semantic_types=_sorted_tuple(
            getattr(cap, "output_artifact_types", None) or ()),
        geometry_requirements=_sorted_tuple(
            getattr(cap, "geometry_requirements", None) or ()),
        deterministic=bool(getattr(cap, "deterministic", True)),
        fallback_targets=_sorted_tuple(
            getattr(cap, "fallback_capabilities", None) or ()),
        provider=PROVIDER_DYNAMIC if is_dynamic else PROVIDER_CORE,
        certification=_certification_projection(
            None, index, is_dynamic=is_dynamic,
            evidence_available=evidence_available),
        detail={
            "domain": str(getattr(cap, "domain", "") or "")[:32],
            "category": str(getattr(cap, "category", "") or "")[:32],
            "offline_capable": getattr(cap, "offline_capable", None),
            "supports_large_data": bool(getattr(cap, "supports_large_data", True)),
            "incompatible_with": list(_sorted_tuple(
                getattr(cap, "incompatible_with", None) or ())),
        },
    )


def _algorithm_entry(
    algo: Any, index: Mapping[str, Mapping[str, Any]],
    *, evidence_available: bool = False,
) -> CatalogEntry:
    algo_id = str(algo.id)
    ns = split_extension_namespace(KIND_ALGORITHM, algo_id, index.keys())
    envelope = getattr(algo, "resource_envelope", None)
    env_proj: Dict[str, Any] = {}
    if envelope is not None:
        env_proj = {
            k: getattr(envelope, k)
            for k in ("bytes_per_feature", "bytes_per_cell", "max_pairs",
                      "hard_max_features", "hard_max_cells")
            if getattr(envelope, k, None) is not None
        }
    scientific = str(getattr(algo, "scientific_status", "") or "")
    fallbacks = _sorted_tuple(getattr(algo, "fallback_algorithms", None) or ())
    deprecated = scientific == "DEPRECATED"
    raw_caps = [str(c or "").strip() for c in
                (getattr(algo, "capabilities", None) or ()) if str(c or "").strip()]
    caps = _sorted_tuple(raw_caps, limit=64)
    return CatalogEntry(
        kind=KIND_ALGORITHM,
        id=algo_id,
        name=str(getattr(algo, "name", "") or "")[:160],
        version=str(getattr(algo, "version", "1.0") or "1.0"),
        contract_version=int(getattr(algo, "contract_version", 1) or 1),
        provider=ns or PROVIDER_CORE,
        status=str(getattr(algo, "runtime_status", "native") or "native"),
        capabilities=caps,
        input_semantic_types=_sorted_tuple(
            getattr(algo, "input_artifact_types", None) or ()),
        output_semantic_types=_sorted_tuple(
            [getattr(algo, "output_artifact_type", "") or ""]),
        geometry_requirements=_sorted_tuple(
            getattr(algo, "geometry_requirements", None) or ()),
        crs_class=str(getattr(algo, "crs_class", "") or ""),
        unit_requirements=str(getattr(algo, "unit_requirements", "") or ""),
        deterministic=bool(getattr(algo, "deterministic", True)),
        random_seed_policy=str(getattr(algo, "random_seed_policy", "") or ""),
        resource_class={
            "cpu": str(getattr(algo, "cpu_cost", "medium") or "medium"),
            "memory": str(getattr(algo, "memory_cost", "medium") or "medium"),
            "io": str(getattr(algo, "io_cost", "medium") or "medium"),
        },
        cancellation_profile=str(
            getattr(algo, "cancellation_profile", "") or ""),
        resource_envelope=env_proj,
        fallback_targets=fallbacks,
        superseded_by=fallbacks[0] if deprecated and fallbacks else "",
        deprecated=deprecated,
        certification=_certification_projection(
            ns, index, evidence_available=evidence_available),
        priority=int(getattr(algo, "priority", 50) or 50),
        detail={
            "tool_candidates": list(getattr(algo, "tool_candidates", None) or ()),
            "capabilities_truncated": len(set(raw_caps)) > len(caps),
            "scientific_status": scientific,
            "approximation_class": str(
                getattr(algo, "approximation_class", "") or ""),
            "parameter_contract_ref": str(
                getattr(algo, "parameter_contract_ref", "") or ""),
            "conformance_tests": len(
                getattr(algo, "conformance_tests", None) or ()),
            "uncertainty_outputs": list(_sorted_tuple(
                getattr(algo, "uncertainty_outputs", None) or (), limit=6)),
        },
    )


def _tool_entry(
    descriptor: Any, index: Mapping[str, Mapping[str, Any]],
    *, evidence_available: bool = False,
) -> CatalogEntry:
    name = str(descriptor.name)
    ns = split_extension_namespace(KIND_TOOL, name, index.keys())
    status = getattr(descriptor, "status", "stable")
    if hasattr(status, "value"):
        status = status.value
    status = str(status or "stable")
    deprecated = status == "deprecated"
    side_effect = getattr(descriptor, "side_effect", "")
    if hasattr(side_effect, "value"):
        side_effect = side_effect.value
    out_type = getattr(descriptor, "output_semantic_type", None) or ""
    timeout = getattr(descriptor, "timeout", None)
    # capability 双口径：capabilities = 生效面（声明或算法派生回填），
    # declared_capabilities = 纯声明面（与 runtime_manifest v4 投影同
    # 口径 —— reconcile 按同口径对账，防派生回填被误判为声明分歧）。
    capability_source = str(
        getattr(descriptor, "capability_source", "none") or "none")
    raw_caps = [str(c or "").strip() for c in
                (getattr(descriptor, "capabilities", None) or ())
                if str(c or "").strip()]
    caps = _sorted_tuple(raw_caps, limit=64)
    declared_caps = caps if capability_source == "declared" else ()
    return CatalogEntry(
        kind=KIND_TOOL,
        id=name,
        name=name,
        version=str(getattr(descriptor, "version", "1.0") or "1.0"),
        contract_version=int(
            getattr(descriptor, "contract_version", 1) or 1),
        provider=ns or PROVIDER_CORE,
        status=status,
        capabilities=caps,
        input_semantic_types=_sorted_tuple(
            getattr(descriptor, "input_artifacts", None) or ()),
        output_semantic_types=_sorted_tuple([out_type] if out_type else ()),
        crs_semantics=str(getattr(descriptor, "crs_semantics", "") or ""),
        unit_semantics=str(getattr(descriptor, "unit_semantics", "") or ""),
        deterministic=(None if getattr(descriptor, "deterministic", None) is None
                       else bool(descriptor.deterministic)),
        side_effect=str(side_effect or ""),
        resource_class={
            "latency": str(getattr(descriptor, "latency_class", "unknown") or "unknown"),
            "memory": str(getattr(descriptor, "memory_class", "unknown") or "unknown"),
            "scale": str(getattr(descriptor, "scale_class", "unknown") or "unknown"),
        },
        cancellation_profile="tool_timeout" if timeout else "",
        fallback_targets=_sorted_tuple(
            [getattr(descriptor, "fallback_tool", None) or ""]),
        superseded_by=str(getattr(descriptor, "deprecation_of", "") or ""),
        deprecated=deprecated,
        certification=_certification_projection(
            ns, index, evidence_available=evidence_available),
        priority=0,
        detail={
            "tier": int(getattr(descriptor, "tier", 1) or 1),
            "domains": list(_sorted_tuple(
                getattr(descriptor, "domains", None) or ())),
            "network": getattr(descriptor, "network", None),
            "idempotent": getattr(descriptor, "effective_idempotent", None),
            "result_size_policy": str(
                getattr(descriptor, "result_size_policy", "unknown") or "unknown"),
            "accepts_ref_types": list(_sorted_tuple(
                getattr(descriptor, "accepts_ref_types", None) or ())),
            "produced_refs": list(_sorted_tuple(
                getattr(descriptor, "produced_refs", None) or ())),
            "required_context": list(_sorted_tuple(
                getattr(descriptor, "required_context", None) or ())),
            "security_tier": getattr(descriptor, "effective_security_tier", None),
            "timeout": timeout,
            "aliases": list(_sorted_tuple(
                getattr(descriptor, "aliases", None) or ())),
            "capability_source": capability_source,
            "declared_capabilities": list(declared_caps),
            "capabilities_truncated": len(set(raw_caps)) > len(caps),
        },
    )


def _recipe_temporal_constraints(recipe: Any) -> Tuple[str, ...]:
    """recipe 资格规则声明的时间面（元素级词表，诚实转述声明）。"""
    out: List[str] = []
    for rule in getattr(recipe, "eligibility", None) or ():
        if bool(getattr(rule, "requires_temporal", False)):
            element = str(getattr(rule, "element", "") or "")[:64]
            if element and element not in out:
                out.append(element)
    return tuple(sorted(out))


def _recipe_entry(
    recipe: Any, index: Mapping[str, Mapping[str, Any]],
    *, evidence_available: bool = False,
) -> CatalogEntry:
    from app.services.gis_harness.workflow_schema import recipe_capability_ids

    recipe_id = str(recipe.id)
    ns = split_extension_namespace(KIND_RECIPE, recipe_id, index.keys())
    fallback_links = getattr(recipe, "fallback_links", None) or []
    requires_projected = any(
        bool(getattr(rule, "require_projected_crs", False))
        for rule in getattr(recipe, "eligibility", None) or ()
    )
    return CatalogEntry(
        kind=KIND_RECIPE,
        id=recipe_id,
        name=str(getattr(recipe, "name", "") or "")[:160],
        version=str(getattr(recipe, "schema_version", 1) or 1),
        status="native",
        capabilities=_sorted_tuple(recipe_capability_ids(recipe)),
        geometry_requirements=_sorted_tuple(
            getattr(recipe, "required_geometry", None) or ()),
        crs_class="PROJECTED_REQUIRED" if requires_projected else "",
        temporal_constraints=_recipe_temporal_constraints(recipe),
        fallback_targets=_sorted_tuple(
            [str(getattr(link, "to", "") or "") for link in fallback_links]),
        priority=int(getattr(recipe, "priority", 50) or 50),
        certification=_certification_projection(
            ns, index, evidence_available=evidence_available),
        detail={
            "schema_version": int(getattr(recipe, "schema_version", 1) or 1),
            "intent_tasks": list(_sorted_tuple(
                getattr(recipe, "intent_tasks", None) or ())),
            "optional_analysis": list(_sorted_tuple(
                getattr(recipe, "optional_analysis", None) or ())),
            "workflow_domain": str(
                getattr(getattr(recipe, "workflow", None), "domain", "") or ""),
        },
    )


# ── 编译（registries → catalog）────────────────────────────────────────


@dataclass
class ExecutionCatalog:
    """不可变执行目录快照（compile once, read everywhere）。"""

    catalog_version: int = EXECUTION_CATALOG_VERSION
    compiled_at: str = ""
    entries: Dict[CatalogKey, CatalogEntry] = field(default_factory=dict)
    generation_fingerprint: str = ""

    # ── 访问器 ─────────────────────────────────────────────────────────
    def get(self, kind: str, entry_id: str) -> Optional[CatalogEntry]:
        return self.entries.get((kind, entry_id))

    def entries_of_kind(self, kind: str) -> List[CatalogEntry]:
        """按 id 稳定排序的某类条目（确定性）。"""
        return [e for e in sorted(
            self.entries.values(), key=lambda e: (e.kind, e.id))
            if e.kind == kind]

    def counts(self) -> Dict[str, int]:
        out = {k: 0 for k in CATALOG_KINDS}
        for e in self.entries.values():
            if e.kind in out:
                out[e.kind] += 1
        return out

    def entry_fingerprints(self) -> Dict[str, str]:
        """``{kind:id: fingerprint}``（排序确定；stale 快照凭证载体）。"""
        return {
            f"{e.kind}:{e.id}": e.fingerprint
            for e in sorted(self.entries.values(), key=lambda e: (e.kind, e.id))
        }

    def tool_candidates_for_capability(
        self, capability_id: str, *, include_non_executable: bool = False,
    ) -> List[str]:
        """capability → 有序工具候选（capability-first 解析视图）。

        排序 = 算法 (priority, id) 稳定序展开 tool_candidates，去重保序；
        默认过滤 PLANNED/HIDDEN 工具与 planned/unavailable 算法（不可执行
        的候选不是候选）；``include_non_executable=True`` 时两者都保留
        （完整声明链视图，审计用）。**描述性视图**：解析权威仍是
        AlgorithmRegistry（与 manifest 反查图同边界 —— 禁止用于复用/回填
        判定）。
        """
        algos = [e for e in self.entries_of_kind(KIND_ALGORITHM)
                 if capability_id in e.capabilities
                 and (include_non_executable
                      or e.status not in ("planned", "unavailable"))]
        out: List[str] = []
        for algo in algos:
            for tool in algo.detail.get("tool_candidates", ()):
                if tool in out:
                    continue
                tool_entry = self.get(KIND_TOOL, str(tool))
                if tool_entry is None:
                    continue
                if not include_non_executable and tool_entry.status in (
                        "planned", "hidden"):
                    continue
                out.append(str(tool))
        return out

    def summary(self) -> Dict[str, Any]:
        deprecated = sum(1 for e in self.entries.values() if e.deprecated)
        providers: Dict[str, int] = {}
        for e in self.entries.values():
            providers[e.provider] = providers.get(e.provider, 0) + 1
        return {
            "catalog_version": self.catalog_version,
            "generation_fingerprint": self.generation_fingerprint,
            "counts": self.counts(),
            "deprecated_entries": deprecated,
            "providers": dict(sorted(providers.items())),
        }


def _lazy_tool_registry() -> Optional[Any]:
    """优先 lifespan 注入的真实 registry；回退私有 init_tools 实例。

    与 capability_graph._lazy_tool_registry 同语义但独立实现（该文件是
    f09 热区，避免耦合其变动）。
    """
    try:
        from app.agent_pi_bridge import try_get_tool_registry

        injected = try_get_tool_registry()
        if injected is not None:
            return injected
    except Exception:  # noqa: BLE001
        logger.debug("[execution-catalog] injected registry lookup failed",
                     exc_info=True)
    try:
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        init_tools(reg)
        return reg
    except Exception:  # noqa: BLE001
        logger.debug("[execution-catalog] tool registry init failed",
                     exc_info=True)
        return None


def compile_execution_catalog(
    *,
    tool_registry: Optional[Any] = None,
    certification_index: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> ExecutionCatalog:
    """从全部权威 registry 编译执行目录（只读投影）。

    ``certification_index``：``{namespace: evidence}`` 扩展认证证据
    （``build_certification_index_from_host`` 产出）；缺省 = 无扩展证据，
    全部条目按 core 投影（fail-safe，绝不虚构认证状态）。
    """
    from datetime import datetime, timezone

    index = dict(certification_index or {})
    evidence_available = bool(certification_index)
    catalog = ExecutionCatalog(
        compiled_at=datetime.now(timezone.utc).isoformat())
    entries = catalog.entries

    try:
        from app.lib.gis.capability_registry import get_capability_registry

        cr = get_capability_registry()
        for cid in cr.all_ids:
            cap = cr.get(cid)
            if cap is not None:
                e = _capability_entry(
                    cap, index, evidence_available=evidence_available)
                entries[e.key] = e
    except Exception as exc:  # noqa: BLE001 —— 单源失败不拖垮整体投影
        logger.error("[execution-catalog] capability registry unavailable: %s", exc)
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        ar = get_algorithm_registry()
        for aid in ar.all_ids:
            algo = ar.get(aid)
            if algo is not None:
                e = _algorithm_entry(
                    algo, index, evidence_available=evidence_available)
                entries[e.key] = e
    except Exception as exc:  # noqa: BLE001
        logger.error("[execution-catalog] algorithm registry unavailable: %s", exc)
    try:
        reg = tool_registry if tool_registry is not None else _lazy_tool_registry()
        descriptors = reg.descriptors() if reg is not None else {}
        for name in sorted(descriptors):
            e = _tool_entry(descriptors[name], index,
                            evidence_available=evidence_available)
            entries[e.key] = e
    except Exception as exc:  # noqa: BLE001
        logger.error("[execution-catalog] tool registry unavailable: %s", exc)
    try:
        from app.services.gis_harness.recipes import get_recipe_registry

        rr = get_recipe_registry()
        for rid in rr.all_ids:
            recipe = rr.get(rid)
            if recipe is not None:
                e = _recipe_entry(recipe, index,
                                  evidence_available=evidence_available)
                entries[e.key] = e
    except Exception as exc:  # noqa: BLE001
        logger.error("[execution-catalog] recipe registry unavailable: %s", exc)

    fingerprint_payload = {
        "catalog_version": EXECUTION_CATALOG_VERSION,
        "entries": catalog.entry_fingerprints(),
    }
    catalog.generation_fingerprint = _digest(
        canonical_payload_json(fingerprint_payload))
    return catalog


_lock = threading.Lock()
_cached_catalog: Optional[ExecutionCatalog] = None


def get_execution_catalog(
    *,
    refresh: bool = False,
    tool_registry: Optional[Any] = None,
    certification_index: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> ExecutionCatalog:
    """进程级单例（compile once）。测试/扩展注册后 refresh=True。

    显式传入 ``tool_registry`` / ``certification_index`` 时**绕过全局
    单例**：返回独立编译快照、不写进程缓存（防 A 会话注入的 registry
    快照被 B 会话无参调用复用而抖动 —— review P2-2）。
    """
    if tool_registry is not None or certification_index is not None:
        return compile_execution_catalog(
            tool_registry=tool_registry,
            certification_index=certification_index,
        )
    global _cached_catalog
    with _lock:
        if _cached_catalog is None or refresh:
            _cached_catalog = compile_execution_catalog()
        return _cached_catalog


def refresh_execution_catalog(**kwargs: Any) -> ExecutionCatalog:
    return get_execution_catalog(refresh=True, **kwargs)


# ── 对账（catalog ↔ runtime_manifest 同一事实校验）──────────────────────

#: 对账议题上限（有界披露）。
MAX_RECONCILE_ISSUES = 128

#: code 词表：
#:  catalog_manifest_id_mismatch    warning  条目在单面缺席（含注册时点差）
#:  catalog_manifest_field_divergence warning  同 id 条目核心字段不一致
#:  catalog_manifest_recipe_fp_divergence warning  recipe 内容指纹不一致
#:  catalog_graph_node_missing      warning  条目在 capability graph 无节点
RECONCILE_ID_MISMATCH = "catalog_manifest_id_mismatch"
RECONCILE_FIELD_DIVERGENCE = "catalog_manifest_field_divergence"
RECONCILE_RECIPE_FP_DIVERGENCE = "catalog_manifest_recipe_fp_divergence"
RECONCILE_GRAPH_NODE_MISSING = "catalog_graph_node_missing"

#: catalog kind → capability graph 节点 kind（recipe 以 workflow 一等节点
#: 入图，capability_graph KIND_WORKFLOW 同词表）。
_GRAPH_NODE_KINDS = {
    KIND_CAPABILITY: "capability",
    KIND_ALGORITHM: "algorithm",
    KIND_TOOL: "tool",
    KIND_RECIPE: "workflow",
}


@dataclass(frozen=True)
class ReconcileIssue:
    code: str
    kind: str
    entry_id: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code, "kind": self.kind,
            "id": self.entry_id[:128], "detail": self.detail[:200],
        }


def reconcile_with_manifest(
    catalog: ExecutionCatalog,
    manifest: Any,
) -> List[ReconcileIssue]:
    """catalog ↔ CompiledRuntimeManifest 对账（只读、有界、排序确定）。

    两面读的是同一批权威 registry；分歧即「存在第二真相或投影遗漏」的
    证据。对账字段取两面**共同承载**的最小核（status/version/capabilities/
    tool_candidates），不比较单面字段。容错：manifest 形态不符 → 单条
    issue 披露（对账器绝不 raise —— 启动闸归 manifest strict 模式）。
    """
    issues: List[ReconcileIssue] = []

    def _emit(code: str, kind: str, eid: str, detail: str) -> None:
        if len(issues) < MAX_RECONCILE_ISSUES:
            issues.append(ReconcileIssue(code, kind, eid, detail))

    sections = {
        KIND_CAPABILITY: getattr(manifest, "capabilities", None) or {},
        KIND_ALGORITHM: getattr(manifest, "algorithms", None) or {},
        KIND_TOOL: getattr(manifest, "tools", None) or {},
        KIND_RECIPE: getattr(manifest, "recipes", None) or {},
    }
    if not any(isinstance(v, dict) for v in sections.values()):
        _emit(RECONCILE_ID_MISMATCH, "", "",
              "manifest has no comparable projections (wrong type?)")
        return sorted(issues, key=lambda i: (i.code, i.kind, i.entry_id))

    for kind, section in sections.items():
        if not isinstance(section, dict):
            continue
        catalog_ids = {e.id for e in catalog.entries.values() if e.kind == kind}
        manifest_ids = set(section.keys())
        for eid in sorted(catalog_ids - manifest_ids)[:MAX_RECONCILE_ISSUES]:
            _emit(RECONCILE_ID_MISMATCH, kind, eid,
                  "in catalog, absent from manifest projection")
        for eid in sorted(manifest_ids - catalog_ids)[:MAX_RECONCILE_ISSUES]:
            _emit(RECONCILE_ID_MISMATCH, kind, eid,
                  "in manifest projection, absent from catalog")
        for eid in sorted(catalog_ids & manifest_ids):
            entry = catalog.get(kind, eid)
            proj = section.get(eid)
            if entry is None or not isinstance(proj, dict):
                continue
            # capability/recipe 无 version 字段差异面 → 只比对共有键。
            if kind in (KIND_CAPABILITY, KIND_ALGORITHM, KIND_TOOL):
                m_version = str(proj.get("version", "1.0") or "1.0")
                if m_version != entry.version:
                    _emit(RECONCILE_FIELD_DIVERGENCE, kind, eid,
                          f"version catalog={entry.version} manifest={m_version}")
            m_status = str(proj.get("status", proj.get("runtime_status", "")) or "")
            if m_status and m_status != entry.status:
                _emit(RECONCILE_FIELD_DIVERGENCE, kind, eid,
                      f"status catalog={entry.status} manifest={m_status}")
            m_caps = proj.get("capabilities")
            if isinstance(m_caps, (list, tuple)):
                # 工具按**声明面**对账：manifest v4 投影只收声明 capabilities，
                # catalog 生效面含算法派生回填 —— 口径必须一致（review P1-1：
                # 179 个 derived-only 工具曾以生效面误报分歧）。
                if kind == KIND_TOOL:
                    declared = list(entry.detail.get(
                        "declared_capabilities", ()) or ())
                    if sorted(str(c) for c in m_caps) != sorted(declared):
                        _emit(RECONCILE_FIELD_DIVERGENCE, kind, eid,
                              "declared capability set divergence")
                elif sorted(str(c) for c in m_caps) != sorted(entry.capabilities):
                    _emit(RECONCILE_FIELD_DIVERGENCE, kind, eid,
                          "capability set divergence")
            if kind == KIND_ALGORITHM:
                m_tools = proj.get("tool_candidates") or []
                c_tools = list(entry.detail.get("tool_candidates", ()))
                if list(str(t) for t in m_tools) != list(c_tools):
                    _emit(RECONCILE_FIELD_DIVERGENCE, kind, eid,
                          "tool_candidates divergence")
            if kind == KIND_RECIPE:
                m_fp = str(proj.get("content_fingerprint", "") or "")
                if m_fp:
                    from app.services.gis_harness.recipes import get_recipe_registry

                    live_fp = get_recipe_registry().content_fingerprint_of(eid)[:32]
                    if m_fp and live_fp and m_fp != live_fp:
                        _emit(RECONCILE_RECIPE_FP_DIVERGENCE, kind, eid,
                              "manifest snapshot vs live recipe fingerprint")
    return sorted(issues, key=lambda i: (i.code, i.kind, i.entry_id))


def reconcile_summary(issues: Sequence[ReconcileIssue]) -> Dict[str, Any]:
    """对账产出摘要（含截断披露；文档/工具面用）。"""
    by_code: Dict[str, int] = {}
    for i in issues:
        by_code[i.code] = by_code.get(i.code, 0) + 1
    return {
        "total": len(issues),
        "by_code": dict(sorted(by_code.items())),
        "truncated": len(issues) >= MAX_RECONCILE_ISSUES,
    }


def reconcile_with_capability_graph(
    catalog: ExecutionCatalog,
    graph: Optional[Any] = None,
) -> List[ReconcileIssue]:
    """catalog ↔ capability graph 节点面对账（第三条腿；只读）。

    图是关系索引投影（ADR-0181）；catalog 条目若在图中无对应节点，
    说明该条目对图消费方（V8 候选规划等）不可见。图对 tool/capability
    有索引预算截断（MAX_INDEX_*），截断导致的缺席按 warning 披露，不
    重复图自身的 budget issue 语义。
    """
    if graph is None:
        from app.services.gis_harness.capability_graph import (
            get_capability_graph,
        )

        graph = get_capability_graph()
    issues: List[ReconcileIssue] = []
    for kind, graph_kind in _GRAPH_NODE_KINDS.items():
        for entry in catalog.entries_of_kind(kind):
            try:
                present = graph.has(graph_kind, entry.id)
            except Exception:  # noqa: BLE001 —— 图形态异常按单条披露
                issues.append(ReconcileIssue(
                    RECONCILE_GRAPH_NODE_MISSING, kind, entry.id,
                    "capability graph unreadable"))
                break
            if not present and len(issues) < MAX_RECONCILE_ISSUES:
                issues.append(ReconcileIssue(
                    RECONCILE_GRAPH_NODE_MISSING, kind, entry.id,
                    f"no {graph_kind} node in capability graph"))
    return sorted(issues, key=lambda i: (i.code, i.kind, i.entry_id))


__all__ = [
    "EXECUTION_CATALOG_VERSION",
    "CATALOG_KINDS",
    "KIND_CAPABILITY",
    "KIND_ALGORITHM",
    "KIND_TOOL",
    "KIND_RECIPE",
    "CERTIFICATION_CONTRACT_FACETS",
    "PROVIDER_CORE",
    "PROVIDER_DYNAMIC",
    "PROVIDER_UNKNOWN",
    "CatalogEntry",
    "CatalogKey",
    "ExecutionCatalog",
    "ReconcileIssue",
    "MAX_RECONCILE_ISSUES",
    "RECONCILE_ID_MISMATCH",
    "RECONCILE_FIELD_DIVERGENCE",
    "RECONCILE_RECIPE_FP_DIVERGENCE",
    "RECONCILE_GRAPH_NODE_MISSING",
    "split_extension_namespace",
    "build_certification_index_from_host",
    "compile_execution_catalog",
    "get_execution_catalog",
    "refresh_execution_catalog",
    "reconcile_with_manifest",
    "reconcile_summary",
    "reconcile_with_capability_graph",
]
