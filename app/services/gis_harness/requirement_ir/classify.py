"""五类顶层意图分类（F02 DoD #1：query-only / analysis / map / edit / export
互不误判）。

设计约束：

- **确定性**：表驱动 + 有序规则，同输入永远同输出；无 LLM、无 IO；
- **不复制理解权威**：core 的 TaskType/Analysis/Cartography 词表来自
  ``resolve_map_request_intent`` 的既有解析，本模块只做顶层 kind 归类
  与显式语气护栏（"只分析不画图"等）的最终裁决；
- **可解释**：每次分类携带 reason_codes + 命中 cues（有界）。
"""
from __future__ import annotations

import re
from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.requirement_ir.contracts import (
    MAX_STAGES,
    MAX_TEXT,
    TaskKind,
)

# ── 语气护栏 cue 表（双语；顺序即优先级；全部小写化匹配） ────────────────

# 显式"不要图"：压制 map（analysis 语义仍在）
_NO_MAP_CUES: Tuple[Tuple[str, str], ...] = (
    ("不画图", "explicit_no_map_cue"),
    ("不用画图", "explicit_no_map_cue"),
    ("不需要画图", "explicit_no_map_cue"),
    ("不需要图", "explicit_no_map_cue"),
    ("不用出图", "explicit_no_map_cue"),
    ("别画图", "explicit_no_map_cue"),
    ("只分析", "explicit_analysis_only_cue"),
    ("只要分析", "explicit_analysis_only_cue"),
    ("不要图", "explicit_no_map_cue"),
    ("无需图", "explicit_no_map_cue"),
    ("no map", "explicit_no_map_cue"),
    ("don't map", "explicit_no_map_cue"),
    ("do not map", "explicit_no_map_cue"),
    ("analysis only", "explicit_analysis_only_cue"),
)

# 显式导出（不可逆/交付面）
_EXPORT_CUES: Tuple[Tuple[str, str], ...] = (
    ("导出", "export_cue"),
    ("下载", "export_cue"),
    ("另存为", "export_cue"),
    ("出版", "export_publish_cue"),
    ("打印", "export_cue"),
    ("export", "export_cue"),
    ("download", "export_cue"),
    ("save as", "export_cue"),
)

# 显式编辑/增量修订（需要既有文档或续接语气才成立）
_EDIT_CUES: Tuple[Tuple[str, str], ...] = (
    ("改成", "edit_cue"),
    ("改为", "edit_cue"),
    ("换成", "edit_cue"),
    ("换为", "edit_cue"),
    ("换色", "edit_cue"),
    ("改色", "edit_cue"),
    ("隐藏", "edit_cue"),
    ("显示出来", "edit_unhide_cue"),
    ("取消隐藏", "edit_unhide_cue"),
    ("去掉", "edit_cue"),
    ("删掉", "edit_cue"),
    ("加上", "edit_cue"),
    ("添加图例", "edit_cue"),
    ("再导出", "edit_export_cue"),
    ("hide", "edit_cue"),
    ("unhide", "edit_unhide_cue"),
    ("change to", "edit_cue"),
    ("recolor", "edit_cue"),
)

# 续接语气：无文档也可判定为对上文的增量修订
_EDIT_CUES: Tuple[Tuple[str, str], ...] = _EDIT_CUES + (
    ("导出", "edit_export_cue"),
    ("下载", "edit_export_cue"),
)

_CONTINUATION_CUES: Tuple[str, ...] = (
    "再", "然后", "接着", "顺便", "同时", "再帮我", "then", "also", "next",
)

# 显式时序（stages）
_SEQUENTIAL_PATTERNS: Tuple[Tuple[str, TaskKind, TaskKind], ...] = (
    (r"先.{1,16}(画图|制图|出图|做图)", "analysis", "map"),
    (r"first .{1,24}then (map|draw|plot)", "analysis", "map"),
)

# resolver 的 fallback 分析族（每次解析都出现，不具区分度）
_FALLBACK_ANALYSIS = frozenset({"profile", "spatial_distribution", "administrative_summary"})

