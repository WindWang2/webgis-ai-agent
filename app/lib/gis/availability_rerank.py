"""可用性 / 输入类型 / 科学约束感知的工具候选重排（G06 / CAP-02）。

本模块是 ``tool_retrieval.rank_tools`` 与 ``tool_semantic_retrieval`` 之上的
**只读重排层**，不替换既有检索器、不新建索引、不改 Pi 宿主。

三条不可突破的规则
------------------
1. 不可用工具（``declared_only`` / ``temporarily_unavailable`` /
   ``forbidden``）**硬过滤**，无论向量相似度多高；进 ``suppressed`` 并披露理由。
2. ``native_verified`` 加权、``available_unverified`` 降权但保留；
   hallucinated 工具名一律拒绝。
3. 结果有界：``max_candidates`` 是硬上限，截断时 ``bounded=True`` 并披露。

中英 GIS 术语黄金语料见 ``app/lib/data/capability_lexicon_golden.json``
（置于 .gitignore 白名单目录，注释见 .gitignore:217-225）：
正例（缓冲 / 空间连接 / 分区统计 / 路网时间 / 等时圈 / 密度 / 通视 / 插值 /
地理综合）+ 负例（秒≠米、停用扩展的高相似度工具、hallucinated 工具名）。
"""
from __future__ import annotations

import json
from itertools import islice
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.lib.gis.capability_availability import (
    AVAILABILITY_STATUS_VOCABULARY, CapabilityAvailabilitySnapshot,
)

# 诊断/离线对拍允许宽面（<=512），生产 Pi 面由调用方传更小的 max_candidates
# （CAP-02 的「有界候选」由调用方预算决定，此处是防御性硬上限）。
_MAX_CANDIDATES = 512
# F-09（独立冷审）：输入侧也必须有界，防止超大/无限 iterable 被全量物化。
_MAX_INPUT_CANDIDATES = 4096
# 语料放在 .gitignore 白名单目录 app/lib/data/（未锚定的 data/ 规则会
# 静默吞掉任意同名子目录 —— 见 .gitignore:216-223 的历史事故注释）。
_GOLDEN_PATH = (Path(__file__).resolve().parents[1] / "data"
                / "capability_lexicon_golden.json")

#: 不可激活状态（硬过滤，不因相似度高而放行）。
_BLOCKED_STATUSES = frozenset({
    "declared_only", "temporarily_unavailable", "forbidden",
})
#: 时间语义触发词（中英）；命中后米制距离类工具必须被披露/抑制。
_TIME_TERMS = (
    "秒", "行程时间", "出行时间", "路网时间", "时间成本", "分钟",
    "travel time", "seconds", "time cost", "duration",
)
#: 米制距离工具名特征（与「秒」语境冲突）。
_METRIC_TOOL_TOKENS = ("distance", "buffer", "euclid", "proximity_m")
#: 中文 GIS 术语 → 检索加权关键词（离线、确定性，不接公网）。
_TERM_HINTS: Mapping[str, tuple[str, ...]] = {
    "缓冲": ("buffer", "缓冲"),
    "空间连接": ("spatial_join", "join", "连接"),
    "分区统计": ("zonal_stats", "zonal", "分区", "aggregate"),
    "路网时间": ("network_od_matrix", "isochrone_network", "network", "时间"),
    "等时圈": ("isochrone", "等时"),
    "密度": ("heatmap", "density", "kernel", "密度"),
    "通视": ("viewshed", "通视"),
    "插值": ("interpolation", "idw", "kriging", "插值"),
    "地理综合": ("generalization", "simplif", "dissolve", "综合"),
}


@dataclass(frozen=True)
class RankedCandidate:
    tool: str
    score: float
    availability_status: str = "available_unverified"
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "score": round(self.score, 6),
            "availability_status": self.availability_status,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RerankResult:
    candidates: tuple[RankedCandidate, ...]
    suppressed: tuple[tuple[str, str], ...]
    diagnostics: tuple[str, ...]
    bounded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "suppressed": [list(s) for s in self.suppressed],
            "diagnostics": list(self.diagnostics),
            "bounded": self.bounded,
        }


