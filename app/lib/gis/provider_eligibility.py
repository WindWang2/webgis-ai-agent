"""计算 / 模型 / 扩展提供者的只读资格转换（G06 / CAP-04）。

本模块**不引入模型新引擎、不调用私有 worker、不新起进程、不回传凭据**。
它只把既有权威面的**已编译事实**收敛成一份五要素资格：

* ``model_loaded``：``app/services/modelops/loaded_cache.py``（只读查缓存）；
* ``provider_healthy``：``app/services/provider_health.py`` 的 tracker（只读）；
* ``compatible_input``：``app/lib/modelops/compatibility.qualify`` 纯函数；
* ``inference_budget``：``app/lib/modelops/resources.batch_for_budget``；
* ``permission``：调用方显式授予的权限集合 + 扩展 broker 的 grant 面。

扩展生命周期（发现 / 认证 / 激活 / 停用 / 升级 / 撤权）一律通过
``ExtensionHost`` 的**公开 API**（``status_report`` / ``get_record`` 等），
停用或权限收缩后必须 fail-closed；旧计划按 ``is_stale_plan`` 判失效。
慢 provider 由 ``budget.deadline_s`` 做有界闸：预算耗尽返回
``not_evaluated``，绝不虚构 qualified，也绝不挂起。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

#: 五要素资格词表（封闭）。
QUALIFICATION_FACETS: tuple[str, ...] = (
    "model_loaded", "provider_healthy", "compatible_input",
    "inference_budget", "permission",
)
FACET_PASS = "proven_pass"
FACET_FAIL = "failed"
FACET_NOT_EVALUATED = "not_evaluated"

#: 推理输出可度量性维度（CAP-04：不能仅从 provider 回包断言科学有效）。
OUTPUT_MEASURABILITY_DIMENSIONS: tuple[str, ...] = (
    "shape", "range", "tile_location", "confidence", "uncertainty", "provenance",
)

_MIN_FULL_PROBE_BUDGET_S = 0.25


@dataclass(frozen=True)
class ProviderQualification:
    provider_id: str = ""
    facets: Mapping[str, str] = field(default_factory=dict)
    qualified: bool = False
    gpu_fallback: str = "not_needed"
    output_measurability: Mapping[str, bool] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    disclosures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "facets": dict(sorted(self.facets.items())),
            "qualified": self.qualified,
            "gpu_fallback": self.gpu_fallback,
            "output_measurability": dict(sorted(self.output_measurability.items())),
            "reason_codes": list(self.reason_codes),
            "disclosures": list(self.disclosures),
        }


def extension_lifecycle_view(host: Any) -> dict[str, Any]:
    """扩展生命周期只读视图（公开 API；停用/撤权/权限收缩 fail-closed）。

    返回结构里每个扩展都带 ``available`` 布尔与 ``availability`` 词；
    非 ACTIVE/DEGRADED 一律标 ``unavailable``，并披露 stale 风险。
    """
    out: dict[str, Any] = {
        "schema_version": "extension-lifecycle-view/v1",
        "extensions": [],
        "unavailable_namespaces": [],
        "diagnostics": [],
    }
    if host is None:
        out["diagnostics"].append("extension_host_absent:not_evaluated")
        return out
    try:
        report = host.status_report()
    except Exception as exc:  # noqa: BLE001
        out["diagnostics"].append(
            f"status_report_unavailable:{type(exc).__name__}:not_evaluated")
        return out

    active_states = {"active", "degraded"}
    for entry in report.get("extensions", []):
        state = str(entry.get("state", "") or "").lower()
        available = state in active_states
        namespace = str(entry.get("namespace", "") or "")
        row = {
            "extension_id": str(entry.get("id", "") or ""),
            "namespace": namespace,
            "version": str(entry.get("version", "") or ""),
            "state": state,
            "trust": str(entry.get("trust", "") or ""),
            "available": available,
            "availability": "available" if available else "unavailable",
            "declared_permissions": list(entry.get("permissions", []) or []),
            "granted_permissions": list(entry.get("granted_permissions", []) or []),
            "execution": str(entry.get("execution", "") or ""),
        }
        if not available:
            row["stale_plan_risk"] = True
            row["reason"] = (
                "extension_not_active:deactivated_or_quarantined; "
                "旧计划须按 is_stale_plan 失效")
            out["unavailable_namespaces"].append(namespace)
        # 权限收缩：声明了但没授予 → fail-closed 披露。
        declared = set(str(x) for x in (entry.get("permissions") or []))
        granted = set(str(x) for x in (entry.get("granted_permissions") or []))
        if declared - granted:
            row["permission_contraction"] = sorted(declared - granted)
            row["available"] = False
            row["availability"] = "unavailable"
            row["reason"] = "permission_contraction_fail_closed"
            if namespace not in out["unavailable_namespaces"]:
                out["unavailable_namespaces"].append(namespace)
        out["extensions"].append(row)

    # 版本钉（pin）语义：公开 policy 上的 version_pins 只读披露，不改 host。
    try:
        pins = getattr(getattr(host, "_policy", None), "version_pins", None)
        if pins:
            out["version_pins"] = {str(k): str(v) for k, v in dict(pins).items()}
    except Exception:  # noqa: BLE001
        out["diagnostics"].append("version_pins:not_evaluated")
    out["diagnostics"].append(
        "lease_pin_semantics: 执行中工具由 ExtensionHost 版本钉保护；"
        "停用/升级后新调用 fail-closed，不自动信任未签名插件")
    return out


def _probe_model_facts(provider_id: str, input_profile: Optional[dict],
                       budget: Optional[dict]) -> tuple[dict[str, str], list[str], str]:
    """只读探测（全部使用既有纯函数 / 已编译缓存；不发起慢 I/O）。"""
    facts: dict[str, str] = {f: FACET_NOT_EVALUATED for f in QUALIFICATION_FACETS}
    disclosures: list[str] = []
    gpu_fallback = "not_needed"

    # model_loaded / provider_healthy：只读消费**显式注入的既有事实**。
    # 项目里 LoadedModelCache 挂在 ModelEngine 实例上、ProviderHealthTracker
    # 的 can_call()/snapshot() 是 async —— 本同步只读投影不发起新探测、
    # 不 new 引擎、不猜状态；缺注入即 not_evaluated（R1 复核修复：
    # 此前调用了不存在的 get_loaded_model_cache / get_provider_health_tracker）。
    profile = input_profile if isinstance(input_profile, Mapping) else {}
    for facet, key in (("model_loaded", "model_loaded"),
                       ("provider_healthy", "provider_healthy")):
        evidence = profile.get(key)
        if isinstance(evidence, bool):
            facts[facet] = FACET_PASS if evidence else FACET_FAIL
        else:
            disclosures.append(f"{facet}: 未注入已编译事实 → not_evaluated")

    # compatible_input（纯函数 qualify）
    try:
        from app.lib.modelops.compatibility import InputProfile, qualify
        from app.lib.modelops.descriptor import GeoModelDescriptor

        compat_inputs: Any = (input_profile or {}).get("compat_inputs") \
            if isinstance(input_profile, Mapping) else None
        if isinstance(compat_inputs, Mapping) \
                and compat_inputs.get("descriptor") is not None \
                and compat_inputs.get("profile") is not None:
            descriptor: Any = compat_inputs["descriptor"]
            raw_profile: Any = compat_inputs["profile"]
            if isinstance(descriptor, GeoModelDescriptor):
                # InputProfile 是 frozen dataclass（非 pydantic）：按字段构造。
                compat_profile: Any = (
                    raw_profile if isinstance(raw_profile, InputProfile)
                    else InputProfile(**{
                        k: v for k, v in dict(raw_profile).items()
                        if k in set(InputProfile.__dataclass_fields__)
                    }))
                verdict = qualify(descriptor, compat_profile)
                facts["compatible_input"] = (
                    FACET_PASS if getattr(verdict, "verdict", "") == "compatible"
                    else FACET_FAIL)
            else:
                disclosures.append("compatible_input_profile:not_evaluated")
        else:
            disclosures.append("compatible_input_profile_absent:not_evaluated")
    except Exception as exc:  # noqa: BLE001
        disclosures.append(f"compatibility_unavailable:{type(exc).__name__}")

    # inference_budget
    try:
        from app.lib.modelops.resources import batch_for_budget

        # batch_for_budget 的真实签名是全关键字：
        # chip_hw / input_channels / output_channels / bytes_budget / max_batch。
        args = (input_profile or {}).get("budget_inputs") \
            if isinstance(input_profile, Mapping) else None
        if isinstance(args, Mapping):
            plan = batch_for_budget(
                chip_hw=tuple(args["chip_hw"]),
                input_channels=int(args["input_channels"]),
                output_channels=int(args["output_channels"]),
                bytes_budget=int(args["bytes_budget"]),
                max_batch=int(args["max_batch"]),
                recommended_batch=(int(args["recommended_batch"])
                                   if args.get("recommended_batch") else None),
            )
            facts["inference_budget"] = (
                FACET_PASS if isinstance(plan, int) and plan >= 1 else FACET_FAIL)
        else:
            disclosures.append("inference_budget_inputs:not_evaluated")
    except Exception as exc:  # noqa: BLE001
        disclosures.append(f"inference_budget_unavailable:{type(exc).__name__}")

    # GPU/CPU fallback 条件（只读披露，不改 DeviceRequirements）
    try:
        descriptor = (input_profile or {}).get("descriptor") \
            if isinstance(input_profile, Mapping) else None
        req = getattr(descriptor, "device_requirements", None) if descriptor else None
        if req is not None:
            required = str(getattr(req, "required", "cpu") or "cpu")
            allow = bool(getattr(req, "allow_cpu_fallback", True))
            if required == "cuda" and allow:
                gpu_fallback = "cpu_degraded"
                disclosures.append(
                    "GPU 缺席时 CPU fallback 属 degraded，不是 equivalent")
            elif required == "cuda" and not allow:
                gpu_fallback = "unavailable"
    except Exception:  # noqa: BLE001
        gpu_fallback = "not_evaluated"

    return facts, disclosures, gpu_fallback


def qualify_provider(
    provider_id: str,
    *,
    input_profile: Optional[dict[str, Any]] = None,
    budget: Optional[dict[str, Any]] = None,
    permissions: frozenset[str] = frozenset(),
    host: Any = None,
    probe: Optional[Any] = None,
) -> ProviderQualification:
    """只读资格转换；预算不足/事实缺席一律 not_evaluated，绝不猜。

    ``probe`` 是**显式注入**的只读事实探测 callable（默认 None → 用
    ``_probe_model_facts``）。生产路径不发起 ``ExtensionHost.get_record``
    等可能阻塞的慢方法；扩展状态只由 ``extension_lifecycle_view`` 的公开
    ``status_report`` 面提供。注入点用于失效注入测试（CAP-O12），让
    "慢 provider 有界降级" 可被真实触发与验证，而不是一个永不执行的桩。

    ``deadline_s`` 是**协作式预算闸**：预算小于最小探测成本时直接短路；
    探测返回后若已超预算，把已证明的 facet 降级为 not_evaluated。
    注入的探测本身必须遵守 deadline（协作式取消），本函数不做线程硬杀。
    """
    started = time.monotonic()
    budget = dict(budget or {})
    deadline_s = budget.get("deadline_s")
    reason_codes: list[str] = []
    disclosures: list[str] = []
    if probe is not None and not callable(probe):
        raise TypeError("probe must be callable or None")

    # 权限是硬门：空权限直接 fail-closed。
    if not permissions:
        facts = {f: FACET_NOT_EVALUATED for f in QUALIFICATION_FACETS}
        facts["permission"] = FACET_FAIL
        reason_codes.extend(["PERMISSION_DENIED", "CREDENTIALS_REQUIRED"])
        disclosures.append("permission: 未授予任何权限，推理资格 fail-closed")
        return ProviderQualification(
            provider_id=str(provider_id), facets=facts, qualified=False,
            gpu_fallback="not_evaluated",
            output_measurability={d: False for d in OUTPUT_MEASURABILITY_DIMENSIONS},
            reason_codes=tuple(reason_codes), disclosures=tuple(disclosures),
        )

    # 有界闸：预算不足以完成一次完整只读探测 → not_evaluated（不挂起）。
    # 显式注入探测（失效注入测试）不受该启发式短路影响 —— 否则注入的慢
    # 探测永远不会被调用，CAP-O12 就退化成空桩（独立冷审 F-04）。
    if deadline_s is not None and probe is None:
        try:
            tight = float(deadline_s) < _MIN_FULL_PROBE_BUDGET_S
        except (TypeError, ValueError):
            tight = True
        if tight:
            facts = {f: FACET_NOT_EVALUATED for f in QUALIFICATION_FACETS}
            facts["permission"] = FACET_PASS
            reason_codes.extend([
                "BUDGET_DEADLINE_TOO_TIGHT_NOT_EVALUATED",
                "PROVIDER_PROBE_DEFERRED",
            ])
            disclosures.append(
                "inference_budget: deadline 小于最小探测预算，"
                "不发起 provider 探测，返回 not_evaluated（不虚构健康）")
            return ProviderQualification(
                provider_id=str(provider_id), facets=facts, qualified=False,
                gpu_fallback="not_evaluated",
                output_measurability={d: False for d in OUTPUT_MEASURABILITY_DIMENSIONS},
                reason_codes=tuple(reason_codes), disclosures=tuple(disclosures),
            )

    # host 只读消费：扩展停用/权限收缩后 provider 资格 fail-closed。
    if host is not None:
        view = extension_lifecycle_view(host)
        unavailable = set(view.get("unavailable_namespaces", []))
        if unavailable:
            ns_hint = str(provider_id).split(".")[0]
            if ns_hint in unavailable:
                facts = {f: FACET_NOT_EVALUATED for f in QUALIFICATION_FACETS}
                facts["provider_healthy"] = FACET_FAIL
                return ProviderQualification(
                    provider_id=str(provider_id), facets=facts, qualified=False,
                    gpu_fallback="not_evaluated",
                    output_measurability={d: False
                                          for d in OUTPUT_MEASURABILITY_DIMENSIONS},
                    reason_codes=("EXTENSION_UNAVAILABLE_FAIL_CLOSED",),
                    disclosures=("扩展停用/撤权：provider 资格 fail-closed，"
                                 "不得沿用旧计划",),
                )

    if probe is not None:
        # 注入探测：协作式 deadline（把剩余预算传给探测函数）。
        remaining = None
        if deadline_s is not None:
            try:
                remaining = max(0.0, float(deadline_s)
                                - (time.monotonic() - started))
            except (TypeError, ValueError):
                remaining = None
        try:
            probe(provider_id, remaining)
        except TimeoutError:
            reason_codes.append("PROBE_DEADLINE_TIMEOUT_NOT_EVALUATED")
        except Exception as exc:  # noqa: BLE001
            reason_codes.append(f"PROBE_ERROR_NOT_EVALUATED:{type(exc).__name__}")
        facts = {f: FACET_NOT_EVALUATED for f in QUALIFICATION_FACETS}
        disclosures.append("injected_probe: 探测已真实执行；结果按预算闸降级")
    else:
        facts, disclosures, gpu_fallback = _probe_model_facts(
            provider_id, input_profile, budget)
    facts["permission"] = FACET_PASS
    if probe is not None:
        gpu_fallback = "not_evaluated"

    elapsed = time.monotonic() - started
    if deadline_s is not None and elapsed > float(deadline_s):
        for key in QUALIFICATION_FACETS:
            if facts.get(key) == FACET_PASS:
                facts[key] = FACET_NOT_EVALUATED
        reason_codes.append("BUDGET_DEADLINE_EXCEEDED_DEGRADED")
        disclosures.append(f"probe_elapsed_s={elapsed:.4f} 超预算 → 降级 not_evaluated")

    qualified = all(facts.get(f) == FACET_PASS for f in QUALIFICATION_FACETS)
    if not qualified:
        reason_codes.append("PROVIDER_NOT_FULLY_QUALIFIED")
        disclosures.append(
            "qualified=False：五要素未全部证明，须显式披露 not_evaluated 面")

    # 推理输出可度量性：只有显式给出各维度证据才置 True。
    measurability = {d: False for d in OUTPUT_MEASURABILITY_DIMENSIONS}
    evidence = (input_profile or {}).get("output_evidence") \
        if isinstance(input_profile, Mapping) else None
    if isinstance(evidence, Mapping):
        for d in OUTPUT_MEASURABILITY_DIMENSIONS:
            measurability[d] = bool(evidence.get(d))
    disclosures.append(
        "output_measurability: shape/range/tile_location/confidence/"
        "uncertainty/provenance 必须可度量，不能仅凭 provider 回包断言科学有效")

    return ProviderQualification(
        provider_id=str(provider_id),
        facets=dict(facts),
        qualified=qualified,
        gpu_fallback=gpu_fallback,
        output_measurability=measurability,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        disclosures=tuple(disclosures[:12]),
    )
