"""跨层唯一能力可用性只读投影（G06 / CAP-01）。

本模块**不是**注册中心：它把既有的权威源编译成一次只读快照 ——

* 工具执行真相：``ToolRegistry`` 的 ``descriptor()`` / ``descriptors()``；
* 八消费面对账：``tool_capability_conformance.audit_registry_conformance``；
* 目录代：``ToolRegistry.registry_fingerprint()`` 与
  ``ExecutionCatalog.generation_fingerprint``；
* 声明面：``CapabilityRegistry`` / ``AlgorithmRegistry``；
* 扩展认证：``build_certification_index_from_host`` 的只读证据。

设计纪律
--------
1. **声明/历史通过 != 当前环境可执行**。只有八面 overall=pass、descriptor
   可执行、且 registration/dispatch/result 三面都有在场证据的工具才标
   ``native_verified``；其余真实注册工具一律 ``available_unverified`` 并带
   显式未验证原因。缺依赖/证据缺席不猜测，落到 ``not_evaluated`` 披露。
2. **单一理由码**。``reason_codes`` 直接取自 ``FaceVerdict.reason``，
   不新造第二套校验逻辑或空挂码位（见 ``FALLBACK_CODE_XWALK`` 纪律）。
3. **只读 + 有界**。快照是 frozen dataclass；查询支持过滤/分页/上限；
   权限隔离在查询面 fail-closed 并脱敏，绝不把明细泄露给越权调用方。
4. **确定性**。指纹只由 records + 目录代 + 认证索引摘要构成，不含时间戳、
   不含随机源；同代编译字节稳定。

CLI::

    python -m app.lib.gis.capability_availability --json --status native_verified
    python -m app.lib.gis.capability_availability --tool zonal_stats --limit 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

AVAILABILITY_SCHEMA_VERSION = "capability-availability/v1"

#: 可信状态词表（封闭）。注意 ``not_evaluated`` 是**理由**而非状态：
#: 无法判定时降到 ``available_unverified`` / ``temporarily_unavailable``
#: 并在 ``unverified_reasons`` 里显式写 ``not_evaluated``，不猜。
AVAILABILITY_STATUS_VOCABULARY: tuple[str, ...] = (
    "native_verified",
    "available_unverified",
    "declared_only",
    "temporarily_unavailable",
    "forbidden",
)

NOT_EVALUATED_REASON = "not_evaluated"
MAX_RECORDS = 1024
MAX_QUERY_LIMIT = 256
VERIFIABLE_FACES = ("registration", "dispatch", "result")
NON_EXECUTABLE_STATUSES = ("planned", "external_unavailable", "hidden")

# forbidden 记录必须脱敏的字段（能力细节不得跨权限边界泄露）。
DETAIL_FIELDS: tuple[str, ...] = (
    "input_artifacts", "output_artifacts", "fields", "units",
    "crs_semantics", "geometry_requirements", "evidence_sources",
    "scientific_status", "resource_class", "side_effect", "determinism",
)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8"),
                          usedforsecurity=False).hexdigest()[:32]


def _short_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8"),
                          usedforsecurity=False).hexdigest()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


@dataclass(frozen=True)
class AvailabilityRecord:
    """一条「能力 → 算法 → 工具」的只读资格记录。"""

    capability_id: str = ""
    algorithm_id: str = ""
    tool_id: str = ""
    provider: str = "core"
    scientific_status: str = ""
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    crs_semantics: str = ""
    geometry_requirements: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    resource_class: str = ""
    side_effect: str = ""
    determinism: str = "unknown"
    ownership: str = ""
    permission: str = ""
    extension_trust: str = "core"
    availability_status: str = AVAILABILITY_STATUS_VOCABULARY[2]
    evidence_sources: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    unverified_reasons: tuple[str, ...] = ()
    registry_generation: str = ""
    tool_version_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "algorithm_id": self.algorithm_id,
            "tool_id": self.tool_id,
            "tool_version_id": self.tool_version_id,
            "provider": self.provider,
            "scientific_status": self.scientific_status,
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "crs_semantics": self.crs_semantics,
            "geometry_requirements": list(self.geometry_requirements),
            "fields": list(self.fields),
            "units": list(self.units),
            "resource_class": self.resource_class,
            "side_effect": self.side_effect,
            "determinism": self.determinism,
            "ownership": self.ownership,
            "permission": self.permission,
            "extension_trust": self.extension_trust,
            "availability_status": self.availability_status,
            "evidence_sources": list(self.evidence_sources),
            "reason_codes": list(self.reason_codes),
            "unverified_reasons": list(self.unverified_reasons),
            "registry_generation": self.registry_generation,
        }


@dataclass(frozen=True)
class CapabilityAvailabilitySnapshot:
    """一次编译、处处只读的可用性快照（compile once, read everywhere）。"""

    schema_version: str = AVAILABILITY_SCHEMA_VERSION
    records: tuple[AvailabilityRecord, ...] = ()
    registry_generation: str = ""
    catalog_generation: str = ""
    content_fingerprint: str = ""
    counts: Mapping[str, int] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    truncated: bool = False
    degraded_sources: tuple[str, ...] = ()

    def to_dict(self, *, include_records: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "registry_generation": self.registry_generation,
            "catalog_generation": self.catalog_generation,
            "content_fingerprint": self.content_fingerprint,
            "counts": dict(sorted(self.counts.items())),
            "diagnostics": list(self.diagnostics),
            "truncated": self.truncated,
            "degraded_sources": list(self.degraded_sources),
        }
        if include_records:
            out["records"] = [r.to_dict() for r in self.records]
        return out

    def for_tool(self, tool_name: str) -> Optional[AvailabilityRecord]:
        for rec in self.records:
            if rec.tool_id == tool_name or rec.tool_version_id.startswith(
                    f"{tool_name}@"):
                return rec
        return None


# ── 编译 ──────────────────────────────────────────────────────────────

def _extension_state_digest() -> str:
    """扩展宿主状态的只读摘要（停用/激活/撤权都会改变它）。

    只用公共 API；宿主缺席返回稳定空摘要，不虚构扩展状态。
    """
    try:
        from app.extensions_platform.host import get_extension_host

        host = get_extension_host()
        if host is None:
            return ""
        report = host.status_report()
        # 只取确定性字段：worker_pid / diagnostics 等运行时抖动不进指纹，
        # 但 state / trust / version 变化（激活、停用、升级、撤权）必须进。
        stable = [
            [e.get("id"), e.get("namespace"), e.get("version"),
             e.get("state"), e.get("trust"), e.get("execution"),
             e.get("permissions"), e.get("granted_permissions")]
            for e in report.get("extensions", [])
        ]
        return _digest(_canonical(stable))
    except Exception:  # noqa: BLE001 — 宿主投影失败诚实留白
        return ""


def _certification_digest(certification_index: Optional[Mapping]) -> str:
    if not certification_index:
        return ""
    return _digest(_canonical(dict(certification_index)))


def compile_availability_snapshot(
    *,
    registry: Any = None,
    bootstrap: bool = False,
    certification_index: Optional[Mapping[str, Mapping[str, Any]]] = None,
    max_records: int = MAX_RECORDS,
) -> CapabilityAvailabilitySnapshot:
    """从既有权威源编译可用性快照（只读、确定性、有界）。

    ``bootstrap=True`` 时用 ``collect_live_facts(bootstrap=True)`` 离线构建
    完整注册面；显式 ``registry`` 时以注入实例为准（测试/扩展热刷新用）。
    """
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.execution_catalog import compile_execution_catalog
    from app.lib.gis.tool_capability_conformance import (
        STATUS_FAIL, STATUS_NOT_EVALUATED, STATUS_PASS, STATUS_WARNING,
        audit_registry_conformance, collect_live_facts,
    )

    diagnostics: list[str] = []
    degraded: list[str] = []

    facts = collect_live_facts(registry=registry, bootstrap=bootstrap)
    reg = facts.registry
    conformance = audit_registry_conformance(facts)
    reports = {r.tool: r for r in conformance.reports}

    cap_reg = get_capability_registry()
    algo_reg = get_algorithm_registry()
    cap_ids = set(cap_reg.all_ids)
    algo_ids = set(algo_reg.all_ids)

    # 目录代：工具注册面自己的指纹（新增/改 schema 立即按代变化）。
    try:
        registry_generation = str(reg.registry_fingerprint() or "")
    except Exception:  # noqa: BLE001
        registry_generation = str(facts.registry_generation or "")
        degraded.append("tool_registry_fingerprint")

    # catalog_generation 来自既有 ExecutionCatalog 的权威指纹。
    try:
        catalog_generation = str(compile_execution_catalog(
            tool_registry=reg,
            certification_index=dict(certification_index or {}),
        ).generation_fingerprint or "")
    except Exception as exc:  # noqa: BLE001 — 单源失败不拖垮整体投影
        catalog_generation = ""
        degraded.append("execution_catalog")
        diagnostics.append(f"execution_catalog_unavailable: {type(exc).__name__}")

    cert_digest = _certification_digest(certification_index)
    host_digest = _extension_state_digest()

    # 工具 → 真实 capability / algorithm 绑定（只保留注册表内真 id）。
    descriptors: Mapping[str, Any] = {}
    try:
        descriptors = reg.descriptors()
    except Exception as exc:  # noqa: BLE001
        degraded.append("descriptors")
        diagnostics.append(f"descriptors_unavailable: {type(exc).__name__}")

    records: list[AvailabilityRecord] = []
    for name in sorted(descriptors):
        desc = descriptors[name]
        status = _enum_value(getattr(desc, "status", "")).lower()
        report = reports.get(name)

        face_pass: list[str] = []
        reasons: list[str] = []
        unverified: list[str] = []
        provider_deps = tuple(getattr(desc, "provider_dependencies", ()) or ())
        if report is not None:
            for v in report.faces:
                if v.status == STATUS_PASS:
                    face_pass.append(v.face)
                elif v.status in (STATUS_WARNING, STATUS_FAIL, STATUS_NOT_EVALUATED):
                    if v.reason and v.reason not in reasons:
                        reasons.append(str(v.reason)[:48])
                    if v.status != STATUS_NOT_EVALUATED:
                        unverified.append(
                            f"{v.face}:{v.reason or v.status}")
            if report.overall != STATUS_PASS:
                unverified.append(f"conformance_overall:{report.overall}")
        else:
            unverified.append("conformance_report_missing:not_evaluated")

        # ── 可用性判定（CAP-O02 硬约束）─────────────────────────────
        declared_only_reason = ""
        if status == "planned" or not getattr(desc, "executable", True):
            declared_only_reason = "tool_status_planned"
        elif status in ("hidden",):
            declared_only_reason = "tool_hidden"

        verifiable = (
            report is not None
            and report.overall == STATUS_PASS
            and all(any(v.face == f and v.status == STATUS_PASS
                        for v in report.faces) for f in VERIFIABLE_FACES)
            and not provider_deps
        )

        if declared_only_reason:
            availability = "declared_only"
            unverified = [f"{declared_only_reason}:not_evaluated"]
        elif status == "external_unavailable" or bool(provider_deps):
            availability = "temporarily_unavailable"
            unverified = ["provider_unavailable:not_evaluated"]
        elif verifiable:
            availability = "native_verified"
            unverified = []  # native_verified 不得带未验证原因
        else:
            availability = "available_unverified"
            if not unverified:
                unverified = ["evidence_incomplete:not_evaluated"]

        # 真实绑定：只取注册表内存在的 id，避免悬空引用进入投影。
        caps = tuple(c for c in (getattr(desc, "capabilities", ()) or ())
                     if c in cap_ids)[:4]
        algos = tuple(a for a in (getattr(desc, "algorithms", ()) or ())
                      if a in algo_ids)[:4]
        cap_desc = cap_reg.get(caps[0]) if caps else None
        algo_desc = algo_reg.get(algos[0]) if algos else None

        if not caps and cap_desc is None and availability != "native_verified":
            unverified = [*unverified, "capability_binding_absent:not_evaluated"]

        unit = str(getattr(desc, "unit_semantics", "") or "")
        produced = tuple(getattr(desc, "produced_refs", ()) or ())
        out_type = str(getattr(desc, "output_semantic_type", "") or "")
        resource = "/".join(x for x in (
            str(getattr(desc, "latency_class", "") or ""),
            str(getattr(desc, "memory_class", "") or ""),
            str(getattr(desc, "scale_class", "") or ""),
        ) if x and x != "unknown") or "unknown"
        det = getattr(desc, "deterministic", None)
        determinism = "unknown" if det is None else (
            "deterministic" if det else "nondeterministic")
        req_perm = str(getattr(desc, "required_permission", "") or "")

        records.append(AvailabilityRecord(
            capability_id=caps[0] if caps else "",
            algorithm_id=algos[0] if algos else "",
            tool_id=name,
            tool_version_id=str(getattr(desc, "tool_id", "") or ""),
            provider=(tuple(getattr(desc, "provider_dependencies", ()) or ())
                      or ("core",))[0],
            scientific_status=_enum_value(
                getattr(algo_desc, "scientific_status", "") if algo_desc else ""),
            input_artifacts=tuple(getattr(desc, "input_artifacts", ()) or ()),
            output_artifacts=produced or ((out_type,) if out_type else ()),
            crs_semantics=str(getattr(desc, "crs_semantics", "") or ""),
            geometry_requirements=tuple(
                getattr(cap_desc, "geometry_requirements", ()) or ()) if cap_desc else (),
            fields=tuple(getattr(desc, "required_fields", ()) or ()),
            units=(unit,) if unit else (),
            resource_class=resource,
            side_effect=_enum_value(getattr(desc, "side_effect", "")),
            determinism=determinism,
            ownership="",  # core 工具无 owner 限定；扩展归属由认证索引决定
            permission=req_perm,
            extension_trust="core",
            availability_status=availability,
            evidence_sources=tuple(sorted(face_pass)),
            reason_codes=tuple(sorted(set(reasons))),
            unverified_reasons=tuple(unverified),
            registry_generation=registry_generation,
        ))

    # 声明态能力：只有声明、没有真实可执行工具 → declared_only（tool_id 空）。
    tool_backed_caps = {r.capability_id for r in records if r.capability_id}
    for cid in sorted(cap_reg.all_ids):
        if cid in tool_backed_caps:
            continue
        cap = cap_reg.get(cid)
        records.append(AvailabilityRecord(
            capability_id=cid,
            algorithm_id="",
            tool_id="",
            provider="core",
            scientific_status=_enum_value(getattr(cap, "status", "")),
            geometry_requirements=tuple(
                getattr(cap, "geometry_requirements", ()) or ()),
            availability_status="declared_only",
            unverified_reasons=("declared_only_no_registered_tool:not_evaluated",),
            registry_generation=registry_generation,
        ))
        if len(records) >= max_records:
            break

    truncated = len(records) > max_records
    kept = tuple(records[:max_records])
    counts: dict[str, int] = {s: 0 for s in AVAILABILITY_STATUS_VOCABULARY}
    for rec in kept:
        counts[rec.availability_status] = counts.get(rec.availability_status, 0) + 1
    counts["total"] = len(kept)

    if conformance.truncated:
        diagnostics.append("conformance_reports_truncated")
    if truncated:
        diagnostics.append(f"records_truncated_to_{max_records}")
    if not cert_digest:
        diagnostics.append("certification_index_absent:not_evaluated")

    payload = {
        "schema_version": AVAILABILITY_SCHEMA_VERSION,
        "registry_generation": registry_generation,
        "catalog_generation": catalog_generation,
        "certification": cert_digest,
        "extension_host": host_digest,
        "records": [
            [r.capability_id, r.algorithm_id, r.tool_id, r.tool_version_id,
             r.availability_status, list(r.reason_codes),
             list(r.unverified_reasons), r.permission, r.ownership]
            for r in kept
        ],
    }
    return CapabilityAvailabilitySnapshot(
        schema_version=AVAILABILITY_SCHEMA_VERSION,
        records=kept,
        registry_generation=registry_generation,
        catalog_generation=catalog_generation,
        content_fingerprint=_short_digest(_canonical(payload)),
        counts=counts,
        diagnostics=tuple(sorted(set(diagnostics))),
        truncated=truncated or bool(conformance.truncated),
        degraded_sources=tuple(sorted(set(degraded))),
    )


# ── 只读查询 / 诊断 ────────────────────────────────────────────────────

def _redacted(rec: AvailabilityRecord) -> dict[str, Any]:
    """权限不足时的脱敏投影：保留身份与理由，清空能力细节。"""
    out = rec.to_dict()
    for key in DETAIL_FIELDS:
        out[key] = () if isinstance(out.get(key), list) else ""
    out["availability_status"] = "forbidden"
    out["reason_codes"] = ["PERMISSION_DENIED"]
    out["unverified_reasons"] = ["permission_scope_fail_closed:not_evaluated"]
    return out


def query_availability(
    snapshot: CapabilityAvailabilitySnapshot,
    *,
    status: Optional[str] = None,
    capability_id: Optional[str] = None,
    tool_id: Optional[str] = None,
    provider: Optional[str] = None,
    owner: Optional[str] = None,
    permissions: Optional[frozenset[str]] = None,
    limit: int = 64,
    offset: int = 0,
) -> dict[str, Any]:
    """只读诊断查询：过滤 + 分页 + 内容指纹 + 显式未验证原因。

    ``permissions=None`` 表示内部诊断视图（无隔离上下文，全可见）；
    **显式**传入集合（含空集合）才启用权限隔离 —— 空集合 = fail-closed，
    所有记录降级为脱敏的 ``forbidden``。这样「未指定上下文」与「零权限」
    不会混淆，也符合最小惊讶原则。
    """
    if status is not None and status not in AVAILABILITY_STATUS_VOCABULARY:
        raise ValueError(
            f"unknown availability status {status!r}; "
            f"vocabulary={AVAILABILITY_STATUS_VOCABULARY}")

    matched: list[dict[str, Any]] = []
    unverified_reasons: list[str] = []
    for rec in snapshot.records:
        if status and rec.availability_status != status:
            continue
        if capability_id and rec.capability_id != capability_id:
            continue
        if tool_id and rec.tool_id != tool_id:
            continue
        if provider and rec.provider != provider:
            continue

        allowed = True
        if permissions is not None:
            perm_ok = not rec.permission or rec.permission in permissions
            owner_ok = not rec.ownership or (
                owner is not None and rec.ownership == owner)
            allowed = bool(permissions) and perm_ok and owner_ok

        if allowed:
            matched.append(rec.to_dict())
            unverified_reasons.extend(rec.unverified_reasons)
        else:
            matched.append(_redacted(rec))

    total = len(matched)
    safe_limit = max(0, min(int(limit), MAX_QUERY_LIMIT))
    safe_offset = max(0, int(offset))
    page = matched[safe_offset:safe_offset + safe_limit]
    return {
        "schema_version": AVAILABILITY_SCHEMA_VERSION,
        "total": total,
        "returned": len(page),
        "offset": safe_offset,
        "limit": safe_limit,
        "truncated": total > safe_offset + safe_limit,
        "records": page,
        "content_fingerprint": snapshot.content_fingerprint,
        "registry_generation": snapshot.registry_generation,
        "catalog_generation": snapshot.catalog_generation,
        "unverified_reasons": tuple(sorted(set(unverified_reasons))[:32]),
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="capability-availability",
        description="只读查询 GIS 能力可用性快照（G06 CAP-01）")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--bootstrap", action="store_true",
                        help="离线构建完整 ToolRegistry（无 lifespan 时用）")
    parser.add_argument("--status", choices=AVAILABILITY_STATUS_VOCABULARY)
    parser.add_argument("--capability")
    parser.add_argument("--tool", dest="tool_id")
    parser.add_argument("--provider")
    parser.add_argument("--owner")
    parser.add_argument("--permissions", default="",
                        help="逗号分隔；留空表示不启用隔离")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args(argv)

    # CLI 一律离线 bootstrap（无需 lifespan / API Key / PostGIS）。
    snap = compile_availability_snapshot(bootstrap=True)
    perms = (frozenset(p.strip() for p in args.permissions.split(",") if p.strip())
             if args.permissions else None)
    result = query_availability(
        snap, status=args.status, capability_id=args.capability,
        tool_id=args.tool_id, provider=args.provider, owner=args.owner,
        permissions=perms, limit=args.limit, offset=args.offset)
    lines: list[str] = []
    if args.json:
        lines.append(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        lines.append(
            f"schema={snap.schema_version} "
            f"generation={snap.registry_generation} "
            f"catalog={snap.catalog_generation} "
            f"fingerprint={snap.content_fingerprint}")
        lines.append(f"counts={dict(sorted(snap.counts.items()))}")
        lines.append(f"total={result['total']} returned={result['returned']} "
                     f"truncated={result['truncated']}")
        for row in result["records"]:
            lines.append(
                f"  {row['availability_status']:<24} "
                f"{row['tool_id'] or '(declared)':<42} "
                f"cap={row['capability_id']} "
                f"reasons={','.join(row['unverified_reasons'][:2])}")
        for diag in snap.diagnostics:
            lines.append(f"  diagnostic: {diag}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
