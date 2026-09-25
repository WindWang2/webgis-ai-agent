"""Registry drift 检测 + 决策重推导比对（方向 8 / ADR-0212 决策四）。

回答「benchmark 能否定位**哪里变了**」：

- ``capability_registry_digest``：capability graph 行为面（节点 id/kind/
  status/version + capability→provider 边 + fallback/conflict 边）的规范
  投影 sha256 —— 录制时进 ``ReplayTrace.env``，重放时对当前 registry
  重算比对，漂移即显式披露（drift ≠ fail，但 digest 漂移有了归因面）。
  进程图按 graph 实例记忆化（ADR-0214 D2：graph 指纹未变 = 同实例 = 零
  重投影；录制热路径 ~255ms/turn 的重复投影成本归零）。
- ``collect_env_fingerprint``：环境指纹 v2（封闭白名单）—— 行为开关布尔
  / policy 版本 / source 内容指纹 / runtime manifest 指纹 / 预算文件摘要
  / python-platform 标识。绝不收录任意 env 值。
- ``env_drift``：v2 环境指纹的分类比对 —— 输出「哪一类环境事实变了」
  （registry/policy/runtime_flags/sources/manifest/budgets/runtime），
  `behavioral=True` 表示影响决策行为的环境面（ADR-0214 D2）。
- ``rederive_capability_decision``：用决策记录里冻结的 inputs
  （capability + situation）离线重跑同源 ``capability_status``，重建
  决策的 selected/alternatives —— 决策级 delta 把「digest 变了」钉到
  「哪个能力的哪个 provider 排序/资格变了」。
- ``diff_decisions``：按 decision_id / kind 对齐两组决策记录，输出
  结构化差异（selected_changed / alternatives_changed / added / removed）。

确定性、无 I/O（env 指纹的只读收集除外）、离线（capability graph 是
静态数据面）。
"""
from __future__ import annotations

import threading
import weakref
from typing import Any, Dict, List, Optional

from app.lib.harness.replay.determinism import canonical_json, sha256_of

#: capability_resolution 决策种类（与 runtime.decision_record 词表一致；
#: 复制为常量避免生产 runtime 模块进入重放器导入面）。
_KIND_CAPABILITY_RESOLUTION = "capability_resolution"
_KIND_PLAN_SELECTION = "plan_selection"
_KIND_DISPATCH_DENIAL = "capability_dispatch_denial"


# ── registry digest ──────────────────────────────────────────────────────────

#: 进程图 digest 记忆化（ADR-0214 D2）：graph 实例为弱引用键 —— 指纹未变
#: 时 ``get_capability_graph`` 返回同一实例，命中零重投影；重建即新实例，
#: 旧条目随之失效。显式传入 ``graph=`` 的调用方（测试 stub）不走记忆化。
_DIGEST_MEMO: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()
#: source 指纹 / 预算摘要记忆化（同键纪律）：collect_env_fingerprint 在录制
#: 缝逐 turn 调用，而 source_fingerprints 全量迭代注册表（冷态秒级）——
#: graph 实例未变即注册表面未变，重复投影是纯浪费。
_SOURCES_MEMO: "weakref.WeakKeyDictionary[Any, Dict[str, str]]" = \
    weakref.WeakKeyDictionary()
_BUDGETS_MEMO: Dict[str, str] = {}
_DIGEST_MEMO_LOCK = threading.Lock()


def _current_graph() -> Any:
    """当前权威图实例（高频只读面专用）：缓存命中走零成本探针，未构建
    才走全量路径（重算 source_fingerprints ~0.5s，录制缝逐 turn 调用
    时不可接受）。缺席 → None（诚实降级）。"""
    try:
        from app.services.gis_harness.capability_graph import (
            get_cached_capability_graph,
            get_capability_graph,
        )

        cached = get_cached_capability_graph()
        if cached is not None:
            return cached
        return get_capability_graph()
    except Exception:  # noqa: BLE001 — 图缺席按无录制
        return None


def capability_registry_digest(*, graph: Any = None) -> str:
    """capability registry 行为面的规范 sha256（确定性、有界投影）。

    参与面 = 会改变决策的行为事实：节点 id/kind/status/version、
    capability→providers 边、fallback 链、conflicts。标签/描述文案
    **不参与**（不改行为的文案变化不制造 drift 噪声）。
    """
    try:
        memoizable = graph is None
        g = graph
        if g is None:
            g = _current_graph()
        if g is None:
            return ""
        if memoizable:
            with _DIGEST_MEMO_LOCK:
                cached = _DIGEST_MEMO.get(g)
            if cached is not None:
                return cached
        digest = _compute_registry_digest(g)
        if memoizable:
            with _DIGEST_MEMO_LOCK:
                _DIGEST_MEMO[g] = digest
        return digest
    except Exception:  # noqa: BLE001 — registry 缺席 → 空 digest（诚实缺席）
        return ""


