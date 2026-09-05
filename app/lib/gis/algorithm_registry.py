"""GIS Algorithm Registry —— 算法语义目录（非执行引擎）。

Algorithm 回答「如何计算」：capability（做什么）→ algorithm（哪种方法）
→ tool_candidates（哪个注册工具实现它）。实际执行永远在 ToolRegistry /
ToolDispatchService —— 本注册表只持 metadata，不持数据、不执行、不做
第二套 runtime。新增算法 = 注册 AlgorithmDescriptor，Harness 主规划代码
不改。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.lib.gis.artifacts import get_artifact_type_registry
from app.lib.gis.capability_registry import get_capability_registry

AlgorithmStatus = Literal["native", "planned", "unavailable"]
CostLevel = Literal["low", "medium", "high"]

# V2(P3)：unit_requirements 的封闭词表 —— 消费方（参数契约/测试）只认
# 这几族；声明词表外的单位一律 validate() 报 issue（死 metadata 防御）。
_UNIT_VOCABULARY = frozenset({"meters", "kilometers", "degrees", "pixels", "seconds"})

# ── VNext（ADR-0099）科学元数据词表 ─────────────────────────────────
# crs_class：resolver 硬门消费（crs_safety.crs_class_allows）。
CRSSpatialClass = Literal[
    "", "CRS_AGNOSTIC", "GEOGRAPHIC_OK", "PROJECTED_REQUIRED",
    "LOCAL_METRIC_REQUIRED", "GEODESIC", "RASTER_GRID",
]
# fallback 科学等价性（resolver fallback trail 携带；proxy/degraded 必须
# 显现在证据里 —— 「网络可达性不可用 → 欧氏缓冲」是 proxy，不是 equivalent）。
FallbackSemanticsClass = Literal[
    "equivalent", "approximation", "proxy", "degraded", "not_allowed",
]
ScientificStatus = Literal["", "EXPERIMENTAL", "VALIDATED", "PRODUCTION", "DEPRECATED"]
RandomSeedPolicy = Literal[
    "deterministic", "fixed_seed", "caller_seeded", "unseeded", "none",
]
# backend_variants 的实现后端词表（封闭；新增需同步 validate 消费方）。
BACKEND_VOCABULARY = frozenset({
    "pure_python", "numpy", "scipy", "shapely", "geopandas", "rasterio",
    "gdal", "pysal", "scikit-learn", "networkx", "h3", "matplotlib",
    "numexpr", "external",
})



# （原 ALGORITHM_TAXONOMY 已删除 —— 2026-09 VNext 死元数据清理：
# 该字典与 AlgorithmDescriptor.id 从不匹配、无运行时消费方；域分组
# 事实源现在是 app/lib/gis/algorithms/ 域包 + descriptor.category。


class BackendVariant(BaseModel):
    """同一算法的一个实现变体（§28：Algorithm → Implementation Variant）。

    所有变体必须通过同一 conformance 套件；resolver 可按规模/环境在
    变体间选择（tool_candidates 顺序即默认偏好序）。
    """

    id: str                                  # 变体内唯一（如 "numpy_batched"）
    backend: str                             # BACKEND_VOCABULARY
    tool: str = ""                           # 绑定的工具实现（可空 = lib 内部）
    deterministic: bool = True
    notes: str = ""

    @field_validator("notes")
    @classmethod
    def _bounded_notes(cls, v: str) -> str:
        return v[:160]


class AlgorithmDescriptor(BaseModel):
    """一个 GIS 算法的机器可读描述。"""

    id: str
    name: str
    capabilities: List[str]
    category: str = ""
    subcategory: str = ""
    tags: List[str] = Field(default_factory=list)
    input_artifact_types: List[str] = Field(default_factory=list)
    output_artifact_type: str = ""
    geometry_requirements: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    optional_fields: List[str] = Field(default_factory=list)
    min_features: Optional[int] = None
    max_features_hint: Optional[int] = None
    crs_requirements: str = ""
    unit_requirements: str = ""
    parameter_contract_ref: str = ""
    deterministic: bool = True
    approximate: bool = False
    complexity: str = ""
    cpu_cost: CostLevel = "medium"
    memory_cost: CostLevel = "medium"
    io_cost: CostLevel = "medium"
    preferred_execution_policy: str = ""
    tool_candidates: List[str] = Field(default_factory=list)
    runtime_status: AlgorithmStatus = "native"
    compatible_map_models: List[str] = Field(default_factory=list)
    fallback_algorithms: List[str] = Field(default_factory=list)
    priority: int = 50
    version: str = "1.0"
    contract_version: int = 1
    # ── VNext（ADR-0099）：科学元数据（全部 additive；每个字段有
    # validate() 校验器或明确消费方，杜绝学术百科式死元数据）─────────
    algorithm_family: str = ""               # 如 "kriging" / "spatial_autocorrelation"
    method_references: List[str] = Field(default_factory=list)   # method_references.py id
    assumptions: List[str] = Field(default_factory=list)         # 进证据块
    limitations: List[str] = Field(default_factory=list)
    crs_class: CRSSpatialClass = ""          # resolver CRS 硬门
    scientific_preconditions: List[str] = Field(default_factory=list)
    uncertainty_outputs: List[str] = Field(default_factory=list)  # uncertainty 词表
    random_seed_policy: RandomSeedPolicy = "deterministic"
    numerical_tolerance: str = ""            # 容差声明（有界文本）
    scientific_status: ScientificStatus = "" # 与 runtime_status 正交：验证强度
    conformance_tests: List[str] = Field(default_factory=list)  # pytest 节点 id
    backend_variants: List[BackendVariant] = Field(default_factory=list)
    # target_id → 科学等价性分类；键必须是 fallback_algorithms 成员。
    fallback_semantics: Dict[str, FallbackSemanticsClass] = Field(default_factory=dict)

    @field_validator("assumptions", "limitations")
    @classmethod
    def _bounded_text_lists(cls, v: List[str]) -> List[str]:
        return [str(x)[:160] for x in v[:8]]

    @field_validator("method_references", "scientific_preconditions")
    @classmethod
    def _bounded_id_lists(cls, v: List[str]) -> List[str]:
        return [str(x)[:96] for x in v[:8]]

    @field_validator("conformance_tests")
    @classmethod
    def _bounded_conformance_nodes(cls, v: List[str]) -> List[str]:
        # pytest 节点 id（文件::函数/类::函数）可远超 96 字符 —— 截断会让
        # 节点级存在性校验误报（评审 M1 的 26 个误报根因）。
        return [str(x)[:220] for x in v[:8]]

    @field_validator("uncertainty_outputs")
    @classmethod
    def _bounded_uncertainty(cls, v: List[str]) -> List[str]:
        return [str(x)[:32] for x in v[:6]]

    @field_validator("numerical_tolerance")
    @classmethod
    def _bounded_tolerance(cls, v: str) -> str:
        return v[:160]

    @field_validator("backend_variants")
    @classmethod
    def _bounded_variants(cls, v: List[BackendVariant]) -> List[BackendVariant]:
        if len(v) > 4:
            raise ValueError("backend_variants exceeds 4 entries")
        ids = [b.id for b in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate backend variant ids: {ids}")
        return v

    @field_validator("algorithm_family")
    @classmethod
    def _family_shape(cls, v: str) -> str:
        if v and (" " in v or not v.replace("_", "a").replace(".", "a").isidentifier()):
            raise ValueError(f"invalid algorithm_family: {v!r}")
        return v


# 域包架构（ADR-0099 §34）：种子描述符迁至 app/lib/gis/algorithms/ 各域
# 模块（逐字迁移）；本文件只保留 descriptor 定义 + registry + 校验。


def _load_seed_algorithms() -> list:
    from app.lib.gis.algorithms import iter_domain_packs

    seeds: list = []
    for pack in iter_domain_packs():
        seeds.extend(pack)
    return seeds


class AlgorithmRegistry:
    """算法目录：by-id / by-capability O(1) 索引、禁止静默重复、稳定排序。"""

    def __init__(self) -> None:
        self._tool_to_capability_cache: Optional[Dict[str, str]] = None
        self._by_id: Dict[str, AlgorithmDescriptor] = {}
        self._by_capability: Dict[str, List[str]] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._by_capability.clear()
        for algo in _load_seed_algorithms():
            self.register(algo)

    def register(self, algo: AlgorithmDescriptor) -> None:
        if algo.id in self._by_id:
            raise ValueError(f"duplicate algorithm id: {algo.id}")
        self._tool_to_capability_cache = None
        self._by_id[algo.id] = algo
        for cap in algo.capabilities:
            candidates = self._by_capability.setdefault(cap, [])
            if algo.id not in candidates:
                candidates.append(algo.id)
            # 稳定排序：priority 升序，id 兜底
            candidates.sort(
                key=lambda aid: (self._by_id[aid].priority, aid),
            )

    def get(self, algorithm_id: str) -> Optional[AlgorithmDescriptor]:
        return self._by_id.get(algorithm_id)

    def has(self, algorithm_id: str) -> bool:
        return algorithm_id in self._by_id

    def algorithms_for_capability(
        self, capability: str, *, include_planned: bool = False,
    ) -> List[AlgorithmDescriptor]:
        ids = self._by_capability.get(capability, [])
        algos = [self._by_id[i] for i in ids]
        if not include_planned:
            algos = [a for a in algos if a.runtime_status != "unavailable"]
        return algos

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def tool_to_capability(self) -> Dict[str, str]:
        """派生的 tool → 主 capability 反查索引（provenance 回填用）。

        确定性两遍：先把每个算法的**首选**工具（tool_candidates[0]）归给
        该算法的主 capability（spatial_aggregate → admin_aggregation 而非
        把它列为第三候选的 analytical_density），再按 (priority, id) 稳定
        序补齐其余候选。

        #1076(D-8): 注册表载入后静态 —— 结果按内容缓存，register 失效。
        此前 webgis_map_product 每调用、session_plan 每工具结果都全量
        重建（每算法两遍排序扫描）。
        """
        cached = self._tool_to_capability_cache
        if cached is not None:
            return cached
        ordered = sorted(self._by_id.values(), key=lambda a: (a.priority, a.id))
        mapping: Dict[str, str] = {}
        for algo in ordered:
            cap = algo.capabilities[0] if algo.capabilities else ""
            if cap and algo.tool_candidates:
                mapping.setdefault(algo.tool_candidates[0], cap)
        for algo in ordered:
            cap = algo.capabilities[0] if algo.capabilities else ""
            if not cap:
                continue
            for tool in algo.tool_candidates:
                mapping.setdefault(tool, cap)
        self._tool_to_capability_cache = mapping
        return mapping

    def capability_tool_map(self) -> Dict[str, List[str]]:
        """派生的 capability → 有序工具候选表（兼容视图，非第二事实源）。"""
        mapping: Dict[str, List[str]] = {}
        for cap, ids in self._by_capability.items():
            tools: List[str] = []
            for aid in ids:
                algo = self._by_id[aid]
                for tool in algo.tool_candidates:
                    if tool not in tools:
                        tools.append(tool)
            if tools:
                mapping[cap] = tools
        return mapping

    def validate(self, available_tools: Optional[set] = None) -> List[str]:
        """结构自检：capability/artifact 引用、native 工具存在性。"""
        capabilities = get_capability_registry()
        artifact_types = get_artifact_type_registry()
        issues: List[str] = []
        for algo in self._by_id.values():
            if not algo.capabilities:
                issues.append(f"algorithm {algo.id}: no capability declared")
            for cap in algo.capabilities:
                if not capabilities.has(cap):
                    issues.append(f"algorithm {algo.id}: unknown capability {cap}")
            if algo.output_artifact_type and not artifact_types.has(algo.output_artifact_type):
                issues.append(
                    f"algorithm {algo.id}: unknown output artifact {algo.output_artifact_type}")
            for ref in algo.input_artifact_types:
                if not artifact_types.has(ref):
                    issues.append(f"algorithm {algo.id}: unknown input artifact {ref}")
            if algo.runtime_status == "native" and not algo.tool_candidates:
                issues.append(f"algorithm {algo.id}: native but no tool candidates")
            if available_tools is not None and algo.runtime_status == "native":
                missing = [t for t in algo.tool_candidates if t not in available_tools]
                if missing:
                    issues.append(
                        f"algorithm {algo.id}: tools not registered: {missing}")
            for fb in algo.fallback_algorithms:
                if fb not in self._by_id:
                    issues.append(f"algorithm {algo.id}: fallback algorithm {fb} not registered")
            # V2(P3) 契约一致性：unit_requirements 只接受已知单位词
            # （封闭词表）；自由字符串等于永远无人可消费的死 metadata。
            # （approximate 与 deterministic 正交：前者是精度折衷，后者是
            # 可复现性 —— 不做静态矛盾判定，§27 的随机性披露由 descriptor
            # 声明者负责。）
            if algo.unit_requirements and algo.unit_requirements not in _UNIT_VOCABULARY:
                issues.append(
                    f"algorithm {algo.id}: unknown unit_requirements "
                    f"'{algo.unit_requirements}' (vocabulary: {sorted(_UNIT_VOCABULARY)})")
            # ── VNext（ADR-0099）科学元数据校验：每个声明字段都有
            # 存在性/一致性消费方 —— 死 metadata 在注册表门被拒。──────
            issues.extend(self._validate_scientific_metadata(algo))
        for cap in capabilities.all_ids:
            if not self._by_capability.get(cap):
                issues.append(f"capability {cap}: no algorithm registered")
        return issues

    def _validate_scientific_metadata(self, algo: AlgorithmDescriptor) -> List[str]:
        """VNext 科学字段的交叉校验（参数契约/出处/前置条件/不确定性/
        复现策略/成熟度/fallback 语义）。"""
        issues: List[str] = []
        if algo.parameter_contract_ref:
            from app.lib.gis.parameter_contracts import get_parameter_contract_registry

            contract = get_parameter_contract_registry().get(algo.parameter_contract_ref)
            if contract is None:
                issues.append(
                    f"algorithm {algo.id}: parameter_contract_ref "
                    f"'{algo.parameter_contract_ref}' not registered")
            elif not contract.parameters:
                issues.append(
                    f"algorithm {algo.id}: parameter contract "
                    f"'{algo.parameter_contract_ref}' has zero parameters")
        if algo.method_references:
            from app.lib.gis.method_references import reference_exists

            for ref in algo.method_references:
                if not reference_exists(ref):
                    issues.append(
                        f"algorithm {algo.id}: unknown method reference {ref}")
        if algo.scientific_preconditions:
            from app.lib.gis.scientific_preconditions import precondition_exists

            for pid in algo.scientific_preconditions:
                if not precondition_exists(pid):
                    issues.append(
                        f"algorithm {algo.id}: unknown scientific precondition {pid}")
        if algo.uncertainty_outputs:
            from app.lib.gis.uncertainty import UNCERTAINTY_TYPE_VOCABULARY

            for u in algo.uncertainty_outputs:
                if u not in UNCERTAINTY_TYPE_VOCABULARY:
                    issues.append(
                        f"algorithm {algo.id}: unknown uncertainty output {u}")
        # 复现策略与 deterministic 声明一致性：
        #   "deterministic"（无随机）⇒ 必须 deterministic=True；
        #   "unseeded"（随机不可控）⇒ 必须 deterministic=False；
        #   "none"（方法无随机成分、种子不适用；复现性告警走 limitations）
        #   / "fixed_seed"（内部固定种子，逐次可复现）/ "caller_seeded"
        #   （种子是参数）与两旗兼容。
        if algo.random_seed_policy == "deterministic" and not algo.deterministic:
            issues.append(
                f"algorithm {algo.id}: deterministic=False 不得声明 deterministic 种子策略")
        if algo.random_seed_policy == "unseeded" and algo.deterministic:
            issues.append(
                f"algorithm {algo.id}: deterministic=True 与 unseeded 矛盾")
        # backend_variants：后端词表 + 实现存在性（native 才谈变体）
        for variant in algo.backend_variants:
            if variant.backend not in BACKEND_VOCABULARY:
                issues.append(
                    f"algorithm {algo.id}: variant {variant.id} backend "
                    f"'{variant.backend}' not in vocabulary")
        if algo.backend_variants and algo.runtime_status == "native" \
                and not algo.tool_candidates:
            issues.append(
                f"algorithm {algo.id}: native with backend_variants but no tools")
        # fallback 语义：键合法 + not_allowed 不得同时是可自动回退目标
        for target, semantics in algo.fallback_semantics.items():
            if target not in algo.fallback_algorithms:
                issues.append(
                    f"algorithm {algo.id}: fallback_semantics key {target} "
                    f"不在 fallback_algorithms 里")
            if semantics == "not_allowed":
                issues.append(
                    f"algorithm {algo.id}: fallback {target} 标记 not_allowed "
                    f"却列在 fallback_algorithms（resolver 会自动采用）")
        for target in algo.fallback_algorithms:
            if target not in algo.fallback_semantics:
                issues.append(
                    f"algorithm {algo.id}: fallback {target} 缺科学等价性声明 "
                    f"(fallback_semantics)")
        # 成熟度必要条件（PRODUCTION/VALIDATED 是可审计承诺）
        if algo.scientific_status == "PRODUCTION":
            if algo.runtime_status != "native" or not algo.tool_candidates:
                issues.append(f"algorithm {algo.id}: PRODUCTION 需要 native 实现")
            if not algo.parameter_contract_ref:
                issues.append(f"algorithm {algo.id}: PRODUCTION 需要参数契约")
            if not algo.method_references:
                issues.append(f"algorithm {algo.id}: PRODUCTION 需要方法出处")
            if not algo.conformance_tests:
                issues.append(f"algorithm {algo.id}: PRODUCTION 需要 conformance tests")
        elif algo.scientific_status == "VALIDATED" and not algo.conformance_tests:
            issues.append(f"algorithm {algo.id}: VALIDATED 需要 conformance tests")
        elif algo.scientific_status == "DEPRECATED" and not algo.fallback_algorithms:
            issues.append(
                f"algorithm {algo.id}: DEPRECATED 必须给出 fallback（否则规划死端）")
        # conformance 节点：仓库布局可用时校验文件存在性 + **节点级**
        # 存在性（评审 M1：文件级检查放过节点改名腐烂 —— VALIDATED 的
        # 可审计承诺必须钉到真实测试函数）。确定性 AST 解析，零导入。
        if algo.conformance_tests:
            import ast
            import os

            if os.path.isdir("tests"):
                for node in algo.conformance_tests:
                    path, _, func = node.partition("::")
                    if not path.startswith("tests/") or not os.path.exists(path):
                        if path.startswith("tests/"):
                            issues.append(
                                f"algorithm {algo.id}: conformance test file "
                                f"missing: {path}")
                        continue
                    if func:
                        try:
                            tree = ast.parse(
                                open(path, encoding="utf-8").read())
                        except (OSError, SyntaxError):
                            continue
                        names = {
                            n.name for n in ast.walk(tree)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                              ast.ClassDef))}
                        # 节点路径可为 file::func 或 file::Class::method ——
                        # 逐段存在性校验。
                        segments = [s for s in func.split("::") if s]
                        if any(s not in names for s in segments):
                            issues.append(
                                f"algorithm {algo.id}: conformance test node "
                                f"missing: {node}")
        return issues


_registry: Optional[AlgorithmRegistry] = None


def get_algorithm_registry() -> AlgorithmRegistry:
    global _registry
    if _registry is None:
        _registry = AlgorithmRegistry()
        _registry.load_builtins()
    return _registry


def reset_algorithm_registry() -> None:
    global _registry
    _registry = None