def load_golden_corpus(path: Optional[Path] = None) -> dict[str, Any]:
    """离线黄金语料（无 API Key、无网络）。文件缺席时诚实返回空语料。"""
    p = path or _GOLDEN_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": "capability-lexicon/v1", "cases": [],
                "diagnostics": ["golden_corpus_unavailable:not_evaluated"]}


def _normalized(items: Iterable[Any]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for item in items:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            name, score = item[0], item[1]
        elif isinstance(item, Mapping):
            name = item.get("tool") or item.get("name")
            score = item.get("score", 0.0)
        elif isinstance(item, str):
            name, score = item, 0.0
        else:
            continue
        if name is None:
            continue
        try:
            value = float(score)
        except (TypeError, ValueError):
            value = 0.0
        out.append((str(name), value))
    return out


def _registry_names(registry: Any, snapshot: CapabilityAvailabilitySnapshot) -> set[str]:
    """候选合法名集合 = 快照内真实注册工具 + 注入 registry 的工具名。"""
    names = {r.tool_id for r in snapshot.records if r.tool_id}
    if registry is not None and hasattr(registry, "tool_names"):
        try:
            names.update(str(n) for n in registry.tool_names())
        except Exception:  # noqa: BLE001
            pass
    return names


def rerank_candidates(
    candidates: Iterable[Any],
    snapshot: CapabilityAvailabilitySnapshot,
    *,
    query: str = "",
    input_types: Sequence[str] = (),
    method_constraints: Sequence[str] = (),
    max_candidates: int = 8,
    registry: Any = None,
) -> RerankResult:
    """对既有检索候选做可用性感知重排（纯函数、有界、可诊断）。"""
    limit = max(1, min(int(max_candidates), _MAX_CANDIDATES))
    status_by_tool = {r.tool_id: r for r in snapshot.records if r.tool_id}
    legal = _registry_names(registry, snapshot)

    q = str(query or "").lower()
    time_context = any(t in q for t in _TIME_TERMS) or any(
        str(c).lower() in ("seconds", "s", "time", "travel_time")
        for c in method_constraints)
    constraints = tuple(str(c).lower() for c in method_constraints)

    suppressed: list[tuple[str, str]] = []
    diagnostics: list[str] = []
    scored: list[RankedCandidate] = []
    seen: set[str] = set()

    # 先对输入做硬上界切片，再物化（islice 对生成器也有效）。
    bounded_input = islice(iter(candidates), _MAX_INPUT_CANDIDATES)
    for name, base_score in _normalized(bounded_input):
        if name in seen:
            continue
        seen.add(name)

        # 1) hallucinated 工具名：不在 registry / 快照里，直接拒绝。
        if legal and name not in legal:
            suppressed.append((name, "hallucinated_tool_name"))
            diagnostics.append(f"hallucinated_tool_rejected:{name}")
            continue

        rec = status_by_tool.get(name)
        status = rec.availability_status if rec else "declared_only"
        if status not in AVAILABILITY_STATUS_VOCABULARY:
            status = "available_unverified"

        # 2) 不可用工具硬过滤，无论相似度多高。
        if status in _BLOCKED_STATUSES or rec is None:
            suppressed.append((name, f"availability_{status}"))
            diagnostics.append(f"unavailable_tool_suppressed:{name}:{status}")
            continue

        # 3) 严格量纲约束：秒语境下米制距离工具不可被抬为首选。
        is_metric = any(tok in name.lower() for tok in _METRIC_TOOL_TOKENS)
        if time_context and is_metric:
            suppressed.append((name, "UNIT_DIMENSION_CONFLICT"))
            diagnostics.append(
                "UNIT_DIMENSION_CONFLICT: 时间量纲(秒)语境下"
                "米制距离工具不可激活（秒≠米）")
            continue
        if time_context:
            diagnostics.append(
                "time_dimension_context: 仅接受显式时间量纲工具，"
                "距离类候选须先做量纲声明")

        # 4) 输入类型 / 科学方法约束感知（只读降权 + 显式披露）。
        reasons: list[str] = []
        score = float(base_score)
        if status == "native_verified":
            score += 0.35
            reasons.append("native_verified")
        else:
            score -= 0.20
            reasons.append("available_unverified_downweighted")
            if rec and rec.unverified_reasons:
                reasons.extend(list(rec.unverified_reasons)[:2])

        if input_types and rec is not None:
            declared = set(rec.input_artifacts)
            if declared and not declared.intersection(set(input_types)):
                score -= 0.30
                reasons.append("input_type_mismatch")
                diagnostics.append(f"input_type_mismatch:{name}")

        if constraints and rec is not None and rec.units:
            # 量纲声明与请求约束不一致 → 降权并披露，不静默放行。
            units = {u.lower() for u in rec.units}
            if "seconds" in constraints and units.intersection({"m", "meters", "km"}):
                score -= 0.40
                reasons.append("UNIT_DIMENSION_CONFLICT")
                diagnostics.append(f"unit_constraint_conflict:{name}")

        # 5) 中文/英文术语正例加权（离线词表，确定性）。
        for term, hints in _TERM_HINTS.items():
            if term in str(query or "") or any(h in q for h in hints if len(h) > 3):
                if any(h in name.lower() for h in hints):
                    score += 0.25
                    reasons.append(f"lexicon_positive:{term}")
                break

        scored.append(RankedCandidate(
            tool=name, score=score, availability_status=status,
            reasons=tuple(dict.fromkeys(reasons)),
        ))

    scored.sort(key=lambda c: (-c.score, c.tool))
    bounded = len(scored) > limit
    kept = tuple(scored[:limit])
    if bounded:
        diagnostics.append(
            f"candidates_bounded:{len(scored)}->{limit}（有界候选，防止工具面膨胀）")
    if not scored:
        diagnostics.append("no_eligible_candidate:not_evaluated")

    return RerankResult(
        candidates=kept,
        suppressed=tuple(suppressed),
        diagnostics=tuple(dict.fromkeys(diagnostics))[:32],
        bounded=bounded,
    )


def validate_pi_call(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    snapshot: CapabilityAvailabilitySnapshot,
    registry: Any = None,
) -> tuple[bool, str]:
    """Pi 工具调用的只读严格校验（服务端必须调用，不信赖 pi-schema 宽松 coercion）。

    * native 与 execute 归属按 ``pi_native_surface.resolve_pi_tool_call`` 对拍；
    * hallucinated 工具名拒绝；
    * 参数多余字段拒绝（用 ToolRegistry 的权威 args_model）；
    * 不可用能力拒绝。
    """
    from app.services.chat.pi_native_surface import (
        EXECUTE_PROXY_NAME, NATIVE_TOOL_NAMES, resolve_pi_tool_call,
    )

    name = str(tool_name or "")
    args = dict(arguments or {})
    legal = _registry_names(registry, snapshot)
    registered = sorted(legal)

    resolved = resolve_pi_tool_call(
        name, args, registered_surface=registered)
    if resolved.kind == "reject":
        return False, resolved.error or "pi_call_rejected"

    # 代理包裹：内层工具名必须是真实注册工具，且不能被包裹的 native 名。
    if name == EXECUTE_PROXY_NAME:
        inner = str(resolved.name or "")
        if inner in NATIVE_TOOL_NAMES:
            return False, "native tool cannot be wrapped in webgis_execute"
        target = inner
    else:
        target = name

    if target not in legal and target not in NATIVE_TOOL_NAMES:
        return False, "hallucinated_tool_name"
    if target not in legal:
        # native 工具不在 ToolRegistry：只做参数白名单级校验。
        return True, "native_frontdoor"

    rec = next((r for r in snapshot.records if r.tool_id == target), None)
    if rec is None or rec.availability_status in _BLOCKED_STATUSES:
        return False, f"capability_unavailable:{rec.availability_status if rec else 'absent'}"

    # 参数多余字段：与 dispatch 用同一个 Pydantic 模型（零语义漂移）。
    if registry is not None and hasattr(registry, "args_model"):
        try:
            model = registry.args_model(target)
        except Exception:  # noqa: BLE001
            model = None
        if model is not None:
            allowed = set(getattr(model, "model_fields", {}))
            extras = sorted(set(args) - allowed)
            if extras:
                return False, (
                    "pi_schema_coercion_rejected:unexpected_argument_field:"
                    + ",".join(extras[:4]))
    return True, f"{resolved.kind}:{rec.availability_status}"
