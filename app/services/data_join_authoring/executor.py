"""G38 执行引擎 —— 规范化计划 → 有界配对 → 输出行（诊断与执行共用同一实现）。

核心纪律（WP07）：

- 阶段化：load → index/candidates → pairs → policy → assemble → publish；
  每阶段真实计数并对照预算（估算先行、运行中强制，绝不截前缀冒充完成）；
- 属性路径与 federation ``attribute_join_local`` 同语义（null 双侧排除、
  左序稳定、右侧多命中按右输入序展开），键先经 ``keys.py`` 类型化规范化
  —— 所有执行面同一空值/类型政策（J05）；
- 空间路径：右侧面 STRtree 候选 + 精确谓词（within=严格内部；边界包含型
  =covers），绝不做首命中冒充全部匹配；多命中策略显式（J10/J11）；
- 取消协作：批粒度 ``should_cancel`` 回调（runner 注入）。

本模块不做 IO/发布（见 ``outputs.py`` 与 ``service.py``）；输入是已在内存
中的源视图（``LoadedSource``），输出是确定性的行 + 几何 + lineage。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.services.data_join_authoring import contracts
from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.keys import row_key
from app.services.data_join_authoring.plan import JoinPlan

logger = logging.getLogger(__name__)

#: 批大小（取消检查粒度）。
_BATCH = 2000

#: 单行估计字节数（临时内存预算的保守估计基数）。
_EST_ROW_BYTES = 2048


@dataclass
class LoadedSource:
    """已装载的源视图（行序 = 冻结快照序；重放确定性锚点）。"""

    side: str                       # "left" | "right"
    label: str
    is_table: bool
    crs: Optional[str]
    content_fingerprint: str
    rows: List[Dict[str, Any]] = field(default_factory=list)
    geometries: Optional[List[Optional[Dict[str, Any]]]] = None  # 矢量源按行几何
    truncated_load: bool = False    # 诊断样本受帽（执行路径不得为 True）
    declared_fields: List[str] = field(default_factory=list)

    def has_geometry(self) -> bool:
        return not self.is_table

    def row_count(self) -> int:
        return len(self.rows)

    def identity_of(self, ordinal: int) -> str:
        """行身份 locator：优先 _wfid/feature id，否则快照内定位（披露非跨修订）。"""
        row = self.rows[ordinal]
        wid = row.get("_wfid")
        if isinstance(wid, str) and wid:
            return wid
        fid = row.get("id")
        if isinstance(fid, str) and fid:
            return fid
        if isinstance(fid, int) and not isinstance(fid, bool):
            return f"id:{fid}"
        return f"idx:{ordinal}@{self.content_fingerprint[:12]}"


@dataclass
class JoinStats:
    """阶段真实计数（预览与执行共用；对账锚点）。"""

    left_rows: int = 0
    right_rows: int = 0
    left_null_keys: int = 0
    right_null_keys: int = 0
    left_null_geometry: int = 0
    right_invalid_geometry: int = 0
    left_distinct_keys: int = 0
    right_distinct_keys: int = 0
    right_duplicate_keys: int = 0        # 值>1 的右键个数
    left_duplicate_keys: int = 0
    candidate_pairs: int = 0             # 索引命中对（策略前）
    matched_pairs: int = 0               # 进入输出的匹配对
    matched_left_rows: int = 0
    unmatched_left_rows: int = 0         # 有键但右无匹配
    unmatched_right_rows: int = 0        # 右侧行未被任何左键命中
    output_rows: int = 0
    basis: str = "full"                  # full | sampled
    working_crs: Optional[str] = None
    transform_note: Optional[str] = None
    stage_counts: Dict[str, int] = field(default_factory=dict)
    examples: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def add_example(self, bucket: str, item: Dict[str, Any], limit: int) -> None:
        items = self.examples.setdefault(bucket, [])
        if len(items) < limit:
            items.append(item)


def _agg_result(
    values: Sequence[Any], function: str, *, count_field: bool = False
) -> Tuple[Any, int]:
    """闭合聚合（null 不当 0）。返回 (值, valid_count)。

    count：无字段 = COUNT(*)（匹配对数）；显式字段 = COUNT(col)（非空计数）。
    """
    valid = [v for v in values if v is not None]
    n = len(valid)
    if function == "count":
        return (n if count_field else len(values)), n
    if n == 0:
        return None, 0
    nums: List[float] = []
    for v in valid:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise DataJoinError(
                "type_conflict",
                f"聚合字段值 {v!r} 不是数值（{function} 仅支持数值列）",
                function=function,
            )
        nums.append(float(v))
    if function == "sum":
        total = math.fsum(nums)
        return (int(total) if float(total).is_integer() else total), n
    if function == "mean":
        m = math.fsum(nums) / n
        return (int(m) if float(m).is_integer() else m), n
    if function == "min":
        return min(nums), n
    if function == "max":
        return max(nums), n
    raise DataJoinError("invalid_plan", f"未知聚合函数 {function!r}")  # pragma: no cover


class JoinExecutor:
    """纯内存执行体（无 IO；可重放：同快照 + 同计划 → 同输出）。"""

    def __init__(
        self,
        plan: JoinPlan,
        *,
        should_cancel: Optional[Callable[[], None]] = None,
        pair_cap: Optional[int] = None,
        output_cap: Optional[int] = None,
        sample_limit: int = contracts.DIAGNOSTIC_SAMPLE_LIMIT,
    ) -> None:
        self.plan = plan
        self.should_cancel = should_cancel or (lambda: None)
        self.pair_cap = pair_cap or plan.effective_budgets()["max_join_pairs"]
        self.output_cap = output_cap or plan.effective_budgets()["max_output_rows"]
        self.sample_limit = sample_limit
        self.stats = JoinStats()
        self._point_transformer: Any = None
        # 每 run 一次的缓存（_emit_row 逐行调用）：字段映射 + 计划 digest。
        # 左/右源在同一 run 内不变（快照冻结），按对象身份缓存。
        self._field_map_cache: Optional[
            Tuple[LoadedSource, LoadedSource, List[Tuple[str, str, str]]]
        ] = None
        self._digest_cache: Optional[str] = None

    # ───────────────────── 属性路径 ─────────────────────

    def _build_right_index(
        self, left: LoadedSource, right: LoadedSource
    ) -> Dict[Tuple[Tuple[str, Any], ...], List[int]]:
        """右键 → 右行序列表（插入序）。同时完成全量基数统计。

        many_to_one（含聚合）要求右键唯一 —— 除非计划声明聚合（右值先聚合，
        matched_count 披露）。one_to_one 另要求左键唯一。
        """
        specs = self.plan.key_specs()
        index: Dict[Tuple[Tuple[str, Any], ...], List[int]] = {}
        dup_keys = 0
        null_keys = 0
        for i, row in enumerate(right.rows):
            if i % _BATCH == 0:
                self.should_cancel()
            key = row_key(row, specs, "right")
            if key is None:
                null_keys += 1
                self.stats.add_example(
                    "right_null_key",
                    {"ordinal": i, "identity": right.identity_of(i)},
                    self.sample_limit,
                )
                continue
            bucket = index.setdefault(key, [])
            bucket.append(i)
            if len(bucket) == 2:
                dup_keys += 1
                self.stats.add_example(
                    "right_duplicate_key",
                    {"key": _key_repr(key), "count": len(bucket)},
                    self.sample_limit,
                )
        self.stats.right_null_keys = null_keys
        self.stats.right_distinct_keys = len(index)
        self.stats.right_duplicate_keys = dup_keys
        if dup_keys:
            total_dup_rows = sum(len(v) for v in index.values() if len(v) > 1)
            self.stats.stage_counts["right_rows_in_duplicate_keys"] = total_dup_rows

        aggregates_declared = bool(self.plan.aggregates)
        if dup_keys and not aggregates_declared and self.plan.output_cardinality in (
            "one_to_one", "many_to_one",
        ):
            raise DataJoinError(
                "cardinality_violation",
                f"右键重复 {dup_keys} 个（涉及 {total_dup_rows} 行），与声明 "
                f"{self.plan.output_cardinality} 不符；改声明 one_to_many 或"
                "声明聚合规则",
                right_duplicate_keys=dup_keys,
                right_rows_in_duplicate_keys=total_dup_rows,
                examples=self.stats.examples.get("right_duplicate_key", [])[:5],
            )

        # 左侧全量键统计（基数校验依据真实全扫，不用 NDV 估计）
        left_seen: Dict[Tuple[Tuple[str, Any], ...], int] = {}
        left_null = 0
        for i, row in enumerate(left.rows):
            if i % _BATCH == 0:
                self.should_cancel()
            key = row_key(row, specs, "left")
            if key is None:
                left_null += 1
                self.stats.add_example(
                    "left_null_key",
                    {"ordinal": i, "identity": left.identity_of(i)},
                    self.sample_limit,
                )
                continue
            left_seen[key] = left_seen.get(key, 0) + 1
        self.stats.left_null_keys = left_null
        self.stats.left_distinct_keys = len(left_seen)
        left_dups = sum(1 for v in left_seen.values() if v > 1)
        self.stats.left_duplicate_keys = left_dups
        if left_dups:
            self.stats.stage_counts["left_rows_in_duplicate_keys"] = sum(
                v for v in left_seen.values() if v > 1
            )
            self.stats.add_example(
                "left_duplicate_key",
                {"count": left_dups},
                self.sample_limit,
            )
        if self.plan.output_cardinality == "one_to_one":
            if left_dups:
                raise DataJoinError(
                    "cardinality_violation",
                    f"左键重复 {left_dups} 个，与 one_to_one 不符",
                    left_duplicate_keys=left_dups,
                )
            if dup_keys:
                raise DataJoinError(
                    "cardinality_violation",
                    f"右键重复 {dup_keys} 个，与 one_to_one 不符",
                    right_duplicate_keys=dup_keys,
                )

        # 匹配对估算（分配前；大桶乘积直达 pair_cap 即 typed 拒绝 —— J08）
        est_pairs = 0
        for key, left_count in left_seen.items():
            bucket = index.get(key)
            if bucket:
                est_pairs += left_count * len(bucket)
                if est_pairs > self.pair_cap:
                    raise DataJoinError(
                        "budget_exceeded",
                        f"匹配对估算 {est_pairs} 已超过 pair 预算 "
                        f"{self.pair_cap}（分配前拦截）",
                        estimated_pairs=est_pairs,
                        pair_cap=self.pair_cap,
                    )
        self.stats.candidate_pairs = est_pairs

        # 右未匹配统计
        matched_right = {k for k in left_seen if k in index}
        self.stats.unmatched_right_rows = sum(
            len(index[k]) for k in index if k not in matched_right
        )
        return index

    def run_attribute(
        self, left: LoadedSource, right: LoadedSource
    ) -> List[Dict[str, Any]]:
        """属性键关联 → 输出行序（左快照序 × 右输入序），确定性。"""
        specs = self.plan.key_specs()
        index = self._build_right_index(left, right)
        self.stats.left_rows = left.row_count()
        self.stats.right_rows = right.row_count()
        keep_left = self.plan.keep_side == "left"
        aggregates = self.plan.aggregates
        expand = self.plan.output_cardinality == "one_to_many"

        outputs: List[Dict[str, Any]] = []
        pair_total = 0
        matched_left = 0
        unmatched_left = 0
        for i, row in enumerate(left.rows):
            if i % _BATCH == 0:
                self.should_cancel()
            key = row_key(row, specs, "left")
            left_ref = left.identity_of(i)
            if key is None:
                if keep_left:
                    outputs.append(self._emit_row(
                        left, right, i, None, status="null_key",
                        left_ref=left_ref, right_ref=None,
                    ))
                    self._check_runtime_budget(pair_total, outputs)
                continue
            bucket = index.get(key)
            if not bucket:
                unmatched_left += 1
                self.stats.add_example(
                    "left_unmatched",
                    {"identity": left_ref, "key": _key_repr(key)},
                    self.sample_limit,
                )
                if keep_left:
                    outputs.append(self._emit_row(
                        left, right, i, None, status="unmatched",
                        left_ref=left_ref, right_ref=None,
                    ))
                    self._check_runtime_budget(pair_total, outputs)
                continue
            matched_left += 1
            if aggregates:
                right_rows = [right.rows[j] for j in bucket]
                outputs.append(self._emit_row(
                    left, right, i, None, status="matched",
                    left_ref=left_ref, right_ref=None,
                    agg_source=(bucket, right_rows),
                ))
                pair_total += len(bucket)
            elif expand:
                child = 0
                for j in bucket:
                    outputs.append(self._emit_row(
                        left, right, i, j, status="matched",
                        left_ref=left_ref, right_ref=right.identity_of(j),
                        child_ordinal=child,
                    ))
                    child += 1
                    pair_total += 1
            else:
                j = bucket[0]
                outputs.append(self._emit_row(
                    left, right, i, j, status="matched",
                    left_ref=left_ref, right_ref=right.identity_of(j),
                ))
                pair_total += 1
            self._check_runtime_budget(pair_total, outputs)
        self.stats.matched_pairs = pair_total
        self.stats.matched_left_rows = matched_left
        self.stats.unmatched_left_rows = unmatched_left
        self.stats.output_rows = len(outputs)
        return outputs

    def _check_runtime_budget(
        self, pair_total: int, outputs: List[Dict[str, Any]]
    ) -> None:
        """运行中预算闸：每个左行处理后强制（含 null_key/unmatched 透传行）。"""
        if pair_total > self.pair_cap or len(outputs) > self.output_cap:
            raise DataJoinError(
                "budget_exceeded",
                f"运行中超出预算（pairs={pair_total}/{self.pair_cap}，"
                f"output={len(outputs)}/{self.output_cap}）",
                pairs=pair_total, output_rows=len(outputs),
            )

    # ───────────────────── 空间路径（点×面） ─────────────────────

    def run_spatial(
        self, left: LoadedSource, right: LoadedSource
    ) -> List[Dict[str, Any]]:
        from shapely.prepared import prep
        from shapely.strtree import STRtree

        if left.is_table or right.is_table:
            raise DataJoinError(
                "source_geometry_missing",
                "空间点面关联要求两侧均为矢量源（左=点，右=面）",
            )
        self.stats.left_rows = left.row_count()
        self.stats.right_rows = right.row_count()

        # CRS 资格：两侧必须显式声明且非偏移帧；不同帧 → 点侧变换到面侧帧
        work_crs, transform_note = self._resolve_crs(left, right)
        self.stats.working_crs = work_crs
        self.stats.transform_note = transform_note

        sp = self.plan.spatial
        poly_objects: List[Any] = []
        valid_right: List[int] = []
        invalid = 0
        for j, geom in enumerate(right.geometries or []):
            if j % _BATCH == 0:
                self.should_cancel()
            ok, shape_or_none, why = _to_polygon(geom)
            if not ok or shape_or_none is None:
                invalid += 1  # 非法/空/非面几何统一计数披露
                self.stats.add_example(
                    "right_invalid_geometry",
                    {"ordinal": j, "identity": right.identity_of(j), "reason": why},
                    self.sample_limit,
                )
                continue
            poly_objects.append(shape_or_none)
            valid_right.append(j)
        self.stats.right_invalid_geometry = invalid
        if invalid and sp.invalid_geometry_policy == "block":
            raise DataJoinError(
                "invalid_geometry",
                f"右侧面图层存在 {invalid} 个非法/空/非面几何（policy=block）",
                invalid_geometry=invalid,
                examples=self.stats.examples.get("right_invalid_geometry", [])[:5],
            )
        if not poly_objects:
            raise DataJoinError(
                "empty_result",
                "右侧面图层没有可用几何（空结果契约：计数见收据）",
            )

        tree = STRtree(poly_objects)
        prepared = [prep(p) for p in poly_objects]
        transformer = self._point_transformer

        keep_left = self.plan.keep_side == "left"
        outputs: List[Dict[str, Any]] = []
        pair_total = 0
        matched_left = 0
        unmatched_left = 0
        null_geom = 0
        matched_right_ords: set = set()   # 右快照序（被任一点命中的面行）
        for i, geom in enumerate(left.geometries or []):
            if i % _BATCH == 0:
                self.should_cancel()
            left_ref = left.identity_of(i)
            pt, why = _to_point(geom, transformer)
            if pt is None:
                null_geom += 1
                self.stats.add_example(
                    "left_null_geometry",
                    {"ordinal": i, "identity": left_ref, "reason": why},
                    self.sample_limit,
                )
                if keep_left:
                    outputs.append(self._emit_row(
                        left, right, i, None, status="null_geometry",
                        left_ref=left_ref, right_ref=None,
                    ))
                    self._check_runtime_budget(pair_total, outputs)
                continue
            cand_idx = list(tree.query(pt))
            hits: List[int] = []  # 右行序（快照序）
            for ci in cand_idx:
                j = valid_right[int(ci)]
                pgeom = prepared[int(ci)]
                if sp.predicate == "within":
                    ok = pgeom.contains(pt)
                else:
                    ok = pgeom.covers(pt)
                if ok:
                    hits.append(j)
            hits.sort()  # 右快照序 —— 确定性（不随 STRtree 内部序）
            matched_right_ords.update(hits)
            pair_total += len(hits)
            self.stats.candidate_pairs += len(hits)
            if pair_total > self.pair_cap:
                raise DataJoinError(
                    "budget_exceeded",
                    f"空间候选对 {pair_total} 超过 pair 预算 {self.pair_cap}"
                    "（运行中拦截）",
                    pairs=pair_total, pair_cap=self.pair_cap,
                )
            if not hits:
                unmatched_left += 1
                self.stats.add_example(
                    "left_unmatched",
                    {"identity": left_ref},
                    self.sample_limit,
                )
                if keep_left:
                    outputs.append(self._emit_row(
                        left, right, i, None, status="unmatched",
                        left_ref=left_ref, right_ref=None,
                    ))
                    self._check_runtime_budget(pair_total, outputs)
                continue
            matched_left += 1
            outputs.extend(self._apply_multi_match(
                left, right, i, hits, left_ref,
            ))
            if len(outputs) > self.output_cap:
                raise DataJoinError(
                    "budget_exceeded",
                    f"输出行 {len(outputs)} 超过输出预算 {self.output_cap}",
                    output_rows=len(outputs), output_cap=self.output_cap,
                )
        self.stats.left_null_geometry = null_geom
        self.stats.matched_pairs = pair_total
        self.stats.matched_left_rows = matched_left
        self.stats.unmatched_left_rows = unmatched_left
        # 右未匹配 = 有效右几何中从未被任何点命中的行（诚实计数，非缺省 0）
        self.stats.unmatched_right_rows = len(valid_right) - len(matched_right_ords)
        self.stats.output_rows = len(outputs)
        return outputs

    def _apply_multi_match(
        self,
        left: LoadedSource,
        right: LoadedSource,
        i: int,
        hits: List[int],
        left_ref: str,
    ) -> List[Dict[str, Any]]:
        sp = self.plan.spatial
        assert sp is not None
        policy = sp.multi_match
        if policy == "aggregate":
            # 聚合策略对全部命中计数（单命中也聚合 —— 聚合列恒存在）
            rows = [right.rows[j] for j in hits]
            return [self._emit_row(
                left, right, i, None, status="matched",
                left_ref=left_ref, right_ref=None,
                agg_source=(hits, rows),
            )]
        if len(hits) == 1 or policy == "expand_all":
            if policy == "expand_all":
                out = []
                for child, j in enumerate(hits):
                    out.append(self._emit_row(
                        left, right, i, j, status="matched",
                        left_ref=left_ref, right_ref=right.identity_of(j),
                        child_ordinal=child,
                    ))
                return out
            j = hits[0]
            return [self._emit_row(
                left, right, i, j, status="matched",
                left_ref=left_ref, right_ref=right.identity_of(j),
            )]
        if policy == "require_unique":
            raise DataJoinError(
                "needs_decision",
                f"左要素 {left_ref} 命中 {len(hits)} 个面（重叠），"
                "require_unique 拒绝：改 expand_all / pick_by_field / aggregate",
                identity=left_ref, hits=len(hits),
                hit_identities=[right.identity_of(j) for j in hits[:5]],
            )
        if policy == "pick_by_field":
            fld = sp.pick_field or ""
            direction = sp.pick_direction or "min"

            def _rank(j: int) -> Tuple[int, Any]:
                v = right.rows[j].get(fld)
                if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise DataJoinError(
                        "needs_decision",
                        f"择一字段 {fld!r} 在候选 "
                        f"{right.identity_of(j)} 上缺失/非数值 —— 需显式决策",
                        identity=left_ref, field=fld,
                        offending_identity=right.identity_of(j),
                    )
                return (0, v) if direction == "min" else (0, -float(v))

            ranked = sorted(hits, key=_rank)
            best = ranked[0]
            best_val = right.rows[best].get(fld)
            ties = [j for j in hits if right.rows[j].get(fld) == best_val]
            if len(ties) > 1:
                raise DataJoinError(
                    "needs_decision",
                    f"择一并列：{len(ties)} 个候选在 {fld!r} 上同为 {best_val!r}"
                    "（拒绝偶然顺序）",
                    identity=left_ref, field=fld, value=best_val,
                    tie_identities=[right.identity_of(j) for j in ties[:5]],
                )
            return [self._emit_row(
                left, right, i, best, status="matched",
                left_ref=left_ref, right_ref=right.identity_of(best),
            )]


    # ───────────────────── 行装配 ─────────────────────

    def _cached_field_map(
        self, left: LoadedSource, right: LoadedSource
    ) -> List[Tuple[str, str, str]]:
        """字段映射按 (left, right) 解析一次。

        每输出行重算 = 每行重放映射校验；declared_fields 空且字段不在前
        500 行时还连带 _field_present 全表扫 → N 行输出 O(N×M)。
        """
        cache = self._field_map_cache
        if cache is not None and cache[0] is left and cache[1] is right:
            return cache[2]
        entries = _resolved_field_map(self.plan, left, right)
        self._field_map_cache = (left, right, entries)
        return entries

    def _cached_digest(self) -> str:
        """normalized_digest 每 run 一次（整计划 json.dumps + sha256）。"""
        if self._digest_cache is None:
            self._digest_cache = self.plan.normalized_digest()
        return self._digest_cache

    def _emit_row(
        self,
        left: LoadedSource,
        right: LoadedSource,
        left_ord: int,
        right_ord: Optional[int],
        *,
        status: str,
        left_ref: str,
        right_ref: Optional[str],
        child_ordinal: int = 0,
        agg_source: Optional[Tuple[List[int], List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        plan = self.plan
        mapping = self._cached_field_map(left, right)
        row: Dict[str, Any] = {"_join_status": status, "_join_left_ref": left_ref}
        for side, field_name, out_name in mapping:
            if side == "left":
                row[out_name] = left.rows[left_ord].get(field_name)
            else:
                if right_ord is not None:
                    row[out_name] = right.rows[right_ord].get(field_name)
                else:
                    row[out_name] = None
        if agg_source is not None:
            hits, right_rows = agg_source
            row["_join_right_ref"] = None
            row["match_count"] = len(hits)
            for agg in plan.aggregates:
                if (agg.function == "count"
                        and agg.output_name() == "match_count"
                        and not agg.right_field):
                    continue  # 无字段缺省 count 与系统 match_count 同义合一
                values = (
                    [r.get(agg.right_field) for r in right_rows]
                    if agg.right_field else [None] * len(right_rows)
                )
                value, valid = _agg_result(
                    values, agg.function, count_field=bool(agg.right_field))
                row[agg.output_name()] = value
                # 分母披露：所有聚合都带 valid_count（评审 F8：count/min/max 同样）
                row[f"{agg.output_name()}_valid_count"] = valid
        else:
            row["_join_right_ref"] = right_ref
            if plan.aggregates:
                # 行 schema 一致（评审 F7）：未匹配/空键行也带聚合列（0/空值）
                row["match_count"] = 0
                for agg in plan.aggregates:
                    if (agg.function == "count"
                            and agg.output_name() == "match_count"
                            and not agg.right_field):
                        continue
                    row[agg.output_name()] = None
                    row[f"{agg.output_name()}_valid_count"] = 0
        # 身份：1:1 / N:1（单行输出）保留左身份；1:N 展开 → 服务端派生身份
        left_row = left.rows[left_ord]
        wid = left_row.get("_wfid")
        if plan.output_cardinality == "one_to_many":
            from app.services.feature_edit.identity import wfid_for_created

            if wid:
                row["_join_left_wfid"] = wid
            row["_wfid"] = wfid_for_created(
                self._cached_digest(), left_ord, child_ordinal
            )
        else:
            row["_wfid"] = wid if isinstance(wid, str) and wid else None
        # 几何：富集图层保留左几何（右几何绝不混入左几何 CRS）
        if left.has_geometry() and left.geometries is not None:
            row["__geometry__"] = left.geometries[left_ord]  # 内部通道；outputs.py 拆出
        return row

    # ───────────────────── CRS 资格 ─────────────────────

    def _resolve_crs(
        self, left: LoadedSource, right: LoadedSource
    ) -> Tuple[Optional[str], Optional[str]]:
        """CRS 资格裁决；不同帧时在 ``self._point_transformer`` 上准备点变换器。"""
        lcrs, rcrs = (left.crs or "").strip() or None, (right.crs or "").strip() or None
        for name, c in (("left", lcrs), ("right", rcrs)):
            if not c:
                raise DataJoinError(
                    "crs_unresolvable",
                    f"{name} 源未声明 CRS —— 拒绝把未知坐标帧伪装成 WGS84；"
                    "请先为图层声明坐标系",
                    side=name,
                )
            low = c.lower()
            if "gcj" in low or "bd09" in low or "baidu" in low:
                raise DataJoinError(
                    "crs_unresolvable",
                    f"{name} 源坐标系为偏移帧（{c}），不可作空间关联基准",
                    side=name, crs=c,
                )
        self._point_transformer = None
        if lcrs == rcrs:
            return lcrs, None
        # 不同帧：点侧（左）变换到面侧（右）帧 —— 点变换代价低且避免面畸变。
        try:
            from pyproj import Transformer

            self._point_transformer = Transformer.from_crs(
                lcrs, rcrs, always_xy=True)
        except Exception as exc:  # noqa: BLE001 — 变换不可用即 typed 阻断
            raise DataJoinError(
                "unsupported_feature",
                f"CRS 变换 {lcrs} → {rcrs} 不可用：{exc}",
                left_crs=lcrs, right_crs=rcrs,
            ) from exc
        return rcrs, f"left points reprojected {lcrs} → {rcrs}"


def _to_point(
    geom: Optional[Dict[str, Any]],
    transformer: Any = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """GeoJSON 几何 → shapely Point（可选帧变换）。None=不可用（原因见返回）。"""
    if not geom:
        return None, "null_geometry"
    if geom.get("type") != "Point":
        return None, f"not_a_point:{geom.get('type')}"
    coords = geom.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None, "bad_coordinates"
    import shapely.geometry as sgeom

    try:
        x, y = float(coords[0]), float(coords[1])
        if transformer is not None:
            x, y = transformer.transform(x, y)
    except (TypeError, ValueError):
        return None, "bad_coordinates"
    return sgeom.Point(x, y), None


def _to_polygon(
    geom: Optional[Dict[str, Any]],
) -> Tuple[bool, Optional[Any], Optional[str]]:
    """GeoJSON 几何 → shapely Polygon/MultiPolygon。

    返回 (计入吗, shape or None, 原因)。Polygon/MultiPolygon 计入；
    其它几何类型计入且 shape=None（按 invalid 计数披露）。
    """
    if not geom:
        return True, None, "null_geometry"
    t = geom.get("type")
    try:
        import shapely.geometry as sgeom

        if t == "Polygon":
            shape = sgeom.shape(geom)
        elif t == "MultiPolygon":
            shape = sgeom.shape(geom)
        else:
            return True, None, f"not_a_polygon:{t}"
        if shape.is_empty:
            return True, None, "empty_geometry"
        if not shape.is_valid:
            return False, shape, "invalid_geometry"
        return True, shape, None
    except Exception as exc:  # noqa: BLE001 — 解析失败按非法披露
        return False, None, f"parse_error:{type(exc).__name__}"


def _key_repr(key: Tuple[Tuple[str, Any], ...]) -> str:
    parts = []
    for fam, val in key:
        parts.append(f"{fam}:{val!r}")
    return "(" + ", ".join(parts) + ")"


def _resolved_field_map(
    plan: JoinPlan, left: LoadedSource, right: LoadedSource
) -> List[Tuple[str, str, str]]:
    """field_map → (side, field, output_name) 序列 + 冲突/保留字段校验。

    空 field_map：左全字段 + 右全字段（重名 → typed 冲突，列出全部冲突项）。
    """
    left_fields = left.declared_fields or sorted({
        k for row in left.rows[:500] for k in row.keys()
        if k not in ("_wfid",)})
    right_fields = right.declared_fields or sorted({
        k for row in right.rows[:500] for k in row.keys()
        if k not in ("_wfid",)})
    reserved = {
        "_wfid", "_join_status", "_join_left_ref", "_join_right_ref",
        "_join_left_wfid", "match_count", "geometry",
    }
    entries: List[Tuple[str, str, str]] = []
    if plan.field_map:
        for e in plan.field_map:
            fields = left_fields if e.side == "left" else right_fields
            if e.field not in fields and not _field_present(e.side, e.field, left, right):
                raise DataJoinError(
                    "field_not_found",
                    f"{e.side} 源不存在字段 {e.field!r}",
                    side=e.side, field=e.field,
                )
            entries.append((e.side, e.field, e.output_name()))
    else:
        # 默认投影：左全字段 + 右全字段；身份列 _wfid 由系统自动输出
        # （左身份/派生身份），不进显式映射（重名 → typed 冲突，见下）。
        for f in left_fields:
            if f != "_wfid":
                entries.append(("left", f, f))
        for f in right_fields:
            if f != "_wfid":
                entries.append(("right", f, f))
    names: Dict[str, str] = {}
    conflicts: List[Dict[str, str]] = []
    for side, f, out in entries:
        if out in reserved:
            conflicts.append({"output": out, "side": side, "field": f,
                              "reason": "reserved"})
            continue
        if out in names and names[out] != f"{side}:{f}":
            conflicts.append({"output": out, "side": side, "field": f,
                              "reason": "duplicate"})
        names[out] = f"{side}:{f}"
    for agg in plan.aggregates:
        out = agg.output_name()
        # 仅「无字段、缺省别名」的 count 与系统 match_count 同义合一；
        # 显式字段 count(col)（非空计数）是独立列（评审 R1）
        if (agg.function == "count" and out == "match_count"
                and not agg.right_field):
            continue
        if out in names or out in reserved:
            conflicts.append({"output": out, "side": "aggregate",
                              "field": agg.function, "reason": "duplicate"})
        names[out] = "aggregate"
        # 生成分母列 <agg>_valid_count 也占用输出名空间（评审 R2）
        vcol = f"{out}_valid_count"
        if vcol in names or vcol in reserved:
            conflicts.append({"output": vcol, "side": "aggregate",
                              "field": agg.function, "reason": "duplicate"})
        names[vcol] = "aggregate"
    if conflicts:
        raise DataJoinError(
            "field_mapping_conflict",
            "输出字段冲突（保留字段不可覆盖；重名字段需显式别名）",
            conflicts=conflicts[:20],
        )
    if len(names) + len(reserved) > contracts.MAX_OUTPUT_FIELDS:
        raise DataJoinError(
            "budget_exceeded",
            f"输出字段 {len(names)} 超过上限 {contracts.MAX_OUTPUT_FIELDS}",
            fields=len(names),
        )
    return entries


def _field_present(side: str, field: str, left: LoadedSource, right: LoadedSource) -> bool:
    src = left if side == "left" else right
    for row in src.rows:
        if field in row:
            return True
    return False