def _compute_registry_digest(g: Any) -> str:
    projection: List[Dict[str, Any]] = []
    for kind in ("capability", "algorithm", "tool", "model", "workflow",
                 "methodology", "template", "execution_backend"):
        nodes_fn = getattr(g, "nodes_by_kind", None)
        if nodes_fn is None:
            break
        for node in sorted(nodes_fn(kind), key=lambda n: str(n.id)):
            entry: Dict[str, Any] = {
                "id": str(node.id),
                "kind": str(getattr(node, "kind", kind)),
            }
            extras = getattr(node, "extras", None) or {}
            for key in ("status", "version", "deterministic",
                        "offline_capable", "side_effect", "scale_class"):
                if key in extras and extras[key] is not None:
                    entry[key] = str(extras[key])
            projection.append(entry)
    # 边（行为面）：capability → providers / fallback / conflicts。
    # capability_providers 生产契约是 Dict[face, List[id]]（tools/
    # models/workflows/templates）—— 逐面排序后整体入投影（review
    # P1-1：对 dict 直接 sorted() 只取键名，provider 重接线不可见）。
    edges: List[Dict[str, Any]] = []
    cap_nodes = g.nodes_by_kind("capability") if hasattr(g, "nodes_by_kind") else []
    for node in sorted(cap_nodes, key=lambda n: str(n.id)):
        cap_id = str(node.id)
        providers_raw = (
            g.capability_providers(cap_id)
            if hasattr(g, "capability_providers") else {}
        )
        if isinstance(providers_raw, dict):
            providers: Any = {
                str(face): sorted(str(p) for p in ids)[:8]
                for face, ids in sorted(providers_raw.items())
                if ids
            }
        else:  # 旧形态 / 测试 stub 容错
            providers = sorted(str(p) for p in providers_raw)
        fallbacks = (
            [str(f) for f in g.fallback_chain("capability", cap_id)]
            if hasattr(g, "fallback_chain") else []
        )
        conflicts = (
            sorted(str(c) for c in g.conflicts_of_capability(cap_id))
            if hasattr(g, "conflicts_of_capability") else []
        )
        edges.append({
            "capability": cap_id,
            "providers": providers,
            "fallbacks": fallbacks,
            "conflicts": conflicts,
        })
    return sha256_of({"nodes": projection, "edges": edges})


def registry_drift(recorded_digest: str, current_digest: str) -> Optional[Dict[str, Any]]:
    """录制 vs 当前 registry 指纹比对（缺席侧不制造假漂移）。"""
    if not recorded_digest or not current_digest:
        return None
    if recorded_digest == current_digest:
        return None
    return {
        "kind": "registry_drift",
        "recorded_digest": recorded_digest[:16],
        "current_digest": current_digest[:16],
        "hint": "capability registry changed between record and replay; "
                "decision deltas below are attributable to it",
    }


# ── 环境指纹 v2（ADR-0214 D2：封闭白名单 + drift 分类）───────────────────────

ENV_SCHEMA_VERSION = 2

#: 行为开关白名单：``(env 名, 缺省态, 解析模式)``。解析模式必须与各生产
#: 闸的**自有语义逐字对齐**（review P2-1）：`off_is_0` = 仅 "0" 关
#: （GOVERNOR_TOOL_SURFACE / GIS_ANALYSIS_REUSE / SPATIAL_GUARDRAILS 的
#: `!= "0"` 判定）；`truthy` = 0/false/off/no 关（capability bind 的
#: _env_truthy）；`on_values` = 仅 1/true 开（HARNESS_REPLAY_RECORD）；
#: `nonempty_on` = 任意非空值即开（CARTO_VISUAL_JUDGE）。
#: 只收录行为开关；**绝不收录任意 env 值**（秘密/路径/凭证禁入）。
_RUNTIME_FLAG_DEFAULTS = (
    ("GIS_CAPABILITY_DISPATCH_BIND", True, "truthy"),
    ("GOVERNOR_TOOL_SURFACE", True, "off_is_0"),
    ("GIS_ANALYSIS_REUSE", True, "off_is_0"),
    ("SPATIAL_GUARDRAILS", True, "off_is_0"),
    ("HARNESS_REPLAY_RECORD", False, "on_values"),
    ("CARTO_VISUAL_JUDGE", False, "nonempty_on"),
)

