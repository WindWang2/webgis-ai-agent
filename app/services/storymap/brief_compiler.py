"""G11 —— 简报草稿编译服务壳（BRF-02）。

从会话的**真实授权产物**生成默认草稿：trace（优先）→ 章节弧
（复用 lib 编译器，不建第二 compiler）→ 产物/事实提取 → 确定性事实
核验 → blocks/facts/provenance 组装。

- LLM 不在链路上：``generator=deterministic``（默认）纯确定性生成；
  stub LLM 模式是显式参数，且只允许组织解释文字、不产生/修改权威数值
  —— 数值永远来自结构化 facts（field_path 可定位）。
- 失败封闭：artifact 不可达/无记录 → 事实 unverified + 原因，不编造。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.lib.storymap.brief_factcheck import apply_outcomes, check_facts
from app.lib.storymap.brief_spec import (
    BRIEF_SPEC_SCHEMA_VERSION,
    MAX_BRIEF_FACTS,
    MAX_BRIEF_SOURCES,
    BriefBlock,
    BriefFact,
    BriefMapView,
    BriefProvenance,
    EvidenceBinding,
    MapBriefSpec,
)
from app.lib.storymap.spec import ArcRole
from app.lib.storymap.story_compiler import compile_story_map

# 每产物行提取上限（确定性：取前 N 行；简报整体另受 MAX_BRIEF_FACTS 约束）。
_ROWS_PER_ARTIFACT = 8
# stats 摘要键值进入事实的上限。
_STATS_PER_STEP = 6


def _canonical_sha256(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value == value  # NaN
            and value not in (float("inf"), float("-inf")))


def _artifact_label(record: Mapping[str, Any]) -> str:
    """台账记录 → 用户可读来源名（BRF-01：不能只有 hash）。"""
    meta = record.get("metadata") or {}
    for key in ("title", "label", "name"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:120]
    capability = record.get("producer_capability") or record.get("producer_tool")
    ref = str(record.get("artifact_id") or "")
    kind = ref[4:].split("-", 1)[0].split("/", 1)[0] if ref.startswith("ref:") else ""
    if capability:
        return f"{capability}（{kind or '产物'}）"
    return f"会话产物 {kind or ref[:24]}"


def _record_time(record: Mapping[str, Any]) -> str:
    import datetime as _dt

    ts = record.get("updated_at") or record.get("created_at") or 0
    try:
        return _dt.datetime.fromtimestamp(
            float(ts), tz=_dt.timezone.utc
        ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def _table_rows(payload: Any) -> Tuple[List[str], List[Mapping[str, Any]]]:
    """{table:{columns,rows}} / {table:[…]} → (列名, 行 dict 列表)。"""
    table = payload.get("table") if isinstance(payload, Mapping) else None
    if isinstance(table, Mapping):
        rows = table.get("rows")
        columns = table.get("columns")
        col_names = [str(c) for c in columns] if isinstance(columns, list) else []
        dict_rows = [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []
        return col_names, dict_rows
    if isinstance(table, list):
        return [], [r for r in table if isinstance(r, Mapping)]
    return [], []


def _chart_rows(payload: Any) -> List[Mapping[str, Any]]:
    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    if not isinstance(chart, Mapping):
        return []
    data = chart.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, Mapping)]
    return []


def _numeric_fields(row: Mapping[str, Any]) -> List[str]:
    return [k for k, v in row.items() if _is_number(v)]


def _locate_value(payload: Any, value: float, key_hint: str) -> str:
    """在产物载荷中定位与 value 相等的数值字段（确定性首匹配）。

    key_hint（统计键名）优先匹配字段名子串（大小写不敏感）；扫描有界
    （行 ≤ 64、字段 ≤ 32）。返回 field_path；未命中返回 ""。
    """
    def rows_of(data: Any) -> List[Tuple[str, int, Mapping[str, Any]]]:
        out: List[Tuple[str, int, Mapping[str, Any]]] = []
        if isinstance(data, Mapping):
            table = data.get("table")
            chart = data.get("chart")
        else:
            table = chart = None
        if isinstance(table, Mapping):
            rows = table.get("rows")
            if isinstance(rows, list):
                out.extend((f"table.rows.{i}", i, r) for i, r in enumerate(rows)
                           if isinstance(r, Mapping))
        elif isinstance(table, list):
            out.extend((f"table.{i}", i, r) for i, r in enumerate(table)
                       if isinstance(r, Mapping))
        if isinstance(chart, Mapping) and isinstance(chart.get("data"), list):
            out.extend((f"chart.data.{i}", i, r)
                       for i, r in enumerate(chart["data"])
                       if isinstance(r, Mapping))
        return out

    hint = (key_hint or "").lower()
    candidates: List[str] = []
    for prefix, _, row in rows_of(payload)[:64]:
        for field in list(row.keys())[:32]:
            val = row.get(field)
            if not _is_number(val) or float(val) != value:
                continue
            path = f"{prefix}.{field}"
            if hint and hint in field.lower():
                return path  # 键名匹配优先，立即命中
            candidates.append(path)
    return candidates[0] if candidates else ""


def _facts_from_artifacts(
    records: Dict[str, Mapping[str, Any]],
    payloads: Dict[str, Any],
) -> List[BriefFact]:
    """chart/table 产物 → 结构化事实（field_path 精确指向权威字段）。"""
    facts: List[BriefFact] = []
    covered: set = set()

    def _binding(ref: str, record: Mapping[str, Any], field: str,
                 field_path: str) -> EvidenceBinding:
        unit = str((record.get("metadata") or {}).get("unit") or "")
        return EvidenceBinding(
            artifact_ref=ref,
            source_label=_artifact_label(record),
            produced_at=_record_time(record),
            method=str(record.get("producer_tool")
                       or record.get("producer_capability") or ""),
            stat_semantics=field[:120],
            unit=unit[:24],
            field_path=field_path,
            artifact_sha256=_canonical_sha256(record),
        )

    for ref in sorted(payloads):
        if len(facts) >= MAX_BRIEF_FACTS:
            break
        record = records.get(ref) or {}
        payload = payloads[ref]
        if not isinstance(payload, Mapping):
            continue
        label_base = _artifact_label(record)
        # 表：每行（≤_ROWS_PER_ARTIFACT）的每个数值字段一条事实。
        columns, rows = _table_rows(payload)
        for row_idx, row in enumerate(rows[:_ROWS_PER_ARTIFACT]):
            name_field = next(
                (k for k in row if isinstance(row[k], str)), None)
            for field in _numeric_fields(row):
                if len(facts) >= MAX_BRIEF_FACTS:
                    break
                field_path = (
                    f"table.rows.{row_idx}.{field}"
                    if isinstance(payload.get("table"), Mapping)
                    else f"table.{row_idx}.{field}")
                covered.add((ref, field_path))
                row_name = row.get(name_field) if name_field else None
                kind = ("count" if "count" in field.lower() or "数量" in field
                        else "other")
                facts.append(BriefFact(
                    fact_id=f"fact-{hashlib.sha1(f'{ref}:{field_path}'.encode()).hexdigest()[:12]}",
                    label=(f"{row_name} · {field}" if isinstance(row_name, str)
                           else f"{label_base} · {field}"),
                    kind=kind,  # type: ignore[arg-type]
                    value=float(row[field]),
                    basis=_binding(ref, record, field, field_path),
                ))
        # 图：data rows 的数值字段。
        for row_idx, row in enumerate(_chart_rows(payload)[:_ROWS_PER_ARTIFACT]):
            for field in _numeric_fields(row):
                if len(facts) >= MAX_BRIEF_FACTS:
                    break
                field_path = f"chart.data.{row_idx}.{field}"
                covered.add((ref, field_path))
                name = row.get("name") or row.get("x") or row.get("label")
                facts.append(BriefFact(
                    fact_id=f"fact-{hashlib.sha1(f'{ref}:{field_path}'.encode()).hexdigest()[:12]}",
                    label=(f"{name} · {field}" if isinstance(name, str)
                           else f"{label_base} · {field}"),
                    kind="other",
                    value=float(row[field]),
                    basis=_binding(ref, record, field, field_path),
                ))
    return facts


def _locate_covered_set(facts: Sequence[BriefFact]) -> set:
    return {(f.basis.artifact_ref, f.basis.field_path) for f in facts}


def _facts_from_trace_stats(
    trace: Optional[Mapping[str, Any]],
    records: Dict[str, Mapping[str, Any]],
    payloads: Dict[str, Any],
    covered: set,
) -> List[BriefFact]:
    """trace 步骤的 stats/metrics 摘要 → 事实。

    正向证明单一通道：统计值必须在绑定产物的载荷中**定位到具体字段**
    （``_locate_value``，键名提示优先）才带 field_path；定位不到 →
    field_path 留空（核验将诚实落 unverified），绝不虚构定位。
    与产物行提取重复（ref+field_path 已覆盖）的统计跳过。
    """
    if not trace:
        return []
    from app.lib.storymap.story_compiler import normalize_trace

    try:
        steps = normalize_trace(trace)
    except (ValueError, TypeError):
        return []
    facts: List[BriefFact] = []
    for step in steps:
        stats = next(
            (step.payload[k] for k in ("stats", "metrics")
             if isinstance(step.payload.get(k), Mapping)), None)
        if not isinstance(stats, Mapping):
            continue
        ref = ""
        for key in ("ref", "artifact_ref", "artifact_id"):
            val = step.payload.get(key)
            if isinstance(val, str) and val.startswith("ref:"):
                ref = val
                break
        record = records.get(ref) if ref else {}
        payload = payloads.get(ref) if ref else None
        for i, (key, val) in enumerate(stats.items()):
            if i >= _STATS_PER_STEP or len(facts) >= MAX_BRIEF_FACTS:
                break
            if not _is_number(val):
                continue
            field_path = ""
            if ref and isinstance(payload, Mapping):
                field_path = _locate_value(payload, float(val), str(key))
                if not field_path or (ref, field_path) in covered:
                    continue  # 定位失败或与行提取重复 → 不造重复事实
            binding = EvidenceBinding(
                artifact_ref=ref,
                method=str(step.payload.get("tool_name") or ""),
                stat_semantics=str(key)[:120],
                field_path=field_path,
            )
            if ref and record:
                binding = binding.model_copy(update={
                    "source_label": _artifact_label(record),
                    "produced_at": _record_time(record),
                    "artifact_sha256": _canonical_sha256(record),
                })
            facts.append(BriefFact(
                fact_id=f"fact-{hashlib.sha1(f's:{step.stage_id}:{step.ts}:{key}'.encode()).hexdigest()[:12]}",
                label=str(key)[:120],
                kind="other",
                value=float(val),
                basis=binding,
            ))
    return facts


def _pick_metric_chapters(spec_draft: MapBriefSpec) -> List[str]:
    """事实挂章的确定性顺序：解剖 → 宏观 → 其余按章节序。"""
    preferred: List[str] = []
    roles: Tuple[str, ...] = ("focus_dissection", "macro_situation")
    for role in roles:
        for ch in spec_draft.chapters:
            if ch.arc_role == role:
                preferred.append(ch.id)
    for ch in spec_draft.chapters:
        if ch.id not in preferred:
            preferred.append(ch.id)
    return preferred


async def _load_session_inputs(
    conv, *, owner_token: str = "", include_payloads: bool = True
) -> Tuple[Dict[str, Mapping[str, Any]], Dict[str, Any], Optional[Mapping[str, Any]], str, List[Mapping[str, Any]]]:
    """会话 → (产物台账投影, 产物载荷, trace, mapspec 指纹, 消息)。

    ``include_payloads=False`` 供只需台账投影的读路径（stale 计算），
    避免大产物会话的读放大（评审 #P3-7）。
    """
    from app.services.artifact_registry import list_artifacts
    from app.services.gis_harness.trace_store import read_chains
    from app.services.session_data import session_data_manager
    from app.services.mapspec.store import mapspec_store_instance

    session_id = str(conv.id)
    records: Dict[str, Mapping[str, Any]] = {}
    payloads: Dict[str, Any] = {}
    artifact_list = await list_artifacts(session_id)
    for rec in artifact_list:
        ref = rec.artifact_id
        records[ref] = rec.to_dict()
        if include_payloads and ref.startswith(
                ("ref:chart-", "ref:table-", "ref:stats-",
                 "ref:grid-", "ref:admin-", "ref:profile-")):
            try:
                res = await session_data_manager.get_ref_data(
                    session_id, ref, owner_token=owner_token or None)
                payloads[ref] = res.data if getattr(res, "success", False) else None
            except Exception:  # noqa: BLE001 — 载荷不可达 → None（失败封闭）
                payloads[ref] = None

    trace: Optional[Mapping[str, Any]] = None
    try:
        chains = read_chains(session_id)
        stages: List[Mapping[str, Any]] = []
        for chain in chains:
            stages.extend(chain.get("stages") or [])
        if stages:
            trace = {"session_id": session_id, "stages": list(stages)}
    except Exception:  # noqa: BLE001 — trace 缺席走消息降级
        trace = None

    mapspec_fp = ""
    try:
        stored = await mapspec_store_instance.get_mapspec(session_id)
        if isinstance(stored, dict):
            mapspec_fp = _canonical_sha256(stored)
    except Exception:  # noqa: BLE001
        mapspec_fp = ""

    messages: List[Mapping[str, Any]] = []
    return records, payloads, trace, mapspec_fp, messages


async def _session_messages(db, conv) -> List[Mapping[str, Any]]:
    from sqlalchemy import select

    from app.models.db_model import Message

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id,
               Message.role.in_(("user", "assistant")))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(200)
    )
    return [
        {"role": m.role, "content": m.content or ""}
        for m in reversed(result.scalars().all())
    ]


async def compile_brief_draft(
    conv,
    db=None,
    *,
    title: Optional[str] = None,
    generator: str = "deterministic",
    owner_token: str = "",
) -> MapBriefSpec:
    """会话真实产物 → 简报草稿（MapBriefSpec）。

    trace 缺席时降级消息编译（与既有 compile_for_session 同纪律）；
    无消息且无 trace → ValueError（调用方 422）。
    """
    records, payloads, trace, mapspec_fp, _ = await _load_session_inputs(
        conv, owner_token=owner_token)
    messages = await _session_messages(db, conv) if db is not None else []

    if trace is None and not messages and records:
        # 产物降级路径：无 trace 且无消息，但会话有真实产物 —— 合成
        # ReplayTrace 形状的确定性投影（叙事骨架来自产物清单本身；
        # 数值仍只经 facts 从产物字段提取，此处不造任何数值）。
        trace = {
            "user_intent": title or "基于会话分析产物生成简报",
            "artifacts": [
                {"ref": ref, "title": _artifact_label(rec)}
                for ref, rec in sorted(records.items())[:16]
            ],
            "final_text": f"本简报基于会话内 {len(records)} 个分析产物生成。",
        }

    try:
        base = compile_story_map(
            trace=trace, messages=messages or None,
            session_id=str(conv.id), title=title,
        )
    except (ValueError, TypeError) as exc:
        if trace is not None and messages:
            base = compile_story_map(messages=messages, session_id=str(conv.id), title=title)
        else:
            raise ValueError(f"cannot compile brief draft: {exc}") from exc

    # 事实：产物行提取（精确 field_path）优先，trace 统计补充（定位到
    # 具体字段才可正向证明；ref+field_path 去重）。
    artifact_facts = _facts_from_artifacts(records, payloads)
    covered = _locate_covered_set(artifact_facts)
    facts = (artifact_facts
             + _facts_from_trace_stats(trace, records, payloads, covered))
    facts = facts[:MAX_BRIEF_FACTS]

    # 确定性核验（positive-proof；不可达 → unverified + 原因）。
    outcomes = check_facts(facts, records=records, payloads=payloads)
    facts = apply_outcomes(facts, outcomes)

    spec = MapBriefSpec(
        **base.model_dump(),
        brief_schema_version=BRIEF_SPEC_SCHEMA_VERSION,
        provenance=BriefProvenance(
            session_id=str(conv.id),
            artifact_refs=sorted(records)[:128],
            mapspec_fingerprint=mapspec_fp,
            generator=generator,  # type: ignore[arg-type]
        ),
        facts=facts,
    )
    spec = _attach_blocks(spec, records)
    return spec


def _attach_blocks(spec: MapBriefSpec, records: Dict[str, Mapping[str, Any]]) -> MapBriefSpec:
    """组装 blocks：正文（从章节 narrative 迁移）/指标/图表/地图/方法/局限/来源。"""
    blocks: List[BriefBlock] = []
    order = 0
    chapters = list(spec.chapters)
    keyframes = {kf.chapter_id: kf for kf in spec.camera_keyframes}

    def _next_order() -> int:
        nonlocal order
        order += 1
        return order

    def _chapter_for_role(role: ArcRole) -> Optional[str]:
        for ch in chapters:
            if ch.arc_role == role:
                return ch.id
        return None

    # 1) 正文块：每章 narrative → text 块（auto）；chapter.narrative 保留投影。
    for ch in chapters:
        if ch.narrative.strip():
            blocks.append(BriefBlock(
                id=f"blk-text-{hashlib.sha1(ch.id.encode()).hexdigest()[:12]}",
                chapter_id=ch.id, kind="text", order=_next_order(),
                origin="auto", text=ch.narrative))

    # 2) 指标块：事实挂章（解剖→宏观→其余）。
    metric_chapters = _pick_metric_chapters(spec)
    for i, fact in enumerate(spec.facts):
        chapter_id = metric_chapters[i % len(metric_chapters)] if metric_chapters else chapters[0].id
        blocks.append(BriefBlock(
            id=f"blk-metric-{hashlib.sha1(fact.fact_id.encode()).hexdigest()[:12]}",
            chapter_id=chapter_id, kind="metric", order=_next_order(),
            fact_id=fact.fact_id))

    # 3) 图表/表块：挂 widget 自带的 chapter_id。
    for w in spec.linked_widgets:
        blocks.append(BriefBlock(
            id=f"blk-{w.kind}-{hashlib.sha1(w.id.encode()).hexdigest()[:12]}",
            chapter_id=w.chapter_id or (chapters[0].id if chapters else ""),
            kind="chart" if w.kind == "chart" else "table",
            order=_next_order(), widget_id=w.id))

    # 4) 地图视图块：每章镜头 + MapSpec 指纹 pin（BRF-O04）。
    for ch in chapters:
        kf = keyframes.get(ch.id)
        if kf is None:
            continue
        blocks.append(BriefBlock(
            id=f"blk-map-{hashlib.sha1(ch.id.encode()).hexdigest()[:12]}",
            chapter_id=ch.id, kind="map_view", order=_next_order(),
            map_view=BriefMapView(
                center=list(kf.center), zoom=kf.zoom, pitch=kf.pitch,
                bearing=kf.bearing, visible_layer_ids=list(ch.visible_layer_ids),
                mapspec_fingerprint=spec.provenance.mapspec_fingerprint)))

    # 5) 方法与局限块（各一条，挂宏观/建言章；缺席挂首章）。
    method_chapter = _chapter_for_role("macro_situation") or (chapters[0].id if chapters else "")
    limitation_chapter = _chapter_for_role("recommendation") or (chapters[-1].id if chapters else "")
    blocks.append(BriefBlock(
        id="blk-method-0001", chapter_id=method_chapter, kind="method",
        order=_next_order(),
        text="## 方法说明\n\n本简报数值来自会话内已授权的分析产物；每个指标卡"
             "可展开查看来源产物、字段口径与时间窗。比例类指标要求分子/分母"
             "同时在场，缺分母的表述以“未验证”披露。"))
    blocks.append(BriefBlock(
        id="blk-limit-0001", chapter_id=limitation_chapter, kind="limitation",
        order=_next_order(),
        text="## 局限与建议\n\n- 简报为特定时点数据快照，数据更新后请重新生成\n"
             "- 离线导出以静态图件呈现空间分布，交互分析请在工作台完成"))

    # 6) 来源块：引用的产物台账（有界；用户可读标签）。
    for ref in sorted(records)[:MAX_BRIEF_SOURCES]:
        record = records[ref]
        blocks.append(BriefBlock(
            id=f"blk-src-{hashlib.sha1(ref.encode()).hexdigest()[:12]}",
            chapter_id=method_chapter, kind="source", order=_next_order(),
            binding=EvidenceBinding(
                artifact_ref=ref,
                source_label=_artifact_label(record),
                produced_at=_record_time(record),
                method=str(record.get("producer_tool") or record.get("producer_capability") or ""),
                artifact_sha256=_canonical_sha256(record))))

    return spec.model_copy(update={"blocks": blocks})


__all__ = ["compile_brief_draft"]
