"""G42 WP01 —— 修复动作 → 可执行绑定矩阵（纯裁决，零副作用）。

每个 ``goal_repair_plan.actions[]`` 行 → 绑定裁决：

- ``executable`` + 真实执行入口（@tool 名 / 既有服务函数 + 服务端派生参数源）；
- ``needs_acknowledgment`` —— 数据交付存在格式损失确认义务（复用 G18 既有确认面）；
- ``needs_user_input`` —— 数据缺口/用户决定（停止请求，不发明默认参数）；
- ``unsupported`` —— 无既有安全执行入口（typed blocked，保留原需求）。

红线：

- **服务端重建**：绑定只消费服务端权威状态（存储块 / 章节 / map_state /
  registry 元数据）；客户端自报的 action/path/tool 名一律不信任（R07）；
- ``path`` 字符串在 repair_actions 里只是披露面 —— 这里以真实调用链为准重新
  裁决，不做字符串路径猜测；
- retry_step / rerun_complete：章节行不携带完整工具实参 → 无法建立「明确
  输入/资格/幂等」保证 → typed blocked（不自动重放未知副作用）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.services.gis_harness.goal_satisfaction.requirements import (
    DATA_DELIVERY_FORMATS,
)

from .contracts import (
    DISPOSITION_EXECUTABLE,
    DISPOSITION_NEEDS_ACK,
    DISPOSITION_NEEDS_USER,
    DISPOSITION_UNSUPPORTED,
    MAX_ACTIONS,
)

#: 地图出版导出格式（webgis_map_export 真实支持集）。
MAP_EXPORT_FORMATS = frozenset({"pdf", "png", "svg"})

#: G18 DeliveryFormat 真实词表 + G28 需求侧别名归一（"shp" 等历史写法 →
#: G18 合法格式；不在两者之内的交付格式 = typed unsupported，不猜默认值）。
DELIVERY_FORMAT_ALIASES = {
    "shp": "shapefile_zip",
    "shapefile": "shapefile_zip",
    "gdb": "", "filegdb": "", "kml": "", "kmz": "", "geojsonseq": "",
}
DELIVERY_FORMATS = frozenset({
    "geojson", "gpkg", "geoparquet", "csv", "shapefile_zip", "flatgeobuf",
})


def normalize_delivery_format(fmt: str) -> str:
    """需求侧格式 token → G18 合法 DeliveryFormat（空串 = 不支持）。"""
    token = _fmt_token(fmt)
    if token in DELIVERY_FORMATS:
        return token
    return DELIVERY_FORMAT_ALIASES.get(token, "")

#: 执行入口词表（与真实注册工具/服务一一对应；仅披露，非可执行权限）。
ENTRY_MAP_EXPORT = "webgis_map_export"
ENTRY_DATA_DELIVERY = "submit_data_delivery"
ENTRY_RUNTIME_REPAIR = "gis_harness.runtime_repair"
ENTRY_REOBSERVE = "render_observation_wait"
ENTRY_REREAD = "session_state_read"
ENTRY_REQUALIFY = "data_qualification_read"


def _fmt_token(fmt: str) -> str:
    return str(fmt or "").strip().lower()[:16]


def plan_version_for(plan: Dict[str, Any]) -> str:
    """计划指纹（动作身份的稳定摘要；同计划 = 同 attempt 幂等键成分）。"""
    actions = plan.get("actions") if isinstance(plan, dict) else None
    ident = [
        f"{str(a.get('action') or '')[:32]}:"
        f"{str(a.get('requirement_id') or '')[:64]}:"
        f"{str(a.get('rule') or '')[:48]}"
        for a in (actions or [])[:MAX_ACTIONS]
        if isinstance(a, dict)
    ]
    blob = json.dumps(
        {"goal_id": str((plan or {}).get("goal_id") or "")[:128],
         "actions": ident},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    # Stable version key for server-reconstructed actions, not a credential.
    return hashlib.sha1(blob, usedforsecurity=False).hexdigest()[:16]


def _requirement_rows(stored_goal: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """goal_satisfaction 块 → requirement_id → 有界行。"""
    rows: Dict[str, Dict[str, Any]] = {}
    gs = stored_goal if isinstance(stored_goal, dict) else {}
    for row in (gs.get("requirements") or [])[:24]:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("requirement_id") or "")[:64]
        if rid:
            rows[rid] = row
    return rows


def _latest_done_ref(chapter: Optional[Dict[str, Any]]) -> str:
    """章节内最新完成行的 bound_ref（交付源派生；缺席 = ""）。"""
    if not isinstance(chapter, dict):
        return ""
    best_ref, best_idx = "", -1
    for section in ("analysis_steps", "map_layers"):
        for i, row in enumerate(chapter.get(section) or []):
            if not isinstance(row, dict):
                continue
            ref = str(row.get("bound_ref") or "").strip()
            status = str(row.get("status") or "").strip().lower()
            if ref.startswith("ref:") and status in ("done", "complete", "completed"):
                # analysis 行优先于 map_layers；同段内后行 = 更新完成事实。
                rank = i + (1000 if section == "analysis_steps" else 0)
                if rank > best_idx:
                    best_ref, best_idx = ref[:256], rank
    return best_ref


def adjudicate_actions(
    plan: Dict[str, Any],
    *,
    stored_goal: Optional[Dict[str, Any]] = None,
    chapter: Optional[Dict[str, Any]] = None,
    mapspec_revision: int = 0,
) -> List[Dict[str, Any]]:
    """计划动作 → 绑定矩阵（纯函数；不产生任何副作用）。

    返回行形状（有界）::

        {"action_id", "action", "requirement_id", "rule", "side_effect",
         "budget_class", "expected_new_evidence", "disposition", "reason",
         "entry", "params"（服务端派生的有界参数源披露）}
    """
    req_rows = _requirement_rows(stored_goal)
    out: List[Dict[str, Any]] = []
    for idx, act in enumerate((plan.get("actions") or [])[:MAX_ACTIONS]):
        if not isinstance(act, dict):
            continue
        action = str(act.get("action") or "")[:32]
        rid = str(act.get("requirement_id") or "")[:64]
        req = req_rows.get(rid, {})
        # 格式源：持久行优先；真实 finalizer 块的 requirement 行不带
        # export_format/delivery_expected（G28 报告形状），但需求 id 按
        # 构造即 ``export:<fmt>``（requirements.py）—— 从 id 恢复，不猜。
        fmt = _fmt_token(req.get("export_format")) or (
            rid.split(":", 1)[1] if rid.startswith("export:") else "")
        delivery_expected = bool(req.get("delivery_expected")) or (
            fmt in DATA_DELIVERY_FORMATS)
        binding = {
            "action_id": f"a{idx + 1}",
            "action": action,
            "requirement_id": rid,
            "rule": str(act.get("rule") or "")[:48],
            "side_effect": str(act.get("side_effect") or "read_only")[:16],
            "budget_class": str(act.get("budget_class") or "repair")[:16],
            "expected_new_evidence":
                str(act.get("expected_new_evidence") or "")[:64],
            "disposition": DISPOSITION_UNSUPPORTED,
            "reason": "",
            "entry": "",
            "params": {},
        }
        _adjudicate_one(
            binding, action=action, rid=rid, fmt=fmt,
            delivery_expected=delivery_expected, chapter=chapter,
            mapspec_revision=mapspec_revision)
        out.append(binding)
    return out


def _adjudicate_one(
    binding: Dict[str, Any], *, action: str, rid: str, fmt: str,
    delivery_expected: bool, chapter: Optional[Dict[str, Any]],
    mapspec_revision: int,
) -> None:
    if action == "reobserve":
        binding.update(
            disposition=DISPOSITION_EXECUTABLE,
            entry=ENTRY_REOBSERVE,
            reason="等待当前 MapSpec 修订的真实浏览器新观察（同修订新序号）",
        )
    elif action == "stale_reread":
        binding.update(
            disposition=DISPOSITION_EXECUTABLE,
            entry=ENTRY_REREAD,
            reason="只读重读当前权威状态；重终验自然消费新事实",
        )
    elif action == "requalify":
        binding.update(
            disposition=DISPOSITION_EXECUTABLE,
            entry=ENTRY_REQUALIFY,
            reason="只读数据资格重判（单位/时间基准/CRS 披露）",
        )
    elif action == "resolve_data":
        binding.update(
            disposition=DISPOSITION_NEEDS_USER,
            reason="数据缺口需用户补充范围/数据源 —— 不自动重放或遍历工具",
        )
    elif action == "repair_cartography":
        binding.update(
            disposition=DISPOSITION_EXECUTABLE,
            entry=ENTRY_RUNTIME_REPAIR,
            reason="复用既有 runtime_repair（CAS + 用户锁 + 预算内部自理）",
        )
    elif action in ("reexport", "export_product"):
        if not fmt:
            binding["reason"] = "需求未携带导出格式 —— 无法派生参数源"
        elif fmt in MAP_EXPORT_FORMATS:
            binding.update(
                disposition=DISPOSITION_EXECUTABLE,
                entry=ENTRY_MAP_EXPORT,
                reason="真实地图导出（blocking 语义保留；CAS 绑当前修订）",
                params={"format": fmt,
                        "expected_revision": int(mapspec_revision or 0)},
            )
        elif delivery_expected or fmt in DATA_DELIVERY_FORMATS:
            _bind_delivery(binding, fmt=fmt, chapter=chapter)
        else:
            binding["reason"] = f"格式 {fmt} 不在地图导出或数据交付支持集内"
    elif action == "run_delivery":
        if not fmt:
            binding["reason"] = "需求未携带交付格式 —— 无法派生参数源"
        else:
            _bind_delivery(binding, fmt=fmt, chapter=chapter)
    elif action in ("retry_step", "rerun_complete"):
        # 章节行不携带完整工具实参 → 无法建立「明确输入/资格/幂等」保证；
        # 既有恢复面（[GIS Recovery] 指令行）已负责把失败步骤路由回 agent。
        binding.update(
            disposition=DISPOSITION_UNSUPPORTED,
            reason="原计划步骤无完整实参/幂等保证 —— 需经新一轮 agent 恢复指令执行",
        )
    else:
        binding["reason"] = f"未注册动作 {action}"


def _bind_delivery(
    binding: Dict[str, Any], *, fmt: str, chapter: Optional[Dict[str, Any]],
) -> None:
    normalized = normalize_delivery_format(fmt)
    if not normalized:
        binding.update(
            disposition=DISPOSITION_UNSUPPORTED,
            reason=f"交付格式 {fmt} 不在数据交付支持集（G18 词表）内",
        )
        return
    ref = _latest_done_ref(chapter)
    if not ref:
        binding.update(
            disposition=DISPOSITION_NEEDS_USER,
            reason="会话内无可交付的完成数据引用（bound_ref）—— 需先补数据/分析",
        )
        return
    binding.update(
        # 损失确认义务在 preview/动作时经 preview_delivery 复核（真实词表）；
        # 绑定期不预判 —— 未确认前零提交（R09）。
        disposition=DISPOSITION_NEEDS_ACK,
        entry=ENTRY_DATA_DELIVERY,
        reason="数据成果交付（预览复检 + 损失确认 + durable job 既有链）",
        params={"format": normalized, "source_ref": ref[:256]},
    )


def executable_selection(bindings: List[Dict[str, Any]]) -> List[str]:
    """默认可选集（disposition ∈ executable/needs_acknowledgment）。"""
    return [str(b.get("action_id") or "") for b in bindings
            if b.get("disposition") in (DISPOSITION_EXECUTABLE,
                                        DISPOSITION_NEEDS_ACK)][:MAX_ACTIONS]


def delivery_ack_codes(preview: Any) -> List[str]:
    """G18 preview → required_acknowledgments（有界；异常 = 空，不猜）。"""
    try:
        codes = list(getattr(preview, "required_acknowledgments", None) or [])
    except Exception:  # noqa: BLE001
        return []
    return [str(c)[:48] for c in codes[:8]]


def delivery_blocking_issues(preview: Any) -> List[str]:
    try:
        issues = list(getattr(preview, "blocking_issues", None) or [])
    except Exception:  # noqa: BLE001
        return []
    out: List[str] = []
    for iss in issues[:4]:
        code = getattr(iss, "code", "")
        msg = getattr(iss, "message", "")
        out.append(f"{str(code)[:40]}: {str(msg)[:120]}".strip(": "))
    return out


__all__ = [
    "MAP_EXPORT_FORMATS", "ENTRY_MAP_EXPORT", "ENTRY_DATA_DELIVERY",
    "ENTRY_RUNTIME_REPAIR", "ENTRY_REOBSERVE", "ENTRY_REREAD",
    "ENTRY_REQUALIFY",
    "plan_version_for", "adjudicate_actions", "executable_selection",
    "delivery_ack_codes", "delivery_blocking_issues",
]