#: 行为面段（变化 = behavioral drift）；其余段（python/platform）为环境面。
_BEHAVIORAL_SECTIONS = frozenset({
    "registry_digest", "policy_versions", "runtime_flags", "sources",
    "manifest", "budgets_digest",
})


def _flag_value(name: str, default: bool, mode: str) -> bool:
    """开关缺省态 + env 覆盖（解析模式与各生产闸逐字对齐，review P2-1）。"""
    import os

    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    stripped = raw.strip().lower()
    if mode == "off_is_0":
        # 生产：`os.getenv(name, "1") != "0"` —— 只有字面 "0" 关。
        return raw != "0"
    if mode == "truthy":
        # 生产 capability_bind._env_truthy：0/false/off/no 关。
        return stripped not in ("0", "false", "off", "no")
    if mode == "on_values":
        # 生产 recorder：取值 in ("1", "true", "True") 开。
        return raw in ("1", "true", "True")
    if mode == "nonempty_on":
        return True
    return default


def collect_env_fingerprint() -> Dict[str, Any]:
    """环境指纹 v2（录制面）：封闭白名单投影，缺席段诚实 'absent'。

    全部段有界、确定性；失败段降级 'absent'，绝不抛（录制面纪律）。
    """
    import sys

    env: Dict[str, Any] = {"env_schema_version": ENV_SCHEMA_VERSION}
    env["registry_digest"] = capability_registry_digest()
    env["python_version"] = ".".join(str(p) for p in sys.version_info[:3])
    env["platform"] = sys.platform
    flags: Dict[str, bool] = {}
    for name, default, mode in _RUNTIME_FLAG_DEFAULTS:
        try:
            flags[name] = _flag_value(name, default, mode)
        except Exception:  # noqa: BLE001 — 单键失败不拖垮指纹
            flags[name] = default
    env["runtime_flags"] = flags

    policy_versions: Dict[str, str] = {}
    for label, module, attr in (
        ("capability_resolution",
         "app.services.gis_harness.capability_resolution",
         "CAPABILITY_RESOLUTION_POLICY_VERSION"),
        ("capability_dispatch_bind",
         "app.services.gis_harness.hotpath_convergence.capability_bind",
         "CAPABILITY_BIND_POLICY_VERSION"),
        ("plan_aggregation", "app.services.governor.plan_aggregation",
         "AGGREGATE_VERSION"),
    ):
        try:
            import importlib

            policy_versions[label] = str(
                getattr(importlib.import_module(module), attr))
        except Exception:  # noqa: BLE001 — 缺席诚实披露
            policy_versions[label] = "absent"
    env["policy_versions"] = policy_versions

    try:
        sources: Dict[str, str] = {}
        graph_for_memo = _current_graph()
        if graph_for_memo is not None:
            with _DIGEST_MEMO_LOCK:
                cached_sources = _SOURCES_MEMO.get(graph_for_memo)
            if cached_sources is not None:
                sources = cached_sources
            else:
                sources = _compute_source_fingerprints()
                with _DIGEST_MEMO_LOCK:
                    _SOURCES_MEMO[graph_for_memo] = sources
        env["sources"] = sources
        env["manifest"] = sources.get("runtime_manifest", "absent")
    except Exception:  # noqa: BLE001
        env["sources"] = {}
        env["manifest"] = "absent"

    env["budgets_digest"] = _budgets_digest()
    return env


def _compute_source_fingerprints() -> Dict[str, str]:
    """source 指纹的有界投影（只在图实例记忆化 miss 时算一次）。"""
    try:
        from app.services.gis_harness.capability_graph import (
            source_fingerprints,
        )

        return {str(k): str(v)[:32]
                for k, v in sorted(source_fingerprints().items())}
    except Exception:  # noqa: BLE001
        return {}


def _budgets_digest() -> str:
    """governor 预算文件内容摘要（mtime 失效缓存；缺席 → 'absent'）。"""
    try:
        from pathlib import Path

        path = Path("config/governor_budgets.json")
        if not path.is_file():
            _BUDGETS_MEMO.clear()
            return "absent"
        stat = path.stat()
        cached = _BUDGETS_MEMO.get("mtime")
        if cached is not None and cached == stat.st_mtime \
                and "digest" in _BUDGETS_MEMO:
            return _BUDGETS_MEMO["digest"]
        import hashlib

        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:32]
        _BUDGETS_MEMO["mtime"] = stat.st_mtime
        _BUDGETS_MEMO["digest"] = digest
        return digest
    except Exception:  # noqa: BLE001
        return "absent"


