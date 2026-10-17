"""PiToolSpecV1 —— canonical ToolDescriptor → Pi native tool ABI 的版本化投影。

Goal 04（ADR-0224）：Pi 侧看到的每个 native GIS tool 不再是无类型 schema dict，
而是一份**带指纹、带安全/资源元数据、带 registry generation** 的版本化 ABI spec。

不变式（红线）：
- 本模块是 ``ToolRegistry`` → Pi 的**只读投影编译器**，不是第二注册中心；
  执行真相仍全部回到 ToolDispatchService（经 /pi-tools/execute → dispatch_tool）。
- schema projection **fail-honest**：只允许删除无损装饰性 JSON Schema 键与
  截断 description 文本（记 finding code）；任何约束性键（type/enum/pattern/
  min/max/required/items/…）永不改写、永不截断。放不进 per-tool 预算的 spec
  把**整个工具**逐出 native 面（长尾仍可经 webgis_execute），绝不裁 schema
  换取预算空间。
- 序列化确定性：canonical JSON（键排序、紧凑分隔符、ensure_ascii）——
  同一 descriptor + schema 在任何进程/任何次编译产生 byte-identical 输出，
  指纹跨进程稳定。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: ABI 语义版本（schema projection 规则或 spec 字段集合变更时 +1）。
PI_TOOL_SPEC_ABI_VERSION = 1
#: 策略标识（receipt / 披露面消费）。
PI_TOOL_SPEC_POLICY = "pi_tool_spec.v1"

#: total flag（kill switch）：关闭时 dump 回 v2 形状、无 spec、无 receipt。
PI_TOOL_ABI_ENV = "PI_TOOL_ABI"

#: per-tool 投影 schema 字节预算（registry.schema_size 口径）。超预算 →
#: 工具逐出 native 面（finding ``spec_over_budget``），不裁 schema。
#: 0 = 关闭。默认 16KB：native7 最大实测 ≈4KB，16KB 只裁病理尾。
PI_SPEC_MAX_SCHEMA_BYTES_ENV = "PI_SPEC_MAX_SCHEMA_BYTES"
_DEFAULT_SPEC_MAX_SCHEMA_BYTES = 16 * 1024

#: spec 内 description 文本的截断上限（装饰性文本，非约束；截断必记 finding）。
_DESCRIPTION_MAX_CHARS = 512

#: 指纹长度（SHA-256 short digest，与 descriptor.py 同款口径）。
_FINGERPRINT_LEN = 16

#: JSON Schema 关键字白名单：Pi 消费面（Type.Object 风格 function schema）
#: 明确支持的词汇。白名单外的键按「无损装饰」候选删除并记 finding ——
#: 宁可少带装饰键，不可带 Pi 端语义未知的约束。
_SUPPORTED_SCHEMA_KEYWORDS = frozenset({
    # 结构
    "type", "properties", "required", "items", "prefixItems", "enum", "const",
    "anyOf", "oneOf", "allOf", "not", "additionalProperties",
    # $ref / defs（pydantic v2 产出 $defs；保留原样，不展开）
    "$ref", "$defs", "definitions",
    # 数值 / 字符串约束（永不改写）
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "minLength", "maxLength", "pattern", "format",
    "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
    # 装饰 / 元信息
    "title", "description", "default", "examples", "deprecated",
})

#: 已知出现且明确判定为无损可删的键（pydantic/registry 产物常见）：一次性
#: 显式列出，未来新增键默认也按白名单逻辑处理（未支持 → 删 + finding）。

#: 封闭 finding codes（投影审计面；新增需同步 ADR + 测试）。
FINDING_OK = "ok"
FINDING_DESC_TRUNCATED = "desc_truncated"                # description 截断
FINDING_KEYWORDS_DROPPED = "keywords_dropped"            # 白名单外装饰键删除
FINDING_PASSTHROUGH_KEY_STRIPPED = "session_id_stripped" # session_id 剥离（扩展侧注入）
FINDING_ADDITIONAL_PROPERTIES_PINNED = "additional_properties_pinned"  # 顶层封口
FINDING_UNION_RETAINED = "union_retained"                # anyOf/oneOf 原样保留（不展开不削弱）
FINDING_REFS_RETAINED = "refs_retained"                  # $defs/$ref 原样保留（不展开）
FINDING_SPEC_OVER_BUDGET = "spec_over_budget"            # 超 per-tool 预算（工具逐出）


def tool_abi_enabled() -> bool:
    """PI_TOOL_ABI 总闸（默认 ON；=0/false/off/no 关闭 → 全量 master 行为）。"""
    raw = (os.getenv(PI_TOOL_ABI_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def spec_max_schema_bytes() -> int:
    """per-tool schema 字节预算（PI_SPEC_MAX_SCHEMA_BYTES；0=关闭）。"""
    raw = (os.getenv(PI_SPEC_MAX_SCHEMA_BYTES_ENV) or "").strip()
    if not raw:
        return _DEFAULT_SPEC_MAX_SCHEMA_BYTES
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_SPEC_MAX_SCHEMA_BYTES


# ---------------------------------------------------------------------------
# 确定性序列化 / 指纹
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """确定性序列化（键排序、紧凑、ASCII；与 app/tools/descriptor.py 同纪律）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _short_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]


