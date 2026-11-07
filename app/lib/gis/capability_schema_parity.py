"""跨 registry → Pi → dispatch → slim payload 的 schema / 摘要对拍（G06 / CAP-06）。

纯**只读诊断**：不修改 ``llm_result_formatter.py`` 的压缩器（Issue #2006 由
其独立修复线负责，本线不抢），但把「摘要丢数值且无可分页 ref」这一失真
显式暴露为 ``SUMMARY_VALUE_LOSS`` 诊断，供 CAP-O14 回归门消费。

对拍维度（同源、不许各写一份）：
* 工具名：``ToolRegistry.tool_names()``；
* required 参数：``ToolRegistry.args_model(name).model_fields``（唯一权威）；
* 量纲：``ToolDescriptor.unit_semantics`` / ``crs_semantics``；
* 摘要：``llm_result_formatter.slim_tool_result`` 的实际输出；
* Pi 面：``pi_native_surface`` 的 native / execute 归属（只读对拍，不改）。
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

PARITY_SCHEMA_VERSION = "capability-schema-parity/v1"
CODE_SUMMARY_VALUE_LOSS = "SUMMARY_VALUE_LOSS"
CODE_SCHEMA_DRIFT = "SCHEMA_REQUIRED_DRIFT"
CODE_UNIT_DRIFT = "UNIT_SEMANTICS_DRIFT"
CODE_NAME_DRIFT = "TOOL_NAME_DRIFT"

#: 回归保护的历史工具（名称 / 调用签名不得漂移）。
LEGACY_TOOLS: tuple[str, ...] = (
    "zonal_stats", "buffer_analysis", "network_od_matrix",
    "idw_interpolation", "spatial_aggregate",
)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _required_from_model(model: Any) -> set[str]:
    try:
        return {n for n, f in model.model_fields.items() if f.is_required()}
    except Exception:  # noqa: BLE001
        return set()


def _schema_required(schemas: Mapping[str, Any], name: str) -> Optional[set[str]]:
    for entry in schemas.values() if isinstance(schemas, dict) else (schemas or []):
        fn = entry.get("function", entry) if isinstance(entry, Mapping) else {}
        nm = fn.get("name") if isinstance(fn, Mapping) else None
        if nm != name:
            continue
        params = fn.get("parameters", {}) if isinstance(fn, Mapping) else {}
        if isinstance(params, Mapping) and "required" in params:
            return set(str(x) for x in (params.get("required") or []))
    return None


def _summary_probe(payload: dict[str, Any]) -> dict[str, Any]:
    """用既有 slim_tool_result 实测摘要是否保留关键数值或可分页 ref。"""
    from app.services.llm_result_formatter import slim_tool_result

    raw = _canonical(payload)
    try:
        slim = slim_tool_result(payload, raw, None)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": f"slim_error:{type(exc).__name__}",
                "slim": "", "raw": raw}
    text = str(slim)
    numbers = [str(v) for v in _numeric_values(payload)]
    has_value = any(n and n in text for n in numbers[:8])
    has_ref = any(k in text.lower() for k in ("ref", "artifact", "cursor", "page"))
    return {
        "ok": bool(has_value or has_ref),
        "has_value": has_value,
        "has_ref": has_ref,
        "code": "" if (has_value or has_ref) else CODE_SUMMARY_VALUE_LOSS,
        "slim_preview": text[:160],
        "raw_len": len(raw),
        "slim_len": len(text),
    }


def _numeric_values(obj: Any, _depth: int = 0) -> list[float]:
    out: list[float] = []
    if _depth > 8:
        return out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, Mapping):
        for v in obj.values():
            out.extend(_numeric_values(v, _depth + 1))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_numeric_values(v, _depth + 1))
    return out


def parity_report(
    *, registry: Any = None, snapshot: Any = None,
    bootstrap: bool = False, max_tools: int = 512,
) -> dict[str, Any]:
    """生成跨面一致性对拍报告（只读，确定性）。"""
    if registry is None:
        if snapshot is not None and getattr(snapshot, "records", None):
            from app.tools import init_tools
            from app.tools.registry import ToolRegistry

            registry = ToolRegistry()
            init_tools(registry)
        else:
            from app.lib.gis.tool_capability_conformance import collect_live_facts

            registry = collect_live_facts(bootstrap=bootstrap or True).registry

    names = sorted(str(n) for n in registry.tool_names())[:max_tools]
    try:
        schemas = registry.get_schemas()
    except Exception:  # noqa: BLE001
        schemas = []

    tools: dict[str, Any] = {}
    diagnostics: list[str] = []
    for name in names:
        model = None
        try:
            model = registry.args_model(name)
        except Exception:  # noqa: BLE001
            pass
        required = _required_from_model(model) if model is not None else set()
        schema_required = _schema_required(schemas, name)
        desc_required: set[str] = set()
        unit = ""
        summary = ""
        try:
            desc = registry.descriptor(name)
            desc_required = set(str(x) for x in (desc.required_fields or ()))
            unit = str(desc.unit_semantics or "")
            summary = str(desc.summary or "")
        except Exception:  # noqa: BLE001
            pass

        entry: dict[str, Any] = {
            "name": name,
            "required": sorted(required),
            "required_params": sorted(required),
            "descriptor_required_fields": sorted(desc_required),
            "unit_semantics": unit,
            "summary": summary,
            "drift_codes": [],
        }
        if schema_required is not None and schema_required != required:
            entry["drift_codes"].append(CODE_SCHEMA_DRIFT)
            entry["schema_required"] = sorted(schema_required)
            diagnostics.append(f"{CODE_SCHEMA_DRIFT}:{name}")
        if desc_required and not desc_required.issubset(required):
            entry["drift_codes"].append("DESCRIPTOR_REQUIRED_SUPERSET")
            diagnostics.append(f"DESCRIPTOR_REQUIRED_SUPERSET:{name}")
        # 量纲声明与工具名语义对拍（网络时间 ≠ 米制距离）。
        if "distance" in name and unit and unit.lower() in ("seconds", "s", "min"):
            entry["drift_codes"].append(CODE_UNIT_DRIFT)
            diagnostics.append(f"{CODE_UNIT_DRIFT}:{name}")
        tools[name] = entry

    for legacy in LEGACY_TOOLS:
        if legacy not in tools:
            diagnostics.append(f"{CODE_NAME_DRIFT}:{legacy}_absent")

    # 结果摘要可信度：构造含关键统计数值的代表性封套，实测 slim 行为。
    probe_payload = {
        "data": {"count": 42, "mean": 3.14, "zones": 7},
        "artifact": {"ref": "artifact://capability-parity-probe",
                     "page_size": 50, "pages": 1},
    }
    summary_probe = _summary_probe(probe_payload)
    if summary_probe["code"]:
        diagnostics.append(summary_probe["code"])
    # 无论当前是否触发，都把该码列为受监控的诊断（供回归门显式检索）。
    monitored = [
        CODE_SUMMARY_VALUE_LOSS, CODE_SCHEMA_DRIFT,
        CODE_UNIT_DRIFT, CODE_NAME_DRIFT,
    ]

    # Pi 面归属只读对拍（不修改 pi_native_surface）。
    pi_surface: dict[str, Any] = {}
    try:
        from app.services.chat.pi_native_surface import (
            EXECUTE_PROXY_NAME, NATIVE_TOOL_NAMES, resolve_pi_tool_call,
        )

        pi_surface = {
            "native_tools": sorted(NATIVE_TOOL_NAMES),
            "execute_proxy": EXECUTE_PROXY_NAME,
            "native_wrapped_rejected": resolve_pi_tool_call(
                EXECUTE_PROXY_NAME,
                {"toolName": sorted(NATIVE_TOOL_NAMES)[0], "arguments": {}},
                registered_surface=names,
            ).kind == "reject",
        }
    except Exception as exc:  # noqa: BLE001
        pi_surface = {"diagnostic": f"pi_surface_not_evaluated:{type(exc).__name__}"}

    return {
        "schema_version": PARITY_SCHEMA_VERSION,
        "tool_count": len(tools),
        "tools": tools,
        "entries": tools,          # 兼容消费者按 entries 读取
        "summary_probe": summary_probe,
        "monitored_codes": monitored,
        "pi_surface": pi_surface,
        "diagnostics": sorted(set(diagnostics))[:64],
    }
