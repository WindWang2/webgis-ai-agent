"""Map Series freeze plan（WP3）—— 计划层面 pin（可序列化 JSON），不复制渲染字节。

简报红线 6：本 WP 只做 plan 结构与 preview；真实生产执行在 WP5 —— 本模块
**不做任何渲染**。冻结语义 = 成员引用 + 指纹物化：

- 成员身份 = ``session_id + mapspec_fingerprint``（store 权威指纹）；
- ``data_fingerprints`` 来自来源层的 ``data_fingerprint``，ref 绑定只记
  **描述指纹**（ref 串的 sha256 短摘要），绝不内嵌 ref: 游标本身；
- ``print_layout_digest`` 为 G34 计划的结构 digest（不携带计划副本）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.map_series import service as ms_service
from app.services.map_series.contract import structural_digest
from app.services.map_series.store import get_series, get_version, resolve_payload

FREEZE_PLAN_VERSION = "map_series_freeze.v1"


def _short_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _data_fingerprints(mapspec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """来源层数据指纹投影（data_digest 优先；ref 只记描述指纹）。"""
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for source_id, src in list(sources.items())[:16]:
        if not isinstance(src, dict):
            continue
        if src.get("data_fingerprint"):
            out[str(source_id)] = {
                "kind": "fingerprint",
                "value": str(src["data_fingerprint"])[:96],
            }
        elif src.get("ref"):
            # 描述指纹：ref 游标本身不进冻结计划（逐图绑定各自会话）。
            out[str(source_id)] = {
                "kind": "ref",
                "ref_digest": _short_digest(str(src["ref"])),
            }
        elif src.get("inlineData") is not None:
            out[str(source_id)] = {
                "kind": "inline",
                "content_digest": _short_digest(src["inlineData"]),
            }
        else:
            out[str(source_id)] = {"kind": "unfingered", "type": str(src.get("type") or "")}
    return out


async def build_freeze_plan(
    db: Any,
    series_id: str,
    *,
    spec_version: Optional[int] = None,
) -> Dict[str, Any]:
    """物化冻结计划 JSON（只读；不渲染、不生产）。

    成员条目：``{member_id, session_id, mapspec_fingerprint,
    print_layout_digest?, data_fingerprints, title, role,
    design_spec_id + version, output_profile}``。不可解析成员如实单列。
    """
    series = get_series(db, series_id)
    if spec_version is not None:
        row = get_version(db, series_id, int(spec_version))
        plan_payload, _chain = resolve_payload(db, row)
    else:
        plan_payload = series.payload or {}

    from app.services.design_specs.versioning import get_spec

    spec_pin: Dict[str, Any]
    try:
        spec = get_spec(db, str(plan_payload.get("design_spec_id") or ""))
        pinned = plan_payload.get("design_spec_version") or spec.current_version
        spec_pin = {
            "design_spec_id": spec.id,
            "version": int(pinned or 1),
            "retired": spec.retired_at is not None,  # 软废弃 pin 兼容，如实披露
        }
    except Exception as exc:  # noqa: BLE001 —— pin 不可解析：计划仍产出，披露原因
        spec_pin = {
            "design_spec_id": str(plan_payload.get("design_spec_id") or ""),
            "version": plan_payload.get("design_spec_version"),
            "unresolvable": str(exc)[:160],
        }

    members: List[Dict[str, Any]] = []
    unresolvable: List[str] = []
    for member in plan_payload.get("members") or []:
        if not isinstance(member, dict) or not member.get("enabled", True):
            continue
        session_id = str(member.get("session_id") or "")
        mapspec = await ms_service.load_member_mapspec(session_id)
        if mapspec is None:
            unresolvable.append(str(member.get("member_id") or ""))
            continue
        fingerprint = await ms_service.member_fingerprint(session_id, mapspec)
        print_layout = mapspec.get("print_layout")
        entry: Dict[str, Any] = {
            "member_id": str(member.get("member_id") or ""),
            "session_id": session_id,
            "mapspec_fingerprint": fingerprint,
            "title": str(member.get("title") or ""),
            "role": str(member.get("role") or "primary"),
            "design_spec_id": spec_pin.get("design_spec_id"),
            "design_spec_version": spec_pin.get("version"),
            "output_profile": plan_payload.get("output") or {},
            "data_fingerprints": _data_fingerprints(mapspec),
        }
        if isinstance(print_layout, dict):
            entry["print_layout_digest"] = "pl-" + structural_digest(print_layout)
        members.append(entry)

    return {
        "plan_version": FREEZE_PLAN_VERSION,
        "series_id": series.id,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "structural_digest": series.structural_digest,
        "spec_pin": spec_pin,
        "dimension": plan_payload.get("dimension") or {},
        "output_profile": plan_payload.get("output") or {},
        "members": members,
        "unresolvable_member_ids": unresolvable,
        "note": "plan-level pin only; production rendering is out of scope (WP5)",
    }


__all__ = ["build_freeze_plan", "FREEZE_PLAN_VERSION"]