# ---------------------------------------------------------------------------
# Schema projection（fail-honest）
# ---------------------------------------------------------------------------

#: 值是「具名 schema 字典」的关键字（键是名字不是关键字 —— 逐值投影）。
_NAMED_SCHEMA_MAP_KEYS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
#: 值是「schema 列表」的关键字。
_SCHEMA_LIST_KEYS = frozenset({"anyOf", "oneOf", "allOf"})
#: 值是字面量（永不递归、永不改写）的关键字。
_LITERAL_KEYS = frozenset({
    "enum", "const", "examples", "default", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength",
    "maxLength", "pattern", "format", "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties", "type", "title", "deprecated",
})


def _truncate_description(value: str, findings: Dict[str, Any]) -> str:
    if len(value) > _DESCRIPTION_MAX_CHARS:
        findings[FINDING_DESC_TRUNCATED] = findings.get(FINDING_DESC_TRUNCATED, 0) + 1
        return value[:_DESCRIPTION_MAX_CHARS]
    return value


def _project_schema_dict(node: Dict[str, Any], findings: Dict[str, Any]) -> Dict[str, Any]:
    """投影一个 schema 节点 dict：白名单外键删除（记 finding）；约束键的
    值永不改写；union/$defs 原样保留并计数。"""
    dropped: list[str] = []
    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key not in _SUPPORTED_SCHEMA_KEYWORDS:
            if isinstance(key, str):
                dropped.append(key)
            continue
        if key == "description":
            out[key] = (
                _truncate_description(value, findings)
                if isinstance(value, str) else value
            )
        elif key in _LITERAL_KEYS:
            out[key] = value  # 字面量/约束：verbatim（含 enum 列表）
        elif key in _NAMED_SCHEMA_MAP_KEYS:
            out[key] = {
                name: (_project_schema_dict(sub, findings) if isinstance(sub, dict) else sub)
                for name, sub in (value or {}).items()
            }
        elif key in _SCHEMA_LIST_KEYS:
            findings[FINDING_UNION_RETAINED] = findings.get(FINDING_UNION_RETAINED, 0) + 1
            out[key] = [
                _project_schema_dict(sub, findings) if isinstance(sub, dict) else sub
                for sub in (value or [])
            ]
        elif key == "items":
            if isinstance(value, dict):
                out[key] = _project_schema_dict(value, findings)
            elif isinstance(value, list):  # draft-2020 prefixItems 风格
                out[key] = [
                    _project_schema_dict(sub, findings) if isinstance(sub, dict) else sub
                    for sub in value
                ]
            else:
                out[key] = value
        elif key in ("prefixItems",):
            out[key] = [
                _project_schema_dict(sub, findings) if isinstance(sub, dict) else sub
                for sub in (value or [])
            ]
        elif key == "required":
            out[key] = [str(item) for item in (value or [])]
        elif key == "not":
            out[key] = (
                _project_schema_dict(value, findings) if isinstance(value, dict) else value
            )
        elif key in ("$ref",):
            out[key] = value
        else:
            # 防御：未知但白名单内的键原样保留（fail-honest —— 不猜语义）。
            out[key] = value
    if dropped:
        kept = findings.setdefault(FINDING_KEYWORDS_DROPPED, {})
        for name in dropped[:8]:  # 有界披露
            kept[name] = int(kept.get(name, 0)) + 1
    return out