# 话语词面信号（resolver 默认族不可区分，五分类以词面 + 特异 task 族为准）
_QUERY_ANALYSIS_CUE_RE = re.compile(
    r"统计|分析|对比|比较|占比|比例|趋势|聚合|密度|每平方公里|"
    r"statistics|analysis|compare|trend|density|per ")
_MAP_MAKING_CUES: Tuple[str, ...] = (
    "做一张图", "做个图", "做图", "画一张", "画个", "画图", "画张", "出图",
    "成图", "热力图", "专题图", "做一张地图", "做个地图", "一张地图", "画地图",
    ".plot", "draw a map", "make a map", "choropleth",
)
_STRONG_EXPORT_RE = re.compile(
    r"导出\s*(?:成|为|as|to)?\s*(?:pdf|png|svg|csv|geojson|jpg|出版|publication)?"
    r"|(?:出版|发布|print-ready|publication)"
    r"|(?:export|download)")

_QUERY_ONLY_TASKS = frozenset({"simple_view", "distribution_overview"})

_ANALYTICAL_TASKS = frozenset({
    "administrative_statistic", "analytical_density", "concentration_analysis",
    "categorical_distribution", "proximity_analysis", "accessibility_analysis",
    "change_detection", "vegetation_index", "mobility_flow", "spatial_equity",
    "site_selection", "suitability_assessment", "risk_exposure",
    "terrain_analysis", "watershed_analysis", "spatial_autocorrelation",
    "temporal_trend", "sar_analysis", "network_route",
})


class TaskClassification(BaseModel):
    """分类结果 + 可解释面。``kind`` 为最终裁决；``stages`` 仅显式时序非空。"""

    model_config = ConfigDict(extra="forbid")

    kind: TaskKind
    stages: Tuple[TaskKind, ...] = Field((), max_length=MAX_STAGES)
    reason_codes: Tuple[str, ...] = ()
    cues: Tuple[str, ...] = Field((), max_length=12)
    confidence: float = Field(0.0, ge=0.0, le=1.0)


def _lower(query: str) -> str:
    return (query or "").lower()[:MAX_TEXT]


def _find_cues(query: str, table: Tuple[Tuple[str, str], ...]) -> Tuple[Tuple[str, str], ...]:
    hits = []
    for cue, code in table:
        if cue in query:
            hits.append((cue, code))
    return tuple(hits)


