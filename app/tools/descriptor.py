"""ToolDescriptor V2 —— 工具描述符契约与指纹（Agent Tool Platform V2, ADR-0101）。

ToolRegistry 仍是唯一的执行真相（ADR-0006/0014/0019/0043）。本模块**不**引入
第二个注册中心：ToolDescriptor 是从注册期元数据 + args model + schema **派生**
的只读投影，与 ToolRegistry._metadata 同源存储（register() 写入的扩展字段即
描述符字段），由 ``ToolRegistry.descriptor(name)`` 构造并缓存。

职责边界：
- 生命周期状态（stable / experimental / deprecated / hidden / external_unavailable /
  planned）驱动模型可见性投影（``model_visible``），不驱动执行；
- 副作用类（SideEffectClass）驱动重试/缓存/重放安全推断（``retry_safe`` 等），
  不替代 tier-3 确认闸（registry._dispatch_impl 仍是唯一闸点）；
- 指纹（schema_fingerprint / descriptor_fingerprint / manifest_fingerprint）
  为确定性 canonical-JSON SHA-256，供 Surface 缓存失效、replay 兼容判定与
  SessionPlan staleness 复用。

描述 change 与入参 schema change 必须可区分：
- ``schema_fingerprint`` 只覆盖模型可见的**入参契约**（function 名 + parameters），
  描述文案变更不影响它；
- ``descriptor_fingerprint`` 覆盖完整描述符（含描述/版本/状态/schema 指纹）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


class ToolStatus(str, Enum):
    """工具生命周期状态（§15）。

    - STABLE: 默认；完全可见、可执行。
    - EXPERIMENTAL: 可见可执行；Surface 投影可标注实验性。
    - DEPRECATED: 可见可执行但模型可见清单优先呈现 canonical 工具；
      ``deprecation_of`` 指向 canonical 名（别名语义，单一实现）。
    - HIDDEN: 不进入任何模型可见清单（内部工具 / 仅显式 API）。
    - EXTERNAL_UNAVAILABLE: 依赖的外部服务不可用，投影时按需隐藏，恢复后自动回归。
    - PLANNED: 未实现 / 不可执行；绝不进入模型可见清单，也绝不可 dispatch。
    """

    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"
    HIDDEN = "hidden"
    EXTERNAL_UNAVAILABLE = "external_unavailable"
    PLANNED = "planned"


_VALID_STATUSES = tuple(s.value for s in ToolStatus)


class SideEffectClass(str, Enum):
    """副作用类（§27）—— 重试 / 并行 / 重放 / 确认的依据。

    未标注默认 UNCLASSIFIED（存量 200+ 工具零改动兼容）；注册期仅告警不阻断，
    增量富化由各工具模块逐步声明。
    """

    UNCLASSIFIED = "unclassified"
    PURE = "pure"                      # 纯读：会话/外部状态零变更
    DETERMINISTIC_COMPUTE = "deterministic_compute"  # 输入决定输出的计算，无副作用
    CACHEABLE_READ = "cacheable_read"  # 外部读取，可缓存
    STATE_MUTATION = "state_mutation"  # 会话/地图状态变更（非破坏性）
    ARTIFACT_CREATION = "artifact_creation"          # 产出工件/ref
    EXTERNAL_SIDE_EFFECT = "external_side_effect"    # 外部系统副作用（发请求改外部状态）
    DESTRUCTIVE = "destructive"        # 破坏性（tier-3 必属此类）

    @property
    def retry_safe(self) -> Optional[bool]:
        """盲目重试是否安全。UNCLASSIFIED → None（未知）。"""
        if self in (
            SideEffectClass.PURE,
            SideEffectClass.DETERMINISTIC_COMPUTE,
            SideEffectClass.CACHEABLE_READ,
        ):
            return True
        if self in (
            SideEffectClass.STATE_MUTATION,
            SideEffectClass.ARTIFACT_CREATION,
            SideEffectClass.EXTERNAL_SIDE_EFFECT,
            SideEffectClass.DESTRUCTIVE,
        ):
            return False
        return None

    @property
    def cacheable(self) -> Optional[bool]:
        if self in (
            SideEffectClass.PURE,
            SideEffectClass.DETERMINISTIC_COMPUTE,
            SideEffectClass.CACHEABLE_READ,
        ):
            return True
        if self == SideEffectClass.UNCLASSIFIED:
            return None
        return False

    @property
    def replay_safe(self) -> Optional[bool]:
        """重放（replay harness）是否可自动再执行。破坏性/外部副作用永不可自动重放。"""
        if self in (
            SideEffectClass.EXTERNAL_SIDE_EFFECT,
            SideEffectClass.DESTRUCTIVE,
        ):
            return False
        if self == SideEffectClass.UNCLASSIFIED:
            return None
        return True


#: 结果尺寸策略（§8 result-size policy）。
RESULT_SIZE_POLICIES = ("unknown", "inline_small", "bounded", "ref_offload")


@dataclass(frozen=True)
class ToolDescriptor:
    """单个工具的稳定描述符（从 ToolRegistry 派生的只读投影）。

    字段分三档：
    - 契约字段：进入 descriptor_fingerprint（描述/版本/状态/入参契约/能力声明）。
    - 派生字段：由其他字段推导（destructive_level / requires_confirmation /
      retry_safe / cacheable），不参与指纹（可由契约字段重现）。
    - 附属字段：model/schema 引用（不进指纹，体积大且可由 name 取回）。
    """

    # --- 身份 ---
    name: str
    description: str = ""
    summary: str = ""                       # 模型可见短摘要（可选；压缩 Surface 用）
    version: str = "1.0"
    contract_version: int = 1
    status: ToolStatus = ToolStatus.STABLE
    deprecation_of: Optional[str] = None    # DEPRECATED 时指向 canonical 工具名

    # --- 分层 / 调度元数据 ---
    tier: int = 1
    domains: Tuple[str, ...] = ()
    cost: str = "light"
    execution_policy: str = "thread"
    timeout: Optional[float] = None

    # --- 副作用 / 安全 ---
    side_effect: SideEffectClass = SideEffectClass.UNCLASSIFIED
    requires_credentials: Tuple[str, ...] = ()

    # --- 能力归属（不得复制 GIS 语义真相，只引用 id）---
    capabilities: Tuple[str, ...] = ()
    algorithms: Tuple[str, ...] = ()
    provider_dependencies: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()   # 自由检索词（词法检索 / Surface 投影用；不改变语义）

    # --- I/O 契约声明 ---
    output_semantic_type: Optional[str] = None   # e.g. "geojson_fc" / "map_product" / "chart"
    produced_refs: Tuple[str, ...] = ()          # 产出的 ref 种类（"data" / "artifact" / "map_layer"）
    accepts_ref_types: Tuple[str, ...] = ()      # 接受的 ref 种类
    required_fields: Tuple[str, ...] = ()        # 入参必填字段（入向校验冗余，便于无 schema 判定）
    network: Optional[bool] = None
    deterministic: Optional[bool] = None
    result_size_policy: str = "unknown"

    # --- 别名（入向工具名别名，registry._TOOL_NAME_ALIASES 的同源快照）---
    aliases: Tuple[str, ...] = ()

    @property
    def tool_id(self) -> str:
        """稳定工具标识：``name@version#cv{n}``（与 ToolRegistry.tool_version 同构）。"""
        return f"{self.name}@{self.version}#cv{self.contract_version}"

    @property
    def destructive_level(self) -> int:
        """0-3；tier>=3 视为破坏性级别 3。"""
        return 3 if int(self.tier) >= 3 else 0

    @property
    def requires_confirmation(self) -> bool:
        # review R1 minor：显式声明 destructive 副作用类的低 tier 工具同样
        # 要求确认语义（tier-3 闸仍只认 tier —— 此处是描述符层的诚实性）。
        return self.destructive_level >= 3 or self.side_effect is SideEffectClass.DESTRUCTIVE

    @property
    def executable(self) -> bool:
        """PLANNED 工具不可执行（注册期与 dispatch 期双重把关）。"""
        return self.status != ToolStatus.PLANNED

    @property
    def model_visible(self) -> bool:
        """是否默认进入模型可见清单（HIDDEN / PLANNED 永不；其余按可用性投影）。"""
        return self.status not in (ToolStatus.HIDDEN, ToolStatus.PLANNED)

    @property
    def retry_safe(self) -> Optional[bool]:
        if self.destructive_level >= 3:
            return False
        se = self.side_effect.retry_safe
        if se is False:
            return False
        # 已知幂等且非破坏性 → 重试安全
        if self.side_effect in (
            SideEffectClass.PURE,
            SideEffectClass.DETERMINISTIC_COMPUTE,
            SideEffectClass.CACHEABLE_READ,
        ):
            return True
        return se

    @property
    def cacheable(self) -> Optional[bool]:
        if self.destructive_level >= 3:
            return False
        return self.side_effect.cacheable

    @property
    def replay_safe(self) -> Optional[bool]:
        if self.destructive_level >= 3:
            return False
        return self.side_effect.replay_safe

    def contract_payload(self) -> Dict[str, Any]:
        """指纹载荷：全部契约字段的 canonical 形态（不含派生字段与 schema 本体）。"""
        return {
            "name": self.name,
            "description": self.description,
            "summary": self.summary,
            "version": self.version,
            "contract_version": self.contract_version,
            "status": self.status.value,
            "deprecation_of": self.deprecation_of,
            "tier": self.tier,
            "domains": sorted(self.domains),
            "cost": self.cost,
            "execution_policy": self.execution_policy,
            "timeout": self.timeout,
            "side_effect": self.side_effect.value,
            "requires_credentials": sorted(self.requires_credentials),
            "capabilities": sorted(self.capabilities),
            "algorithms": sorted(self.algorithms),
            "provider_dependencies": sorted(self.provider_dependencies),
            "tags": sorted(self.tags),
            "output_semantic_type": self.output_semantic_type,
            "produced_refs": sorted(self.produced_refs),
            "accepts_ref_types": sorted(self.accepts_ref_types),
            "required_fields": sorted(self.required_fields),
            "network": self.network,
            "deterministic": self.deterministic,
            "result_size_policy": self.result_size_policy,
        }


