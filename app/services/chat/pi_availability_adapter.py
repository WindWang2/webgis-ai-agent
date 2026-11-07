"""Pi 工具面的**稳定只读消费者适配器**（G06 / CAP-02）。

边界纪律（G01）：本模块**不修改** ``pi_native_surface.py`` / ``agent_pi_bridge.py``
/ ``pi_rpc_client.py`` / Pi Extension 宿主生命周期。它把
``CapabilityAvailabilitySnapshot`` 投影成 Pi 可消费的有界工具描述 + 资格结果，
跨模块变更被限制在这个 adapter 内。

生产语义：
* 模型只看到**有界候选**（默认 8，硬上限 64）；
* 只投影 ``native_verified`` / ``available_unverified`` 的真实注册工具；
* ``declared_only`` / ``temporarily_unavailable`` / ``forbidden`` 一律不可见；
* 每个候选带 schema 指纹与未验证原因，供服务端严格校验对拍；
* 拒绝 hallucinated 工具名 / 参数多余字段（复用 ``validate_pi_call``）。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.lib.gis.capability_availability import CapabilityAvailabilitySnapshot

ADAPTER_SCHEMA_VERSION = "pi-availability-projection/v1"
VISIBLE_STATUSES = ("native_verified", "available_unverified")
DEFAULT_BOUND = 8
MAX_BOUND = 64


def project_pi_tool_surface(
    snapshot: CapabilityAvailabilitySnapshot,
    *,
    registry: Any = None,
    status_filter: Sequence[str] = ("native_verified",),
    bound: int = DEFAULT_BOUND,
) -> dict[str, Any]:
    """只读投影：把可用性快照转成有界、可对拍的 Pi 工具描述面。"""
    limit = max(1, min(int(bound), MAX_BOUND))
    allowed = set(status_filter) & set(VISIBLE_STATUSES)
    if not allowed:
        allowed = {"native_verified"}

    tools: list[dict[str, Any]] = []
    for rec in snapshot.records:
        if not rec.tool_id or rec.availability_status not in allowed:
            continue
        args_model = None
        required: list[str] = []
        if registry is not None and hasattr(registry, "args_model"):
            try:
                args_model = registry.args_model(rec.tool_id)
            except Exception:  # noqa: BLE001
                args_model = None
        if args_model is not None:
            required = sorted(
                n for n, f in getattr(args_model, "model_fields", {}).items()
                if f.is_required())
        tools.append({
            "name": rec.tool_id,
            "availability_status": rec.availability_status,
            "required": required,
            "unit_semantics": list(rec.units),
            "crs_semantics": rec.crs_semantics,
            "side_effect": rec.side_effect,
            "permission": rec.permission,
            "unverified_reasons": list(rec.unverified_reasons),
            "registry_generation": rec.registry_generation,
        })
        if len(tools) >= limit:
            break

    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "tools": tools,
        "returned": len(tools),
        "bound": limit,
        "truncated": len([
            r for r in snapshot.records
            if r.tool_id and r.availability_status in allowed
        ]) > len(tools),
        "snapshot_fingerprint": snapshot.content_fingerprint,
        "registry_generation": snapshot.registry_generation,
        "catalog_generation": snapshot.catalog_generation,
        "disclosure": (
            "本面只含真实注册且当前可用的工具；声明态/停用/越权能力不可见。"
            "服务端仍必须用 validate_pi_call 做工具名与参数严格校验。"),
    }


def validate_projected_call(
    tool_name: str, arguments: Mapping[str, Any],
    *, projection: Mapping[str, Any], snapshot: CapabilityAvailabilitySnapshot,
    registry: Any = None,
) -> tuple[bool, str]:
    """投影面 + 快照 + registry 三方严格校验（服务端唯一入口）。"""
    from app.lib.gis.availability_rerank import validate_pi_call

    names = {t["name"] for t in projection.get("tools", [])}
    if tool_name not in names:
        return False, (
            "tool_not_in_bounded_projection: 拒绝 hallucinated 工具名 / "
            "不可用工具 / 超出有界候选面")
    return validate_pi_call(
        tool_name, arguments, snapshot=snapshot, registry=registry)