def env_drift(recorded: Any, current: Any) -> List[Dict[str, Any]]:
    """v2 环境指纹分类比对（「裸 hash 变了」→「哪类环境事实变了」）。

    输出条目：``{kind, behavioral, changed_keys}``（有界 ≤16）。任一侧
    缺席 / schema 版本不一致 → 诚实返回 []（不制造假漂移；v1 录制件
    只有 registry_digest，其漂移仍由 registry_drift 显式披露）。
    """
    if not isinstance(recorded, dict) or not isinstance(current, dict):
        return []
    if recorded.get("env_schema_version") != current.get("env_schema_version"):
        return []
    drifts: List[Dict[str, Any]] = []

    def _add(kind: str, changed: List[str]) -> None:
        if changed:
            drifts.append({
                "kind": kind,
                "behavioral": kind in _BEHAVIORAL_SECTIONS,
                "changed_keys": [str(k)[:96] for k in changed[:8]],
            })

    if str(recorded.get("registry_digest") or "") \
            != str(current.get("registry_digest") or ""):
        _add("registry_digest", ["registry_digest"])
    for section in ("policy_versions", "runtime_flags", "sources"):
        base = recorded.get(section) if isinstance(recorded.get(section), dict) else {}
        cur = current.get(section) if isinstance(current.get(section), dict) else {}
        changed = [
            k for k in sorted(set(base) | set(cur))
            if str(base.get(k)) != str(cur.get(k))
        ]
        _add(section, changed)
    if str(recorded.get("manifest") or "") != str(current.get("manifest") or ""):
        _add("manifest", ["manifest"])
    if str(recorded.get("budgets_digest") or "") \
            != str(current.get("budgets_digest") or ""):
        _add("budgets_digest", ["budgets_digest"])
    for section in ("python_version", "platform"):
        if str(recorded.get(section) or "") != str(current.get(section) or ""):
            _add(section, [section])
    return drifts[:16]


# ── 决策重推导（capability_resolution 面的确定性重跑）────────────────────────