def project_schema_for_pi(
    function_schema: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """OpenAI function schema → Pi 参数 schema 投影 + findings。

    输入兼容两种既有形态：registry ``get_schemas_subset`` 的条目
    （``{"type": "function", "function": {...}}``）或内层 function dict ——
    与 ``_pi_parameters`` 同语义（session_id 剥离、顶层 additionalProperties
    封口、$defs 保留），外加白名单键过滤与 description 截断审计。findings
    是 ``{code: count | {key: count}}``（封闭 codes，见 FINDING_*）。
    """
    fn = function_schema.get("function") if isinstance(
        function_schema.get("function"), Mapping
    ) else function_schema
    params = fn.get("parameters") or {"type": "object", "properties": {}}
    findings: Dict[str, Any] = {}
    projected = _project_schema_dict(dict(params), findings)
    properties = dict(projected.get("properties") or {})
    required = list(projected.get("required") or [])
    if "session_id" in properties:
        properties.pop("session_id", None)
        required = [k for k in required if k != "session_id"]
        findings[FINDING_PASSTHROUGH_KEY_STRIPPED] = (
            findings.get(FINDING_PASSTHROUGH_KEY_STRIPPED, 0) + 1
        )
    out: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    findings[FINDING_ADDITIONAL_PROPERTIES_PINNED] = 1
    if "$defs" in projected:
        out["$defs"] = projected["$defs"]
        findings[FINDING_REFS_RETAINED] = findings.get(FINDING_REFS_RETAINED, 0) + 1
    if "definitions" in projected:
        out["definitions"] = projected["definitions"]
        findings[FINDING_REFS_RETAINED] = findings.get(FINDING_REFS_RETAINED, 0) + 1
    return out, findings


# ---------------------------------------------------------------------------
# PiToolSpecV1
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PiToolSpecV1:
    """单个 native tool 的版本化 ABI spec（ToolDescriptor 的 Pi 投影）。

    分三档（与 ToolDescriptor 同纪律）：
    - 契约字段进指纹（name/abi_version/description/schema/安全与资源元数据）；
    - ``findings`` 是投影审计面（进指纹——投影规则变化即新 ABI 代）；
    - ``registry_generation`` 是**来源锚**，不进 spec 指纹（spec 内容不变时
      指纹不随 registry 其它工具增删抖动；surface 指纹另行绑定 generation）。
    """

    # --- 身份 / 契约 ---
    name: str
    abi_version: int = PI_TOOL_SPEC_ABI_VERSION
    description: str = ""
    summary: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_semantic_type: Optional[str] = None
    produced_refs: Tuple[str, ...] = ()
    accepts_ref_types: Tuple[str, ...] = ()

    # --- 安全 / 资格元数据（来自 ToolDescriptor，dispatch 闸的先验披露）---
    tier: int = 1
    effective_security_tier: int = 1
    side_effect: str = "unclassified"
    requires_confirmation: bool = False
    cancellable: bool = True
    idempotent: Optional[bool] = None
    requires_credentials: Tuple[str, ...] = ()
    execution_policy: str = "thread"
    network: Optional[bool] = None

    # --- 资源分级（surface 预算先验）---
    latency_class: str = "unknown"
    memory_class: str = "unknown"
    scale_class: str = "unknown"

    # --- 能力归属（只引用 id，不复制语义真相）---
    capabilities: Tuple[str, ...] = ()

    # --- 身份锚 ---
    tool_id: str = ""            # name@version#cvN
    schema_fingerprint: str = "" # 入参契约指纹（projection 后）
    descriptor_fingerprint: str = ""  # 完整 descriptor 契约指纹（registry 派生）
    registry_generation: str = ""     # runtime manifest fingerprint（来源锚）
    schema_bytes: int = 0             # 投影后 schema 字节数（预算口径）

    # --- 投影审计 ---
    findings: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """确定性序列化形态（dump v3 / receipt / 测试消费；canonical 友好）。"""
        return {
            "name": self.name,
            "abi_version": self.abi_version,
            "description": self.description,
            "summary": self.summary,
            "input_schema": self.input_schema,
            "output_semantic_type": self.output_semantic_type,
            "produced_refs": list(self.produced_refs),
            "accepts_ref_types": list(self.accepts_ref_types),
            "tier": int(self.tier),
            "effective_security_tier": int(self.effective_security_tier),
            "side_effect": self.side_effect,
            "requires_confirmation": bool(self.requires_confirmation),
            "cancellable": bool(self.cancellable),
            "idempotent": self.idempotent,
            "requires_credentials": list(self.requires_credentials),
            "execution_policy": self.execution_policy,
            "network": self.network,
            "latency_class": self.latency_class,
            "memory_class": self.memory_class,
            "scale_class": self.scale_class,
            "capabilities": list(self.capabilities),
            "tool_id": self.tool_id,
            "schema_fingerprint": self.schema_fingerprint,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "schema_bytes": int(self.schema_bytes),
            "findings": self.findings,
        }

    def fingerprint(self) -> str:
        """spec 指纹：契约字段 + 投影 findings 的 canonical digest。

        registry_generation 刻意排除（见类注释）；surface 级指纹由
        ``surface_fingerprint`` 绑定 generation。
        """
        payload = self.to_dict()
        return _short_digest(canonical_json(payload))

    def to_dump_dict(self) -> Dict[str, Any]:
        """dump v3 的 per-tool spec 形态 = to_dict() + 自身指纹。

        扩展从 ``spec.fingerprint`` 原样回传（toolFingerprint echo），后端
        与 SurfaceReceipt.tool_fingerprints 同值对照 —— 二者必须同源，
        否则 echo 校验会永假阳性。
        """
        out = self.to_dict()
        out["fingerprint"] = self.fingerprint()
        return out


def compile_tool_spec(
    registry: Any,
    name: str,
    *,
    registry_generation: str = "",
) -> Optional[PiToolSpecV1]:
    """从活 registry 编译单个工具的 PiToolSpecV1（fail-fast 于注册缺席）。

    返回 None 仅当：工具不在 registry、无 schema，或**超 per-tool schema 预算**
    （spec_over_budget —— 调用方把工具逐出 native 面，绝不裁 schema）。
    """
    descriptor = registry.descriptor(name)
    subset = registry.get_schemas_subset({name})
    if not subset:
        logger.warning("[PiToolSpec] no live schema for %s; excluded from native surface", name)
        return None
    fn = subset[0].get("function", {})
    schema, findings = project_schema_for_pi(subset[0])
    schema_bytes = len(json.dumps(schema, ensure_ascii=False))
    budget = spec_max_schema_bytes()
    if budget and schema_bytes > budget:
        # fail-honest：放不下 → 整工具逐出（proxy 长尾仍可达），不削弱 schema。
        logger.info(
            "[PiToolSpec] %s projected schema %dB exceeds per-tool budget %dB — "
            "excluded from native surface (proxy path unchanged)",
            name, schema_bytes, budget,
        )
        return None
    description = str(fn.get("description") or descriptor.description or name)
    if len(description) > _DESCRIPTION_MAX_CHARS:
        description = description[:_DESCRIPTION_MAX_CHARS]
        findings[FINDING_DESC_TRUNCATED] = findings.get(FINDING_DESC_TRUNCATED, 0) + 1
    return PiToolSpecV1(
        name=name,
        abi_version=PI_TOOL_SPEC_ABI_VERSION,
        description=description,
        summary=str(descriptor.summary or "")[:_DESCRIPTION_MAX_CHARS],
        input_schema=schema,
        output_semantic_type=descriptor.output_semantic_type,
        produced_refs=tuple(descriptor.produced_refs),
        accepts_ref_types=tuple(descriptor.accepts_ref_types),
        tier=int(descriptor.tier),
        effective_security_tier=int(descriptor.effective_security_tier),
        side_effect=descriptor.side_effect.value,
        requires_confirmation=bool(descriptor.requires_confirmation),
        cancellable=True,  # dispatch 全链绑 turn CancellationToken（use_token）
        idempotent=descriptor.effective_idempotent,
        requires_credentials=tuple(sorted(descriptor.requires_credentials)),
        execution_policy=str(descriptor.execution_policy or "thread"),
        network=descriptor.network,
        latency_class=str(descriptor.latency_class or "unknown"),
        memory_class=str(descriptor.memory_class or "unknown"),
        scale_class=str(descriptor.scale_class or "unknown"),
        capabilities=tuple(sorted(descriptor.capabilities)),
        tool_id=descriptor.tool_id,
        schema_fingerprint=_short_digest(canonical_json(
            {"name": name, "parameters": schema}
        )),
        descriptor_fingerprint=(
            str(registry.descriptor_fingerprint(name) or "")
            if hasattr(registry, "descriptor_fingerprint") else ""
        ),
        registry_generation=str(registry_generation or ""),
        schema_bytes=schema_bytes,
        findings=findings,
    )


def surface_fingerprint(
    specs: Sequence[PiToolSpecV1],
    *,
    registry_generation: str = "",
) -> str:
    """有序 surface 指纹：(name, spec.fingerprint) 序列 + registry generation。

    顺序敏感（surface 的 deterministic ordering 是契约的一部分）；generation
    参与 —— registry 其它工具增删导致 generation 变化时，即使选中集不变，
    下一 turn 的 surface 指纹也不同（stale-call 判定的依据）。
    """
    payload = {
        "abi_version": PI_TOOL_SPEC_ABI_VERSION,
        "registry_generation": str(registry_generation or ""),
        "tools": [[s.name, s.fingerprint()] for s in specs],
    }
    return _short_digest(canonical_json(payload))


def current_registry_generation() -> str:
    """来源锚：runtime manifest fingerprint（缺席 → 空串，投影面诚实降级）。

    manifest 是进程级 compile-once 投影（``set_tool_registry`` 注入时已
    refresh）；此处只读，不触发重编译。
    """
    try:
        from app.lib.gis.runtime_manifest import get_runtime_manifest

        manifest = get_runtime_manifest()
        return str(getattr(manifest, "fingerprint", "") or "")
    except Exception:  # noqa: BLE001 — manifest 是投影；缺席不阻断 spec 编译
        return ""