def classify_task_kind(
    query: str,
    *,
    has_document: bool = False,
    core_task: str = "",
    analysis_intents: Tuple[str, ...] = (),
    cartography_intents: Tuple[str, ...] = (),
    output_intents: Tuple[str, ...] = ("map",),
    export_intents: Tuple[str, ...] = (),
) -> TaskClassification:
    """顶层意图五分类（纯函数）。

    Args:
        query: 用户原话（语气护栏 cue 的匹配面）。
        has_document: 会话中是否已存在需求文档（edit 判定的必要条件之一）。
        core_task / *_intents: 既有解析权威（MapRequestIntent）的字段。

    规则顺序：edit（修订在先，语义是"改需求"而非"新任务"）→
    export → map（受 no-map 护栏压制）→ analysis → query_only。
    """
    q = _lower(query)
    reasons: list[str] = []
    cues: list[str] = []

    has_continuation = any(c in q for c in _CONTINUATION_CUES)

    # 信号面：resolver 默认族（output/carto/analysis 全量默认）不具区分度，
    # 五分类以话语词面 + 特异 task 族为准（词面表见模块头）。
    analysis_task_present = core_task in _ANALYTICAL_TASKS or bool(
        set(analysis_intents or ()) - _FALLBACK_ANALYSIS)
    analysis_cue = _QUERY_ANALYSIS_CUE_RE.search(q) is not None
    map_making_cue = any(cue in q for cue in _MAP_MAKING_CUES)
    strong_export = _STRONG_EXPORT_RE.search(q) is not None

    # 1) edit：编辑 cue +（已有文档 或 续接语气）。修订路由到 patch 协议。
    edit_hits = _find_cues(q, _EDIT_CUES)
    if edit_hits and (has_document or has_continuation):
        cues.extend(c for c, _ in edit_hits[:4])
        reasons.append("edit_route")
        if has_document:
            reasons.append("existing_document")
        if has_continuation:
            reasons.append("continuation_prefix")
        return TaskClassification(
            kind="edit", reason_codes=tuple(reasons), cues=tuple(cues),
            confidence=0.86)

    # 2) 显式时序（先分析后制图）
    stages: Tuple[TaskKind, ...] = ()
    for pattern, first, second in _SEQUENTIAL_PATTERNS:
        if re.search(pattern, q):
            stages = (first, second)
            reasons.append("sequential_stages_cue")
            break

    # 3) 强导出话语（导出成/为/格式、出版、export/download）→ 交付面语义。
    #    优先于 analysis 词面：『把统计图导出成PDF』的「统计」修饰既有成果。
    export_hits = _find_cues(q, _EXPORT_CUES)
    if strong_export and export_hits:
        cues.extend(c for c, _ in export_hits[:4])
        reasons.append("strong_export_cue")
        if any(c in q for c in ("出版", "发布", "publication", "print-ready")):
            reasons.append("export_publish_cue")
        return TaskClassification(
            kind=stages[-1] if stages else "export", stages=stages,
            reason_codes=tuple(reasons), cues=tuple(cues), confidence=0.84)

    no_map_hits = _find_cues(q, _NO_MAP_CUES)

    # 4) no-map 护栏：显式"不要图"压制 map/成图语义 → analysis
    if no_map_hits and (analysis_task_present or analysis_cue or map_making_cue):
        cues.extend(c for c, _ in no_map_hits[:4])
        reasons.append("no_map_suppression")
        return TaskClassification(
            kind="analysis", reason_codes=tuple(reasons), cues=tuple(cues),
            confidence=0.88)

    # 5) map：话语里真的在要一张图（做图/画/出图/热力图…词面）
    if map_making_cue:
        reasons.append("map_making_cue")
        return TaskClassification(
            kind=stages[-1] if stages else "map", stages=stages,
            reason_codes=tuple(reasons), cues=tuple(cues),
            confidence=0.9 if stages else 0.82)

    # 6) analysis：分析/统计语义（task 特异族 或 话语词面）且无成图诉求
    if analysis_task_present or analysis_cue:
        reasons.append("analysis_semantics_task" if analysis_task_present
                       else "analysis_semantics_cue")
        return TaskClassification(
            kind="analysis", stages=stages, reason_codes=tuple(reasons),
            cues=tuple(cues), confidence=0.78)

    # 7) 弱导出兜底（有 export cue 但无强信号）
    if export_hits:
        cues.extend(c for c, _ in export_hits[:4])
        reasons.append("export_deliverable_fallback")
        return TaskClassification(
            kind="export", reason_codes=tuple(reasons), cues=tuple(cues),
            confidence=0.6)

    # 8) query_only：默认兜底（浏览/查看语义）
    if core_task in _QUERY_ONLY_TASKS or not core_task:
        reasons.append("default_query_only")
        return TaskClassification(
            kind="query_only", reason_codes=tuple(reasons), cues=tuple(cues),
            confidence=0.6)
    reasons.append("residual_query_only")
    return TaskClassification(
        kind="query_only", reason_codes=tuple(reasons), cues=tuple(cues),
        confidence=0.5)


def classification_from_core(
    query: str,
    core,  # MapRequestIntent（鸭子类型，避免 contracts↔intent 环）
    *,
    has_document: bool = False,
) -> TaskClassification:
    """便捷入口：从 MapRequestIntent 提取字段后分类。"""
    return classify_task_kind(
        query,
        has_document=has_document,
        core_task=str(getattr(core, "task", "") or ""),
        analysis_intents=tuple(getattr(core, "analysis_intents", ()) or ()),
        cartography_intents=tuple(getattr(core, "cartography_intents", ()) or ()),
        output_intents=tuple(getattr(core, "output_intents", ()) or ()),
        export_intents=tuple(getattr(core, "export_intents", ()) or ()),
    )
