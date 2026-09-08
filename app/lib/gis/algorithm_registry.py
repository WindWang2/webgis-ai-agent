"""GIS Algorithm Registry —— 算法语义目录（非执行引擎）。

Algorithm 回答「如何计算」：capability（做什么）→ algorithm（哪种方法）
→ tool_candidates（哪个注册工具实现它）。实际执行永远在 ToolRegistry /
ToolDispatchService —— 本注册表只持 metadata，不持数据、不执行、不做
第二套 runtime。新增算法 = 注册 AlgorithmDescriptor，Harness 主规划代码
不改。
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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
    "",
    "CRS_AGNOSTIC",
    "GEOGRAPHIC_OK",
    "PROJECTED_REQUIRED",
    "LOCAL_METRIC_REQUIRED",
    "GEODESIC",
    "RASTER_GRID",
]
# fallback 科学等价性（resolver fallback trail 携带；proxy/degraded 必须
# 显现在证据里 —— 「网络可达性不可用 → 欧氏缓冲」是 proxy，不是 equivalent）。
FallbackSemanticsClass = Literal[
    "equivalent",
    "approximation",
    "proxy",
    "degraded",
    "not_allowed",
]
ScientificStatus = Literal["", "EXPERIMENTAL", "VALIDATED", "PRODUCTION", "DEPRECATED"]
RandomSeedPolicy = Literal[
    "deterministic",
    "fixed_seed",
    "caller_seeded",
    "unseeded",
    "none",
]
# backend_variants 的实现后端词表（封闭；新增需同步 validate 消费方）。
BACKEND_VOCABULARY = frozenset(
    {
        "pure_python",
        "numpy",
        "scipy",
        "shapely",
        "geopandas",
        "rasterio",
        "gdal",
        "pysal",
        "scikit-learn",
        "networkx",
        "h3",
        "matplotlib",
        "numexpr",
        "external",
    }
)

# ── Backend SDK V3（ADR-0117）词表与结构化契约 ────────────────────────
# ApproximationClass：一个算法/变体的精度分类学。descriptor 或变体级
# 声明；空串 = 未声明（存量算法零迁移负担）。声明即约束：
# exact/approximate 与布尔 approximate 交叉一致（validate 强制）。
# 语义界定（review R1-4）：exact 指该实现路径**精确求解其数学模型**
# （如克里金方程组的精确解），不等于「精确插值器」（带 nugget 的克里金
# 不过样本点）—— 插值性质由 assumptions/limitations 表达。
ApproximationClass = Literal[
    "",
    "exact",
    "approximate",
    "heuristic",
    "sampling",
    "streaming",
]
_APPROXIMATION_CLASSES_REQUIRING_FLAG = frozenset(
    {"approximate", "heuristic", "sampling", "streaming"}
)
# 变体级声明的合法词表（不含空串——变体显式声明时必须给出分类）。
APPROXIMATION_CLASS_VOCABULARY = frozenset(
    {"exact", "approximate", "heuristic", "sampling", "streaming"}
)

# CancellationProfile：算法对协作式取消（app/lib/cancellation）的响应
# 能力声明 —— none=无取消点；coarse=仅入口/出口；chunk_boundary=分块
# 边界可响应（栅格窗口/批量迭代）；fine=内层重循环检查点。空 = 未声明。
CancellationProfile = Literal[
    "",
    "none",
    "coarse",
    "chunk_boundary",
    "fine",
]


class ResourceEnvelope(BaseModel):
    """声明式资源包络（estimate-before-allocate 的机器可读事实源）。

    把散落在各实现的护栏常数（_MAX_IDW_OBSERVATIONS / RBF_HARD_CAP /
    时维上限 / pair 截断…）中**可声明**的部分上收为 descriptor 契约，
    供 backend_selection 做结构化内存/对预算估算（诊断性）—— 实现层
    的类型化硬闸（ResourceScaleMismatch）仍是执行权威，本模型不替代。
    全字段可缺省；至少声明一项。
    """

    bytes_per_feature: Optional[float] = None  # 向量：每要素主数组字节
    bytes_per_cell: Optional[float] = None  # 栅格：每像元主数组字节
    max_pairs: Optional[int] = None  # O(n²) 对预算硬上限
    hard_max_features: Optional[int] = None  # 实现层拒绝阈值（向量）
    hard_max_cells: Optional[int] = None  # 实现层拒绝阈值（栅格）
    notes: str = ""  # 口径说明（有界）

    @field_validator("notes")
    @classmethod
    def _bounded_notes(cls, v: str) -> str:
        return v[:160]

    @field_validator("bytes_per_feature", "bytes_per_cell")
    @classmethod
    def _nonneg_bytes(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("resource envelope byte coefficients must be >= 0")
        return v

    @field_validator("max_pairs", "hard_max_features", "hard_max_cells")
    @classmethod
    def _positive_caps(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("resource envelope caps must be >= 1")
        return v

    @model_validator(mode="after")
    def _at_least_one(self) -> "ResourceEnvelope":
        declared = (
            self.bytes_per_feature is not None
            or self.bytes_per_cell is not None
            or self.max_pairs is not None
            or self.hard_max_features is not None
            or self.hard_max_cells is not None
        )
        if not declared:
            raise ValueError("resource_envelope must declare at least one bound")
        return self


class NumericalTolerance(BaseModel):
    """结构化数值容差（Wave 9 golden/双跑验证的机器可读锚）。

    自由文本 ``numerical_tolerance`` 保留为人类可读口径；本模型供
    验证框架消费（rtol/atol 语义对齐 numpy.isclose）。
    """

    rtol: Optional[float] = None  # 相对容差 (0, 0.1]
    atol: Optional[float] = None  # 绝对容差 [0, ∞)
    policy: str = ""  # 验证口径（"golden"/"double_run"…）

    @field_validator("rtol")
    @classmethod
    def _rtol_range(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not 0.0 < float(v) <= 0.1:
            raise ValueError("tolerance rtol must be in (0, 0.1]")
        return v

    @field_validator("atol")
    @classmethod
    def _atol_range(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("tolerance atol must be >= 0")
        return v

    @field_validator("policy")
    @classmethod
    def _bounded_policy(cls, v: str) -> str:
        return v[:64]

    @model_validator(mode="after")
    def _at_least_one(self) -> "NumericalTolerance":
        if self.rtol is None and self.atol is None:
            raise ValueError("tolerance must declare rtol and/or atol")
        return self


# （原 ALGORITHM_TAXONOMY 已删除 —— 2026-09 VNext 死元数据清理：
# 该字典与 AlgorithmDescriptor.id 从不匹配、无运行时消费方；域分组
# 事实源现在是 app/lib/gis/algorithms/ 域包 + descriptor.category。


class BackendVariant(BaseModel):
    """同一算法的一个实现变体（§28：Algorithm → Implementation Variant）。

    所有变体必须通过同一 conformance 套件；resolver 可按规模/环境在
    变体间选择（tool_candidates 顺序即默认偏好序）。

    V2（Foundation）：``min_features``/``max_features`` 是该变体的适用
    规模窗口（特征数，None = 无界）。backend_selection 层据此做确定性
    选择并把决策写入证据块 —— 变体不再是纯 metadata。
    """

    id: str  # 变体内唯一（如 "numpy_batched"）
    backend: str  # BACKEND_VOCABULARY
    tool: str = ""  # 绑定的工具实现（可空 = lib 内部）
    deterministic: bool = True
    notes: str = ""
    min_features: Optional[int] = None  # 变体适用规模下界（含）
    max_features: Optional[int] = None  # 变体适用规模上界（含）
    # V3（ADR-0117）：变体级精度分类 —— exact 与 approximate 变体共存时
    # backend 选择层据此披露近似语义（空 = 未声明，随 descriptor）。
    approximation_class: ApproximationClass = ""

    @field_validator("notes")
    @classmethod
    def _bounded_notes(cls, v: str) -> str:
        return v[:160]

    @model_validator(mode="after")
    def _scale_window_consistent(self) -> "BackendVariant":
        lo, hi = self.min_features, self.max_features
        if lo is not None and lo < 0:
            raise ValueError("backend variant min_features must be >= 0")
        if hi is not None and hi < 1:
            raise ValueError("backend variant max_features must be >= 1")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(
                f"backend variant {self.id!r}: min_features {lo} > max_features {hi}"
            )
        return self


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
    algorithm_family: str = ""  # 如 "kriging" / "spatial_autocorrelation"
    method_references: List[str] = Field(
        default_factory=list
    )  # method_references.py id
    assumptions: List[str] = Field(default_factory=list)  # 进证据块
    limitations: List[str] = Field(default_factory=list)
    crs_class: CRSSpatialClass = ""  # resolver CRS 硬门
    scientific_preconditions: List[str] = Field(default_factory=list)
    uncertainty_outputs: List[str] = Field(default_factory=list)  # uncertainty 词表
    random_seed_policy: RandomSeedPolicy = "deterministic"
    numerical_tolerance: str = ""  # 容差声明（有界文本）
    scientific_status: ScientificStatus = ""  # 与 runtime_status 正交：验证强度
    conformance_tests: List[str] = Field(default_factory=list)  # pytest 节点 id
    backend_variants: List[BackendVariant] = Field(default_factory=list)
    # target_id → 科学等价性分类；键必须是 fallback_algorithms 成员。
    fallback_semantics: Dict[str, FallbackSemanticsClass] = Field(default_factory=dict)
    # ── Backend SDK V3（ADR-0117）：全部 additive，缺省 = 存量语义不变 ──
    approximation_class: ApproximationClass = ""  # 算法级精度分类
    resource_envelope: Optional[ResourceEnvelope] = None  # 声明式资源包络
    tolerance: Optional[NumericalTolerance] = None  # 结构化数值容差
    cancellation_profile: CancellationProfile = ""  # 协作式取消响应能力
    # ── Wave 8（不确定性契约）：declared uncertainty → producer test ────
    # 键 = uncertainty_outputs 成员；值 = 真实产出该不确定性类型并断言其
    # 形状/数值的 conformance 测试节点（AST 存在性校验，同 conformance_tests）。
    # 缺省空表 = 不约束存量算法；声明即机器可查（审计 G1 缺口的闭环）。
    uncertainty_producer_tests: Dict[str, str] = Field(default_factory=dict)

    @field_validator("uncertainty_producer_tests")
    @classmethod
    def _bounded_producer_tests(cls, v: Dict[str, str]) -> Dict[str, str]:
        if len(v) > 6:
            raise ValueError("uncertainty_producer_tests exceeds 6 entries")
        for key, node in v.items():
            # review R1-3：静默截断会让键与 uncertainty_outputs 失配，
            # 产生难排查的 validate 报错 —— 超限直接拒绝。
            if len(str(key)) > 32:
                raise ValueError(
                    f"uncertainty_producer_tests key too long: {key!r}")
            if len(str(node)) > 220:
                raise ValueError(
                    f"uncertainty_producer_tests node too long: {node!r}")
        return dict(v)

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
        self._tool_to_algorithms_cache: Optional[Dict[str, List[str]]] = None
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
        self._tool_to_algorithms_cache = None
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

    def unregister(self, algorithm_id: str) -> bool:
        """ADR-0104：扩展卸载回滚用。清理 by-id 与 by-capability 索引并
        失效全部派生缓存；目标不存在返回 False（幂等）。核心种子算法
        从不调用——生命周期由 load_builtins 决定。"""
        if algorithm_id not in self._by_id:
            return False
        algo = self._by_id.pop(algorithm_id)
        for cap in algo.capabilities:
            candidates = self._by_capability.get(cap)
            if candidates and algorithm_id in candidates:
                candidates.remove(algorithm_id)
                if not candidates:
                    self._by_capability.pop(cap, None)
        self._tool_to_capability_cache = None
        self._tool_to_algorithms_cache = None
        return True

    def has(self, algorithm_id: str) -> bool:
        return algorithm_id in self._by_id

    def algorithms_for_capability(
        self,
        capability: str,
        *,
        include_planned: bool = False,
    ) -> List[AlgorithmDescriptor]:
        ids = self._by_capability.get(capability, [])
        algos = [self._by_id[i] for i in ids]
        if not include_planned:
            # V3 修复：参数语义是 include_planned —— planned 与 unavailable
            # 都属"不可运行"，一律过滤（此前只滤 unavailable，planned 走漏
            # 到调用方再被 resolver 原生门拒绝，命名与行为不符）。
            algos = [
                a for a in algos if a.runtime_status not in ("planned", "unavailable")
            ]
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

        只派发**分析语义成立**的算法：主 capability 非 native（planned /
        unavailable —— 如平台工具面绑定 platform.*）的算法不进入本索引，
        其候选工具因此不被 dispatch 复用层当作可复用分析（非分析工具
        恒真实执行，见 test_non_analysis_tool_never_reused 契约）。

        #1076(D-8): 注册表载入后静态 —— 结果按内容缓存，register 失效。
        此前 webgis_map_product 每调用、session_plan 每工具结果都全量
        重建（每算法两遍排序扫描）。
        """
        cached = self._tool_to_capability_cache
        if cached is not None:
            return cached
        capabilities = get_capability_registry()
        ordered = sorted(self._by_id.values(), key=lambda a: (a.priority, a.id))
        mapping: Dict[str, str] = {}
        for algo in ordered:
            cap = algo.capabilities[0] if algo.capabilities else ""
            if cap and algo.tool_candidates and self._is_analysis_capability(cap, capabilities):
                mapping.setdefault(algo.tool_candidates[0], cap)
        for algo in ordered:
            cap = algo.capabilities[0] if algo.capabilities else ""
            if not cap or not self._is_analysis_capability(cap, capabilities):
                continue
            for tool in algo.tool_candidates:
                mapping.setdefault(tool, cap)
        self._tool_to_capability_cache = mapping
        return mapping

    @staticmethod
    def _is_analysis_capability(cap: str, capabilities) -> bool:
        """capability 缺席（未注册，容错）或 native 才算分析语义派生源。"""
        descriptor = capabilities.get(cap)
        return descriptor is None or descriptor.status == "native"

    def tool_to_algorithms(self) -> Dict[str, List[str]]:
        """派生的 tool → 关联算法 id 列表反查索引（ADR-0103 descriptor 回填用）。

        与 tool_to_capability 同门：注册表静态后按内容缓存，register 失效；
        顺序 = (priority, id) 稳定序（capability_tool_map 同款语义）。
        """
        cached = self._tool_to_algorithms_cache
        if cached is not None:
            return cached
        ordered = sorted(self._by_id.values(), key=lambda a: (a.priority, a.id))
        mapping: Dict[str, List[str]] = {}
        for algo in ordered:
            for tool in algo.tool_candidates:
                bucket = mapping.setdefault(tool, [])
                if algo.id not in bucket:
                    bucket.append(algo.id)
        self._tool_to_algorithms_cache = mapping
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
                    continue
                # V3（A1）：算法输出必须是所属能力声明输出的成员 —— 能力层
                # output_artifact_types 是消费方（规划/校验/地图模型适配）的
                # 合同，算法层漂移出去等于绕过合同。
                cap_descriptor = capabilities.get(cap)
                cap_outputs = list(
                    getattr(cap_descriptor, "output_artifact_types", []) or []
                )
                if (
                    cap_outputs
                    and algo.output_artifact_type
                    and algo.output_artifact_type not in cap_outputs
                ):
                    issues.append(
                        f"algorithm {algo.id}: output artifact "
                        f"{algo.output_artifact_type} not declared by capability "
                        f"{cap} (declared: {cap_outputs})"
                    )
            if algo.output_artifact_type and not artifact_types.has(
                algo.output_artifact_type
            ):
                issues.append(
                    f"algorithm {algo.id}: unknown output artifact {algo.output_artifact_type}"
                )
            for ref in algo.input_artifact_types:
                if not artifact_types.has(ref):
                    issues.append(f"algorithm {algo.id}: unknown input artifact {ref}")
            if algo.runtime_status == "native" and not algo.tool_candidates:
                issues.append(f"algorithm {algo.id}: native but no tool candidates")
            if available_tools is not None and algo.runtime_status == "native":
                missing = [t for t in algo.tool_candidates if t not in available_tools]
                if missing:
                    issues.append(
                        f"algorithm {algo.id}: tools not registered: {missing}"
                    )
            for fb in algo.fallback_algorithms:
                if fb not in self._by_id:
                    issues.append(
                        f"algorithm {algo.id}: fallback algorithm {fb} not registered"
                    )
            # V2(P3) 契约一致性：unit_requirements 只接受已知单位词
            # （封闭词表）；自由字符串等于永远无人可消费的死 metadata。
            # （approximate 与 deterministic 正交：前者是精度折衷，后者是
            # 可复现性 —— 不做静态矛盾判定，§27 的随机性披露由 descriptor
            # 声明者负责。）
            if (
                algo.unit_requirements
                and algo.unit_requirements not in _UNIT_VOCABULARY
            ):
                issues.append(
                    f"algorithm {algo.id}: unknown unit_requirements "
                    f"'{algo.unit_requirements}' (vocabulary: {sorted(_UNIT_VOCABULARY)})"
                )
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

            contract = get_parameter_contract_registry().get(
                algo.parameter_contract_ref
            )
            if contract is None:
                issues.append(
                    f"algorithm {algo.id}: parameter_contract_ref "
                    f"'{algo.parameter_contract_ref}' not registered"
                )
            elif not contract.parameters:
                issues.append(
                    f"algorithm {algo.id}: parameter contract "
                    f"'{algo.parameter_contract_ref}' has zero parameters"
                )
        if algo.method_references:
            from app.lib.gis.method_references import reference_exists

            for ref in algo.method_references:
                if not reference_exists(ref):
                    issues.append(
                        f"algorithm {algo.id}: unknown method reference {ref}"
                    )
        if algo.scientific_preconditions:
            from app.lib.gis.scientific_preconditions import precondition_exists

            for pid in algo.scientific_preconditions:
                if not precondition_exists(pid):
                    issues.append(
                        f"algorithm {algo.id}: unknown scientific precondition {pid}"
                    )
        if algo.uncertainty_outputs:
            from app.lib.gis.uncertainty import UNCERTAINTY_TYPE_VOCABULARY

            for u in algo.uncertainty_outputs:
                if u not in UNCERTAINTY_TYPE_VOCABULARY:
                    issues.append(
                        f"algorithm {algo.id}: unknown uncertainty output {u}"
                    )
        # 复现策略与 deterministic 声明一致性：
        #   "deterministic"（无随机）⇒ 必须 deterministic=True；
        #   "unseeded"（随机不可控）⇒ 必须 deterministic=False；
        #   "none"（方法无随机成分、种子不适用；复现性告警走 limitations）
        #   / "fixed_seed"（内部固定种子，逐次可复现）/ "caller_seeded"
        #   （种子是参数）与两旗兼容。
        if algo.random_seed_policy == "deterministic" and not algo.deterministic:
            issues.append(
                f"algorithm {algo.id}: deterministic=False 不得声明 deterministic 种子策略"
            )
        if algo.random_seed_policy == "unseeded" and algo.deterministic:
            issues.append(f"algorithm {algo.id}: deterministic=True 与 unseeded 矛盾")
        # backend_variants：后端词表 + 实现存在性（native 才谈变体）
        for variant in algo.backend_variants:
            if variant.backend not in BACKEND_VOCABULARY:
                issues.append(
                    f"algorithm {algo.id}: variant {variant.id} backend "
                    f"'{variant.backend}' not in vocabulary"
                )
        if (
            algo.backend_variants
            and algo.runtime_status == "native"
            and not algo.tool_candidates
        ):
            issues.append(
                f"algorithm {algo.id}: native with backend_variants but no tools"
            )
        # fallback 语义：键合法 + not_allowed 不得同时是可自动回退目标
        for target, semantics in algo.fallback_semantics.items():
            if target not in algo.fallback_algorithms:
                issues.append(
                    f"algorithm {algo.id}: fallback_semantics key {target} "
                    f"不在 fallback_algorithms 里"
                )
            if semantics == "not_allowed":
                issues.append(
                    f"algorithm {algo.id}: fallback {target} 标记 not_allowed "
                    f"却列在 fallback_algorithms（resolver 会自动采用）"
                )
        for target in algo.fallback_algorithms:
            if target not in algo.fallback_semantics:
                issues.append(
                    f"algorithm {algo.id}: fallback {target} 缺科学等价性声明 "
                    f"(fallback_semantics)"
                )
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
                f"algorithm {algo.id}: DEPRECATED 必须给出 fallback（否则规划死端）"
            )
        # conformance 节点：仓库布局可用时校验文件存在性 + **节点级**
        # 存在性（评审 M1：文件级检查放过节点改名腐烂 —— VALIDATED 的
        # 可审计承诺必须钉到真实测试函数）。确定性 AST 解析，零导入。
        if algo.conformance_tests:
            issues.extend(self._check_test_nodes(
                algo, list(algo.conformance_tests), "conformance test"))
        # ── Wave 8（不确定性契约）：declared uncertainty → producer test。
        # 键必须是 uncertainty_outputs 成员；值节点经同一 AST 校验 ——
        # 「声明了不确定性就必须有真实产出并断言它的测试」机器可查。
        if algo.uncertainty_producer_tests:
            for u in algo.uncertainty_producer_tests:
                if u not in algo.uncertainty_outputs:
                    issues.append(
                        f"algorithm {algo.id}: uncertainty_producer_tests key "
                        f"{u!r} not declared in uncertainty_outputs")
            issues.extend(self._check_test_nodes(
                algo, list(algo.uncertainty_producer_tests.values()),
                "uncertainty producer test"))
        # ── Backend SDK V3（ADR-0117）：additive —— 只约束显式声明 ────
        issues.extend(self._validate_backend_sdk(algo))
        return issues

    @staticmethod
    def _check_test_nodes(
        algo: AlgorithmDescriptor, nodes: List[str], kind: str,
    ) -> List[str]:
        """测试节点存在性（文件 + AST 级逐段校验；确定性、零导入）。"""
        import ast
        import os

        issues: List[str] = []
        if not os.path.isdir("tests"):
            return issues
        for node in nodes:
            path, _, func = node.partition("::")
            if not path.startswith("tests/") or not os.path.exists(path):
                if path.startswith("tests/"):
                    issues.append(
                        f"algorithm {algo.id}: {kind} file missing: {path}")
                continue
            if func:
                try:
                    tree = ast.parse(open(path, encoding="utf-8").read())
                except (OSError, SyntaxError):
                    continue
                names = {
                    n.name
                    for n in ast.walk(tree)
                    if isinstance(
                        n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    )
                }
                # 节点路径可为 file::func 或 file::Class::method ——
                # 逐段存在性校验。
                segments = [s for s in func.split("::") if s]
                if any(s not in names for s in segments):
                    issues.append(
                        f"algorithm {algo.id}: {kind} node missing: {node}")
        return issues

    @staticmethod
    def _validate_backend_sdk(algo: AlgorithmDescriptor) -> List[str]:
        """V3 新字段的一致性校验（缺省字段零约束，存量算法不迁移）。"""
        issues: List[str] = []
        if algo.approximation_class:
            expects_flag = (
                algo.approximation_class in _APPROXIMATION_CLASSES_REQUIRING_FLAG
            )
            if expects_flag and not algo.approximate:
                issues.append(
                    f"algorithm {algo.id}: approximation_class "
                    f"'{algo.approximation_class}' 需要 approximate=True"
                )
            if algo.approximation_class == "exact" and algo.approximate:
                issues.append(
                    f"algorithm {algo.id}: approximation_class 'exact' "
                    f"与 approximate=True 矛盾"
                )
        for variant in algo.backend_variants:
            if (
                variant.approximation_class
                and variant.approximation_class not in APPROXIMATION_CLASS_VOCABULARY
            ):
                issues.append(
                    f"algorithm {algo.id}: variant {variant.id} unknown "
                    f"approximation_class {variant.approximation_class!r}"
                )
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
