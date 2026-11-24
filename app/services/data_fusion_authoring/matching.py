"""实体匹配引擎 —— blocking、证据评分、消歧、冲突检测（纯函数核心）。

科学语义（reviewer 重点）：
- **候选生成用三个互补 blocking 通道**（ID 桶 / 空间格网 / 名称桶），
  绝不 O(N²) 全表比较；候选总量在预算内先估算后强制；
- **证据评分可解释**：每对记录逐证据分项值；缺失证据双缺失时跳过并
  重归一（记录 renormalized），单侧缺失按 0 计（不可佐证）；绝不把
  缺失折算成满分或把缺数据当零值；
- **歧义保护**：非互最优 / top-2 边际不足 / 单实体候选溢出 → 一律
  ambiguous/needs_review，任何单一证据不得越过歧义保护自动合并；
- **ID 硬证据**：双侧外部标识均有且不同 → 硬冲突（恒不自动合并）；
- **稳定确定性**：同输入同输出（快照序 + 显式 tie-break），结果可重放；
- 距离：地理 CRS 用 pyproj.Geod(WGS84) 大地距离；投影 CRS 用平面距离
  （米 —— 单位由 executor 经 crs_safety 校验）；栅格不在此域。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.data_fusion_authoring import contracts, textsim
from app.services.data_fusion_authoring.errors import DataFusionError
from app.services.data_fusion_authoring.plan import (
    FusionPlan,
    FusionSourceSpec,
)

_RADIUS_M = 6_378_137.0  # WGS84 长半轴（pyproj.Geod 一致口径）


# ───────────────────────── 空间度量上下文 ─────────────────────────

class SpatialContext:
    """CRS 感知的距离/重叠/分格（executor 按 crs_safety 分类构建）。

    ``geographic``：pyproj.Geod 大地距离（米）+ 等经纬度格网（赤道口径
    cell 度数，3×3 邻域覆盖 max_distance —— 东向收缩纬度只会更密）。
    ``projected``：平面距离（CRS 单位须为米，executor 已闸）+ 等米格网。
    """

    def __init__(self, crs_kind: str, max_distance_m: float) -> None:
        if crs_kind not in ("geographic", "projected"):
            raise DataFusionError(
                "crs_unresolvable", f"未知空间度量类型：{crs_kind}")
        self.crs_kind = crs_kind
        self.max_distance_m = float(max_distance_m)
        self._geod = None
        if crs_kind == "geographic":
            from pyproj import Geod
            self._geod = Geod(ellps="WGS84")
            self._cell = self.max_distance_m / 111_320.0
        else:
            self._cell = self.max_distance_m

    # 点距离（米）；非点几何返回 None（由 overlap_ratio 处理）
    def distance_m(self, geom_a: Any, geom_b: Any) -> Optional[float]:
        if geom_a is None or geom_b is None:
            return None
        gt_a = getattr(geom_a, "geom_type", "")
        gt_b = getattr(geom_b, "geom_type", "")
        if gt_a == "Point" and gt_b == "Point":
            if self.crs_kind == "geographic":
                _, _, d = self._geod.inv(
                    geom_a.x, geom_a.y, geom_b.x, geom_b.y)
                return float(d)
            return float(geom_a.distance(geom_b))
        if gt_a == gt_b and gt_a in ("Polygon", "MultiPolygon", "LineString",
                                     "MultiLineString"):
            return None  # 面/线用 overlap_ratio 证据
        return None  # 形态不可比 —— 证据缺失（executor 资格闸先行拦截）

    # 面/线重叠证据 ∈ [0,1]；点对返回 None
    def overlap_ratio(self, geom_a: Any, geom_b: Any) -> Optional[float]:
        if geom_a is None or geom_b is None:
            return None
        gt_a = getattr(geom_a, "geom_type", "")
        gt_b = getattr(geom_b, "geom_type", "")
        if gt_a != gt_b:
            return None
        if gt_a in ("Polygon", "MultiPolygon"):
            inter = geom_a.intersection(geom_b).area
            union = geom_a.area + geom_b.area - inter
            return (inter / union) if union > 0 else None
        if gt_a in ("LineString", "MultiLineString"):
            try:
                inter_len = geom_a.intersection(geom_b).length
            except Exception:  # noqa: BLE001 — 拓扑异常按零重叠计（不计分）
                return 0.0
            denom = min(geom_a.length, geom_b.length)
            return (inter_len / denom) if denom > 0 else None
        return None

    # 候选格键（3×3 邻域在调用方展开）
    def base_cell(self, geom: Any) -> Optional[Tuple[int, int]]:
        if geom is None:
            return None
        try:
            x, y = geom.x, geom.y
        except AttributeError:
            x, y = geom.centroid.x, geom.centroid.y
        return (int(x // self._cell), int(y // self._cell))

    def neighborhood(
        self, cell: Tuple[int, int]
    ) -> Iterable[Tuple[int, int]]:
        cx, cy = cell
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                yield (cx + dx, cy + dy)


# ───────────────────────── 实体记录 ─────────────────────────

@dataclass
class EntityRecord:
    """单侧单实体投影（装载后冻结；重放确定性锚点 = 快照序 index）。"""

    index: int
    row: Dict[str, Any]
    geom: Any                      # shapely 几何（可 None —— 表源/无几何）
    name_norm: str
    name_missing: bool
    ext_id: Optional[str]
    ext_id_present: bool
    addr_norm: str
    addr_present: bool
    category_canon: Optional[str]  # None = 未知（unknown，诚实缺失）
    category_known: bool
    temporal: Optional[float]


@dataclass
class EvidenceItem:
    kind: str
    value: Optional[float]         # None = skipped（双缺失重归一）
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind, "value": self.value}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class PairCandidate:
    """候选配对（含状态/证据/字段冲突/几何证据）。"""

    idx_a: int
    idx_b: int
    status: str                    # contracts.PAIR_STATUSES
    score: Optional[float]         # None = 无可评估证据
    evidence: List[EvidenceItem] = field(default_factory=list)
    renormalized: bool = False
    field_conflicts: Dict[str, str] = field(default_factory=dict)
    geometry_evidence: Dict[str, Any] = field(default_factory=dict)
    hard_block: Optional[str] = None
    candidate_overflow: bool = False
    #: 共享标识空间精确匹配（身份钉定 —— 互最优时优先于模糊分值排序，
    #: 距离仍在 max 内则可越过分数阈值自动合并）
    id_pinned: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idx_a": self.idx_a,
            "idx_b": self.idx_b,
            "status": self.status,
            "score": self.score,
            "evidence": [e.to_dict() for e in self.evidence],
            "renormalized": self.renormalized,
            "field_conflicts": dict(self.field_conflicts),
            "geometry_evidence": dict(self.geometry_evidence),
            "hard_block": self.hard_block,
            "candidate_overflow": self.candidate_overflow,
            "id_pinned": self.id_pinned,
        }


@dataclass
class MatchResult:
    """匹配结果（pairs 按 (idx_a, idx_b) 稳定序；unmatched 按快照序）。"""

    pairs: List[PairCandidate]
    unmatched_a: List[int]
    unmatched_b: List[int]
    stats: Dict[str, int]

    def to_dict(self, *, sample_limit: int = contracts.DIAGNOSTIC_SAMPLE_LIMIT
                ) -> Dict[str, Any]:
        return {
            "stats": dict(self.stats),
            "pairs": [p.to_dict() for p in self.pairs],
            "unmatched_a": list(self.unmatched_a),
            "unmatched_b": list(self.unmatched_b),
            "sample_limit": sample_limit,
        }


# ───────────────────────── 实体投影 ─────────────────────────

def _norm_id(v: Any) -> Tuple[Optional[str], bool]:
    """外部标识归一：字符串化 strip；空串/None = 缺失（≠零）。"""
    if v is None:
        return None, False
    s = str(v).strip()
    if not s:
        return None, False
    return s, True


def build_records(
    rows: Sequence[Dict[str, Any]],
    geoms: Sequence[Any],
    spec: FusionSourceSpec,
    plan: FusionPlan,
) -> List[EntityRecord]:
    """行 + 几何 + 源声明 → 实体投影（不做任何静默清洗）。"""
    normalizers = plan.strategy.name_normalizers
    records: List[EntityRecord] = []
    if len(rows) != len(geoms):
        raise DataFusionError(
            "internal_error",
            f"行数 {len(rows)} 与几何数 {len(geoms)} 不一致（装载缺陷）")
    for i, row in enumerate(rows):
        name_raw = row.get(spec.name_field)
        name_missing = name_raw is None or str(name_raw).strip() == ""
        name_norm = "" if name_missing else textsim.normalize_text(
            str(name_raw), normalizers)
        ext_id, id_ok = _norm_id(row.get(spec.external_id_field)) \
            if spec.external_id_field else (None, False)
        addr_missing = True
        addr_norm = ""
        if spec.address_field:
            addr_raw = row.get(spec.address_field)
            addr_missing = addr_raw is None or str(addr_raw).strip() == ""
            addr_norm = "" if addr_missing else textsim.normalize_text(
                str(addr_raw), ["trim", "casefold", "collapse_spaces"])
        cat_canon: Optional[str] = None
        cat_known = False
        if spec.category_field:
            raw = row.get(spec.category_field)
            if raw is not None and str(raw).strip() != "":
                raw_s = str(raw).strip()
                if spec.category_map and raw_s not in spec.category_map:
                    # 计划声明了词表而原始值不在词表内 → 规范含义未知
                    #（诚实 unknown，绝不透传原始值冒充可比类别）
                    cat_canon = None
                    cat_known = False
                else:
                    cat_canon = spec.category_map.get(raw_s, raw_s)
                    cat_known = True
        temporal = None
        if spec.temporal_field:
            temporal = textsim.parse_temporal(row.get(spec.temporal_field))
        records.append(EntityRecord(
            index=i,
            row=row,
            geom=geoms[i],
            name_norm=name_norm,
            name_missing=name_missing or not name_norm,
            ext_id=ext_id,
            ext_id_present=id_ok,
            addr_norm=addr_norm,
            addr_present=(not addr_missing) and bool(addr_norm),
            category_canon=cat_canon,
            category_known=cat_known,
            temporal=temporal,
        ))
    return records


# ───────────────────────── blocking ─────────────────────────

def _blocks_id(
    recs_a: Sequence[EntityRecord], recs_b: Sequence[EntityRecord],
    pairs: Dict[Tuple[int, int], None],
    max_pairs: int,
) -> None:
    """ID 桶：精确等值（两侧 id 均在场）。"""
    buckets: Dict[str, List[int]] = {}
    for r in recs_b:
        if r.ext_id_present:
            buckets.setdefault(r.ext_id, []).append(r.index)
    for ra in recs_a:
        if not ra.ext_id_present:
            continue
        for j in buckets.get(ra.ext_id, ()):
            pairs.setdefault((ra.index, j), None)
            if len(pairs) > max_pairs:
                raise DataFusionError(
                    "budget_exceeded",
                    f"候选对（ID 通道）超过预算 {max_pairs}",
                    cap=max_pairs)


def _blocks_spatial(
    recs_a: Sequence[EntityRecord], recs_b: Sequence[EntityRecord],
    ctx: SpatialContext, plan: FusionPlan,
    pairs: Dict[Tuple[int, int], None],
    max_pairs: int,
) -> None:
    """空间格网：点 3×3 邻域；面/线 STRtree 包络查询。"""
    if plan.entity_kind == "point_poi":
        grid: Dict[Tuple[int, int], List[int]] = {}
        for r in recs_b:
            cell = ctx.base_cell(r.geom)
            if cell is not None:
                grid.setdefault(cell, []).append(r.index)
        for ra in recs_a:
            cell = ctx.base_cell(ra.geom)
            if cell is None:
                continue
            for nc in ctx.neighborhood(cell):
                for j in grid.get(nc, ()):
                    pairs.setdefault((ra.index, j), None)
                    if len(pairs) > max_pairs:
                        raise DataFusionError(
                            "budget_exceeded",
                            f"候选对（空间通道）超过预算 {max_pairs}",
                            cap=max_pairs)
    else:
        from shapely.strtree import STRtree
        geoms_b = [r.geom for r in recs_b]
        valid_idx = [i for i, g in enumerate(geoms_b) if g is not None]
        if not valid_idx:
            return
        tree = STRtree([geoms_b[i] for i in valid_idx])
        pad = ctx.max_distance_m * 0.01
        for ra in recs_a:
            if ra.geom is None:
                continue
            env = ra.geom.buffer(pad).envelope
            for k in tree.query(env):
                j = valid_idx[int(k)]
                pairs.setdefault((ra.index, j), None)
                if len(pairs) > max_pairs:
                    raise DataFusionError(
                        "budget_exceeded",
                        f"候选对（空间通道）超过预算 {max_pairs}",
                        cap=max_pairs)


def _blocks_name(
    recs_a: Sequence[EntityRecord], recs_b: Sequence[EntityRecord],
    pairs: Dict[Tuple[int, int], None],
    max_pairs: int,
) -> None:
    """名称桶：归一化名称精确等值（跨距离召回同名实体）。"""
    buckets: Dict[str, List[int]] = {}
    for r in recs_b:
        if r.name_norm:
            buckets.setdefault(r.name_norm, []).append(r.index)
    for ra in recs_a:
        if not ra.name_norm:
            continue
        for j in buckets.get(ra.name_norm, ()):
            pairs.setdefault((ra.index, j), None)
            if len(pairs) > max_pairs:
                raise DataFusionError(
                    "budget_exceeded",
                    f"候选对（名称通道）超过预算 {max_pairs}",
                    cap=max_pairs)


# ───────────────────────── 评分 ─────────────────────────

def _sim_name(ra: EntityRecord, rb: EntityRecord) -> Tuple[
        Optional[float], Dict[str, Any]]:
    sim = textsim.name_similarity(ra.name_norm, rb.name_norm)
    detail: Dict[str, Any] = {}
    if sim["value"] is not None:
        detail = {"char": round(sim["char"], 6), "token": round(sim["token"], 6)}
    return sim["value"], detail


def _sim_category(
    ra: EntityRecord, rb: EntityRecord, plan: FusionPlan
) -> Tuple[Optional[float], Dict[str, Any]]:
    factor = plan.strategy.category_unknown_factor
    if ra.category_known and rb.category_known:
        if ra.category_canon == rb.category_canon:
            return 1.0, {"comparison": "equal",
                         "canonical": ra.category_canon}
        return 0.0, {"comparison": "different",
                     "a": ra.category_canon, "b": rb.category_canon}
    return factor, {"comparison": "unknown"}


def _sim_distance(
    ra: EntityRecord, rb: EntityRecord, ctx: SpatialContext, plan: FusionPlan
) -> Tuple[Optional[float], Dict[str, Any]]:
    if plan.entity_kind == "point_poi":
        d = ctx.distance_m(ra.geom, rb.geom)
        if d is None:
            return None, {}
        s = ctx.max_distance_m - d
        full = ctx.max_distance_m - plan.strategy.close_distance_m
        ratio = 1.0 if full <= 0 else max(0.0, min(1.0, s / full))
        return ratio, {"distance_m": round(d, 3)}
    ov = ctx.overlap_ratio(ra.geom, rb.geom)
    if ov is None:
        return None, {}
    st = plan.strategy
    if ov >= st.overlap_full:
        ratio = 1.0
    elif ov <= st.overlap_min:
        ratio = 0.0
    else:
        ratio = (ov - st.overlap_min) / (st.overlap_full - st.overlap_min)
    return ratio, {"overlap_ratio": round(ov, 6)}


def _sim_address(ra: EntityRecord, rb: EntityRecord) -> Tuple[
        Optional[float], Dict[str, Any]]:
    if not ra.addr_present and not rb.addr_present:
        return None, {}
    if not ra.addr_present or not rb.addr_present:
        return 0.0, {"reason": "one_side_missing"}
    from app.services.data_fusion_authoring.textsim import char_similarity, token_similarity
    c = char_similarity(ra.addr_norm, rb.addr_norm)
    t = token_similarity(ra.addr_norm, rb.addr_norm)
    return max(c, t), {"char": round(c, 6), "token": round(t, 6)}


def _sim_ext_id(
    ra: EntityRecord, rb: EntityRecord, plan: FusionPlan
) -> Tuple[Optional[float], Optional[str], Dict[str, Any]]:
    """外部标识证据（仅两侧声明同一 id_space 时可比）。

    返回 (sim, hard_block, detail)。不可比 id 空间 → 证据缺失（跳过重
    归一），绝不把不同体系的编码拿去比较。
    """
    ida, idb = plan.source_a.id_space, plan.source_b.id_space
    comparable = bool(ida and idb and ida == idb)
    if not comparable:
        return None, None, {"reason": "id_space_not_comparable"}
    if not ra.ext_id_present and not rb.ext_id_present:
        return None, None, {}
    if not ra.ext_id_present or not rb.ext_id_present:
        return None, None, {"reason": "one_side_missing"}
    if ra.ext_id == rb.ext_id:
        return 1.0, None, {"match": True, "id_space": ida}
    return 0.0, "id_mismatch", {"a": ra.ext_id, "b": rb.ext_id,
                                "id_space": ida}


def _score_pair(
    ra: EntityRecord, rb: EntityRecord, ctx: SpatialContext, plan: FusionPlan
) -> PairCandidate:
    """证据评分 → 候选对（状态在 assignment 阶段统一裁定）。"""
    st = plan.strategy
    weights = st.weights
    sim_id, hard_block, id_detail = _sim_ext_id(ra, rb, plan)
    id_pinned = (sim_id == 1.0)  # 共享标识空间精确匹配 —— 身份钉定
    sim_name, name_detail = _sim_name(ra, rb)
    sim_addr, addr_detail = _sim_address(ra, rb)
    sim_cat, cat_detail = _sim_category(ra, rb, plan)
    sim_dist, dist_detail = _sim_distance(ra, rb, ctx, plan)

    items = [
        EvidenceItem("external_id", sim_id, id_detail),
        EvidenceItem("name", sim_name, name_detail),
        EvidenceItem("address", sim_addr, addr_detail),
        EvidenceItem("category", sim_cat, cat_detail),
        EvidenceItem("distance", sim_dist, dist_detail),
    ]
    used_w = 0.0
    acc = 0.0
    renormalized = False
    for it in items:
        w = weights[it.kind]
        if it.value is None:
            renormalized = True
            continue
        acc += w * it.value
        used_w += w
    score: Optional[float]
    if used_w <= 0.0:
        score = None  # 全部证据缺失 —— 不可评估（诚实 unmatched）
    elif renormalized:
        score = acc / used_w
    else:
        score = acc
    if hard_block is not None and not st.id_mismatch_blocks_match:
        # 计划显式放弃 id 硬阻断 —— 仅当 external_id 权重为 0 才可达
        hard_block = None
    return PairCandidate(
        idx_a=ra.index, idx_b=rb.index, status="needs_review",
        score=None if score is None else round(score, 6),
        evidence=items, renormalized=renormalized, hard_block=hard_block,
        id_pinned=id_pinned,
    )


# ───────────────────────── 字段冲突 ─────────────────────────

def _detect_field_conflicts(
    ra: EntityRecord, rb: EntityRecord, plan: FusionPlan
) -> Dict[str, str]:
    """按输出字段联合检测同值/冲突/缺失（绝不折算缺失）。"""
    out: Dict[str, str] = {}
    for field_name, m in _union_output_fields(plan).items():
        field_a = m.get("a")
        field_b = m.get("b")
        if field_a is None:
            # 仅 B 侧映射：按 B 值有无计 missing_b / missing_both
            vb = rb.row.get(field_b)
            b_missing = vb is None or vb == ""
            out[field_name] = "missing_both" if b_missing else "missing_b"
            continue
        if field_b is None:
            # 仅 A 侧映射：按 A 值有无计 missing_a / missing_both
            va = ra.row.get(field_a)
            a_missing = va is None or va == ""
            out[field_name] = "missing_both" if a_missing else "missing_a"
            continue
        va = ra.row.get(field_a)
        vb = rb.row.get(field_b)
        eq = textsim.field_values_equal(va, vb)
        if eq is None:
            a_missing = va is None or va == ""
            b_missing = vb is None or vb == ""
            if a_missing and b_missing:
                out[field_name] = "missing_both"
            elif a_missing:
                out[field_name] = "missing_a"
            else:
                out[field_name] = "missing_b"
        elif eq:
            out[field_name] = "identical"
        else:
            out[field_name] = "conflict"
    return out


def _union_output_fields(plan: FusionPlan) -> Dict[str, Dict[str, Optional[str]]]:
    """输出字段 → {a: 源字段|None, b: 源字段|None, rule}（跨源联合）。"""
    fields: Dict[str, Dict[str, Optional[str]]] = {}
    for side, src in (("a", plan.source_a), ("b", plan.source_b)):
        for m in src.field_mappings:
            entry = fields.setdefault(m.output_field, {"a": None, "b": None})
            entry[side] = m.source_field
            entry["rule"] = m.rule
    return fields


def _geometry_evidence(
    ra: EntityRecord, rb: EntityRecord, ctx: SpatialContext, plan: FusionPlan
) -> Dict[str, Any]:
    if plan.entity_kind == "point_poi":
        d = ctx.distance_m(ra.geom, rb.geom)
        if d is None:
            return {}
        return {"kind": "point_distance_m", "distance_m": round(d, 3),
                "within_close": d <= plan.strategy.close_distance_m}
    ov = ctx.overlap_ratio(ra.geom, rb.geom)
    if ov is None:
        return {}
    return {"kind": "overlap_ratio", "overlap_ratio": round(ov, 6),
            "exact_overlap": ov >= 0.999999}


# ───────────────────────── 主入口 ─────────────────────────

def match_sources(
    rows_a: Sequence[Dict[str, Any]],
    geoms_a: Sequence[Any],
    rows_b: Sequence[Dict[str, Any]],
    geoms_b: Sequence[Any],
    plan: FusionPlan,
    ctx: Optional[SpatialContext] = None,
) -> MatchResult:
    """两源实体匹配 → MatchResult（纯函数；幂等；稳定序）。"""
    st = plan.strategy
    budgets = plan.effective_budgets()
    max_pairs = budgets["max_candidate_pairs"]
    cap_per_entity = contracts.DEFAULT_MAX_CANDIDATES_PER_ENTITY

    recs_a = build_records(rows_a, geoms_a, plan.source_a, plan)
    recs_b = build_records(rows_b, geoms_b, plan.source_b, plan)

    if ctx is None:
        ctx = SpatialContext("geographic", st.max_match_distance_m)
    metric = ctx

    # blocking（三通道并集）
    pairs: Dict[Tuple[int, int], None] = {}
    _blocks_id(recs_a, recs_b, pairs, max_pairs)
    _blocks_spatial(recs_a, recs_b, metric, plan, pairs, max_pairs)
    _blocks_name(recs_a, recs_b, pairs, max_pairs)

    # 单实体候选帽（溢出 → 该实体强制 needs_review；保留稳定前 K）
    by_a: Dict[int, List[Tuple[int, int]]] = {}
    by_b: Dict[int, List[Tuple[int, int]]] = {}
    for (i, j) in sorted(pairs.keys()):
        by_a.setdefault(i, []).append((i, j))
        by_b.setdefault(j, []).append((i, j))
    overflow_a = {i for i, lst in by_a.items() if len(lst) > cap_per_entity}
    overflow_b = {j for j, lst in by_b.items() if len(lst) > cap_per_entity}

    scored: List[PairCandidate] = []
    for (i, j) in sorted(pairs.keys()):
        pc = _score_pair(recs_a[i], recs_b[j], metric, plan)
        pc.candidate_overflow = (i in overflow_a) or (j in overflow_b)
        if pc.score is not None:
            pc.field_conflicts = _detect_field_conflicts(
                recs_a[i], recs_b[j], plan)
            pc.geometry_evidence = _geometry_evidence(
                recs_a[i], recs_b[j], metric, plan)
        scored.append(pc)

    _assign_statuses(scored, recs_a, recs_b, plan)

    pairs_by_status: Dict[str, int] = {}
    kept: List[PairCandidate] = []
    for pc in scored:
        if pc.status == "unmatched":
            continue
        pairs_by_status[pc.status] = pairs_by_status.get(pc.status, 0) + 1
        kept.append(pc)

    # unmatched：无任何 ≥review_min 候选的实体（快照序；确定性）
    engaged_a = {pc.idx_a for pc in kept}
    engaged_b = {pc.idx_b for pc in kept}
    unmatched_a = [r.index for r in recs_a if r.index not in engaged_a]
    unmatched_b = [r.index for r in recs_b if r.index not in engaged_b]

    stats = {
        "rows_a": len(recs_a),
        "rows_b": len(recs_b),
        "candidates": len(scored),
        "candidate_overflow_entities_a": len(overflow_a),
        "candidate_overflow_entities_b": len(overflow_b),
        "pairs_matched": pairs_by_status.get("matched", 0),
        "pairs_needs_review": pairs_by_status.get("needs_review", 0),
        "pairs_ambiguous": pairs_by_status.get("ambiguous", 0),
        "unmatched_a": len(unmatched_a),
        "unmatched_b": len(unmatched_b),
        "hard_blocked": sum(1 for p in scored if p.hard_block),
    }
    kept.sort(key=lambda p: (p.idx_a, p.idx_b))
    return MatchResult(
        pairs=kept, unmatched_a=unmatched_a, unmatched_b=unmatched_b,
        stats=stats)


def _assign_statuses(
    scored: List[PairCandidate],
    recs_a: Sequence[EntityRecord],
    recs_b: Sequence[EntityRecord],
    plan: FusionPlan,
) -> None:
    """互最优 + 边际 + 阈值 + 硬阻断 + 溢出 → 状态裁定（确定性）。"""
    st = plan.strategy
    review_min = st.review_min
    auto_min = st.auto_match_min
    margin = st.ambiguous_margin

    evaluable = [p for p in scored if p.score is not None]
    best_a: Dict[int, List[PairCandidate]] = {}
    best_b: Dict[int, List[PairCandidate]] = {}
    for p in evaluable:
        if p.score >= review_min:
            best_a.setdefault(p.idx_a, []).append(p)
            best_b.setdefault(p.idx_b, []).append(p)

    def _sorted_desc(lst: List[PairCandidate]) -> List[PairCandidate]:
        # id 钉定优先于模糊分值：共享标识空间的精确匹配是最强身份证据；
        # 同分内以快照序 tie-break（确定性，不随候选顺序漂移）。
        return sorted(lst, key=lambda p: (
            -int(p.id_pinned), -p.score, p.idx_a, p.idx_b))

    mutual: Dict[Tuple[int, int], PairCandidate] = {}
    for i, lst in best_a.items():
        srt = _sorted_desc(lst)
        top = srt[0]
        if top.hard_block is not None:
            continue  # 硬阻断 → 不可能互最优自动合并
        blst = best_b.get(top.idx_b, [])
        bsrt = _sorted_desc(blst)
        if bsrt and bsrt[0].idx_a == i and bsrt[0].hard_block is None:
            mutual[(i, top.idx_b)] = top

    for p in scored:
        if p.score is None:
            p.status = "unmatched"
            continue
        if p.score < review_min and p.hard_block is None and not p.id_pinned:
            p.status = "unmatched"
            continue
        # ≥ review_min / 硬阻断 / id 钉定 → 至少进审阅
        if (p.idx_a, p.idx_b) in mutual:
            srt_a = _sorted_desc(best_a[p.idx_a])
            srt_b = _sorted_desc(best_b[p.idx_b])
            margin_a = (srt_a[0].score - srt_a[1].score) if len(srt_a) > 1 else 1.0
            margin_b = (srt_b[0].score - srt_b[1].score) if len(srt_b) > 1 else 1.0
            within_distance = _within_max_distance(p, plan)
            margin_ok = margin_a >= margin and margin_b >= margin
            if (not p.hard_block and not p.candidate_overflow
                    and within_distance and margin_ok
                    and (p.id_pinned or p.score >= auto_min)):
                p.status = "matched"
            elif p.hard_block is not None:
                p.status = "needs_review"
            elif p.id_pinned and not within_distance:
                # 同 id 但距离超界：数据可疑（同一标识跨城）→ 审阅裁决
                p.status = "needs_review"
            elif not margin_ok or p.candidate_overflow:
                # 边际不足 / 候选溢出 —— 歧义保护（绝不在歧义下自动合并）
                p.status = "ambiguous"
            else:
                # 互最优、边际足够、但分值在审阅带 —— 需人审（非歧义）
                p.status = "needs_review"
        else:
            # 非互最优：若为单侧最优 → ambiguous（被争抢）；否则 needs_review
            top_a = _sorted_desc(best_a[p.idx_a])[0] if best_a.get(p.idx_a) else None
            top_b = _sorted_desc(best_b[p.idx_b])[0] if best_b.get(p.idx_b) else None
            is_contested = (
                (top_a is not None and top_a.idx_b == p.idx_b)
                or (top_b is not None and top_b.idx_a == p.idx_a))
            p.status = ("ambiguous"
                        if is_contested and p.hard_block is None
                        else "needs_review")
        if p.hard_block is not None and p.status == "matched":
            p.status = "needs_review"


def _within_max_distance(p: PairCandidate, plan: FusionPlan) -> bool:
    """点实体：id 钉定对仍需距离证据在 max 内（同 id 跨城 = 数据可疑）。"""
    if plan.entity_kind != "point_poi":
        return True
    for e in p.evidence:
        if e.kind == "distance" and e.value is not None:
            d = e.detail.get("distance_m")
            return d is not None and d <= plan.strategy.max_match_distance_m
    # 无距离证据（缺几何）：不因缺失阻断 id 钉定（几何证据缺失已被
    # 记录；资格闸在 executor 层对需几何实体另行把关）
    return True
