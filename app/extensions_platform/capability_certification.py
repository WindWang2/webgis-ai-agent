"""Pack 能力分级认证 v2（ADR-0199）。

与 :mod:`certification` 的关系：那是 V2 的**包级**确定性检查套件（12 项
CLI 消费、不 gate 任何东西、不留痕）；本模块是 V4 的**能力级**分级认证
管线——同一扩展的 tools / algorithms / skills 逐能力给出认证证据，并把
结论持久化为指纹绑定的 ``.certification.json``，供激活 gate
（``HostPolicy.require_certified``）消费。

分级（declared → schema → implementation → tests → runtime probe →
lifecycle）：

1. ``supply_chain``：签名 / SBOM secret / 包布局 / 资源预算 / 协议面
   （复用 certification.py 的确定性检查助手，语义不变）；
2. ``schema``：manifest 声明面（工具 side_effect 分类、算法 scientific
   status、skill 契约结构 + 引用存在性）；
3. ``implementation``：真实激活后逐声明条目核对投影存在（声明 ↔ 注册
   fail closed；未实现的 capability 在此失认证）；
4. ``tests``：pack 自带证据 —— SDK spec 的 ``smoke_cases`` 经
   ``run_authoring_checks`` 重放 + 算法 descriptor 元数据规则
   （VALIDATED/PRODUCTION ⇒ conformance + uncertainty）；
5. ``runtime_probe``：经**真实注册的**（权限包裹后）可调用对象执行
   manifest ``certification`` 探针：结果断言 + 确定性重放（连跑两次深度
   相等）+ latency/result-size 类别核验（判决桶，不记录原始耗时——
   报告必须逐字节确定）；
6. ``lifecycle``：干净停用 + 无孤儿投影（升级/卸载后不留 dangling
   callable）。

报告确定性：检查项固定顺序、无时间戳、无随机源、无原始墙钟读数。
``certified`` = 全部检查无 fail（warning 如实呈现不阻断）。

信任模型（gate 消费报告时）：报告绑定包内容指纹；``evidence`` 模式
（本地开发默认）接受未签名报告但**不防篡改**（每次接受产出 warning 级
诊断）；``strict`` 模式要求报告携带运维认证密钥的 HMAC（复用
signing.py 的 stdlib ``hmac`` 原语，域分隔前缀防拼接）。无密钥时 strict
fail closed。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic
from .discovery import CERTIFICATION_FILENAME, compute_fingerprint
from .host import ExtensionHost, ExtensionState

logger = logging.getLogger(__name__)

REPORT_SCHEMA_VERSION = 1
# 报告 HMAC 域分隔前缀（与指纹/签名的命名风格一致，防跨域拼接）。
_CERTIFICATION_HMAC_PREFIX = b"webgis-extension-certification-v1\n"
_REPORT_MAX_BYTES = 256 * 1024
# latency_class / result_size_policy 判决上界（秒 / 字节）。
_LATENCY_CLASS_BUDGET_S = {"fast": 1.0, "medium": 10.0, "slow": 120.0}
# 实测超出声明类别预算的倍数容差（类别是作者自述；判决只抓数量级谎报）。
_LATENCY_TOLERANCE_FACTOR = 10.0
# result_size_policy 判决预算（词表 = 核心 descriptor.RESULT_SIZE_POLICIES；
# inline 进 LLM 上下文的最紧，ref_offload 的载荷在 ref 后面故同 bounded）。
_RESULT_SIZE_BUDGET_BYTES = {
    "inline_small": 16 * 1024,
    "bounded": 1024 * 1024,
    "ref_offload": 1024 * 1024,
}


# ── 检查项构造（与 certification.py 的 {check,status,detail} 同形）──────
def _check(
    stage: str, target: str, ok: bool, detail: str, *, warn_only: bool = False
) -> dict[str, Any]:
    status = "warn" if warn_only else ("pass" if ok else "fail")
    return {"stage": stage, "capability": target, "status": status, "detail": detail}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _result_digest(result: Any) -> str:
    return hashlib.sha256(_canonical_json(result).encode("utf-8"), usedforsecurity=False).hexdigest()


# ── 阶段 1：supply_chain（复用既有确定性助手）────────────────────────────
def _stage_supply_chain(host: ExtensionHost, record) -> list[dict[str, Any]]:
    from .certification import (
        _check_package_layout,
        _check_protocol_compat,
        _check_provider_conformance,
        _check_resource_budget,
    )
    from .api_version import check_extension_api_compatibility
    from .sbom import build_sbom
    from .signing import verify_pack_signature

    manifest = record.manifest
    checks: list[dict[str, Any]] = []
    baseline_errors = [
        d
        for d in record.diagnostics
        # 认证 gate 产生的诊断不是包契约失败（它们由「报告缺失/过期」
        # 触发，正是本管线要补的证据缺口）；否则被 gate 拒过的 pack 永远
        # 无法通过认证补证（鸡生蛋死锁）。
        if d.severity.value == "error"
        and d.code
        not in (
            DiagnosticCode.CERTIFICATION_REQUIRED,
            DiagnosticCode.CERTIFICATION_STALE,
            DiagnosticCode.CERTIFICATION_INVALID,
        )
    ]
    checks.append(
        _check("supply_chain", "pack", not baseline_errors,
               "; ".join(d.message for d in baseline_errors) or "no error diagnostics")
    )
    api = check_extension_api_compatibility(manifest.api_version)
    checks.append(
        _check("supply_chain", "pack", api.compatible,
               api.reason or f"api_version {manifest.api_version}")
    )
    signature = verify_pack_signature(
        record.path,
        host._policy.trusted_publishers,
        trust_store=host._policy.trust_store,
        package_id=manifest.id,
        version=manifest.version,
    )
    checks.append(
        _check("supply_chain", "pack",
               signature.status not in ("invalid", "tampered", "revoked"),
               signature.status
               + (f" (publisher={signature.publisher})" if signature.publisher else "")
               + (f": {signature.detail}" if signature.detail else ""))
    )
    fingerprint, fp_diag = compute_fingerprint(record.path)
    if fingerprint is None:
        checks.append(_check("supply_chain", "pack", False,
                             fp_diag.message if fp_diag else "fingerprint unavailable"))
        sbom_clean, sbom_detail = False, "fingerprint unavailable"
    else:
        sbom = build_sbom(record.path, manifest, fingerprint)
        scan = sbom["secret_scan"]
        sbom_clean, sbom_detail = bool(scan["clean"]), (
            "clean" if scan["clean"] else f"findings: {scan['findings']}"
        )
    checks.append(_check("supply_chain", "pack", sbom_clean, sbom_detail))
    checks.append(_check("supply_chain", "pack", *_check_package_layout(record.path)))
    checks.append(_check("supply_chain", "pack", *_check_resource_budget(manifest)))
    checks.append(_check("supply_chain", "pack", *_check_protocol_compat(manifest)))
    checks.append(_check("supply_chain", "pack", *_check_provider_conformance(manifest)))
    return checks


# ── 阶段 2：schema（manifest 声明面）─────────────────────────────────────
_SCHEMA_STATUS_VOCAB = frozenset({"", "EXPERIMENTAL", "VALIDATED", "PRODUCTION", "DEPRECATED"})


def _stage_schema(manifest) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for decl in manifest.tools:
        classified = decl.side_effect != "unclassified"
        checks.append(
            _check("schema", f"tool:{decl.name}", classified,
                   f"side_effect={decl.side_effect}"
                   if classified
                   else "certified tools must declare a classified side_effect "
                        "(unclassified is not certifiable evidence)")
        )
    for decl in manifest.algorithms:
        ok = decl.scientific_status in _SCHEMA_STATUS_VOCAB
        checks.append(
            _check("schema", f"algorithm:{decl.id}", ok,
                   f"scientific_status={decl.scientific_status}"
                   if ok else f"invalid scientific_status {decl.scientific_status!r}")
        )
    for decl in getattr(manifest, "skills", []) or []:
        ok, detail = _validate_skill_contract(manifest, decl)
        checks.append(_check("schema", f"skill:{decl.skill_id}", ok, detail))
    return checks


def _validate_skill_contract(manifest, decl) -> tuple[bool, str]:
    """skill 契约校验：结构（SkillContract pydantic）+ 引用存在性。

    不复制第二套 schema：结构校验复用 gis_harness ``SkillContract``；
    引用存在性复用 loader 的 ``default_validation_predicates``。跨技能
    id 引用（fallback/deprecated_by）对 pack 技能只给 warning（跨包引用
    解析是运行时 overlay 的治理面，见模块 docstring 边界说明）。
    """
    from app.services.gis_harness.skills.bridges import default_validation_predicates
    from app.services.gis_harness.skills.contract import SkillContract

    payload = dict(decl.contract)
    payload["id"] = manifest.namespaced_skill_id(decl.skill_id)
    payload["pack"] = manifest.namespace
    try:
        contract = SkillContract.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - 归一为 schema 失败诊断
        return False, f"SkillContract invalid: {exc}"
    from app.services.gis_harness.skills.contract import SKILL_DOMAINS

    if contract.domain not in SKILL_DOMAINS:
        return False, f"domain {contract.domain!r} not in SKILL_DOMAINS vocabulary"
    predicates = default_validation_predicates()
    problems: list[str] = []
    for req in contract.capability_requirements:
        cap_id = getattr(req, "capability_id", None) or getattr(req, "id", "")
        if cap_id and not predicates["capability_exists"](cap_id):
            problems.append(f"unknown capability ref {cap_id!r}")
    for ref in contract.recipe_refs:
        if ref and not predicates["recipe_exists"](ref):
            problems.append(f"unknown recipe ref {ref!r}")
    for artifact in contract.output_artifacts:
        if artifact and not predicates["artifact_type_exists"](artifact):
            problems.append(f"unknown artifact type {artifact!r}")
    for task in contract.ontology_tasks:
        if task and not predicates["ontology_task_exists"](task):
            problems.append(f"unknown ontology task {task!r}")
    if problems:
        return False, "; ".join(problems)
    return True, "contract structurally valid, references resolve"


# ── 阶段 5：runtime probe ────────────────────────────────────────────────
def _probe_callable(func, args: dict[str, Any]) -> tuple[Optional[Any], str]:
    try:
        return func(**args), ""
    except Exception as exc:  # noqa: BLE001 - 探针收集而非中断
        return None, f"{type(exc).__name__}: {exc}"


def _classify_latency(latency_class: Optional[str], elapsed_s: float) -> tuple[bool, str]:
    if not latency_class or latency_class == "unknown":
        return True, "latency_class unknown (no assertion)"
    budget = _LATENCY_CLASS_BUDGET_S.get(latency_class)
    if budget is None:
        return True, f"latency_class {latency_class!r} has no probe budget (no assertion)"
    allowed = budget * _LATENCY_TOLERANCE_FACTOR
    if elapsed_s > allowed:
        return False, (
            f"probe took {elapsed_s:.3f}s, exceeds {latency_class} budget "
            f"{budget:.3f}s x{_LATENCY_TOLERANCE_FACTOR:g}"
        )
    return True, f"latency within {latency_class} budget ({elapsed_s:.3f}s observed)"


def _classify_result_size(policy: Optional[str], result: Any) -> tuple[bool, str, int]:
    size = len(_canonical_json(result).encode("utf-8"))
    if not policy or policy == "unknown":
        return True, f"result_size_policy unknown ({size} bytes observed)", size
    budget = _RESULT_SIZE_BUDGET_BYTES.get(policy)
    if budget is None:
        return True, f"result_size_policy {policy!r} has no probe budget", size
    if size > budget:
        return False, f"result {size} bytes exceeds {policy} budget {budget} bytes", size
    return True, f"result within {policy} budget ({size} bytes)", size


def _run_probe(host: ExtensionHost, manifest, target_tool: str, probe, stage: str,
               capability_label: str) -> list[dict[str, Any]]:
    """按 manifest 短名定位工具并执行探针。"""
    return _run_probe_by_registered_name(
        host, manifest.namespaced_tool_name(target_tool), probe, stage, capability_label,
    )


def _run_probe_by_registered_name(
    host: ExtensionHost, projected: str, probe, stage: str, capability_label: str,
) -> list[dict[str, Any]]:
    """对真实注册的工具执行一个探针（断言 + 重放 + 类别核验）。"""
    checks: list[dict[str, Any]] = []
    registry = host._tool_registry
    func = registry._tools.get(projected)
    if func is None:
        return [_check(stage, capability_label, False,
                       f"probe target tool {projected!r} not registered")]

    t0 = time.perf_counter()
    first, err = _probe_callable(func, probe.args)
    elapsed_s = time.perf_counter() - t0
    if err:
        return [_check(stage, capability_label, False, f"probe raised: {err}")]
    # 结果断言。
    if probe.expect_key:
        actual = first.get(probe.expect_key) if isinstance(first, dict) else None
        if isinstance(probe.expect_value, float) and isinstance(actual, (int, float)):
            ok = abs(float(actual) - probe.expect_value) <= probe.tolerance
        else:
            ok = actual == probe.expect_value
        if not ok:
            return [_check(
                stage, capability_label, False,
                f"probe expected {probe.expect_key}≈{probe.expect_value!r}, got {actual!r}",
            )]
        checks.append(_check(stage, capability_label, True,
                             f"expectation {probe.expect_key}≈{probe.expect_value!r} holds"))
    # 确定性重放（连跑两次深度相等；sha256 of canonical JSON）。
    if probe.replay:
        second, err2 = _probe_callable(func, probe.args)
        if err2:
            return checks + [_check(stage, capability_label, False,
                                    f"replay probe raised: {err2}")]
        if _result_digest(first) != _result_digest(second):
            return checks + [_check(
                stage, capability_label, False,
                "deterministic replay mismatch (two probes produced different results)",
            )]
        checks.append(_check(stage, capability_label, True, "deterministic replay holds"))
    # latency / result-size 类别核验（声明探针的首次调用实测；报告只记
    # 判决不记原始耗时——报告必须逐字节确定）。
    meta = registry._metadata.get(projected)
    latency_class = meta.get("latency_class") if isinstance(meta, dict) else None
    latency_ok, latency_detail = _classify_latency(latency_class, elapsed_s)
    checks.append(_check(stage, capability_label, latency_ok, latency_detail))
    return checks


# ── 主管线 ───────────────────────────────────────────────────────────────
def run_pack_certification(
    host: ExtensionHost,
    extension_id: str,
    *,
    save: bool = False,
    sign_key_file: Optional[Path] = None,
    sign_key_id: str = "certification",
) -> dict[str, Any]:
    """对已发现扩展执行分级能力认证；未知 id 返回单检查失败报告。

    认证管线自己驱动真实激活/停用（``override_gate=True``——认证是 gate
    的证据生产者，不递归消费 gate）。``save=True`` 时把报告持久化到包内
    ``.certification.json``（指纹绑定；``sign_key_file`` 提供时附 HMAC）。
    """
    record = host.get_record(extension_id)
    if record is None:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "extension_id": extension_id,
            "certified": False,
            "checks": [
                _check("discovery", "pack", False, f"unknown extension {extension_id!r}")
            ],
        }
    manifest = record.manifest
    checks: list[dict[str, Any]] = []
    checks.extend(_stage_supply_chain(host, record))
    checks.extend(_stage_schema(manifest))

    # ── 阶段 3-6 需要真实激活。状态分支（不扰动运维状态）：
    #    ACTIVE/DEGRADED → 只做投影核对与探针，不停用；
    #    其余状态（含 FAILED/INCOMPATIBLE/DISABLED 的复位语义）统一交给
    #    host.activate(override_gate=True)——它自身处理 FAILED→DISCOVERED
    #    的重试验证；成功则认证结束前停用还原 + 孤儿检查。
    #    ────────────────────────────────────────────────────────────────
    deactivate_after = False
    if record is not None and record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
        pass  # 已激活：投影与探针照常，lifecycle 阶段跳过
    else:
        activate_diags = host.activate(extension_id, override_gate=True)
        record = host.get_record(extension_id)
        failed = any(d.severity.value == "error" for d in activate_diags)
        if (
            failed or record is None
            or record.state not in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
        ):
            checks.append(_check(
                "implementation", "pack", False,
                "activation failed: "
                + ("; ".join(d.message for d in activate_diags if d.severity.value == "error")
                   or "unknown activation failure"),
            ))
        else:
            deactivate_after = True

    if deactivate_after:
        checks.extend(_stage_implementation(host, record))
        checks.extend(_stage_tests(record))
        checks.extend(_stage_runtime_probe(host, record))
        checks.extend(_stage_lifecycle(host, extension_id))
    elif record is not None and record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
        checks.extend(_stage_implementation(host, record))
        checks.extend(_stage_tests(record))
        checks.extend(_stage_runtime_probe(host, record))

    capabilities = _capability_summary(record)
    certified = all(c["status"] != "fail" for c in checks)
    fingerprint, _fp_diag = compute_fingerprint(record.path)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "extension_id": extension_id,
        "certified": certified,
        "fingerprint": fingerprint,
        "trust": record.trust.value,
        "execution_mode": (
            manifest.execution.mode if manifest.execution else "in_process"
        ),
        "capabilities": capabilities,
        "checks": checks,
    }
    if save:
        try:
            save_certification_report(
                record.path, report,
                sign_key_file=sign_key_file, sign_key_id=sign_key_id,
            )
            report["saved"] = str(CERTIFICATION_FILENAME)
        except ExtensionDiagnostic as exc:  # _fingerprint 失败等 typed 路径
            report["saved"] = f"failed: {exc.message}"
    return report


# ── 阶段 3：implementation（声明 ↔ 投影核对）────────────────────────────
def _stage_implementation(host: ExtensionHost, record) -> list[dict[str, Any]]:
    manifest = record.manifest
    checks: list[dict[str, Any]] = []
    registry = host._tool_registry
    for decl in manifest.tools:
        projected = manifest.namespaced_tool_name(decl.name)
        checks.append(_check(
            "implementation", f"tool:{decl.name}", registry.has(projected),
            f"projected as {projected!r}" if registry.has(projected)
            else f"declared but not registered ({projected!r}) — "
                 "unimplemented capability cannot be certified",
        ))
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    algo_registry = get_algorithm_registry()
    for decl in manifest.algorithms:
        projected = manifest.namespaced_algorithm_id(decl.id)
        checks.append(_check(
            "implementation", f"algorithm:{decl.id}", algo_registry.has(projected),
            f"projected as {projected!r}" if algo_registry.has(projected)
            else f"declared but not registered ({projected!r})",
        ))
    if manifest.data_providers:
        from app.services.data_fabric.registry import get_registry

        fabric = get_registry()
        supported = set(fabric.supported_source_types())
        for decl in manifest.data_providers:
            projected = manifest.namespaced_source_type(decl.source_type)
            checks.append(_check(
                "implementation", f"provider:{decl.source_type}", projected in supported,
                f"projected as {projected!r}" if projected in supported
                else f"declared but not registered ({projected!r})",
            ))
    for decl in getattr(manifest, "skills", []) or []:
        # skill 是资产声明（本层无运行时投影面）；实现核对 = schema 阶段
        # 的契约校验，这里记录治理边界（不虚报「已投影」）。
        checks.append(_check(
            "implementation", f"skill:{decl.skill_id}", True,
            "declared asset (runtime overlay is a separate governance surface)",
            warn_only=True,
        ))
    return checks


# ── 阶段 4：tests（pack 自带证据重放）────────────────────────────────────
def _stage_tests(record) -> list[dict[str, Any]]:
    manifest = record.manifest
    checks: list[dict[str, Any]] = []
    module = record.module
    specs_by_kind: dict[str, list[Any]] = {}
    for attr, kind in (("TOOLS", "tool"), ("ALGORITHMS", "algorithm")):
        raw = getattr(module, attr, None) if module is not None else None
        if isinstance(raw, list):
            specs_by_kind[kind] = raw
    from .sdk.algorithm import run_authoring_checks

    for decl in manifest.algorithms:
        spec = next(
            (s for s in specs_by_kind.get("algorithm", [])
             if getattr(s, "id", None) == decl.id),
            None,
        )
        if spec is None:
            if manifest.certification is not None and decl.id in manifest.certification.algorithms:
                checks.append(_check(
                    "tests", f"algorithm:{decl.id}", False,
                    "certification probe declared but pack does not expose an "
                    "AlgorithmExtensionSpec (module-level ALGORITHMS list) — "
                    "cannot produce test evidence",
                ))
            else:
                checks.append(_check(
                    "tests", f"algorithm:{decl.id}", True,
                    "no smoke cases declared (module ALGORITHMS not exposed)",
                    warn_only=True,
                ))
            continue
        diagnostics = run_authoring_checks(
            spec, _algorithm_implementation(spec, module, manifest.namespace)
        )
        for d in diagnostics:
            checks.append(_check("tests", f"algorithm:{decl.id}",
                                 d.severity.value != "error", d.message))
        if not diagnostics:
            checks.append(_check(
                "tests", f"algorithm:{decl.id}", True,
                f"{len(spec.smoke_cases)} smoke case(s) passed"
                if spec.smoke_cases else "no smoke cases declared",
                warn_only=not spec.smoke_cases,
            ))
        # VALIDATED/PRODUCTION 元数据规则（与核心 registry 同规则）。
        if decl.scientific_status in ("VALIDATED", "PRODUCTION"):
            ok = bool(spec.conformance_tests) and bool(spec.uncertainty_outputs)
            checks.append(_check(
                "tests", f"algorithm:{decl.id}", ok,
                f"{decl.scientific_status}: conformance_tests="
                f"{len(spec.conformance_tests)}, uncertainty_outputs="
                f"{len(spec.uncertainty_outputs)}"
                if ok else
                f"{decl.scientific_status} requires non-empty conformance_tests "
                "and uncertainty_outputs",
            ))
    for decl in manifest.tools:
        spec = next(
            (s for s in specs_by_kind.get("tool", [])
             if getattr(s, "name", None) == decl.name),
            None,
        )
        if spec is None:
            continue
        diagnostics = spec.validate()
        errors = [d for d in diagnostics if d.severity.value == "error"]
        checks.append(_check(
            "tests", f"tool:{decl.name}", not errors,
            "; ".join(d.message for d in errors) or "SDK spec validation clean",
        ))
    return checks


def _algorithm_implementation(spec, module, namespace: str):
    """从 pack 模块解析算法实现函数（authoring smoke 用）。

    约定：spec.tool_candidates 指向**已命名空间化**的注册名（与
    AlgorithmRegistry descriptor 一致）；pack 模块内的工具 spec 用短名。
    这里按 ``<ns>_<name>`` 对齐两端。
    """
    tools = getattr(module, "TOOLS", []) or []
    bound = spec.tool_candidates or []
    for tool_name in bound:
        short = tool_name[len(namespace) + 1:] if tool_name.startswith(f"{namespace}_") else tool_name
        tool_spec = next((t for t in tools if getattr(t, "name", None) == short), None)
        if tool_spec is not None and callable(getattr(tool_spec, "func", None)):
            return tool_spec.func
    return lambda **_kwargs: {}


# ── 阶段 5：runtime probe（真实注册面执行）───────────────────────────────
def _stage_runtime_probe(host: ExtensionHost, record) -> list[dict[str, Any]]:
    manifest = record.manifest
    cert = manifest.certification
    checks: list[dict[str, Any]] = []
    if cert is None:
        return [_check(
            "runtime_probe", "pack", True,
            "no certification probes declared", warn_only=True,
        )]
    for tool_name, probe in cert.tools.items():
        label = f"tool:{tool_name}"
        checks.extend(_run_probe(host, manifest, tool_name, probe, "runtime_probe", label))
    for algo_id, probe in cert.algorithms.items():
        # 算法探针经其绑定工具执行（算法 descriptor 不直接可调用）。
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        descriptor = get_algorithm_registry().get(manifest.namespaced_algorithm_id(algo_id))
        if descriptor is None:
            checks.append(_check("runtime_probe", f"algorithm:{algo_id}", False,
                                 "descriptor not registered"))
            continue
        via = descriptor.tool_candidates or []
        if not via:
            checks.append(_check("runtime_probe", f"algorithm:{algo_id}", False,
                                 "descriptor binds no tool_candidates; probe cannot run"))
            continue
        # descriptor.tool_candidates 是已命名空间化的注册名；探针直接按
        # 注册名取可调用对象（不再二次前缀）。
        registered = via[0]
        checks.extend(_run_probe_by_registered_name(
            host, registered, probe, "runtime_probe", f"algorithm:{algo_id}",
        ))
    return checks


# ── 阶段 6：lifecycle（干净停用 + 无孤儿投影）────────────────────────────
def _stage_lifecycle(host: ExtensionHost, extension_id: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    record = host.get_record(extension_id)
    if record is None:
        return [_check("lifecycle", "pack", False, "record vanished during certification")]
    manifest = record.manifest
    from .diagnostics import has_errors

    deactivate_diags = host.deactivate(extension_id)
    checks.append(_check(
        "lifecycle", "pack", not has_errors(deactivate_diags),
        "; ".join(d.message for d in deactivate_diags) or "projections rolled back",
    ))
    # 孤儿投影检查：升级/卸载语义（Oracle）——停用后不得残留任何本包
    # 命名空间条目（dangling callable 防线）。
    orphans: list[str] = []
    registry = host._tool_registry
    for decl in manifest.tools:
        projected = manifest.namespaced_tool_name(decl.name)
        if registry.has(projected):
            orphans.append(projected)
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    algo_registry = get_algorithm_registry()
    for decl in manifest.algorithms:
        projected = manifest.namespaced_algorithm_id(decl.id)
        if algo_registry.has(projected):
            orphans.append(projected)
    if manifest.data_providers:
        from app.services.data_fabric.registry import get_registry

        fabric = get_registry()
        supported = set(fabric.supported_source_types())
        for decl in manifest.data_providers:
            projected = manifest.namespaced_source_type(decl.source_type)
            if projected in supported:
                orphans.append(projected)
    checks.append(_check(
        "lifecycle", "pack", not orphans,
        "no orphan projections after deactivation" if not orphans
        else f"orphan projections remain after deactivation: {sorted(orphans)}",
    ))
    return checks


def _capability_summary(record) -> dict[str, Any]:
    manifest = record.manifest
    return {
        "tools": sorted(manifest.namespaced_tool_name(t.name) for t in manifest.tools),
        "algorithms": sorted(
            manifest.namespaced_algorithm_id(a.id) for a in manifest.algorithms
        ),
        "skills": sorted(
            manifest.namespaced_skill_id(s.skill_id)
            for s in (getattr(manifest, "skills", []) or [])
        ),
    }


# ── 持久化（指纹绑定 + 可选 HMAC）────────────────────────────────────────
def _report_hmac(key: bytes, key_id: str, fingerprint: str, report: dict[str, Any]) -> str:
    payload = (
        _CERTIFICATION_HMAC_PREFIX
        + key_id.encode("utf-8") + b"\n"
        + fingerprint.encode("utf-8") + b"\n"
        + _canonical_json(report).encode("utf-8")
    )
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def save_certification_report(
    pack_dir: Path,
    report: dict[str, Any],
    *,
    sign_key_file: Optional[Path] = None,
    sign_key_id: str = "certification",
) -> dict[str, Any]:
    """把认证报告写入包内 ``.certification.json``（确定性序列化）。

    EOL 安全（commit 017d1d41 教训）：指纹绑定的是「报告以外的包内容」，
    校验只比对 ``report.fingerprint`` 与重算指纹，绝不 hash 报告文件自身
    的字节——autocrlf 检出差异不会造成假 stale。HMAC 计算用规范化 JSON
    （排序键、紧凑分隔符），与文件呈现形态解耦。
    """
    from .diagnostics import ExtensionPlatformError

    pack_dir = Path(pack_dir)
    fingerprint = report.get("fingerprint")
    if not fingerprint:
        actual, _diag = compute_fingerprint(pack_dir)
        if actual is None:
            raise ExtensionPlatformError(ExtensionDiagnostic.error(
                DiagnosticCode.CERTIFICATION_INVALID,
                f"cannot fingerprint {str(pack_dir)!r} to bind the report",
            ))
        fingerprint = actual
        report = dict(report)
        report["fingerprint"] = fingerprint
    doc = dict(report)
    if sign_key_file is not None:
        try:
            key = Path(sign_key_file).read_bytes()
        except OSError as exc:
            raise ExtensionPlatformError(ExtensionDiagnostic.error(
                DiagnosticCode.CERTIFICATION_INVALID,
                f"cannot read certification key file {str(sign_key_file)!r}: {exc}",
            )) from exc
        # 签名载荷 = 含 hmac_key_id 的完整 doc（hmac 自身除外）；验证侧按
        # 同一构成重算（pop hmac），两端对称。
        doc["hmac_key_id"] = sign_key_id
        doc["hmac"] = _report_hmac(key, sign_key_id, fingerprint, doc)
    body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (pack_dir / CERTIFICATION_FILENAME).write_text(body, encoding="utf-8")
    return doc


def load_certification_report(
    pack_dir: Path,
    expected_fingerprint: str,
    *,
    mode: str = "evidence",
    hmac_key_file: Optional[Path] = None,
) -> tuple[Optional[dict[str, Any]], Optional[ExtensionDiagnostic]]:
    """读取并校验认证报告。返回 (report, None) 或 (None, typed diagnostic)。

    校验顺序（fail closed）：文件存在且可解析 → schema_version 认识 →
    ``certified is True`` → 指纹与当前包内容一致（stale 检测）→
    ``mode="strict"`` 时 HMAC 必须验证通过。evidence 模式接受未签名
    报告但产出 warning（不防篡改，模块 docstring 的信任模型）。
    """
    path = Path(pack_dir) / CERTIFICATION_FILENAME
    if not path.is_file():
        return None, ExtensionDiagnostic.error(
            DiagnosticCode.CERTIFICATION_REQUIRED,
            f"no certification report at {path.name}; run `ext certify --staged --save` first",
        )
    try:
        raw = path.read_bytes()
        if len(raw) > _REPORT_MAX_BYTES:
            raise ValueError(f"report exceeds {_REPORT_MAX_BYTES} bytes")
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        return None, ExtensionDiagnostic.error(
            DiagnosticCode.CERTIFICATION_INVALID, f"certification report unreadable: {exc}"
        )
    if not isinstance(doc, dict):
        return None, ExtensionDiagnostic.error(
            DiagnosticCode.CERTIFICATION_INVALID, "certification report must be a JSON object"
        )
    if doc.get("schema_version") != REPORT_SCHEMA_VERSION:
        return None, ExtensionDiagnostic.error(
            DiagnosticCode.CERTIFICATION_INVALID,
            f"unsupported report schema_version {doc.get('schema_version')!r}",
        )
    if doc.get("certified") is not True:
        return None, ExtensionDiagnostic.error(
            DiagnosticCode.CERTIFICATION_INVALID,
            "report records certified=false; re-run certification after fixing failures",
        )
    if doc.get("fingerprint") != expected_fingerprint:
        return None, ExtensionDiagnostic.error(
            DiagnosticCode.CERTIFICATION_STALE,
            "report fingerprint does not match current pack contents "
            "(pack changed after certification; re-certify)",
        )
    signed = "hmac" in doc
    if mode == "strict":
        if not signed:
            return None, ExtensionDiagnostic.error(
                DiagnosticCode.CERTIFICATION_INVALID,
                "strict certification gate requires an HMAC-signed report "
                "(certify --save --sign-key); unsigned evidence is not accepted",
            )
        if hmac_key_file is None:
            return None, ExtensionDiagnostic.error(
                DiagnosticCode.CERTIFICATION_INVALID,
                "strict certification gate requires the operator certification key "
                "(EXTENSIONS_CERTIFICATION_KEY) to verify report HMAC",
            )
        try:
            key = Path(hmac_key_file).read_bytes()
        except OSError as exc:
            return None, ExtensionDiagnostic.error(
                DiagnosticCode.CERTIFICATION_INVALID,
                f"cannot read certification key file {str(hmac_key_file)!r}: {exc}",
            )
        hmac_doc = dict(doc)
        expected = hmac_doc.pop("hmac")
        actual = _report_hmac(
            key, str(doc.get("hmac_key_id") or ""), str(doc.get("fingerprint")), hmac_doc
        )
        if not hmac.compare_digest(actual, str(expected)):
            return None, ExtensionDiagnostic.error(
                DiagnosticCode.CERTIFICATION_INVALID,
                "certification report HMAC mismatch (wrong key or forged report)",
            )
    else:
        if not signed:
            # evidence 模式的诚实性留痕：接受的报告不防篡改。
            return doc, ExtensionDiagnostic.warning(
                DiagnosticCode.CERTIFICATION_INVALID,
                "evidence-mode gate accepted an UNSIGNED certification report "
                "(not tamper-evident; use EXTENSIONS_CERTIFICATION_TRUST=strict "
                "in production)",
            )
    return doc, None


def certification_gate_diagnostic(record, policy) -> Optional[ExtensionDiagnostic]:
    """激活 gate：require_certified 开启时校验持久化报告。

    返回 None = 放行；返回 error 级诊断 = 拒绝激活。``builtin_ids`` 豁免
    （随仓库发行、CI 认证的内置包不经本地报告 gate）。
    """
    if not getattr(policy, "require_certified", False):
        return None
    if record.extension_id in policy.builtin_ids:
        return None
    mode = getattr(policy, "certification_trust", "evidence")
    key_file = getattr(policy, "certification_key", None)
    fingerprint = record.fingerprint
    if fingerprint is None:
        return ExtensionDiagnostic.error(
            DiagnosticCode.CERTIFICATION_INVALID,
            "pack fingerprint unavailable; cannot bind certification evidence",
            extension_id=record.extension_id,
        )
    report, diag = load_certification_report(
        record.path, fingerprint, mode=mode, hmac_key_file=key_file
    )
    if report is None:
        assert diag is not None
        return ExtensionDiagnostic.error(
            diag.code, diag.message, extension_id=record.extension_id, **diag.context
        )
    if diag is not None:
        # evidence 模式下未签名报告的 warning 如实上抛（不阻断），并补全
        # 扩展 id 归属（load 层不知道扩展上下文）。
        return ExtensionDiagnostic.warning(
            diag.code, diag.message, extension_id=record.extension_id, **diag.context
        )
    return None


__all__ = [
    "CERTIFICATION_FILENAME",
    "REPORT_SCHEMA_VERSION",
    "certification_gate_diagnostic",
    "load_certification_report",
    "run_pack_certification",
    "save_certification_report",
]
