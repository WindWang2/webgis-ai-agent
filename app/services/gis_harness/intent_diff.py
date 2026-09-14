"""Chapter Intent Diff —— 用户后续变更 → 最小失效/携带裁决（方向 5，E5-E6 计划侧）。

现状断链（recon G1/G2）：follow-up 改变情境时，``webgis_map_intent`` 的
replace 分支 void 全部行、supersede 分支全量归档（session_plan）——两条路
都是全量重算语义；V5 运行时的 ``compute_affected_subgraph`` +
``ChangeApplier`` 只被 REST 调用。本模块把「旧/新两个 gis_chapter」的
结构化差异变成最小失效种子与**可携带完成事实**（carried completions）：

    old_chapter, new_chapter（纯函数，零 LLM / 零 I/O / 零状态写入）
        → ChapterIntentDiff
            ├─ fine_dims      精细变更维（scope/subject/time/…，门控用）
            ├─ carried        {capability: bound_ref} 完成事实可跨计划携带
            └─ lost           [(capability, coarse_dim, detail)] 完成事实失效

红线：

- **不是第二事实源**：行状态唯一写手仍是 ``SessionPlan._mark_progress``；
  本模块只输出裁决，落地在 session_plan（计划侧）与 workflow_runtime
  service（执行侧，V5 STALE / 携带完成）。
- **宁可漏携带，绝不错误携带**（fingerprints 同纪律）：携带必须同时满足
  行语义签名不变 + 全局重塑维未变 + 依赖闭包全部携带。
- **词表单一事实源**：coarse 维 ⊆ ``workflow_schema.RECOMPUTE_DIMENSIONS``
  （V5 recompute 引擎直接消费）；精细维是本模块的封闭词表（只做门控，
  不进引擎）。
- 全部有界：行 ≤64、lost ≤32、carried ≤32（与 runtime_bridge 预算同规）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

#: 精细变更维（封闭词表；只做携带门控，不进 V5 引擎）。
#: - global：任一变化 → 全量失效（重塑计划语义，不做部分携带）
#: - data：数据行携带门（范围/时间/数据集版本漂移 → 数据行不可携带）
#: - subject：主体维 —— 只通过行语义签名体现（planner 把主体写进行参数/
#:   capability），不额外门控数据行（否则 boundary 行被错误失效）
FINE_DIM_GLOBAL = ("task", "recipe", "measure", "group_by", "comparison")
FINE_DIM_DATA = ("scope", "time", "dataset")

#: intent 字段 → 精细维（chapter.intent 的结构化投影；值为空的旧字段
#: 不参与对比 —— 空→有 视为该维变化，有→空 同理）。
_INTENT_FACTS: Tuple[Tuple[str, Tuple[Tuple[str, ...], str]], ...] = (
    ("scope", (("scope", "name"), ("scope", "level"))),
    ("subject", (("subject", "type"), ("subject", "category"))),
    ("time", (("time",),)),
    ("measure", (("measure",),)),
    ("group_by", (("group_by",),)),
    ("comparison", (("comparison",),)),
    ("task", (("task",),)),
)

#: 有界预算（与 runtime_bridge._MAX_NODES 同规）。
_MAX_ROWS = 64
_MAX_FACTS = 32

#: 完成态（data_requirements 行 available / analysis_steps 行 done）。
_COMPLETE_ROW_STATUS = ("available", "done")


class ChapterIntentDiff(BaseModel):
    """一次意图差异的最小失效/携带裁决（纯数据，可序列化、有界）。"""

    fine_dims: List[str] = Field(default_factory=list)
    global_reshape: bool = False       # 任一 global 维变化 → 全量失效
    carried: Dict[str, str] = Field(default_factory=dict)   # cap → bound_ref
    lost: List[Dict[str, str]] = Field(default_factory=list)
    # lost[] 元素: {"capability", "dimension", "detail"}；dimension ⊆
    # RECOMPUTE_DIMENSIONS（V5 WorkflowChange 直接可用）。

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "fine_dims": [d[:16] for d in self.fine_dims[:8]],
            "global_reshape": self.global_reshape,
            "carried": {k[:64]: v[:96]
                        for k, v in list(self.carried.items())[:_MAX_FACTS]},
            "lost": [
                {k: str(v)[:96 if k == "detail" else 64]
                 for k, v in item.items()}
                for item in self.lost[:_MAX_FACTS]
            ],
        }


def _fact_values(intent: Any, paths: Tuple[Tuple[str, ...], ...]) -> Tuple[str, ...]:
    """intent dict 的嵌套字段取值（缺席/非 dict → 空串）。"""
    out: List[str] = []
    for path in paths:
        cur = intent
        for key in path:
            if not isinstance(cur, dict):
                cur = ""
                break
            cur = cur.get(key, "")
        out.append(str(cur or "").strip())
    return tuple(out)


def intent_dimension_diff(old: Any, new: Any) -> List[str]:
    """新旧 intent 的精细变更维（确定性；无 intent 视为空事实基线）。

    ``dataset`` 维是保留词（chapter 当前无数据集版本事实可比对，ADS-V1
    pinning 合并后在此接入）：数据版本漂移由既有 artifact health 复用校验
    （runtime_bridge W5）在执行面兜底 —— 携带绝不越过会话边界，跨租户
    不可能（V5 owner 域 + 会话内 ref）。
    """
    old_c = old if isinstance(old, dict) else {}
    new_c = new if isinstance(new, dict) else {}
    dims: List[str] = []
    for dim, paths in _INTENT_FACTS:
        old_v = _fact_values(_intent_of(old_c), paths)
        new_v = _fact_values(_intent_of(new_c), paths)
        if old_v != new_v and dim not in dims:
            dims.append(dim)
    # chapter 级事实：recipe 承载方法族（重塑分析链语义 → global 维）。
    if str(old_c.get("recipe_id") or "") != str(new_c.get("recipe_id") or ""):
        dims.append("recipe")
    return dims


def _intent_of(chapter: Dict[str, Any]) -> Dict[str, Any]:
    """chapter → intent dict（缺席给空 dict，空基线不制造伪差异）。"""
    intent = chapter.get("intent") if isinstance(chapter, dict) else None
    return intent if isinstance(intent, dict) else {}


def semantic_row_signature(row: Dict[str, Any]) -> str:
    """行语义签名（排除 status/bound_ref 完成态字段 —— 与
    workflow_instance.row_signature 互补：那个含完成态，这个只含语义面。

    覆盖：capability/purpose/resolved_tool/resolved_algorithm/params/
    depends_on/optional —— planner 行模型的全部语义字段。任一变化 →
    签名变化 → 不携带。
    """
    from app.services.gis_harness.workflow_instance import canonical_fingerprint

    if not isinstance(row, dict):
        return ""
    params = row.get("params")
    deps = row.get("depends_on")
    return canonical_fingerprint({
        "capability": str(row.get("capability") or ""),
        "purpose": str(row.get("purpose") or ""),
        "tool": str(row.get("resolved_tool") or ""),
        "algorithm": str(row.get("resolved_algorithm") or ""),
        "params": params if params else {},
        "depends_on": [str(d) for d in deps] if isinstance(deps, list) else [],
        "optional": bool(row.get("optional") or False),
    })


def _rows_by_cap(chapter: Dict[str, Any], key: str) -> Dict[str, Dict[str, Any]]:
    """chapter 某行族（data_requirements / analysis_steps）→ {capability: 行}。"""
    out: Dict[str, Dict[str, Any]] = {}
    for row in list(chapter.get(key) or [])[:_MAX_ROWS]:
        if isinstance(row, dict) and row.get("capability"):
            out.setdefault(str(row["capability"]), row)
    return out


def _coarse_dim(old_row: Dict[str, Any], new_row: Dict[str, Any]) -> Tuple[str, str]:
    """行级字段对比 → coarse 维（runtime_bridge W4 同规则：算法 > 参数 > 数据）。"""
    old_alg = str(old_row.get("resolved_algorithm") or "")
    new_alg = str(new_row.get("resolved_algorithm") or "")
    if new_alg and old_alg != new_alg:
        return "algorithm", f"{old_alg}->{new_alg}"[:200]
    old_tool = str(old_row.get("resolved_tool") or "")
    new_tool = str(new_row.get("resolved_tool") or "")
    old_params = canonical_params(old_row.get("params"))
    new_params = canonical_params(new_row.get("params"))
    if (new_alg or old_alg) and (new_tool and old_tool != new_tool
                                 or new_params != old_params):
        return "parameter", "params/tool fingerprint changed"
    return "data", "row semantics drifted"


def canonical_params(params: Any, *, _depth: int = 0) -> Any:
    """参数 canonical 化（键排序递归；与 fingerprints 同纪律的轻量版）。

    review P2-7：恶意/意外的深嵌套 params 触发 RecursionError → 被
    _intent_facts 的兜底吃掉 = 全量失效降级（fail-safe 但过保守）。
    深度封顶（>12 以 repr 字符串参与签名）—— 超深结构本身即语义漂移。
    """
    if _depth > 12:
        return repr(params)[:200]
    if isinstance(params, dict):
        return {str(k): canonical_params(params[k], _depth=_depth + 1)
                for k in sorted(params, key=str)}
    if isinstance(params, (list, tuple)):
        return [canonical_params(x, _depth=_depth + 1) for x in params]
    if isinstance(params, bool) or params is None:
        return params
    if isinstance(params, (int, float)):
        return float(params)
    return str(params)


def diff_chapters(old_chapter: Any, new_chapter: Any) -> ChapterIntentDiff:
    """新旧 chapter → 最小失效/携带裁决（E5-E6 计划侧唯一入口）。

    规则（保守正确，宁可漏携带）：

    1. 精细维对比；任一 global 维（task/recipe/measure/group_by/comparison）
       变化 → ``global_reshape=True`` → 零携带（等价现状全量失效）；
    2. 行语义签名相同 + 旧行已完成 + 数据行门（scope/time/dataset 未变）
       + 依赖闭包全部携带 → 携带（complete + bound_ref 跨计划存续）；
    3. 其余旧完成行 → lost（capability + coarse 维 + detail），消费方
       （session_plan void 行 / V5 STALE 节点）各取所需。
    """
    old_c = old_chapter if isinstance(old_chapter, dict) else {}
    new_c = new_chapter if isinstance(new_chapter, dict) else {}
    # 首次计划 / 章节缺席：无差异基线 —— 零 dims 零裁决（诚实留白）。
    if not old_c or not new_c:
        return ChapterIntentDiff()
    dims = intent_dimension_diff(old_c, new_c)
    global_reshape = any(d in dims for d in FINE_DIM_GLOBAL)

    # 数据维门：scope/time 变化 → 数据行整体不可携带（分析行仅当依赖闭包
    # 携带才可携带 —— 数据行必然不携带，故分析行随之自然失效）。
    data_gate_open = not any(d in dims for d in FINE_DIM_DATA)

    old_req = _rows_by_cap(old_c, "data_requirements")
    new_req = _rows_by_cap(new_c, "data_requirements")
    old_step = _rows_by_cap(old_c, "analysis_steps")
    new_step = _rows_by_cap(new_c, "analysis_steps")

    # 旧完成事实：cap → (语义签名, bound_ref, 是否数据行)
    old_complete: Dict[str, Tuple[str, str, bool]] = {}
    for cap, row in old_req.items():
        if str(row.get("status") or "") == "available":
            old_complete[cap] = (
                semantic_row_signature(row), str(row.get("bound_ref") or ""), True)
    for cap, row in old_step.items():
        if str(row.get("status") or "") == "done":
            old_complete[cap] = (
                semantic_row_signature(row), str(row.get("bound_ref") or ""), False)

    # 新计划的依赖闭包（拓扑序携带判定；未知依赖按未携带处理 —— 保守）。
    # global 重塑维变化 → 零携带（计划语义整体更换，见 lost 分支）。
    carried: Dict[str, str] = {}
    lost: List[Dict[str, str]] = []
    caps_in_new = list(dict.fromkeys(
        list(new_req.keys()) + list(new_step.keys())))
    carried_done: Dict[str, bool] = {}
    if not global_reshape:
        for _pass in range(len(caps_in_new) + 1):
            changed = False
            for cap in caps_in_new:
                if cap in carried_done:
                    continue
                new_row = new_req.get(cap) or new_step.get(cap)
                old_sig_tuple = old_complete.get(cap)
                if new_row is None or old_sig_tuple is None:
                    carried_done[cap] = False
                    changed = True
                    continue
                sig, ref, is_data = old_sig_tuple
                ok = semantic_row_signature(new_row) == sig and bool(ref)
                if ok and is_data:
                    ok = data_gate_open
                if ok:
                    deps = new_row.get("depends_on")
                    for dep in [str(d) for d in deps] if isinstance(deps, list) else []:
                        if not carried_done.get(dep, False):
                            ok = False
                            break
                if ok:
                    carried_done[cap] = True
                    carried[cap] = ref
                    changed = True
            if not changed:
                break

    for cap, (_sig, _ref, _is_data) in old_complete.items():
        if cap in carried:
            continue
        new_row = new_req.get(cap) or new_step.get(cap)
        if new_row is None:
            # 新计划不再需要该 capability —— 无需重算，也无需失效披露。
            continue
        if global_reshape:
            # 全局重塑维变化：同签名行也一律失效（计划语义整体更换，
            # 保守等价现状全量失效 —— 行签名在此不构成携带证据）。
            lost.append({
                "capability": cap[:64],
                "dimension": "data",
                "detail": ("global reshape: "
                           + ",".join(dims)[:160])[:200],
            })
            continue
        dimension, detail = _coarse_dim(
            (old_req.get(cap) or old_step.get(cap) or {}),
            new_row)
        lost.append({
            "capability": cap[:64],
            "dimension": dimension,
            "detail": detail[:200],
        })
    lost.sort(key=lambda item: item["capability"])
    return ChapterIntentDiff(
        fine_dims=dims, global_reshape=global_reshape,
        carried={k: v for k, v in list(carried.items())[:_MAX_FACTS]},
        lost=lost[:_MAX_FACTS],
    )


__all__ = [
    "FINE_DIM_DATA",
    "FINE_DIM_GLOBAL",
    "ChapterIntentDiff",
    "canonical_params",
    "diff_chapters",
    "intent_dimension_diff",
    "semantic_row_signature",
]
