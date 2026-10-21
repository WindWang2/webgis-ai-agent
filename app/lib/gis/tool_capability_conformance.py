"""Tool Capability Conformance —— 单工具 × 8 消费面的机器对账报告（G03）。

工具能力元数据有 8 个消费面（注册 / 搜索 / 规划 / Pi native / proxy /
dispatch / 结果 / 科学语义），此前各面各有校验器（``capability_conformance``
管声明面、``execution_catalog_conformance`` 管目录链、``quality manifest``
管覆盖率软闸），但**没有一份 per-tool 的跨面对账投影**——「这个工具在
每个面上一致吗、不一致在哪、证据是什么」无处可查。

本模块把对账收敛为**纯函数审计器**：

- 输入全部依赖注入（registry 投影 + 各权威 registry 的只读 facts），
  零全局单例、零 I/O —— 同输入跨进程逐字节同输出；
- 每面裁决 ``pass | warning | fail | not_evaluated``（not_evaluated ≠
  pass：缺证据就如实说缺，不虚构覆盖）+ 稳定 reason code + 有界 detail
  + 来源权威面标注；
- 报告带指纹（canonical JSON SHA-256 短摘要）与 registry generation
  来源锚，可做漂移检测；
- 产出排序确定、条数有界；只读，绝不反写 registry。

红线（与 execution_catalog / tool_surface_v2 同款纪律）：

- 本模块是既有 registry 的**有界只读投影**，不是第二注册中心；
- **不接管任何裁决**：turn 级资格（pi_surface_receipt）、执行闸
  （registry.dispatch chokepoint）、启动 fail-fast（runtime_manifest）
  的权威归属不变——报告是披露面，fail 不进启动闸（G03 DECISIONS D2）；
- 判定条件与既有校验器同源同判（capability 悬空 = 词表成员检查，与
  capability_conformance.CODE_ID_DANGLING 同一语义），不产生第二真相。

分级哲学：

- **fail** = 声明的契约在静态上已经破损（悬空引用 / 不可达 / 投影被
  逐出）：任何环境下这个工具的这一面都兑现不了；
- **warning** = 完整度 / 两侧漂移（元数据缺席、schema 与参数契约分叉、
  搜索面漏激活风险）：可用但有披露义务；
- **not_evaluated** = 该面对此工具构造性不适用（tier-3 无关键词面、
  非 native 前门、无参数契约）或证据缺席——诚实留白。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "STATUS_PASS",
    "STATUS_WARNING",
    "STATUS_FAIL",
    "STATUS_NOT_EVALUATED",
    "CONFORMANCE_FACES",
    "FACE_REGISTRATION",
    "FACE_SEARCH",
    "FACE_PLANNER",
    "FACE_PI_NATIVE",
    "FACE_PROXY",
    "FACE_DISPATCH",
    "FACE_RESULT",
    "FACE_SCIENCE",
    "FaceVerdict",
    "ToolConformanceReport",
    "RegistryConformanceSummary",
    "ConformanceFacts",
    "collect_live_facts",
    "audit_tool_conformance",
    "audit_registry_conformance",
    "FALLBACK_CODE_XWALK",
    "MAX_REPORTS",
    "MAX_DETAIL_CHARS",
    "MAX_SCIENCE_ISSUES",
]

# ── 裁决词表（封闭） ──────────────────────────────────────────────

STATUS_PASS = "pass"
STATUS_WARNING = "warning"
STATUS_FAIL = "fail"
STATUS_NOT_EVALUATED = "not_evaluated"

#: 8 个消费面（排序即报告输出序）。
FACE_REGISTRATION = "registration"
FACE_SEARCH = "search"
FACE_PLANNER = "planner"
FACE_PI_NATIVE = "pi_native"
FACE_PROXY = "proxy"
FACE_DISPATCH = "dispatch"
FACE_RESULT = "result"
FACE_SCIENCE = "science"

CONFORMANCE_FACES: Tuple[str, ...] = (
    FACE_REGISTRATION, FACE_SEARCH, FACE_PLANNER, FACE_PI_NATIVE,
    FACE_PROXY, FACE_DISPATCH, FACE_RESULT, FACE_SCIENCE,
)

#: 每面来源权威（披露用；不是可写接口）。
_FACE_SOURCES: Dict[str, str] = {
    FACE_REGISTRATION: "ToolRegistry._metadata/ToolDescriptor",
    FACE_SEARCH: "ToolCatalog.DOMAIN_KEYWORDS",
    FACE_PLANNER: "CapabilityRegistry+AlgorithmRegistry",
    FACE_PI_NATIVE: "PiToolSpecV1.compile_tool_spec",
    FACE_PROXY: "registry.dispatch/webgis_execute",
    FACE_DISPATCH: "registry.dispatch/capability_policy",
    FACE_RESULT: "ToolDescriptor.io_contract",
    FACE_SCIENCE: "ParameterContractRegistry×args_schema",
}

# ── 有界预算 ─────────────────────────────────────────────────────

#: 全 registry 审计的报告条数上界（超出截断并在 summary 披露，不静默）。
MAX_REPORTS = 1024
#: 单条 detail 文本上界。
MAX_DETAIL_CHARS = 160
#: science 面单工具披露的契约分叉条数上界。
MAX_SCIENCE_ISSUES = 4
#: reason code 长度上界（词表自封，防御性）。
_MAX_REASON_CHARS = 48

# ── typed fallback 码表交叉引用（G03 WP-C.9） ─────────────────────
#
# 回退路径的 typed failure 不新造码：每个语义都已有权威生产者。本表把
# G03 审计面的回退语义 → 既有码钉成冻结映射，parity 测试保证目标码
# 真实存在（词表演进时目标码缺席即测试红）。新增回退语义时先找既有码，
# 确实没有生产者才走 dispatch_reason_codes 的登记流程（新码必须带真实
# 生产者，禁止空挂码位）。
FALLBACK_CODE_XWALK: Tuple[Tuple[str, str, str], ...] = (
    # (回退语义, 权威层, 既有码)
    ("credential_absent", "pi_surface_receipt", "credential_absent"),
    ("credential_absent", "registry_dispatch", "CREDENTIALS_REQUIRED"),
    ("offline", "pi_surface_receipt", "offline_network_tool"),
    ("offline", "capability_reasons", "offline_network_required"),
    ("unsupported_method", "registry_dispatch", "TOOL_NOT_EXECUTABLE"),
    ("stale_surface", "pi_bridge_surface_gate", "surface_stale"),
    ("stale_surface", "pi_bridge_surface_gate", "SURFACE_FINGERPRINT_MISMATCH"),
    ("data_requirement_missing", "pi_input_gate", "SCHEMA_VALIDATION_REJECTED"),
    ("data_requirement_missing", "parameter_contracts", "parameter_contract_violation"),
)


# ── 数据形状 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class FaceVerdict:
    """一个消费面的裁决（排序键 = face）。"""

    face: str
    status: str
    #: 稳定 reason code；pass 时空串。
    reason: str = ""
    detail: str = ""
    #: 该面裁决所读的权威来源（模块级标注，非可写接口）。
    source: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "face": self.face,
            "status": self.status,
            "reason": self.reason[:_MAX_REASON_CHARS],
            "detail": self.detail[:MAX_DETAIL_CHARS],
            "source": self.source,
        }


@dataclass(frozen=True)
class ToolConformanceReport:
    """单工具的 8 面对账报告。"""

    tool: str
    tool_id: str
    overall: str
    faces: Tuple[FaceVerdict, ...]
    #: 报告指纹：裁决面（face/status/reason）的 canonical JSON 短摘要。
    fingerprint: str

    def face(self, name: str) -> FaceVerdict:
        for v in self.faces:
            if v.face == name:
                return v
        raise KeyError(name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "tool_id": self.tool_id,
            "overall": self.overall,
            "faces": [v.to_dict() for v in self.faces],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RegistryConformanceSummary:
    """全 registry 审计的有界汇总。"""

    tool_count: int
    by_overall: Dict[str, int]
    #: reason code → 出现次数（warning/fail 面，降序截断）。
    top_reasons: Tuple[Tuple[str, int], ...]
    reports: Tuple[ToolConformanceReport, ...]
    truncated: bool
    #: 来源锚：runtime manifest fingerprint（缺席诚实空串）。
    registry_generation: str

    def to_dict(self, *, include_reports: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "tool_count": self.tool_count,
            "by_overall": dict(sorted(self.by_overall.items())),
            "top_reasons": [[r, n] for r, n in self.top_reasons],
            "truncated": self.truncated,
            "registry_generation": self.registry_generation,
        }
        if include_reports:
            out["reports"] = [r.to_dict() for r in self.reports]
        return out


@dataclass(frozen=True)
class ConformanceFacts:
    """审计输入事实（全部由调用方收集；引擎零 I/O）。

    各字段对应的权威源见 ``_FACE_SOURCES``；测试可全部注入合成值。
    """

    registry: Any
    #: CapabilityRegistry 词表（权威）。
    capability_ids: Tuple[str, ...] = ()
    #: AlgorithmRegistry 描述符迭代（capabilities/tool_candidates/
    #: parameter_contract_ref）。
    algorithms: Tuple[Any, ...] = ()
    #: 契约 id → ParameterContract（``parameters`` 列表即可，鸭子类型）。
    parameter_contracts: Mapping[str, Any] = field(default_factory=dict)
    #: Pi native 前门名快照（pi_native_surface.NATIVE_TOOL_NAMES）。
    native_tool_names: Tuple[str, ...] = ()
    #: ToolCatalog.DOMAIN_KEYWORDS 快照（domain → 关键词列表）。
    domain_keywords: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: spec 编译器注入（默认绑定 compile_tool_spec；测试可替换）。
    spec_compiler: Optional[Callable[[Any, str], Any]] = None
    #: registry generation 来源锚（runtime manifest fingerprint；可空）。
    registry_generation: str = ""


# ── 小工具 ───────────────────────────────────────────────────────

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _short_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _detail(text: str) -> str:
    return str(text or "")[:MAX_DETAIL_CHARS]


def _verdict(face: str, status: str, reason: str = "", detail: str = "") -> FaceVerdict:
    return FaceVerdict(
        face=face, status=status, reason=reason or "",
        detail=_detail(detail), source=_FACE_SOURCES[face],
    )


def _schema_for(registry: Any, name: str) -> Optional[Dict[str, Any]]:
    """registry → 该工具的 OpenAI function parameters schema（无则 None）。"""
    try:
        subset = registry.get_schemas_subset({name})
    except Exception:  # noqa: BLE001 — 投影面防御：registry 缺 schema 面不崩
        return None
    if not subset:
        return None
    fn = subset[0].get("function", {}) or {}
    params = fn.get("parameters")
    return params if isinstance(params, dict) else None


# ── 各面审计器（纯函数） ─────────────────────────────────────────

def _audit_registration(
    descriptor: Any, facts: ConformanceFacts,
) -> FaceVerdict:
    """注册面：descriptor 契约的结构完整性与悬空引用。

    悬空引用（fallback_tool 指向未注册工具）= fail：执行期建议的替代
    工具不存在，契约在任何环境下兑现不了。deprecation_of 悬空是元数据
    面问题 = warning（deprecated 工具仍可执行）。
    """
    side_effect = str(getattr(descriptor.side_effect, "value", descriptor.side_effect) or "")
    gaps: List[str] = []
    if side_effect == "unclassified":
        gaps.append("side_effect")
    if getattr(descriptor, "network", None) is None:
        gaps.append("network")
    if getattr(descriptor, "deterministic", None) is None:
        gaps.append("deterministic")
    if str(getattr(descriptor, "result_size_policy", "") or "unknown") == "unknown":
        gaps.append("result_size_policy")

    fallback = str(getattr(descriptor, "fallback_tool", "") or "")
    if fallback and fallback not in facts.registry.tool_names():
        return _verdict(
            FACE_REGISTRATION, STATUS_FAIL, "fallback_tool_dangling",
            f"fallback_tool {fallback!r} not registered")

    deprecation_of = str(getattr(descriptor, "deprecation_of", "") or "")
    if deprecation_of and deprecation_of not in facts.registry.tool_names():
        gaps.append(f"deprecation_of_dangling:{deprecation_of}")

    if gaps:
        return _verdict(
            FACE_REGISTRATION, STATUS_WARNING, "metadata_incomplete",
            "missing: " + ",".join(gaps))
    return _verdict(FACE_REGISTRATION, STATUS_PASS, "descriptor_complete")


def _audit_search(descriptor: Any, facts: ConformanceFacts) -> FaceVerdict:
    """搜索面：ToolCatalog 选择语义的静态可判定部分。

    tier-2 工具靠 domain 关键词或显式 declared_domains 激活；无 domains
    或 domain 无关键词覆盖 = 漏激活风险（warning）。本地 OSM 抑制等运行
    时环境事实不进离线判定（诚实边界，G03 DECISIONS D7）。
    """
    tier = int(getattr(descriptor, "tier", 1) or 1)
    if tier >= 3:
        return _verdict(
            FACE_SEARCH, STATUS_NOT_EVALUATED, "tier3_explicit_only",
            "tier-3 selection is explicit-only; keyword face not applicable")
    if tier <= 1:
        return _verdict(FACE_SEARCH, STATUS_PASS, "always_visible")
    domains = tuple(getattr(descriptor, "domains", ()) or ())
    if not domains:
        return _verdict(
            FACE_SEARCH, STATUS_WARNING, "tier2_no_domains",
            "tier-2 without domains: only explicit declared_domains can activate")
    uncovered = [d for d in domains if not facts.domain_keywords.get(d)]
    if uncovered:
        return _verdict(
            FACE_SEARCH, STATUS_WARNING, "domain_keywords_missing",
            f"no DOMAIN_KEYWORDS coverage: {','.join(uncovered[:4])}")
    return _verdict(FACE_SEARCH, STATUS_PASS, "domain_covered")


def _audit_planner(
    descriptor: Any, facts: ConformanceFacts,
    derived_capability_tools: Mapping[str, set],
) -> FaceVerdict:
    """规划面：capability/algorithm 链的可解析性（与既有校验器同源同判）。

    capability 悬空 = fail（与 capability_conformance.CODE_ID_DANGLING
    同一判定：词表成员检查）；声明无算法链支撑 = warning（合法态：交互/
    编排/巡检工具）；无绑定 = not_evaluated（纯 I/O 工具，规划面不适用）。
    """
    caps = tuple(str(c) for c in (getattr(descriptor, "capabilities", ()) or ()))
    dangling = [c for c in caps if c not in set(facts.capability_ids)]
    if dangling:
        return _verdict(
            FACE_PLANNER, STATUS_FAIL, "capability_id_dangling",
            f"declared capability not in vocabulary: {','.join(dangling[:3])}")
    if caps:
        backed = any(
            descriptor.name in derived_capability_tools.get(c, set()) for c in caps)
        if backed:
            return _verdict(FACE_PLANNER, STATUS_PASS, "algorithm_backed")
        return _verdict(
            FACE_PLANNER, STATUS_WARNING, "binding_unbacked",
            f"declared {','.join(caps[:3])} without algorithm tool_candidates")
    if str(getattr(descriptor, "capability_source", "") or "") == "derived:algorithm_registry":
        return _verdict(FACE_PLANNER, STATUS_PASS, "derived_from_algorithm_registry")
    return _verdict(
        FACE_PLANNER, STATUS_NOT_EVALUATED, "no_capability_binding",
        "no capabilities declared or derived; planner face not applicable")


def _audit_pi_native(
    descriptor: Any, facts: ConformanceFacts,
) -> Tuple[FaceVerdict, str]:
    """Pi native 面：仅 7 前门评估 spec 可编译性与字节预算。

    spec 编译失败/超预算 = fail（native 面逐出，与 pi_native_surface 的
    逐出语义一致）；非前门工具 = not_evaluated（proxy 面承载可达性）。
    返回 (verdict, schema_fingerprint)——指纹进报告（漂移检测）。
    """
    name = descriptor.name
    if name not in set(facts.native_tool_names):
        return _verdict(
            FACE_PI_NATIVE, STATUS_NOT_EVALUATED, "not_native_frontdoor",
            "long-tail tool; native face N/A (see proxy face)"), ""
    compiler = facts.spec_compiler
    if compiler is None:
        from app.services.chat.pi_tool_spec import compile_tool_spec

        compiler = compile_tool_spec
    has_schema = _schema_for(facts.registry, name) is not None
    if not has_schema:
        return _verdict(
            FACE_PI_NATIVE, STATUS_FAIL, "no_live_schema",
            "native frontdoor without live schema; excluded from native surface"), ""
    spec = compiler(facts.registry, name)
    if spec is None:
        return _verdict(
            FACE_PI_NATIVE, STATUS_FAIL, "spec_over_budget",
            "projected schema exceeds per-tool budget; excluded from native surface"), ""
    fp = str(getattr(spec, "schema_fingerprint", "") or "")
    return _verdict(
        FACE_PI_NATIVE, STATUS_PASS, "spec_compiled",
        f"schema {getattr(spec, 'schema_bytes', 0)}B within budget"), fp


def _audit_proxy(descriptor: Any) -> FaceVerdict:
    """proxy 面：webgis_execute 长尾可达性（经同一 dispatch chokepoint）。

    构造性口径（G03 DECISIONS D4）：registered ∧ status 可执行 = 可达。
    planned = fail（chokepoint 拒绝）；hidden/external_unavailable =
    warning（dispatch 可达但模型面生命周期投影逐出）。
    """
    status = str(getattr(descriptor.status, "value", descriptor.status) or "")
    if status == "planned":
        return _verdict(
            FACE_PROXY, STATUS_FAIL, "not_executable",
            "status=planned: dispatch chokepoint refuses execution")
    if status == "hidden":
        return _verdict(
            FACE_PROXY, STATUS_WARNING, "hidden_from_model_surface",
            "internal/explicit-API only; lifecycle projection hides from model surface")
    if status == "external_unavailable":
        return _verdict(
            FACE_PROXY, STATUS_WARNING, "external_unavailable",
            "declared external dependency unavailable; recovers automatically")
    return _verdict(FACE_PROXY, STATUS_PASS, "proxy_reachable")


def _audit_dispatch(descriptor: Any) -> FaceVerdict:
    """dispatch 面：执行闸输入事实的结构一致性。

    权威裁决（capability_policy / qualification）是运行时的，本面只做
    静态结构检查：凭证 id 必须是非空字符串（chokepoint 逐 id 对照
    presence，空 id 永远无法满足）。凭证/权限的实际可用性由 per-turn
    qualification 披露，不在离线报告虚构。
    """
    creds = tuple(getattr(descriptor, "requires_credentials", ()) or ())
    invalid = [c for c in creds if not isinstance(c, str) or not c.strip()]
    if invalid:
        return _verdict(
            FACE_DISPATCH, STATUS_FAIL, "credential_id_invalid",
            f"non-string/empty credential ids: {invalid[:2]!r}")
    notes: List[str] = []
    if creds:
        notes.append(f"credentials={','.join(sorted(creds)[:3])}")
    perm = str(getattr(descriptor, "required_permission", "") or "").strip()
    if perm:
        notes.append(f"permission={perm}")
    if int(getattr(descriptor, "tier", 1) or 1) >= 3:
        notes.append("tier3 confirmation gate applies")
    detail = "; ".join(notes) if notes else "no credential/permission requirements"
    return _verdict(FACE_DISPATCH, STATUS_PASS, "gate_facts_coherent", detail)


#: 输出语义类型 → dispatch chokepoint 是否**无条件**把 FC 形状结果铸成
#: geojson ref（tool_dispatch_service：顶层 FC / ``geojson`` 键 / ``data``
#: 包裹 FC 三种形状都入 session ref store）。集合只收**形状可证**的成员：
#: ``geojson_fc`` 与 FC 形状规则一一对应；更宽泛的 "geojson"（可能是裸
#: Feature）不满足任何形状条件，不纳入 —— 宁可 warning 也不虚构 pass。
_STRUCTURAL_REF_OUTPUT_TYPES = frozenset({"geojson_fc"})


def _audit_result(descriptor: Any) -> FaceVerdict:
    """结果面：输出契约的结构声明（ref-first 纪律的静态面）。

    ref_offload 声明却无 produced_refs 的判定分两档：

    - geojson 输出族 → **结构性 pass**：dispatch chokepoint 对 FC 形状
      结果无条件铸 ref（行为事实，非逐工具声明依赖）；
    - 其他输出族（text/stats/map_product…）→ warning：offload 行为依赖
      未声明的实现细节，produced_refs 缺席就是信封契约缺口。

    result_size_policy unknown / output_semantic_type 缺席 = 完整度
    warning。
    """
    policy = str(getattr(descriptor, "result_size_policy", "") or "unknown")
    produced = tuple(getattr(descriptor, "produced_refs", ()) or ())
    out_type = str(getattr(descriptor, "output_semantic_type", "") or "")
    if policy == "unknown":
        return _verdict(
            FACE_RESULT, STATUS_WARNING, "result_size_unknown",
            "result_size_policy not declared")
    if policy == "ref_offload" and not produced:
        if out_type in _STRUCTURAL_REF_OUTPUT_TYPES:
            return _verdict(
                FACE_RESULT, STATUS_PASS, "ref_structural_offload",
                f"{out_type} results are ref-offloaded at the dispatch chokepoint")
        return _verdict(
            FACE_RESULT, STATUS_WARNING, "ref_offload_without_ref_contract",
            "ref_offload declared without produced_refs contract")
    if not out_type:
        return _verdict(
            FACE_RESULT, STATUS_WARNING, "output_type_missing",
            "output_semantic_type not declared")
    return _verdict(FACE_RESULT, STATUS_PASS, "io_contract_declared")


def _audit_science(
    descriptor: Any, facts: ConformanceFacts,
    algorithm_by_id: Mapping[str, Any],
    contract_tools: Optional[Mapping[str, set]] = None,
) -> FaceVerdict:
    """科学语义面：args schema × ParameterContract 的离线交叉校验。

    契约（required/enum/min/max/unit）与 registry schema 是两份独立声明，
    此前零对账（G03 RECON §A.2）。本面逐参数比较声明面：

    - required 分叉：schema required 而契约有默认（或反之）——dispatch
      放行的调用会被 ``apply_contract`` 拒绝（或反之），两侧漂移；
    - enum 分叉：两侧枚举集不一致（schema enum 存在才比较）；
    - range 分叉：schema 范围比契约更宽（schema 最小值 < 契约最小值等）；
    - 契约参数在 schema 缺席：契约描述的算法参数工具不暴露（漂移披露）。

    **家族并集豁免**：一个契约可以服务一族算法/工具（如
    ``science_temporal_analysis`` 同时覆盖物候/立方体/异常三工具，
    ``network_od_matrix`` 同时覆盖离线与 amap provider）。某参数缺席于
    本工具 schema、但**同契约的兄弟工具**（``tool_candidates`` 家族）
    已暴露 → 家族并集分工，不是漂移（detail 披露豁免数）；全族无人
    暴露才是真死参数（如未接线的能力），照报 warning。

    unit 只在契约侧存在（schema 无单位通道），无第二真相可比较，不判。
    """
    algo_ids = tuple(str(a) for a in (getattr(descriptor, "algorithms", ()) or ()))
    contract_ids: List[str] = []
    for aid in algo_ids:
        algo = algorithm_by_id.get(aid)
        ref = str(getattr(algo, "parameter_contract_ref", "") or "") if algo else ""
        if ref:
            contract_ids.append(ref)
    if not contract_ids:
        return _verdict(
            FACE_SCIENCE, STATUS_NOT_EVALUATED, "no_parameter_contract",
            "no algorithm parameter_contract_ref for this tool")
    schema = _schema_for(facts.registry, descriptor.name)
    if schema is None:
        return _verdict(
            FACE_SCIENCE, STATUS_NOT_EVALUATED, "no_args_schema",
            "tool without args schema; cross-check not applicable")
    properties = schema.get("properties") if isinstance(schema, dict) else None
    properties = properties if isinstance(properties, dict) else {}
    required = schema.get("required") if isinstance(schema, dict) else None
    required = {str(r) for r in required} if isinstance(required, (list, tuple)) else set()

    def _sibling_exposes(pname: str, cid: str) -> bool:
        """同契约家族中是否有别的工具在 schema 里暴露该参数。"""
        if not contract_tools:
            return False
        for sibling in contract_tools.get(cid, ()):
            if sibling == descriptor.name:
                continue
            s_schema = _schema_for(facts.registry, sibling)
            if not isinstance(s_schema, dict):
                continue
            s_props = s_schema.get("properties")
            if isinstance(s_props, dict) and pname in s_props:
                return True
        return False

    issues: List[str] = []
    family_waived = 0
    for cid in dict.fromkeys(contract_ids):
        contract = facts.parameter_contracts.get(cid)
        if contract is None:
            issues.append(f"contract {cid} declared but not registered")
            continue
        for spec in getattr(contract, "parameters", ()) or ():
            pname = str(getattr(spec, "name", ""))
            prop = properties.get(pname)
            if not isinstance(prop, dict):
                if _sibling_exposes(pname, cid):
                    family_waived += 1
                    continue
                issues.append(f"{cid}.{pname}: absent from schema")
                continue
            is_required = pname in required
            contract_required = bool(getattr(spec, "required", False))
            has_default = getattr(spec, "default", None) is not None
            if is_required and not contract_required and has_default:
                issues.append(f"{cid}.{pname}: schema required, contract has default")
            elif not is_required and contract_required:
                issues.append(f"{cid}.{pname}: contract required, schema optional")
            enum_values = getattr(spec, "enum_values", None) or []
            schema_enum = prop.get("enum")
            if enum_values and isinstance(schema_enum, (list, tuple)):
                if {str(e) for e in schema_enum} != {str(e) for e in enum_values}:
                    issues.append(f"{cid}.{pname}: enum sets diverge")
            for bound, contract_key in (
                ("below contract minimum", "minimum"),
                ("above contract maximum", "maximum"),
            ):
                c_val = getattr(spec, contract_key, None)
                s_val = prop.get(contract_key)
                if c_val is None or not isinstance(s_val, (int, float)):
                    continue
                diverges = (
                    float(s_val) < float(c_val) if contract_key == "minimum"
                    else float(s_val) > float(c_val)
                )
                if diverges:
                    issues.append(f"{cid}.{pname}: schema {contract_key} {s_val} {bound} {c_val}")
                    break
            if len(issues) >= MAX_SCIENCE_ISSUES:
                break
        if len(issues) >= MAX_SCIENCE_ISSUES:
            break
    if issues:
        more = f" (+{len(issues) - MAX_SCIENCE_ISSUES} more)" if len(issues) > MAX_SCIENCE_ISSUES else ""
        return _verdict(
            FACE_SCIENCE, STATUS_WARNING, "contract_divergence",
            "; ".join(issues[:MAX_SCIENCE_ISSUES]) + more)
    detail = f"contracts {','.join(dict.fromkeys(contract_ids))} aligned with schema"
    if family_waived:
        detail += f"; {family_waived} param(s) via family-union siblings"
    return _verdict(FACE_SCIENCE, STATUS_PASS, "contract_aligned", detail)


# ── 审计入口 ─────────────────────────────────────────────────────

def _overall(faces: Sequence[FaceVerdict]) -> str:
    """聚合序：fail > warning > pass >（全 not_evaluated 才是）not_evaluated。

    not_evaluated 不参与「压过」：一个 pass 面就足以让 overall = pass
    （混合 pass/not_evaluated = 已有可信面）；只有全部面都缺证据时才
    整体 not_evaluated（诚实留白，不虚构覆盖）。
    """
    statuses = {v.status for v in faces}
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_WARNING in statuses:
        return STATUS_WARNING
    if STATUS_PASS in statuses:
        return STATUS_PASS
    return STATUS_NOT_EVALUATED


def audit_tool_conformance(
    name: str, facts: ConformanceFacts,
) -> ToolConformanceReport:
    """编译单工具的 8 面对账报告（纯函数；工具缺席抛 KeyError——调用方
    先做词表检查，缺席不是「不合格」是「不存在」，不虚构报告）。"""
    descriptor = facts.registry.descriptor(str(name))
    derived_capability_tools: Dict[str, set] = {}
    algorithm_by_id: Dict[str, Any] = {}
    contract_tools: Dict[str, set] = {}
    for algo in facts.algorithms:
        aid = str(getattr(algo, "id", "") or "")
        if aid:
            algorithm_by_id[aid] = algo
        candidates = {str(t) for t in (getattr(algo, "tool_candidates", None) or ())}
        for cap in getattr(algo, "capabilities", None) or ():
            derived_capability_tools.setdefault(str(cap), set()).update(candidates)
        ref = str(getattr(algo, "parameter_contract_ref", "") or "")
        if ref:
            contract_tools.setdefault(ref, set()).update(candidates)

    pi_verdict, schema_fp = _audit_pi_native(descriptor, facts)
    face_verdicts = (
        _audit_registration(descriptor, facts),
        _audit_search(descriptor, facts),
        _audit_planner(descriptor, facts, derived_capability_tools),
        pi_verdict,
        _audit_proxy(descriptor),
        _audit_dispatch(descriptor),
        _audit_result(descriptor),
        _audit_science(descriptor, facts, algorithm_by_id, contract_tools),
    )
    faces = tuple(sorted(face_verdicts, key=lambda v: v.face))
    payload = {
        "tool_id": descriptor.tool_id,
        "faces": [[v.face, v.status, v.reason] for v in faces],
    }
    fingerprint = _short_digest(_canonical(payload))
    if schema_fp:
        fingerprint = _short_digest(_canonical({**payload, "schema_fp": schema_fp}))
    return ToolConformanceReport(
        tool=descriptor.name,
        tool_id=descriptor.tool_id,
        overall=_overall(faces),
        faces=faces,
        fingerprint=fingerprint,
    )


def audit_registry_conformance(
    facts: ConformanceFacts,
    *,
    tool_names: Optional[Iterable[str]] = None,
) -> RegistryConformanceSummary:
    """全 registry（或指定子集）审计；排序确定、条数有界。"""
    names = sorted(tool_names) if tool_names is not None else list(facts.registry.tool_names())
    reports: List[ToolConformanceReport] = []
    truncated = False
    for name in names:
        if len(reports) >= MAX_REPORTS:
            truncated = True
            break
        try:
            reports.append(audit_tool_conformance(name, facts))
        except Exception as exc:  # noqa: BLE001 — 单工具审计缺陷不得中断全量报告
            logger.warning(
                "[tool-conformance] audit raised for %s: %s: %s",
                name, type(exc).__name__, exc, exc_info=True)
            # 降级报告保持 8 面不变式（消费者按 face 名取值，缺面=KeyError）：
            # 全面 not_evaluated + registration 面 fail，异常细节进日志与 detail。
            reports.append(ToolConformanceReport(
                tool=name, tool_id=f"{name}@?", overall=STATUS_FAIL,
                faces=tuple(sorted(
                    (
                        _verdict(
                            f,
                            STATUS_FAIL if f == FACE_REGISTRATION
                            else STATUS_NOT_EVALUATED,
                            "audit_error",
                            f"auditor raised {type(exc).__name__}; see warning log",
                        ) for f in CONFORMANCE_FACES
                    ), key=lambda v: v.face)),
                fingerprint=""))
    by_overall: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    for r in reports:
        by_overall[r.overall] = by_overall.get(r.overall, 0) + 1
        for v in r.faces:
            if v.status in (STATUS_WARNING, STATUS_FAIL) and v.reason:
                reason_counts[v.reason] = reason_counts.get(v.reason, 0) + 1
    top_reasons = tuple(
        sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:16])
    return RegistryConformanceSummary(
        tool_count=len(reports),
        by_overall=by_overall,
        top_reasons=top_reasons,
        reports=tuple(reports),
        truncated=truncated,
        registry_generation=str(facts.registry_generation or ""),
    )


# ── 活 registry facts 收集器（只读；CLI / 工具面 / 测试复用） ────

def collect_live_facts(
    registry: Optional[Any] = None,
    *,
    bootstrap: bool = False,
) -> ConformanceFacts:
    """从既有单例收集审计事实（零写入；与 compile_quality_manifest 同款
    离线引导口径）。

    ``bootstrap=True`` 时用 ``ToolRegistry() + init_tools`` 离线构建完整
    注册面（脚本场景）；否则要求传入已引导的 registry。
    """
    if registry is None:
        if bootstrap:
            from app.tools import init_tools
            from app.tools.registry import ToolRegistry

            registry = ToolRegistry()
            init_tools(registry)
        else:
            from app.agent_pi_bridge import try_get_tool_registry

            registry = try_get_tool_registry()
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.parameter_contracts import get_parameter_contract_registry

    algo_reg = get_algorithm_registry()
    cap_reg = get_capability_registry()
    contracts = get_parameter_contract_registry()
    contracts_by_id = {
        cid: contracts.get(cid) for cid in contracts.all_ids
    }
    from app.services.chat.pi_native_surface import NATIVE_TOOL_NAMES
    from app.services.tool_catalog import DOMAIN_KEYWORDS

    try:
        from app.lib.gis.runtime_manifest import get_runtime_manifest

        generation = str(getattr(get_runtime_manifest(), "fingerprint", "") or "")
    except Exception:  # noqa: BLE001 — manifest 是投影；缺席诚实空串
        generation = ""

    return ConformanceFacts(
        registry=registry,
        capability_ids=tuple(cap_reg.all_ids),
        algorithms=tuple(algo_reg.get(aid) for aid in algo_reg.all_ids),
        parameter_contracts=contracts_by_id,
        native_tool_names=tuple(NATIVE_TOOL_NAMES),
        domain_keywords=dict(DOMAIN_KEYWORDS),
        spec_compiler=None,
        registry_generation=generation,
    )
