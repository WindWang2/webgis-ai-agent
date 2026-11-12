"""G30 prepare 服务 —— prepared call 的确定性编译（零业务副作用）。

职责（全部只读/纯计算；不授权、不执行、不写业务状态）：

1. 资产解析与治理：id+version 精确命中；digest = SkillContract.fingerprint()；
   deprecated / guidance_only / quarantined → typed 拒绝（附替代/原因）；
2. 数据绑定事实抽取：ref → session store 读取（只读）→ 几何/数量/字段/
   CRS/内容 digest；inline 数据同规则抽取（start 时才落 store）；
3. typed 参数校验：必填/类型/枚举/范围/单位/条件必填/字段身份；
   来源四值（user/skill_default/preset/derived）逐参数记录；
4. 能力与工具核对（阳性事实）：执行映射的工具须注册且归属技能声明能力；
   required+hard 能力快照不可用 → typed 拒绝（证据缺席不越权）；
5. 编译：argument_map 闭词表解析（role 叶子保留 ``$role`` 标记，run 时解析）；
6. 有界计划预览：步骤/读写性质/预期结果/证据覆盖/缺口/降级披露。

红线：prepare 的显式选择只消除「用哪个方法」的歧义，不改变科学适用性、
能力可用性与权限（它们在 dispatch 期仍由既有闸裁决）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.skill_jobs.contracts import (
    BindingFacts,
    PreparedBinding,
    PreparedParam,
    PreparedSkillCall,
    PreparedStep,
    SkillJobErrorCode,
)


class _OmitSentinel:
    def __repr__(self) -> str:  # pragma: no cover - debug only
        return "<omit>"


_OMIT = _OmitSentinel()


@dataclass
class PrepareResult:
    """prepare 输出：prepared call + 内联数据原文（由 prepared store 持有，
    start 时落会话存储；不进 prepared 快照本体 —— 快照只留 digest 事实）。"""
    prepared: PreparedSkillCall
    inline_payloads: Dict[str, Any] = field(default_factory=dict)

#: 有界事实抽取的采样上限（字段类型判定样本；大表只看前 N 行）。
_FIELD_SAMPLE_MAX = 200
#: 绑定数据要素上限（防御：超大内联数据在 prepare 拒绝而不是拖垮进程）。
_MAX_BINDING_FEATURES = 200_000
#: 内联绑定字节预算（review P1-5：单一巨型要素/超长属性串不受要素数约束；
#: 大数据请走会话 ref —— ref 通道有自己的体积治理与 spill 机制）。
_MAX_INLINE_BINDING_BYTES = 8 * 1024 * 1024


def _digest_payload(data: Any) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


def extract_binding_facts(data: Any) -> Tuple[BindingFacts, Optional[str]]:
    """从 FeatureCollection 抽取有界事实；返回 (facts, error)。

    只陈述不推断：几何类别取主导类别（混合几何如实标 mismatch）；
    字段类型由有界值采样判 numeric/categorical/unknown。
    """
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" \
            or not isinstance(data.get("features"), list):
        return BindingFacts(), "DATA_NOT_GEOJSON"
    features = data["features"]
    if len(features) > _MAX_BINDING_FEATURES:
        # review P1-5：要素超限是资源事实，不是形状错误 —— 诚实typed码。
        return BindingFacts(), "PAYLOAD_TOO_LARGE"
    kind_counts: Dict[str, int] = {}
    fields: List[str] = []
    field_seen = set()
    for f in features[:_FIELD_SAMPLE_MAX]:
        if not isinstance(f, dict):
            continue
        g = f.get("geometry") or {}
        gt = str(g.get("type") or "none").lower()
        if "point" in gt:
            kind_counts["point"] = kind_counts.get("point", 0) + 1
        elif "linestring" in gt:
            kind_counts["line"] = kind_counts.get("line", 0) + 1
        elif "polygon" in gt:
            kind_counts["polygon"] = kind_counts.get("polygon", 0) + 1
        else:
            kind_counts["unknown"] = kind_counts.get("unknown", 0) + 1
        props = f.get("properties") or {}
        if isinstance(props, dict):
            for k in props.keys():
                if k not in field_seen and len(fields) < 64:
                    field_seen.add(k)
                    fields.append(str(k))
    # 主导几何（并列取字典序稳定；全空 = unknown）
    geometry_kind = "unknown"
    if kind_counts:
        geometry_kind = max(sorted(kind_counts), key=lambda k: kind_counts[k])
    # 字段类型（有界采样：数值占比 ≥0.8 → numeric；有非数值 → categorical；
    # 无样本 → unknown）
    field_types: Dict[str, str] = {}
    for name in fields:
        numeric = 0
        total = 0
        for f in features[:_FIELD_SAMPLE_MAX]:
            if not isinstance(f, dict):
                continue
            v = (f.get("properties") or {}).get(name)
            if v is None:
                continue
            total += 1
            if isinstance(v, bool):
                numeric += 0
            elif isinstance(v, (int, float)):
                numeric += 1
        if total == 0:
            field_types[name] = "unknown"
        elif numeric / total >= 0.8:
            field_types[name] = "numeric"
        else:
            field_types[name] = "categorical"
    crs_member = data.get("crs") or {}
    crs_name = ""
    if isinstance(crs_member, dict):
        crs_name = str(crs_member.get("name") or "")[:64]
    facts = BindingFacts(
        geometry_kind=geometry_kind,
        feature_count=len(features),
        fields=fields,
        crs=crs_name,
        content_digest=_digest_payload(data),
        field_types=field_types,
    )
    return facts, None


def _validate_param_value(spec: Any, raw: Any) -> Tuple[Optional[Any], Optional[Tuple[str, str]]]:
    """单参数类型校验；返回 (coerced, None) 或 (None, (code, message))。"""
    name = spec.name
    if raw is None:
        return None, ("PARAM_TYPE_INVALID", f"参数 {name} 提供了 null（未提供请直接省略该键）")
    if spec.type == "number":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None, ("PARAM_TYPE_INVALID", f"参数 {name} 需要数值")
        v = float(raw)
        if spec.min_value is not None and v < spec.min_value:
            return None, ("PARAM_RANGE_INVALID", f"参数 {name}={v} 低于下界 {spec.min_value}")
        if spec.max_value is not None and v > spec.max_value:
            return None, ("PARAM_RANGE_INVALID", f"参数 {name}={v} 超过上界 {spec.max_value}")
        return v, None
    if spec.type == "string":
        if not isinstance(raw, str):
            return None, ("PARAM_TYPE_INVALID", f"参数 {name} 需要字符串")
        if spec.max_length and len(raw) > spec.max_length:
            return None, ("PARAM_RANGE_INVALID", f"参数 {name} 超长（≤{spec.max_length}）")
        return raw, None
    if spec.type == "enum":
        if not isinstance(raw, str) or raw not in spec.enum_values:
            return None, ("PARAM_ENUM_INVALID",
                          f"参数 {name} 取值 {raw!r} 不在词表 {spec.enum_values}")
        return raw, None
    if spec.type == "boolean":
        if not isinstance(raw, bool):
            return None, ("PARAM_TYPE_INVALID", f"参数 {name} 需要布尔值")
        return raw, None
    if spec.type == "field_ref":
        if not isinstance(raw, str) or not raw:
            return None, ("PARAM_TYPE_INVALID", f"参数 {name} 需要字段名字符串")
        return raw, None
    return None, ("PARAM_TYPE_INVALID", f"参数 {name} 类型词表外 {spec.type}")


def _resolve_args(argument_map: Dict[str, Any],
                  param_map: Dict[str, PreparedParam]) -> Dict[str, Any]:
    """argument_map → 具体实参（role 叶子保留 $role 标记；param 缺席省略）。"""

    def _resolve_node(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                r = _resolve_node(v)
                if r is _OMIT:
                    continue
                out[k] = r
            return out
        if isinstance(node, list):
            out = []
            for v in node:
                r = _resolve_node(v)
                if r is _OMIT:
                    continue
                out.append(r)
            return out
        if isinstance(node, str) and node.startswith(("role:", "param:", "const:")):
            kind, _, value = node.partition(":")
            if kind == "role":
                return {"$role": value}
            if kind == "param":
                p = param_map.get(value)
                if p is None:
                    return _OMIT  # 未提供且无缺省 → 整体省略
                return p.value
            return json.loads(value)
        return node

    args: Dict[str, Any] = {}
    for arg, expr in argument_map.items():
        r = _resolve_node(expr)
        if r is _OMIT:
            continue
        args[arg] = r
    return args


def _get_registry(registry: Optional[Any] = Any):
    """解析工具注册表：显式注入 > bridge 单例（生产 lifespan 注入）。"""
    if registry is not None:
        return registry
    from app.agent_pi_bridge import get_tool_registry

    return get_tool_registry()


async def prepare_skill_job(
    *,
    session_id: str,
    skill_id: str,
    skill_version: str = "",
    params: Optional[Dict[str, Any]] = None,
    bindings: Optional[List[Dict[str, Any]]] = None,
    param_sources: Optional[Dict[str, str]] = None,
    owner_fingerprint: str = "",
    registry: Optional[Any] = None,
) -> PrepareResult:
    """编译 prepared call（typed 拒绝抛 SkillJobErrorCode）。

    ``param_sources``：调用方可标注参数来源（preset 重用/派生值），缺省
    user（显式提供）/skill_default（缺省值兜底）。返回 prepared call 与
    内联数据原文（后者由调用方交给 prepared store 持有）。
    ``registry``：工具注册表（生产 lifespan 注入 bridge 单例；测试可覆写）。
    """
    from app.services.gis_harness.skills.loader import get_skill_library
    from app.services.gis_harness.skills.policy import TRUSTED_PACKS

    lib = get_skill_library()
    skill = lib.get(skill_id)
    if skill is None:
        raise SkillJobErrorCode("SKILL_NOT_FOUND", f"技能 {skill_id!r} 不存在")
    if skill_version and skill_version != skill.version:
        raise SkillJobErrorCode(
            "SKILL_VERSION_MISMATCH",
            f"技能 {skill_id} 当前版本为 {skill.version}（请求 {skill_version}）；"
            "版本资产不回滚，请按当前版本重新准备",
            detail={"current_version": skill.version, "requested": skill_version})
    if skill.deprecated:
        raise SkillJobErrorCode(
            "SKILL_DEPRECATED",
            f"技能 {skill_id} 已弃用（v{skill.version}）",
            detail={"deprecated_by": skill.deprecated_by or "",
                    "note": "不静默换新版：请显式选择替代技能"})
    if skill.pack not in TRUSTED_PACKS:
        raise SkillJobErrorCode(
            "SKILL_GUIDANCE_ONLY",
            f"技能 {skill_id} 属 pack={skill.pack}（非 trusted），仅方法指导，不可执行",
            detail={"pack": skill.pack})
    if skill.execution is None or not skill.execution.steps or not skill.is_runnable_asset():
        raise SkillJobErrorCode(
            "SKILL_GUIDANCE_ONLY",
            f"技能 {skill_id} 无执行映射（知识/方法指导资产），不可作为作业执行",
            detail={"note": "该技能仍可经规划路径提供方法指导"})

    raw_params = dict(params or {})
    param_sources = dict(param_sources or {})
    unknown = [k for k in raw_params if k not in {p.name for p in skill.parameters}]
    if unknown:
        raise SkillJobErrorCode(
            "PARAM_UNKNOWN",
            f"未声明参数（不透传任意键）：{', '.join(sorted(unknown))}",
            detail={"unknown": sorted(unknown),
                    "declared": [p.name for p in skill.parameters]})

    # ── 数据绑定解析（先于 field_ref 校验：字段身份来自绑定事实）──────
    raw_bindings = list(bindings or [])
    known_roles = set(skill.input_roles)
    rb_specs = {rb.role: rb for rb in skill.role_bindings}
    if not session_id:
        raise SkillJobErrorCode("SESSION_REQUIRED", "解析数据 ref 需要会话上下文")

    resolved_bindings: List[PreparedBinding] = []
    # 内联数据由调用方传递（不进 prepared 快照本体，只留 digest 事实；
    # start 时调用方需重供 inline 原文 —— LRU store 单独持有，见 store.py）
    inline_payloads: Dict[str, Any] = {}
    seen_roles: set = set()
    for b in raw_bindings:
        role = str(b.get("role") or "")
        if role not in known_roles:
            raise SkillJobErrorCode(
                "BINDING_UNKNOWN_ROLE", f"绑定角色 {role!r} 不在技能 input_roles",
                detail={"input_roles": sorted(known_roles)})
        if role in seen_roles:
            raise SkillJobErrorCode("BINDING_BAD_SHAPE", f"角色 {role} 重复绑定")
        seen_roles.add(role)
        ref = str(b.get("ref") or "")
        inline = b.get("inline")
        if bool(ref) == (inline is not None):
            raise SkillJobErrorCode(
                "BINDING_BAD_SHAPE",
                f"角色 {role} 必须且只能提供 ref 或 inline 之一")
        if ref:
            from app.services.session_data import session_data_manager
            resolved = await session_data_manager.resolve_alias(session_id, ref) \
                if not ref.startswith("ref:") else ref
            data = await session_data_manager.get(session_id, resolved)
            if data is None:
                raise SkillJobErrorCode(
                    "REF_NOT_FOUND",
                    f"角色 {role} 的数据 ref {ref!r} 在会话存储不可读（过期/不存在）",
                    detail={"role": role, "ref": ref})
            facts, err = extract_binding_facts(data)
            if err:
                raise SkillJobErrorCode(err, f"角色 {role} 绑定数据不是可用的 FeatureCollection")
            resolved_bindings.append(PreparedBinding(
                role=role, ref=resolved, inline=False, facts=facts))
        else:
            import json as _json
            try:
                _inline_bytes = len(_json.dumps(
                    inline, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                _inline_bytes = _MAX_INLINE_BINDING_BYTES + 1
            if _inline_bytes > _MAX_INLINE_BINDING_BYTES:
                raise SkillJobErrorCode(
                    "PAYLOAD_TOO_LARGE",
                    f"角色 {role} 内联数据超字节预算"
                    f"（{_inline_bytes} > {_MAX_INLINE_BINDING_BYTES}）；"
                    "大数据请先入会话存储，用 ref 绑定")
            facts, err = extract_binding_facts(inline)
            if err:
                raise SkillJobErrorCode(
                    err, f"角色 {role} 内联数据不是可用的 FeatureCollection")
            resolved_bindings.append(PreparedBinding(
                role=role, ref="", inline=True, facts=facts))
            inline_payloads[role] = inline

    binding_map = {b.role: b for b in resolved_bindings}
    for role, spec in rb_specs.items():
        if spec.required and role not in binding_map:
            raise SkillJobErrorCode(
                "BINDING_MISSING", f"必需数据角色 {role} 未绑定",
                detail={"role": role})
    for role, b in binding_map.items():
        spec = rb_specs.get(role)
        if spec is None:
            continue
        if spec.geometry_kinds and b.facts.geometry_kind not in spec.geometry_kinds:
            raise SkillJobErrorCode(
                "GEOMETRY_MISMATCH",
                f"角色 {role} 绑定数据几何为 {b.facts.geometry_kind}，"
                f"执行面要求 {spec.geometry_kinds}",
                detail={"role": role, "actual": b.facts.geometry_kind,
                        "required": spec.geometry_kinds})
        if spec.min_features and b.facts.feature_count < spec.min_features:
            raise SkillJobErrorCode(
                "MIN_FEATURES_NOT_MET",
                f"角色 {role} 绑定数据仅 {b.facts.feature_count} 要素"
                f"（需 ≥{spec.min_features}）",
                detail={"role": role, "actual": b.facts.feature_count,
                        "min": spec.min_features})

    # ── typed 参数校验 ────────────────────────────────────────────────
    prepared_params: List[PreparedParam] = []
    values: Dict[str, Any] = {}
    for spec in skill.parameters:
        provided = spec.name in raw_params
        if provided:
            coerced, err = _validate_param_value(spec, raw_params[spec.name])
            if err:
                raise SkillJobErrorCode(err[0], err[1], detail={"param": spec.name})
            source = param_sources.get(spec.name) or "user"
            if source not in ("user", "preset", "derived"):
                source = "user"
            prepared_params.append(PreparedParam(
                name=spec.name, value=coerced, source=source, unit=spec.unit))
            values[spec.name] = coerced
        elif spec.default is not None:
            coerced, err = _validate_param_value(spec, spec.default)
            if err:
                raise SkillJobErrorCode(  # 资产缺陷：缺省值非法即红（fail loud）
                    err[0], f"技能缺省参数非法：{err[1]}",
                    detail={"param": spec.name, "asset_bug": True})
            prepared_params.append(PreparedParam(
                name=spec.name, value=coerced, source="skill_default", unit=spec.unit))
            values[spec.name] = coerced
        else:
            values[spec.name] = None
            if spec.required:
                raise SkillJobErrorCode(
                    "PARAM_MISSING",
                    f"必填参数 {spec.name} 未提供",
                    detail={"param": spec.name, "type": spec.type})

    # 条件必填（声明式）：gate 参数取某值时本参数必须显式提供
    for spec in skill.parameters:
        if not spec.required_if_param:
            continue
        gate = values.get(spec.required_if_param)
        if gate in spec.required_if_in and spec.name not in raw_params:
            raise SkillJobErrorCode(
                "PARAM_CONFLICT",
                f"参数 {spec.required_if_param}={gate!r} 时必须提供 {spec.name}"
                f"（不能把总量悄悄改名成率）",
                detail={"param": spec.name, "gate": spec.required_if_param,
                        "gate_value": gate})

    # field_ref 身份核对（绑定数据的真实字段；显示名/别名不认）
    for spec in skill.parameters:
        if spec.type != "field_ref" or spec.name not in values or values[spec.name] is None:
            continue
        fname = str(values[spec.name])
        target = binding_map.get(spec.role or "")
        if target is None:
            raise SkillJobErrorCode(
                "BINDING_MISSING",
                f"参数 {spec.name} 引用角色 {spec.role!r}，该角色未绑定")
        if fname not in target.facts.fields:
            raise SkillJobErrorCode(
                "FIELD_NOT_FOUND",
                f"字段 {fname!r} 不在角色 {spec.role} 绑定数据的字段集中",
                detail={"role": spec.role,
                        "available": target.facts.fields[:24]})
        if spec.expects == "numeric" and \
                target.facts.field_types.get(fname) not in ("numeric", "unknown"):
            raise SkillJobErrorCode(
                "FIELD_TYPE_MISMATCH",
                f"字段 {fname!r} 在绑定数据中不是数值字段（expects=numeric）",
                detail={"role": spec.role, "field": fname,
                        "observed": target.facts.field_types.get(fname)})

    # ── 能力/工具核对（阳性事实）+ 编译 ────────────────────────────────
    registry = _get_registry(registry)
    declared_caps = {r.capability_id for r in skill.capability_requirements}
    availability: Dict[str, str] = {}
    try:
        from app.lib.gis.capability_availability_cache import (
            get_availability_snapshot,
        )
        snapshot = get_availability_snapshot()
    except Exception:  # noqa: BLE001 — 证据缺席不越权（dispatch 期仍会复核）
        snapshot = None

    steps_out: List[PreparedStep] = []
    param_map = {p.name: p for p in prepared_params}
    for st in skill.execution.steps:
        try:
            desc = registry.descriptor(st.tool)
        except KeyError:
            raise SkillJobErrorCode(
                "TOOL_NOT_REGISTERED",
                f"执行映射的工具 {st.tool!r} 未注册（步骤 {st.step_id}）",
                detail={"step": st.step_id, "tool": st.tool})
        tool_caps = set(getattr(desc, "capabilities", ()) or ())
        overlap = tool_caps & declared_caps
        if not overlap:
            raise SkillJobErrorCode(
                "TOOL_CAPABILITY_MISMATCH",
                f"工具 {st.tool} 不归属技能声明能力（步骤 {st.step_id}）",
                detail={"tool_caps": sorted(tool_caps)[:6],
                        "skill_caps": sorted(declared_caps)[:6]})
        if snapshot is not None:
            rec = snapshot.for_tool(st.tool)
            if rec is not None:
                status = str(getattr(rec, "status", "") or "")
                if status in ("declared_only", "temporarily_unavailable"):
                    raise SkillJobErrorCode(
                        "CAPABILITY_UNAVAILABLE",
                        f"步骤 {st.step_id} 的工具 {st.tool} 当前不可用"
                        f"（{status}）—— 显式选择技能不解禁能力",
                        detail={"step": st.step_id, "tool": st.tool,
                                "status": status})
                availability[st.tool] = status
        args = _resolve_args(st.argument_map, param_map)
        steps_out.append(PreparedStep(
            step_id=st.step_id, tool=st.tool,
            capability_id=sorted(overlap)[0], args=args,
            produces_evidence=list(st.produces_evidence),
            required=st.required))

    # ── 有界计划预览 ──────────────────────────────────────────────────
    evidence_produced: set = set()
    for st in steps_out:
        evidence_produced.update(st.produces_evidence)
    obligations = [o.obligation_id for o in skill.quality_obligations]
    uncovered_evidence = [e for e in skill.completion_evidence
                          if e not in evidence_produced]
    step_previews = []
    for st in steps_out:
        try:
            desc = registry.descriptor(st.tool)
            side = str(getattr(getattr(desc, "side_effect", None), "value", "") or "")
        except KeyError:  # pragma: no cover - descriptor 已在上面核对
            side = ""
        step_previews.append({
            "step_id": st.step_id, "tool": st.tool,
            "capability": st.capability_id,
            "side_effect": side,
            "reads_roles": sorted({
                v["$role"] for v in st.args.values()
                if isinstance(v, dict) and "$role" in v
            }),
            "produces_evidence": st.produces_evidence,
            "required": st.required,
        })
    preview = {
        "skill": {"id": skill.id, "name": skill.name, "version": skill.version,
                  "digest": skill.fingerprint()[:16], "domain": skill.domain},
        "result_kind": skill.execution.result_kind,
        "steps": step_previews,
        "bindings": [
            {"role": b.role, "ref": b.ref or "(inline)", "inline": b.inline,
             "geometry_kind": b.facts.geometry_kind,
             "feature_count": b.facts.feature_count,
             "fields": b.facts.fields[:16], "crs": b.facts.crs,
             "content_digest": b.facts.content_digest[:16]}
            for b in resolved_bindings
        ],
        "params": [{"name": p.name, "source": p.source} for p in prepared_params],
        "obligations": obligations[:8],
        "evidence_covered": sorted(evidence_produced & set(skill.completion_evidence)),
        "evidence_not_covered": uncovered_evidence[:8],
        "warnings_note": "evidence_not_covered 的义务在运行记录中保持未覆盖披露，不伪造满足",
    }

    prepared = PreparedSkillCall(
        prepared_id=f"skp-{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        skill_id=skill.id,
        skill_version=skill.version,
        skill_digest=skill.fingerprint(),
        params=prepared_params,
        bindings=resolved_bindings,
        steps=steps_out,
        result_kind=skill.execution.result_kind,
        idempotency_key="",  # 下面铸造
        preview=preview,
        warnings=[],
        prepared_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    prepared.idempotency_key = prepared.compute_idempotency_key(
        owner_fingerprint or session_id)
    return PrepareResult(prepared=prepared, inline_payloads=inline_payloads)
