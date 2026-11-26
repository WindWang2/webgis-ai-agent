"""G46 单一编排面 —— REST 路由与 Pi 工具共用（UI 与 Pi 同一生产服务）。

生命周期：inspect_source（同代冻结 + 字段/类别候选）→ preview（组合条件
求值：匹配计数 + per-chart facet 统计 + where-scope handle + MapLibre
表达式）→ 视图 CRUD（命名保存 / CAS 修改 / 锚校验重开 / 归档）→
apply（图表组件原子写入 MapSpec）。

授权：HTTP 由路由层 ``require_owned_session``；Pi 由 ToolRegistry 注入
session_id（trusted_dispatch）。每次读取/保存/重开/导出重新冻结与校验
（ref/hash/handle 都不充当权限）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence

from app.services.feature_pages import count_matching, evaluate_where
from app.services.linked_exploration import contract
from app.services.linked_exploration import metrics as metrics_module
from app.services.linked_exploration import relations
from app.services.linked_exploration.buckets import chart_statistics
from app.services.linked_exploration.errors import LinkedExplorationError
from app.services.linked_exploration.snapshot import freeze_source_typed

logger = logging.getLogger(__name__)

_PLAN_JSON_MAX = 32 * 1024
_PREVIEW_JSON_MAX = 8 * 1024
#: 类别候选披露上限（inspect 阶段；不含机器值集合全量 —— 全量走 preview）
_INSPECT_CATEGORY_SAMPLE = 24
#: inspect 数值字段抽样行数（min/max 估计）
_INSPECT_SAMPLE_ROWS = 20_000


def _count_matching_exact(features, where):
    """(count, exact) —— count_matching 对 ≤250k 冻结集恒精确可证。"""
    n = count_matching(features, where=where, bbox=None)
    return n, n is not None


def _actor_owner_token(actor: Dict[str, Any]) -> Optional[str]:
    return actor.get("owner_token")


def _trusted(actor: Dict[str, Any]) -> bool:
    return bool(actor.get("trusted_session_id"))


# ─── inspect：同代源卡 + 字段/类别候选 ───────────────────────────────────

async def inspect_source(
    session_id: str,
    ref_id: str,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    """冻结源 → 源卡（字段 schema + 类别候选 top 值 + 数值字段 min/max）。

    预览样本可以有但必须标 sample：类别候选是**抽样估计**（精确机器值集
    合在 preview 的分桶结果里）；``is_sample`` 明示。
    """
    from app.services.session_data import session_data_manager

    frozen = await freeze_source_typed(
        session_id, ref_id,
        owner_token=_actor_owner_token(actor), trusted_dispatch=_trusted(actor))
    descriptor = await session_data_manager.get_ref_descriptor(
        session_id, frozen.ref_id)
    field_schema: Dict[str, Any] = {}
    if isinstance(descriptor, dict):
        raw = descriptor.get("field_schema")
        if isinstance(raw, dict):
            field_schema = raw

    fields_out: List[Dict[str, Any]] = []
    sample = frozen.features[:_INSPECT_SAMPLE_ROWS]
    for name in sorted(field_schema.keys()):
        meta = field_schema.get(name) or {}
        ftype = str(meta.get("type") or "unknown")
        entry: Dict[str, Any] = {
            "name": name,
            "type": ftype,
            "null_count": meta.get("null_count"),
        }
        if ftype in ("string", "boolean") or ftype == "unknown":
            seen: Dict[Any, int] = {}
            for feat in sample:
                props = feat.get("properties") if isinstance(feat, dict) else None
                v = props.get(name) if isinstance(props, dict) else None
                if v is not None and not isinstance(v, (list, dict)):
                    seen[v] = seen.get(v, 0) + 1
            top = sorted(seen.items(), key=lambda kv: (-kv[1], str(kv[0])))[:_INSPECT_CATEGORY_SAMPLE]
            entry["category_sample"] = [
                {"value": k, "count": c} for k, c in top
            ]
            entry["is_sample"] = len(sample) < frozen.feature_count
        if ftype == "number":
            vals = []
            for feat in sample:
                props = feat.get("properties") if isinstance(feat, dict) else None
                v = props.get(name) if isinstance(props, dict) else None
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                    vals.append(float(v))
            if vals:
                entry["min"], entry["max"] = min(vals), max(vals)
                entry["min_max_is_sample"] = len(sample) < frozen.feature_count
        fields_out.append(entry)

    return {
        "anchor": frozen.anchor(),
        "feature_count": frozen.feature_count,
        "fields": fields_out,
        "schema_complete": bool(descriptor.get("field_schema_complete", True))
            if isinstance(descriptor, dict) else False,
        "sample_note": (
            f"类别候选/数值范围基于前 {len(sample)} 行抽样（is_sample=true 时"
            "为估计值；精确值集在图表统计里）"
            if len(sample) < frozen.feature_count else "全量行统计"
        ),
    }


# ─── preview：组合条件求值（同代多视图投影） ─────────────────────────────

async def preview_conditions(
    session_id: str,
    ref_id: str,
    charts: Sequence[contract.ChartSpec],
    conditions: Sequence[Any],
    actor: Dict[str, Any],
    *,
    create_handle: bool = True,
) -> Dict[str, Any]:
    """组合条件求值权威面（facet 口径：每图自排除自身条件，其余保留）。

    返回：源锚、组合 where digest、完整匹配计数（全条件口径）、每图两套
    统计（``facet``=自排除口径 / ``full``=全条件匹配集口径）、MapLibre
    表达式（typed identity 精确编译）、where-scope selection handle。
    """
    frozen = await freeze_source_typed(
        session_id, ref_id,
        owner_token=_actor_owner_token(actor), trusted_dispatch=_trusted(actor))
    parsed = list(conditions)
    combined = contract.compile_combined_where(parsed)
    digest = contract.conditions_digest(parsed)

    match_count, match_exact = await asyncio.to_thread(
        _count_matching_exact, frozen.features, combined)

    charts_out: List[Dict[str, Any]] = []
    for chart in charts:
        facet_where = contract.compile_combined_where(
            parsed, exclude_chart_id=chart.chart_id)
        facet_stats = await asyncio.to_thread(
            chart_statistics, frozen.features, chart, where=facet_where)
        full_stats = await asyncio.to_thread(
            chart_statistics, frozen.features, chart, where=combined)
        charts_out.append({
            "chart_id": chart.chart_id,
            "kind": chart.kind,
            "field": chart.field,
            "aggregation": chart.aggregation,
            "title": chart.title,
            "facet": {
                "scope": "excluding-own-condition",
                "note": "其它条件下的可选项（本图自身条件已排除）",
                "statistics": facet_stats,
            },
            "full": {
                "scope": "all-conditions",
                "note": "全部条件后的匹配集统计",
                "statistics": full_stats,
            },
        })

    map_filter = contract.where_to_maplibre(combined)
    handle_entry = None
    if create_handle and combined is not None:
        from app.services.selection_handles import create_selection

        handle_entry = await create_selection(
            session_id, frozen.ref_id, frozen.content_revision,
            {"kind": "where", "where": combined}, frozen.features)

    out: Dict[str, Any] = {
        "anchor": frozen.anchor(),
        "conditions_digest": digest,
        "combined_where": combined,
        "match_count": match_count,
        # review P3-3：精确性按 count_matching 的实际语义投影（≤250k 冻结
        # 帽内恒精确；不硬编码 True 与上游常数隐式耦合）
        "match_count_exact": match_exact,
        "total": frozen.feature_count,
        "map_filter": map_filter,
        "charts": charts_out,
        "completeness": "complete",
    }
    if handle_entry is not None:
        out["selection_handle"] = {
            "handle": handle_entry.handle,
            "count": handle_entry.count,
            "count_exact": handle_entry.count_exact,
            "ttl_seconds": 600,
        }
    return out


# ─── 多源 preview：传播键集 + facets + 指标（v3 主面；v2 同面兼容） ─────

def _resolve_metric_sources(
    plan: contract.ExplorationPlan,
) -> List[contract.MetricSpec]:
    """把指标的缺省源引用解析为显式 id（primary）；返回解析后的副本。"""
    primary_id = contract.plan_primary_binding(plan).source_id
    resolved: List[contract.MetricSpec] = []
    for metric in plan.metrics:
        updates: Dict[str, Any] = {}
        if metric.source_id is None:
            updates["source_id"] = primary_id
        if metric.grain_source_id is None and metric.source_id is None:
            updates["grain_source_id"] = primary_id
        if updates:
            metric = metric.model_copy(update=updates)
        resolved.append(metric)
    return resolved


def _final_map_filter(
    own_where: Optional[Dict[str, Any]],
    propagations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """源的最终地图投影：own where + 可投影的单键对传播 AND 组合。

    复合键对传播不编译（AND-in 是行乘积超集）→ 整体拒绝投影并披露，
    绝不给过宽的地图过滤假象。"""
    parts: List[List[Any]] = []
    expr = contract.where_to_maplibre(own_where)
    if expr is not None:
        parts.append(expr)
    projected = True
    note_parts: List[str] = []
    for prop in propagations:
        expr = prop.get("map_filter")
        if prop.get("key_count", 0) == 0 and prop.get("from_unfiltered"):
            continue  # 未过滤上游 → 无限制
        if prop.get("key_count", 0) == 0 and not prop.get("from_unfiltered"):
            # review A-P3-7：上游选择为空 → 下游范围是空集。空 in-叶
            # （literal []）对 MapLibre 精确恒假 —— 空集是**可精确投影**
            # 的（无论单键还是复合键），复合键"不可投影"文案在此误导
            fields = prop.get("target_fields") or []
            if fields:
                parts.append(["all", *[
                    ["in", ["get", f], ["literal", []]] for f in fields]])
            continue
        if expr is None:
            projected = False
            note_parts.append(
                f"关系 {prop.get('link_id')} 为复合键传播，地图过滤不可"
                "精确编译（统计仍精确）")
            continue
        parts.append(expr)
    if not projected:
        return {
            "map_filter": None,
            "map_filter_projected": False,
            "map_filter_note": "；".join(note_parts),
        }
    if not parts:
        return {"map_filter": None, "map_filter_projected": True}
    if len(parts) == 1:
        return {"map_filter": parts[0], "map_filter_projected": True}
    return {"map_filter": ["all", *parts], "map_filter_projected": True}


def _combine_scope_where_ast(
    own_where: Optional[Dict[str, Any]],
    propagations: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """尝试把最终 scope 组合成 AST（供 where-scope handle 与结果表下推）。

    传播 in-叶受 FILTER_MAX_VALUES/FILTER_MAX_JSON_BYTES 帽约束 ——
    超帽不可表达 → None（调用方披露 handle 跳过原因，不让结果表拿
    过宽范围冒充最终 scope）。"""
    terms: List[Dict[str, Any]] = []
    if own_where is not None:
        terms.append(own_where)
    for prop in propagations:
        if prop.get("from_unfiltered"):
            continue
        keys = prop.get("keys") or []
        field = (prop.get("target_fields") or [None])[0]
        if field is None or len(prop.get("target_fields") or []) != 1:
            return None  # 复合键：AST 不可表达
        if len(keys) > 64:  # FILTER_MAX_VALUES
            return None
        leaf = {
            "op": "in", "field": field,
            "values": [v for (_fam, v) in keys],
        }
        terms.append(leaf)
    if not terms:
        return None
    if len(terms) == 1:
        return terms[0]
    return {"op": "and", "terms": terms}


async def preview_plan(
    session_id: str,
    plan_raw: Optional[Dict[str, Any]],
    actor: Dict[str, Any],
    *,
    create_handle: bool = True,
    selection_ref: Optional[str] = None,
    selection_source_id: Optional[str] = None,
    _validated_plan: Optional[contract.ExplorationPlan] = None,
) -> Dict[str, Any]:
    """多源联动预览权威面（v3 主面；v2 单源计划同面兼容）。

    ``selection_ref``：选择史指代（``previous`` / ``<epoch_seq>``，来自
    gis_situation selection_history）—— 解析成功 → 在目标源（缺省
    primary）注入 id 集合条件（"只看刚才选中的那组"）；不可解析 →
    ``SELECTION_REF_UNRESOLVED`` typed 拒绝 + 最小澄清提示（绝不随机挑
    一代冒充）。ids 摄入上限 50（MAX_HISTORY_IDS）+ 截断权威披露
    ``ids_truncated``/``matched_count`` —— 真实命中数以后者为准。

    ``_validated_plan``：服务层内部通道（drill 注入保留前缀 id 后的已验
    证计划对象）—— 跳过用户面校验（保留前缀 id 会被外部入口的
    ``_check_id`` 拒绝；该命名空间仅服务层可写）。外部调用方不得使用。

    语义：
    1. 逐源冻结并核对计划锚（任一漂移 → STALE_SOURCE，逐源列明）——
       传播与指标只跑在与计划锚同代的数据上；
    2. per-source 条件编译（AND-跨控件 / OR-同控件，facet 只排除自身）；
    3. 传播沿声明方向单遍 DAG 求值：上游**最终匹配行**的（复合）键集
       半连接约束下游；未过滤上游不产生限制；上游零匹配 → 下游零匹配
       （诚实空集）；键集超帽 typed 拒绝，绝不截断；
    4. 每源 match_count（内存精确）、图表 facet/full 统计、指标求值
       （联动范围过滤后的行集）；
    5. where-scope handle 逐源创建（scope 可表达为 AST 时；否则跳过
       并披露原因 —— 结果表不得拿过宽范围冒充最终 scope）。
    """
    if _validated_plan is not None:
        plan = _validated_plan
    else:
        plan = _validate_plan_payload(plan_raw)  # type: ignore[arg-type]
    conditions = _parse_plan_conditions(plan)
    bindings = contract.plan_source_bindings(plan)
    owner = _actor_owner_token(actor)
    trusted = _trusted(actor)

    # 0. 选择史指代（"只看刚才选中的那组"）：解析句柄（解析只需账本，
    #    id 字段解析在冻结后做 —— 见下方 step 1.5）
    selection_claimed: Optional[Dict[str, Any]] = None
    selection_target_sid: Optional[str] = None
    if selection_ref:
        target_sid = selection_source_id or \
            contract.plan_primary_binding(plan).source_id
        if target_sid not in {b.source_id for b in bindings}:
            raise LinkedExplorationError(
                "UNKNOWN_SOURCE",
                f"selection_source_id {target_sid!r} 不在计划 sources 中",
                status_code=422)
        selection_claimed = await _resolve_selection_ref(session_id, selection_ref)
        selection_target_sid = target_sid

    # 1. 逐源冻结 + 锚核对（同 ref 去重）
    frozen_by_ref: Dict[str, Any] = {}
    for binding in bindings:
        ref_id = binding.anchor.ref_id
        if ref_id not in frozen_by_ref:
            frozen_by_ref[ref_id] = await freeze_source_typed(
                session_id, ref_id, owner_token=owner, trusted_dispatch=trusted)
    drift: List[Dict[str, Any]] = []
    for binding in bindings:
        frozen = frozen_by_ref[binding.anchor.ref_id]
        if (frozen.content_revision != binding.anchor.content_revision
                or frozen.content_fingerprint
                != binding.anchor.content_fingerprint):
            drift.append({
                "source_id": binding.source_id,
                "ref_id": binding.anchor.ref_id,
                "planned_revision": binding.anchor.content_revision,
                "current_revision": frozen.content_revision,
            })
    if drift:
        first = drift[0]
        raise LinkedExplorationError(
            "STALE_SOURCE",
            f"计划锚已过期（源 {first['source_id']}：计划 rev "
            f"{first['planned_revision']}，当前 rev "
            f"{first['current_revision']}）—— 请重新 inspect/preview 后更新锚",
            status_code=409,
            correction_hint="重新执行 inspect 并用新锚更新计划。",
            detail={"sources": drift})

    features_by_source: Dict[str, List[Any]] = {
        b.source_id: frozen_by_ref[b.anchor.ref_id].features for b in bindings
    }
    # 1.5 选择史 ids → 目标源 id 字段条件（冻结后才能解析 id 字段链）
    selection_disclosure: Optional[Dict[str, Any]] = None
    if selection_claimed is not None and selection_target_sid is not None:
        target_features = features_by_source[selection_target_sid]
        id_field = _resolve_id_field(target_features)
        if id_field is None:
            raise LinkedExplorationError(
                "SELECTION_ID_FIELD_UNRESOLVED",
                "目标源要素没有稳定 id 字段链（_wfid/id/OBJECTID/fid…）——"
                "历史选择指代无法映射为条件",
                status_code=422,
                correction_hint="改用显式条件（如按键字段筛选）替代选择史指代。")
        ids = list(selection_claimed.get("feature_ids") or [])
        # 服务层注入（保留前缀 id，同 drill：model_construct 绕过用户面
        # 校验，字段内部受控恒合法）
        conditions.append(contract.CategoryCondition.model_construct(
            kind="category",
            condition_id=f"sel-ref-{selection_claimed.get('claimed_at_seq')}",
            chart_id=f"sel-ref-chart-{selection_target_sid}",
            field=id_field,
            values=ids,
            include_null=False,
            labels={},
            source_id=selection_target_sid,
        ))
        selection_disclosure = {
            "selection_ref": selection_ref,
            "source_id": selection_target_sid,
            "id_field": id_field,
            "claimed_epoch_seq": selection_claimed.get("claimed_at_seq"),
            "id_count": len(ids),
            "matched_count": selection_claimed.get("matched_count"),
            "ids_truncated": bool(selection_claimed.get("ids_truncated")),
            "layer_id": selection_claimed.get("layer_id"),
            "note": "历史 ids 摄入上限 50；ids_truncated=true 时真实命中数"
                    "以 matched_count 为准",
        }

    own_wheres = contract.compile_plan_wheres(plan, conditions)

    # 2. 传播：拓扑序单遍；上游最终匹配行的键集 → 下游谓词
    order = relations.propagation_order(plan)
    # 出边表：上游源 → [(link, 目标源, 目标侧)]
    outgoing: Dict[str, List[Any]] = {}
    for link in plan.links:
        if link.propagate == "left_to_right":
            outgoing.setdefault(link.left_source_id, []).append(
                (link, link.right_source_id, "right"))
        elif link.propagate == "right_to_left":
            outgoing.setdefault(link.right_source_id, []).append(
                (link, link.left_source_id, "left"))

    def _prop_target_fields(link: contract.LinkSpec, target_side: str):
        return [p.left_field if target_side == "left" else p.right_field
                for p in link.keys]

    # review B-P2：逐行过滤 + 键集收集是 O(总行数) 纯 CPU 循环 —— 打包为
    # 同步函数走 to_thread（250k 行内联会拖住事件循环，多租户面排队）。
    def _evaluate_propagation():
        matching: Dict[str, List[Any]] = {}
        prop_disclosures: Dict[str, List[Dict[str, Any]]] = {
            b.source_id: [] for b in bindings
        }
        incoming_preds: Dict[str, List[Any]] = {b.source_id: [] for b in bindings}
        for sid in order:
            own = own_wheres.get(sid)
            preds = incoming_preds.get(sid, [])
            rows = []
            for feat in features_by_source[sid]:
                if own is not None and not evaluate_where(own, feat):
                    continue
                if not all(p(feat) for p in preds):
                    continue
                rows.append(feat)
            matching[sid] = rows

            # 为下游目标源收集本源最终匹配行的传播键集（拓扑序保证已定稿）
            for link, target_sid, target_side in outgoing.get(sid, ()):
                # review A-P2-1：执行态判定（非声明边）—— B 上无 own 条件
                # 且无生效入边谓词 = B 实际未被过滤 → 它的出边也不得产生
                # 限制（否则惰性链 A→B→C 会静默收窄 C，与"未过滤上游不
                # 产生限制"的披露语义矛盾）
                upstream_unfiltered = (
                    own is None and not incoming_preds.get(sid))
                target_fields = _prop_target_fields(link, target_side)
                if upstream_unfiltered:
                    # 未过滤上游 → 无限制（键集全量无意义且易超帽）
                    prop_disclosures[target_sid].append({
                        "link_id": link.link_id,
                        "from_source": sid,
                        "target_fields": target_fields,
                        "key_count": 0,
                        "from_unfiltered": True,
                        "note": "上游无任何生效条件：本关系不产生传播限制",
                    })
                    continue
                upstream_side = "left" if target_side == "right" else "right"
                keyset = relations.collect_propagated_composite_keys(
                    rows, None, link=link, side=upstream_side)
                relations.require_composite_within_cap(keyset)
                target_specs = relations.composite_key_specs(link, target_side)
                pred = relations.composite_predicate(
                    keyset.target_fields, target_specs, keyset.keys)
                incoming_preds[target_sid].append(pred)
                single_pair = len(link.keys) == 1
                prop_disclosures[target_sid].append({
                    "link_id": link.link_id,
                    "from_source": sid,
                    "target_fields": keyset.target_fields,
                    "key_count": len(keyset.keys),
                    "from_unfiltered": False,
                    "skipped_missing": keyset.skipped_missing,
                    "skipped_invalid": keyset.skipped_invalid,
                    "map_filter": relations.composite_single_pair_projection(keyset),
                    # 单键对时给出投影就绪的 typed 值（[族, 值] 列表）
                    "keys": (
                        [list(kt[0]) for kt in sorted(
                            keyset.keys, key=lambda parts: str(parts))]
                        if single_pair else []),
                })
        return matching, prop_disclosures

    matching, prop_disclosures = await asyncio.to_thread(_evaluate_propagation)

    # 3. per-source 权威面
    per_source: Dict[str, Dict[str, Any]] = {}
    for binding in bindings:
        sid = binding.source_id
        frozen = frozen_by_ref[binding.anchor.ref_id]
        rows = matching[sid]
        props = prop_disclosures[sid]
        own = own_wheres.get(sid)
        filter_out = _final_map_filter(own, props)
        active_props = [p for p in props if not p.get("from_unfiltered")]
        empty_propagation = any(p.get("key_count") == 0
                                for p in active_props)
        scope_ast = None if empty_propagation else \
            _combine_scope_where_ast(own, props)
        entry: Dict[str, Any] = {
            "source_id": sid,
            "role": binding.role,
            "granularity_note": binding.granularity_note,
            "anchor": frozen.anchor(),
            "total": frozen.feature_count,
            "match_count": len(rows),
            "match_count_exact": True,
            "own_where": own,
            "propagations": props,
            **filter_out,
        }
        if create_handle and empty_propagation:
            # 传播键集为空 = 下游联动范围为空集 —— where AST 无法表达
            # 空集（in 叶要求 ≥1 值），句柄跳过并如实披露（不给全量句柄
            # 冒充空 scope）
            entry["selection_handle_skipped"] = {
                "reason": "propagated_scope_empty",
                "note": "上游选择为空 → 本源联动范围为空集；句柄不可表达"
                        "空集（结果表按空集处理，不回退全量）",
            }
        elif create_handle and scope_ast is not None:
            from app.services.selection_handles import create_selection
            handle_entry = await create_selection(
                session_id, frozen.ref_id, frozen.content_revision,
                {"kind": "where", "where": scope_ast}, frozen.features)
            entry["selection_handle"] = {
                "handle": handle_entry.handle,
                "count": handle_entry.count,
                "count_exact": handle_entry.count_exact,
                "ttl_seconds": 600,
                "scope": "final-linked-scope",
            }
        elif create_handle:
            entry["selection_handle_skipped"] = {
                "reason": "propagation_not_ast_expressible",
                "note": "传播约束超 AST 键值帽或为复合键 —— 结果表口径"
                        "以下方 propagations 披露为准，不提供 where-scope "
                        "句柄（避免过宽范围冒充最终 scope）",
            }
        per_source[sid] = entry

    # 4. 图表 facet/full（图表源最终匹配行 + facet 只排除自身条件）
    charts_out: List[Dict[str, Any]] = []
    for chart in plan.charts:
        sid = contract.resolve_chart_source_id(plan, chart)
        rows = matching.get(sid, [])
        facet_where = contract.compile_plan_wheres(
            plan, conditions, exclude_chart_id=chart.chart_id).get(sid)
        facet_stats = await asyncio.to_thread(
            chart_statistics, rows, chart, where=facet_where)
        full_stats = await asyncio.to_thread(
            chart_statistics, rows, chart, where=own_wheres.get(sid))
        charts_out.append({
            "chart_id": chart.chart_id,
            "source_id": sid,
            "kind": chart.kind,
            "field": chart.field,
            "aggregation": chart.aggregation,
            "title": chart.title,
            "facet": {
                "scope": "excluding-own-condition",
                "note": "其它条件（含跨源传播）下的可选项",
                "statistics": facet_stats,
            },
            "full": {
                "scope": "all-own-conditions",
                "note": "本源全部条件后的匹配集统计（已含传播范围）",
                "statistics": full_stats,
            },
        })

    # 5. 指标（联动范围过滤后的行集）
    metrics_out: List[Dict[str, Any]] = []
    links_by_id = {lk.link_id: lk for lk in plan.links}
    for metric in _resolve_metric_sources(plan):
        metrics_out.append(await asyncio.to_thread(
            metrics_module.compute_metric, metric, matching,
            links_by_id=links_by_id))

    primary = contract.plan_primary_binding(plan)
    out = {
        "schema_version": plan.schema_version,
        "primary_source_id": primary.source_id,
        "anchors": {b.source_id: frozen_by_ref[b.anchor.ref_id].anchor()
                    for b in bindings},
        "conditions_digest": contract.plan_conditions_digest(plan, conditions),
        "plan_digest": _plan_digest_of(plan),
        "per_source": per_source,
        "charts": charts_out,
        "metrics": metrics_out,
        "completeness": "complete",
    }
    if selection_disclosure is not None:
        out["selection_ref_resolved"] = selection_disclosure
    return out


def _source_has_incoming(plan: contract.ExplorationPlan, sid: str) -> bool:
    """源是否有任何指向它的传播边（含经其它源传递的判定交给拓扑序 ——
    此处只看直接入边）。"""
    for link in plan.links:
        if link.propagate == "left_to_right" and link.right_source_id == sid:
            return True
        if link.propagate == "right_to_left" and link.left_source_id == sid:
            return True
    return False


async def _resolve_selection_ref(session_id: str, selection_ref: str) -> Dict[str, Any]:
    """选择史句柄解析（gis_situation selection_history；不可解析 →
    typed 拒绝 + 最小澄清，绝不随机挑一代）。基础设施读失败 ≠ 史空：
    区分为 5xx（review B-P3：误报"史空"会引导用户无谓重选）。"""
    from app.services.gis_situation.selection_history import resolve_selection_ref
    from app.services.session_data import session_data_manager

    try:
        ledger = await session_data_manager.get_state_field(
            session_id, "_interaction_ir_ledger")
    except LinkedExplorationError:
        raise
    except Exception as e:  # noqa: BLE001 — 存储故障 → 5xx（非"史空"）
        raise LinkedExplorationError(
            "SELECTION_LEDGER_UNAVAILABLE",
            f"选择史账本读取失败：{e}", status_code=503,
            correction_hint="稍后重试；或改用显式条件替代选择史指代。") from e
    claimed = resolve_selection_ref(
        ledger if isinstance(ledger, list) else [], selection_ref)
    if claimed is None:
        raise LinkedExplorationError(
            "SELECTION_REF_UNRESOLVED",
            f"选择史中无可解析句柄 {selection_ref!r}（史空/越界/已清除）",
            status_code=422,
            correction_hint="请说明具体哪一组（如 'previous' 或代号），"
                            "或重新选择后重试。")
    return claimed


def _resolve_id_field(features: Sequence[Any]) -> Optional[str]:
    """要素 id 字段链解析（与 feature_pages.FEATURE_ID_KEYS 同源；
    前 50 行抽样 —— review B-P3：抽样口径在错误 detail 披露）。"""
    from app.services.feature_pages import FEATURE_ID_KEYS

    for feat in list(features)[:50]:
        props = feat.get("properties") if isinstance(feat, dict) else None
        if not isinstance(props, dict):
            continue
        for key in FEATURE_ID_KEYS:
            if key in props:
                return key
    return None


# ─── 层级钻取：显式层级声明 + 计划改写重放（无层级 → 诚实阻塞） ──────────

_DRILL_CHART_PREFIX = "drill-"
_DRILL_COND_PREFIX = "drill-cond-"


async def drill_plan(
    session_id: str,
    plan_raw: Dict[str, Any],
    actor: Dict[str, Any],
    *,
    source_id: str,
    direction: str,
    parent_value: Any = None,
    create_handle: bool = False,
) -> Dict[str, Any]:
    """按源声明的层级做一次显式钻取/上卷，并重放权威 preview。

    - 层级只来自计划内 ``SourceBinding.hierarchy`` 声明（parent_field）；
      未声明 → ``HIERARCHY_NOT_DECLARED`` 诚实阻塞（绝不按字段名称猜测
      行政区划映射 —— 名称猜测是不可靠的隐式 join）。
    - ``down``：``parent_value`` 必填（typed scalar）→ 在该源追加/替换
      parent_field 条件（同控件 OR 语义单值）；``up``：移除该源
      parent_field 上的钻取条件（回上一级汇总视野）。
    - 钻取摘要 = 以 parent_field 分类图（full 口径）给出逐父级计数
      （typed 分组、溢出披露），回答"下一级有哪些、各多少"。
    """
    plan = _validate_plan_payload(plan_raw)
    binding = next((b for b in contract.plan_source_bindings(plan)
                    if b.source_id == source_id), None)
    if binding is None:
        raise LinkedExplorationError(
            "UNKNOWN_SOURCE",
            f"source_id {source_id!r} 不在计划 sources 中", status_code=422)
    if direction not in ("down", "up"):
        raise LinkedExplorationError(
            "INVALID_DRILL_DIRECTION", "direction 必须是 down/up",
            status_code=422)
    hierarchy = binding.hierarchy
    if hierarchy is None or not hierarchy.parent_field:
        raise LinkedExplorationError(
            "HIERARCHY_NOT_DECLARED",
            f"源 {source_id} 未声明层级映射（hierarchy.parent_field）—— "
            "钻取需要显式父子字段关系（用户指定或既有字段关系），"
            "本域不按名称猜测行政区层级",
            status_code=422,
            correction_hint="在计划中为该源声明 hierarchy.parent_field "
                            "（子粒度行持有父级键的字段）后再钻取。")
    # review B-P3：跨源父表（parent_source_id）需要键对齐的上卷语义，本域
    # 尚未实现 —— 诚实拒绝，不静默按本源字段过滤（会得到全 null 组）。
    if hierarchy.parent_source_id and hierarchy.parent_source_id != source_id:
        raise LinkedExplorationError(
            "CROSS_SOURCE_HIERARCHY_UNSUPPORTED",
            f"源 {source_id} 声明了跨源父表（parent_source_id="
            f"{hierarchy.parent_source_id}）—— 跨源上卷尚未支持",
            status_code=422,
            correction_hint="把父级键冗余进本源字段后声明同源 hierarchy，"
                            "或用键关系 + 指标（grain=父表）表达上卷口径。")
    if direction == "down":
        if parent_value is None or not isinstance(
                parent_value, (str, int, float, bool)):
            raise LinkedExplorationError(
                "INVALID_DRILL_VALUE",
                "down 需要父级键 parent_value（typed scalar）",
                status_code=422)

    parent_field = hierarchy.parent_field
    chart_id = f"{_DRILL_CHART_PREFIX}{source_id}"
    cond_id = f"{_DRILL_COND_PREFIX}{source_id}"

    # 1. 移除本源旧钻取条件/图（幂等重入；**精确 id 匹配** —— review
    #    B-P3：按前缀删会吞掉用户同前缀条目；drill 返回的计划可安全回喂）
    conditions = [c for c in plan.conditions
                  if not (isinstance(c, dict)
                          and c.get("condition_id") == cond_id)]
    if direction == "down":
        conditions.append({
            "kind": "category", "condition_id": cond_id,
            "chart_id": chart_id, "field": parent_field,
            "values": [parent_value], "source_id": source_id,
        })
    charts = [c for c in plan.charts if c.chart_id != chart_id]
    if len(charts) >= contract.MAX_CHARTS:
        raise LinkedExplorationError(
            "DRILL_PLAN_CAP",
            f"计划图表已满（{contract.MAX_CHARTS}），无法注入钻取摘要图",
            status_code=422,
            correction_hint="移除次要图表后再钻取。")
    # 服务层注入固定 id（与清理精确对应；model_construct 绕过用户面
    # id 校验，其余字段内部受控恒合法）
    charts.append(contract.ChartSpec.model_construct(
        chart_id=chart_id, kind="category", field=parent_field,
        aggregation="count", value_field=None,
        source_id=source_id, title=f"钻取摘要 · {parent_field}",
        bins=contract.DEFAULT_BINS, layer_id=None))
    new_plan = plan.model_copy(update={"charts": charts,
                                       "conditions": conditions})

    # 2. 权威重放（同代 + 传播 + 指标全口径）
    # 内部通道：new_plan 含服务层注入的保留前缀 id（已验证对象，model_copy
    # 构造）—— 外部入口的 plan_raw 校验面不适用
    preview = await preview_plan(
        session_id, None, actor,
        create_handle=create_handle, _validated_plan=new_plan)
    drill_chart = next((c for c in preview.get("charts", [])
                        if c.get("chart_id") == chart_id), None)
    stats = (drill_chart or {}).get("full", {}).get("statistics", {})
    rows = (stats.get("rows") or stats.get("bins") or [])
    summary_rows = [
        {"key": r.get("machine_value", r.get("label")),
         "label": r.get("label"), "count": r.get("count")}
        for r in rows[:32]
    ]
    return {
        "plan": new_plan.model_dump(mode="json"),
        "drill": {
            "source_id": source_id,
            "direction": direction,
            "parent_field": parent_field,
            "parent_value": parent_value if direction == "down" else None,
            "hierarchy_note": hierarchy.level_note,
            "groups": summary_rows,
            "group_count": len(rows),
            "truncated": bool(stats.get("truncated"))
            or len(rows) > len(summary_rows),
            "matched_in_scope": stats.get("matched"),
            "denominator_note": stats.get("denominator_note"),
        },
        "preview_brief": {
            "conditions_digest": preview.get("conditions_digest"),
            "per_source": {
                sid: {"match_count": e.get("match_count"),
                      "total": e.get("total")}
                for sid, e in (preview.get("per_source") or {}).items()
            },
        },
    }


# ─── 命名视图：持久化 CRUD + 锚校验 ──────────────────────────────────────

def _row_to_view(row: Any, *, include_plan: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "view_id": row.id,
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
        "source_ref": row.source_ref,
        "source_revision": row.source_revision,
        "source_fingerprint": row.source_fingerprint,
        "normalized_digest": row.normalized_digest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_plan:
        out["plan"] = row.plan
    if row.preview is not None:
        out["preview"] = row.preview
    return out


def _validate_plan_payload(plan_raw: Dict[str, Any]) -> contract.ExplorationPlan:
    try:
        return contract.ExplorationPlan.model_validate(plan_raw)
    except Exception as e:
        raise LinkedExplorationError(
            "INVALID_PLAN", f"计划校验失败：{e}", status_code=422) from e


def _parse_plan_conditions(plan: contract.ExplorationPlan) -> List[Any]:
    try:
        return contract.parse_conditions(plan.conditions)
    except LinkedExplorationError as e:
        raise LinkedExplorationError(
            "INVALID_PLAN", f"计划内条件非法：{e.message}", status_code=422) from e


def _plan_digest_of(plan: contract.ExplorationPlan) -> str:
    return contract.plan_digest_of_plan(plan)


def _plan_charts(plan: contract.ExplorationPlan) -> List[contract.ChartSpec]:
    """图表列表访问面（export.py 消费；v2/v3 同形）。"""
    return list(plan.charts)


async def _freeze_plan_sources(
    session_id: str,
    plan: contract.ExplorationPlan,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    """逐源冻结（同 ref 去重）并核对计划锚；返回 {ref_id: frozen}。
    任一源锚漂移 → STALE_SOURCE（detail 列出全部漂移源）。"""
    owner = _actor_owner_token(actor)
    trusted = _trusted(actor)
    frozen_by_ref: Dict[str, Any] = {}
    drift: List[Dict[str, Any]] = []
    for binding in contract.plan_source_bindings(plan):
        ref_id = binding.anchor.ref_id
        if ref_id in frozen_by_ref:
            continue
        frozen = await freeze_source_typed(
            session_id, ref_id, owner_token=owner, trusted_dispatch=trusted)
        frozen_by_ref[ref_id] = frozen
    for binding in contract.plan_source_bindings(plan):
        frozen = frozen_by_ref[binding.anchor.ref_id]
        if (frozen.content_revision != binding.anchor.content_revision
                or frozen.content_fingerprint
                != binding.anchor.content_fingerprint):
            drift.append({
                "source_id": binding.source_id,
                "ref_id": binding.anchor.ref_id,
                "planned_revision": binding.anchor.content_revision,
                "current_revision": frozen.content_revision,
            })
    if drift:
        first = drift[0]
        raise LinkedExplorationError(
            "STALE_SOURCE",
            f"计划锚已过期（源 {first['source_id']}：计划 rev "
            f"{first['planned_revision']}，当前 rev "
            f"{first['current_revision']}）—— 保存前请重新预览并更新计划锚",
            status_code=409,
            correction_hint="重新执行多源 preview 并用新锚保存。",
            detail={"sources": drift})
    return frozen_by_ref


async def save_view(
    session_id: str,
    actor: Dict[str, Any],
    *,
    title: str,
    plan_raw: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """保存命名视图：逐源重新冻结核对锚（每个源都必须仍同代）。"""
    plan = _validate_plan_payload(plan_raw)
    if not title or len(title) > 200:
        raise LinkedExplorationError(
            "INVALID_TITLE", "标题必须是 1-200 字符", status_code=422)
    await _freeze_plan_sources(session_id, plan, actor)
    primary = contract.plan_primary_binding(plan)
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob) > _PLAN_JSON_MAX:
        raise LinkedExplorationError(
            "PLAN_OVER_LIMIT",
            f"计划过大（{len(blob)} B > {_PLAN_JSON_MAX}）", status_code=422)

    def _insert() -> Any:
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        with SessionLocal() as db:
            row = ExplorationView(
                session_id=session_id,
                project_id=project_id,
                created_by=actor.get("user_id"),
                org_id=actor.get("org_id"),
                title=title,
                status="draft",
                revision=1,
                plan=plan.model_dump(mode="json"),
                normalized_digest=_plan_digest_of(plan),
                # 冗余列语义固化为 primary 源（多源完整清单以 plan JSON
                # 为权威 —— v3 起逐源锚存 sources，无需新迁移）
                source_ref=primary.anchor.ref_id,
                source_revision=primary.anchor.content_revision,
                source_fingerprint=primary.anchor.content_fingerprint,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    row = await asyncio.to_thread(_insert)
    return _row_to_view(row)


async def list_views(
    session_id: str,
    actor: Dict[str, Any],
    *,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    def _query() -> List[Any]:
        from sqlalchemy import select as sa_select
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        with SessionLocal() as db:
            stmt = sa_select(ExplorationView).where(
                ExplorationView.session_id == session_id,
            ).order_by(ExplorationView.created_at.desc()).limit(100)
            rows = db.scalars(stmt).all()
            return [r for r in rows if include_archived or r.status != "archived"]

    rows = await asyncio.to_thread(_query)
    return [_row_to_view(r, include_plan=False) for r in rows]


async def get_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    row = await _owned_view(view_id, session_id)
    return _row_to_view(row)


async def _owned_view(view_id: str, session_id: str) -> Any:
    """会话归属校验：不区分『不存在』与『别人的』（无存在性泄漏）。"""
    def _query() -> Any:
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        with SessionLocal() as db:
            row = db.get(ExplorationView, view_id)
            if row is None or row.session_id != session_id:
                return None
            return row

    row = await asyncio.to_thread(_query)
    if row is None:
        raise LinkedExplorationError(
            "VIEW_NOT_FOUND", "视图不存在或不在当前会话（不区分两者）",
            status_code=404)
    return row


async def update_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    title: Optional[str],
    plan_raw: Optional[Dict[str, Any]],
    status: Optional[str],
    expected_revision: int,
) -> Dict[str, Any]:
    """CAS 修改（E25：并发作者 → stale_view_revision，不盲覆盖）。"""
    if status is not None and status not in ("draft", "archived"):
        raise LinkedExplorationError(
            "INVALID_STATUS", "status 必须是 draft/archived", status_code=422)
    row = await _owned_view(view_id, session_id)
    new_plan_raw = plan_raw if plan_raw is not None else row.plan
    plan = _validate_plan_payload(new_plan_raw)
    primary = contract.plan_primary_binding(plan)
    if plan_raw is not None:
        # review P2-1：换计划与保存同面 —— 逐源重新冻结核对锚（防 PATCH
        # 绕过 save_view 的 STALE_SOURCE 合同）
        await _freeze_plan_sources(session_id, plan, actor)
    digest = _plan_digest_of(plan)
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob) > _PLAN_JSON_MAX:
        raise LinkedExplorationError(
            "PLAN_OVER_LIMIT",
            f"计划过大（{len(blob)} B > {_PLAN_JSON_MAX}）", status_code=422)

    def _update() -> Any:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            fresh = db.get(type(row), row.id)
            if fresh is None:
                return None
            if fresh.revision != expected_revision:
                raise LinkedExplorationError(
                    "STALE_VIEW_REVISION",
                    f"视图已被其他作者修改（current revision={fresh.revision}）",
                    status_code=409,
                    detail={"current_revision": fresh.revision},
                )
            if title is not None:
                if not title or len(title) > 200:
                    raise LinkedExplorationError(
                        "INVALID_TITLE", "标题必须是 1-200 字符", status_code=422)
                fresh.title = title
            if status is not None:
                fresh.status = status
            if plan_raw is not None:
                fresh.plan = plan.model_dump(mode="json")
                fresh.normalized_digest = digest
                fresh.source_ref = primary.anchor.ref_id
                fresh.source_revision = primary.anchor.content_revision
                fresh.source_fingerprint = primary.anchor.content_fingerprint
            fresh.revision = expected_revision + 1
            db.commit()
            db.refresh(fresh)
            return fresh

    fresh = await asyncio.to_thread(_update)
    if fresh is None:
        raise LinkedExplorationError(
            "VIEW_NOT_FOUND", "视图不存在或不在当前会话", status_code=404)
    return _row_to_view(fresh)


async def delete_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    expected_revision: int,
) -> Dict[str, Any]:
    row = await _owned_view(view_id, session_id)

    def _delete() -> bool:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            fresh = db.get(type(row), row.id)
            if fresh is None:
                return False
            if fresh.revision != expected_revision:
                raise LinkedExplorationError(
                    "STALE_VIEW_REVISION",
                    f"视图已被其他作者修改（current revision={fresh.revision}）",
                    status_code=409,
                    detail={"current_revision": fresh.revision},
                )
            db.delete(fresh)
            db.commit()
            return True

    ok = await asyncio.to_thread(_delete)
    if not ok:
        raise LinkedExplorationError(
            "VIEW_NOT_FOUND", "视图不存在或不在当前会话", status_code=404)
    return {"deleted": True, "view_id": view_id}


async def open_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    revalidate: bool = True,
) -> Dict[str, Any]:
    """重开命名视图：逐源按 pinned 锚校验当前源。

    - 全部源锚匹配 → ``state=ready``，返回计划 + 重新预览的完整统计
      （同代；多源计划含传播键集与指标）；
    - 任一源推进/消失/无强版本 → ``state=stale``，逐源列明漂移，明确
      要求重新预览/重新绑定，绝不无声转为最新数据（E20/E21）。
    """
    row = await _owned_view(view_id, session_id)
    plan = _validate_plan_payload(row.plan)
    out: Dict[str, Any] = {
        "view": _row_to_view(row),
        "plan": row.plan,
    }
    if not revalidate:
        return out
    bindings = contract.plan_source_bindings(plan)
    owner = _actor_owner_token(actor)
    trusted = _trusted(actor)
    drift: List[Dict[str, Any]] = []
    frozen_by_ref: Dict[str, Any] = {}
    for binding in bindings:
        ref_id = binding.anchor.ref_id
        if ref_id in frozen_by_ref:
            frozen = frozen_by_ref[ref_id]
        else:
            try:
                frozen = await freeze_source_typed(
                    session_id, ref_id,
                    owner_token=owner, trusted_dispatch=trusted)
            except LinkedExplorationError as e:
                # 源消失/无强版本 → 视图标记 stale（保留计划，要求重绑）
                out["state"] = "stale"
                out["stale_reason"] = {
                    "code": e.code,
                    "message": e.message,
                    "sources": [{"source_id": binding.source_id,
                                 "ref_id": ref_id}],
                }
                return out
            frozen_by_ref[ref_id] = frozen
        if (frozen.content_revision != binding.anchor.content_revision
                or frozen.content_fingerprint
                != binding.anchor.content_fingerprint):
            drift.append({
                "source_id": binding.source_id,
                "ref_id": ref_id,
                "planned_revision": binding.anchor.content_revision,
                "current_revision": frozen.content_revision,
            })
    if drift:
        first = drift[0]
        out["state"] = "stale"
        out["stale_reason"] = {
            "code": "SOURCE_ADVANCED",
            "message": (
                f"源 {first['source_id']} 已从 rev "
                f"{first['planned_revision']} 推进到 "
                f"{first['current_revision']}；视图保留原锚，需重新预览"
                "/重新绑定"),
            "sources": drift,
        }
        return out
    # 锚匹配：同代重放（预览统计 + handle 都基于当前冻结）。
    # review B-P3：open 自身冻结与 preview_plan 二次冻结之间源可能推进 ——
    # 把 STALE_SOURCE 折算为 state=stale（与锚漂移同一响应形，不抛 409）。
    try:
        preview = await preview_plan(session_id, row.plan, actor)
    except LinkedExplorationError as e:
        if e.code == "STALE_SOURCE":
            out["state"] = "stale"
            out["stale_reason"] = {
                "code": "SOURCE_ADVANCED",
                "message": e.message,
                "sources": (e.detail or {}).get("sources", []),
            }
            return out
        raise
    out["state"] = "ready"
    out["preview"] = _bounded_preview(preview)

    def _persist_preview() -> None:
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        with SessionLocal() as db:
            fresh = db.get(ExplorationView, row.id)
            if fresh is None:
                return
            fresh.preview = out["preview"]
            fresh.preview_digest = out["preview"].get("conditions_digest")
            db.commit()

    await asyncio.to_thread(_persist_preview)
    return out


def _bounded_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    """preview 的有界投影（入行 ≤8KB；完整统计由前端实时 preview 获取）。"""
    charts_brief = []
    for c in preview.get("charts", []):
        facet = c.get("facet", {}).get("statistics", {})
        full = c.get("full", {}).get("statistics", {})
        charts_brief.append({
            "chart_id": c.get("chart_id"),
            "source_id": c.get("source_id"),
            "kind": c.get("kind"),
            "field": c.get("field"),
            "facet_matched": facet.get("matched"),
            "full_matched": full.get("matched"),
            "truncated": bool(facet.get("truncated") or full.get("truncated")),
        })
    per_source = preview.get("per_source") or {}
    primary_id = preview.get("primary_source_id")
    primary_entry = per_source.get(primary_id) or {}
    source_brief = {}
    for sid, entry in per_source.items():
        source_brief[sid] = {
            "match_count": entry.get("match_count"),
            "total": entry.get("total"),
            "has_handle": "selection_handle" in entry,
            "handle_skipped": "selection_handle_skipped" in entry,
            "propagations": len(entry.get("propagations") or []),
        }
    metrics_brief = []
    for m in preview.get("metrics", []):
        groups = m.get("groups", [])
        available = sum(1 for g in groups if g.get("available"))
        metrics_brief.append({
            "metric_id": m.get("metric_id"),
            "op": m.get("op"),
            "groups": len(groups),
            "available_groups": available,
        })
    blob = {
        "anchor": preview.get("anchors", {}).get(primary_id),
        "primary_source_id": primary_id,
        "conditions_digest": preview.get("conditions_digest"),
        "plan_digest": preview.get("plan_digest"),
        "match_count": primary_entry.get("match_count"),
        "total": primary_entry.get("total"),
        "per_source": source_brief,
        "charts": charts_brief,
        "metrics": metrics_brief,
    }
    if len(json.dumps(blob, ensure_ascii=False, default=str)) > _PREVIEW_JSON_MAX:
        blob["charts"] = [
            {"chart_id": c.get("chart_id")} for c in charts_brief
        ]
        blob["per_source"] = {
            sid: {"match_count": e.get("match_count")}
            for sid, e in per_source.items()
        }
    return blob


# ─── apply：图表组件原子写入 MapSpec ─────────────────────────────────────

async def apply_view_to_mapspec(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """把视图图表组件写入 committed MapSpec（upsert；分步失败回滚）。

    组件 options 携带 viewId/chartId/field/kind/aggregation/layerId —— 渲染
    数据由前端实时 preview 获取（跟当前源版本），组件本身只存配置。
    apply 不改源数据、不改既有图层 filter（组合过滤是本域活动状态，
    保存不隐含换当前地图，E26）。
    """
    from app.services.mapspec_store import mapspec_store

    row = await _owned_view(view_id, session_id)
    plan = _validate_plan_payload(row.plan)
    if not plan.charts:
        raise LinkedExplorationError(
            "EMPTY_VIEW", "视图没有图表实例，无可应用内容", status_code=422)
    applied: List[str] = []
    revision = expected_revision
    # v3：组件源引用按图表绑定源解析（缺省 primary）；v2 → primary 即 anchor
    source_of = {
        c.chart_id: contract.resolve_chart_source_id(plan, c)
        for c in plan.charts
    }
    anchors_by_source = {
        b.source_id: b.anchor for b in contract.plan_source_bindings(plan)
    }
    try:
        for chart in plan.charts:
            component_id = f"exploration-chart-{chart.chart_id}"
            binding = anchors_by_source[source_of[chart.chart_id]]
            res = await mapspec_store.patch_component(
                session_id,
                component_id=component_id,
                component_type="exploration_chart_panel",
                options={
                    "viewId": view_id,
                    "chartId": chart.chart_id,
                    "kind": chart.kind,
                    "field": chart.field,
                    "aggregation": chart.aggregation,
                    "valueField": chart.value_field or "",
                    "bins": chart.bins if chart.kind == "range" else None,
                    "layerId": chart.layer_id or "",
                    "sourceId": source_of[chart.chart_id],
                    "sourceRef": binding.ref_id,
                    "sourceRevision": binding.content_revision,
                    "title": chart.title or contract.chart_kind_label(chart.kind),
                },
                upsert=True,
                expected_revision=revision,
                # origin=agent：服务编排写入（用户经面板/Pi 触发，但写入方是
                # 领域服务）；expected_revision 透传可选 CAS（G41 apply 同语义）
                origin="agent",
                actor="linked_exploration",
                mutation_id=f"g46-apply-{view_id}-{chart.chart_id}",
            )
            if res.get("status") == "superseded" or res.get("superseded"):
                raise LinkedExplorationError(
                    "MAPSPEC_SUPERSEDED",
                    "MapSpec 已被并发修改（superseded）—— 请刷新后重试",
                    status_code=409,
                    detail={"component_id": component_id})
            if res.get("success") is False or res.get("is_error"):
                raise LinkedExplorationError(
                    "APPLY_REJECTED",
                    f"组件写入被拒绝：{res.get('message') or res.get('warnings') or 'unknown'}",
                    status_code=409,
                    detail={"component_id": component_id,
                            "result": {k: v for k, v in res.items()
                                       if k in ("message", "warnings", "correction_hint")}})
            applied.append(component_id)
            # patch_component 回执的权威 CAS 令牌是 mutation_revision
            # （review P1-1：此前误读不存在的 "revision" 键 → 多图表 CAS
            # 链从不前进，第二个组件必被拒）。superseded 折在 status 键。
            new_rev = res.get("mutation_revision")
            if isinstance(new_rev, int) and new_rev > (revision or 0):
                revision = new_rev
    except LinkedExplorationError:
        await _rollback_components(session_id, applied)
        raise
    except Exception as e:  # noqa: BLE001 — 任何底层失败都不得留半套组件
        await _rollback_components(session_id, applied)
        raise LinkedExplorationError(
            "APPLY_FAILED", f"写入 MapSpec 失败：{e}", status_code=502) from e
    return {
        "applied_components": applied,
        "count": len(applied),
        "state": "applied",
    }


async def _rollback_components(session_id: str, component_ids: List[str]) -> None:
    """尽力回滚已写入的组件（失败披露在日志；不掩盖原错误）。"""
    from app.services.mapspec_store import mapspec_store

    for cid in component_ids:
        try:
            await mapspec_store.patch_component(
                session_id, component_id=cid, enabled=False,
                origin="agent", actor="linked_exploration-rollback",
                mutation_id=f"g46-rollback-{cid}",
            )
        except Exception:  # noqa: BLE001 — 回滚尽力而为
            logger.warning("G46 rollback failed for %s", cid, exc_info=True)


__all__ = [
    "inspect_source", "preview_conditions", "preview_plan", "drill_plan",
    "save_view", "list_views", "get_view", "update_view", "delete_view",
    "open_view", "apply_view_to_mapspec",
]