# ---------------------------------------------------------------------------
# 指纹（§9）—— canonical JSON + SHA-256，跨进程确定性
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """确定性序列化：键排序、紧凑分隔符、ASCII 转义（跨平台/跨进程稳定）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _short_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def schema_fingerprint(schema: Mapping[str, Any]) -> str:
    """模型可见入参契约指纹：仅 function.name + parameters（描述文案不参与）。

    对 OpenAI function-schema dict 生效；description 变更不影响该指纹，
    入参 properties/required 变更必然变更 —— 二者可区分（§9）。
    """
    fn = schema.get("function", {}) if isinstance(schema, Mapping) else {}
    payload = {"name": fn.get("name"), "parameters": fn.get("parameters")}
    return _short_digest(canonical_json(payload))


def descriptor_fingerprint(descriptor: ToolDescriptor, schema_fp: Optional[str] = None) -> str:
    """完整描述符指纹 = 契约字段 + 入参 schema 指纹。"""
    payload = descriptor.contract_payload()
    payload["schema_fingerprint"] = schema_fp
    return _short_digest(canonical_json(payload))


def manifest_fingerprint(entries: Iterable[Tuple[str, str]]) -> str:
    """工具清单指纹：(name, schema_fingerprint) 集合的顺序无关指纹。

    用于 ToolSurface / context 投影缓存失效：清单内容不变 → 指纹不变，
    与投影内 schema 顺序无关（顺序是投影期决策，不是契约变更）。
    """
    payload = sorted(entries)
    return _short_digest(canonical_json(payload))


def registry_fingerprint(entries: Iterable[Tuple[str, str]]) -> str:
    """整个 registry 的内容指纹（别名同 manifest_fingerprint，语义标签区分用途）。"""
    return manifest_fingerprint(entries)


def validate_descriptor_fields(
    *,
    name: str,
    status: str,
    deprecation_of: Optional[str],
    side_effect: str,
    result_size_policy: str,
    summary: str,
    capabilities: Optional[List[str]],
    algorithms: Optional[List[str]],
    produced_refs: Optional[List[str]],
    accepts_ref_types: Optional[List[str]],
    requires_credentials: Optional[List[str]],
    provider_dependencies: Optional[List[str]],
    domains: Optional[List[str]],
    tags: Optional[List[str]] = None,
) -> List[str]:
    """注册期校验门：返回**致命**错误列表（空列表 = 通过）。

    设计为纯函数便于契约测试；registry.register 在写元数据前调用，
    有致命错误即 raise ValueError（注册失败 → 进程启动失败，符合
    「注册期显式失败」的既有 cost/execution_policy 先例）。
    """
    errors: List[str] = []
    if status not in _VALID_STATUSES:
        errors.append(
            f"工具 {name} 声明了非法 status={status!r}，合法值: {', '.join(_VALID_STATUSES)}"
        )
    if status == ToolStatus.DEPRECATED.value and not deprecation_of:
        errors.append(f"工具 {name} 声明 status=deprecated 但未提供 deprecation_of=<canonical 工具名>")
    if deprecation_of and status != ToolStatus.DEPRECATED.value:
        errors.append(f"工具 {name} 提供了 deprecation_of 但 status={status!r}（仅 deprecated 可用）")
    if deprecation_of == name:
        errors.append(f"工具 {name} 的 deprecation_of 不能指向自身")
    if side_effect not in tuple(s.value for s in SideEffectClass):
        errors.append(
            f"工具 {name} 声明了非法 side_effect={side_effect!r}，"
            f"合法值: {', '.join(s.value for s in SideEffectClass)}"
        )
    if result_size_policy not in RESULT_SIZE_POLICIES:
        errors.append(
            f"工具 {name} 声明了非法 result_size_policy={result_size_policy!r}，"
            f"合法值: {', '.join(RESULT_SIZE_POLICIES)}"
        )
    if len(summary) > 600:
        errors.append(f"工具 {name} 的 summary 超长（{len(summary)} > 600 字符）")
    for label, seq in (
        ("capabilities", capabilities),
        ("algorithms", algorithms),
        ("produced_refs", produced_refs),
        ("accepts_ref_types", accepts_ref_types),
        ("requires_credentials", requires_credentials),
        ("provider_dependencies", provider_dependencies),
        ("domains", domains),
        ("tags", tags),
    ):
        if seq is not None:
            if not isinstance(seq, (list, tuple)) or any(
                not isinstance(x, str) or not x.strip() for x in seq
            ):
                errors.append(f"工具 {name} 的 {label} 必须是非空字符串列表")
            elif len(set(seq)) != len(seq):
                errors.append(f"工具 {name} 的 {label} 含重复项")
    return errors