def rederive_capability_decision(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """用决策记录冻结的 inputs 重跑 capability_status → 重建决策投影。

    只支持 ``capability_resolution`` 种类（inputs 携带 capability +
    situation；resolve 侧是纯函数，离线可重跑）。其余种类返回 None
    （plan_selection 需要意图对象重建，dispatch denial 需要 registry
    fixture —— 见 ADR-0212 §2 决策五的边界）。
    """
    if not isinstance(record, dict) \
            or record.get("kind") != _KIND_CAPABILITY_RESOLUTION:
        return None
    inputs = record.get("inputs") or {}
    capability_id = str(inputs.get("capability") or "")
    if not capability_id:
        return None
    situation = _situation_from_projection(inputs.get("situation"))
    try:
        from app.services.gis_harness.capability_resolution import (
            capability_status,
        )

        status, ranked, rejected = capability_status(
            capability_id, situation)
    except Exception:  # noqa: BLE001 — 重推导缺席不制造假 delta
        return None
    best = ranked[0] if ranked else None
    alternatives = []
    for cand in ranked[:6]:
        alternatives.append({
            "id": str(cand.id),
            "score": round(float(cand.score), 4),
            "status": str(cand.qualification.status),
        })
    for cand in rejected[:2]:
        alternatives.append({
            "id": str(cand.id),
            "status": str(cand.qualification.status),
        })
    rederived = {
        "kind": _KIND_CAPABILITY_RESOLUTION,
        "selected": (f"{best.kind}:{best.id}" if best else ""),
        "status": status,
        "alternatives": alternatives,
    }
    recorded_selected = str(record.get("selected") or "")
    recorded_alts = [
        {"id": str(a.get("id") or ""),
         **({"score": round(float(a["score"]), 4)}
            if isinstance(a.get("score"), (int, float)) else {}),
         **({"status": str(a["status"])}
            if a.get("status") else {})}
        for a in (record.get("alternatives") or [])[:8]
        if isinstance(a, dict)
    ]
    rederived["decision_id"] = str(record.get("decision_id") or "")
    rederived["capability"] = capability_id
    rederived["recorded_selected"] = recorded_selected
    rederived["recorded_alternatives"] = recorded_alts
    return rederived


def _situation_from_projection(projection: Any) -> Any:
    """决策 inputs 里的 situation 投影 → QualificationContext（缺席默认）。

    与 ``QualificationContext.to_rederive_dict()`` 的全息快照字段一一
    对应（review P1-3：资格判定读取的 credentials_present /
    dependency_available / field_names 等必须还原，否则 rederive 会在
    被重置的默认上下文上重跑 → 假 delta）。旧有损投影（to_dict 形态）
    缺这些键时按缺席诚实降级（unknown），不猜值。
    """
    from app.services.gis_harness.qualification_v8 import QualificationContext

    ctx = QualificationContext()
    if not isinstance(projection, dict):
        return ctx
    simple = {
        "task_hint", "geometry_kinds", "crs", "crs_is_geographic",
        "field_names", "feature_count", "raster_bands",
        "resolution_m_per_px", "sensor", "temporal_inputs", "data_bytes",
        "map_layer_count", "gpu_available", "vram_bytes", "memory_bytes",
        "max_latency_class", "owner_scope_key", "offline", "auth_tier",
        "budget_cost_class", "quality_gate", "dependency_available",
        "credentials_present",
    }
    for key in simple:
        if key in projection and projection[key] is not None:
            try:
                setattr(ctx, key, projection[key])
            except (TypeError, ValueError):
                pass
    blocking = projection.get("blocking_issue_codes")
    if isinstance(blocking, list):
        ctx.blocking_issue_codes = [str(c)[:64] for c in blocking[:16]]
    return ctx


# ── decision delta ───────────────────────────────────────────────────────────


def decisions_digest(decisions: List[Dict[str, Any]]) -> str:
    """决策序列的行为摘要（不含墙钟/关联 id 的确定性投影）。"""
    projection = [
        {
            "kind": str(d.get("kind") or ""),
            "selected": str(d.get("selected") or ""),
            "inputs_digest": str(d.get("inputs_digest") or ""),
            "alternatives": [
                {"id": str(a.get("id") or ""),
                 "score": a.get("score"),
                 "status": str(a.get("status") or "")}
                for a in (d.get("alternatives") or [])[:8]
                if isinstance(a, dict)
            ],
        }
        for d in (decisions or [])[:16]
        if isinstance(d, dict)
    ]
    return sha256_of(projection)


def diff_decisions(baseline: List[Dict[str, Any]],
                   current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """两组决策记录的结构化差异（「哪里变了」的决策级归因）。

    对齐：先按 decision_id（同 id 但 selected/alternatives 变化 → 精确
    归因），再按 kind 序配对剩余条目（id 变化的重推导面）。输出有界
    （≤32 条 diff）。
    """
    diffs: List[Dict[str, Any]] = []

    def _alt_key(entry: Dict[str, Any]) -> str:
        return canonical_json({
            "id": entry.get("id"),
            "score": entry.get("score"),
            "status": entry.get("status"),
        })

    base_ids = {
        str(d.get("decision_id") or "")
        for d in (baseline or []) if isinstance(d, dict) and d.get("decision_id")
    }
    base_by_id = {
        str(d.get("decision_id") or ""): d
        for d in (baseline or []) if isinstance(d, dict) and d.get("decision_id")
    }
    cur_by_id = {
        str(d.get("decision_id") or ""): d
        for d in (current or []) if isinstance(d, dict) and d.get("decision_id")
    }
    for did, cur in cur_by_id.items():
        base = base_by_id.pop(did, None)
        if base is None:
            continue
        if str(base.get("selected") or "") != str(cur.get("selected") or ""):
            diffs.append({
                "type": "selected_changed", "decision_id": did,
                "kind": str(cur.get("kind") or ""),
                "baseline": str(base.get("selected") or ""),
                "current": str(cur.get("selected") or ""),
            })
        base_alts = [_alt_key(a) for a in (base.get("alternatives") or [])
                     if isinstance(a, dict)]
        cur_alts = [_alt_key(a) for a in (cur.get("alternatives") or [])
                    if isinstance(a, dict)]
        if base_alts != cur_alts:
            diffs.append({
                "type": "alternatives_changed", "decision_id": did,
                "kind": str(cur.get("kind") or ""),
                "baseline_count": len(base_alts),
                "current_count": len(cur_alts),
            })
    for did, cur in cur_by_id.items():
        if did not in base_ids:
            diffs.append({
                "type": "added", "decision_id": did,
                "kind": str(cur.get("kind") or ""),
                "selected": str(cur.get("selected") or ""),
            })
    for did, base in base_by_id.items():
        diffs.append({
            "type": "removed", "decision_id": did,
            "kind": str(base.get("kind") or ""),
            "selected": str(base.get("selected") or ""),
        })
    return diffs[:32]


__all__ = [
    "ENV_SCHEMA_VERSION",
    "capability_registry_digest",
    "registry_drift",
    "collect_env_fingerprint",
    "env_drift",
    "rederive_capability_decision",
    "decisions_digest",
    "diff_decisions",
]
