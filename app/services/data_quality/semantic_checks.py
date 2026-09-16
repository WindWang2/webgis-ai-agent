"""语义和谐化检测器（DQH V1）：timezone / unit / role / admin 四族质量事实。

定位（与既有系统的边界，见 .agent-work/data-quality-harmonization-v1/GAP_ANALYSIS.md）：

- **聚合层的检测器，不是第二套规则引擎**：输入是既有剖析证据
  （``FieldProfile``）与既有语义画像（``SemanticDatasetProfile``），输出
  是既有词表（``app.lib.data.quality.QualityIssue``）的 issue —— 不发明
  新 issue 类型、不读全量数据、不执行任何修复；
- **受控词表扩展的生产者**：``timezone_missing`` / ``unit_ambiguous`` /
  ``field_role_ambiguous`` / ``admin_mismatch`` 四码（以及
  ``inconsistent_unit`` 的首个生产者）；
- **确定性 + 有界**：纯函数，同输入同 issues；样本受 ``MAX_VALUE_SAMPLES``
  与 ``FieldProfile`` 自身的样本帽约束；
- **诚实缺省**：证据不足 → 不发 issue（unknown ≠ 不满足）；行政区参考表
  覆盖范围外的值域 → 不把「表没收录」当「数据错」；
- **guardrails 边界**：只读复用 ``admin_division_verifier`` 的码表与校验，
  不复制、不改写其层级（父子关系）校验语义。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.lib.data.profile import FieldProfile
from app.lib.data.quality import QualityIssue, QualityIssueCode

#: 每字段参与的样本上限（与 lib.gis.semantic_profile.MAX_VALUE_SAMPLES 同量级）。
_MAX_VALUE_SAMPLES = 200
#: 证据里携带的未解析示例上限（有界披露）。
_MAX_EXAMPLES = 5

CHECK_TIMEZONE = "timezone_missing"
CHECK_UNIT = "unit_ambiguity"
CHECK_ROLE = "field_role_ambiguity"
CHECK_ADMIN = "admin_mismatch"

# ── 名称线索（与 semantic_profile._NAME_RULES 同源语义的投影；仅用于
#    决定「该字段是否属于检查口径」，不承担角色绑定）────────────────────
_ADMIN_NAME_RE = re.compile(
    r"(省|市|区|县|旗|镇|乡|街道|村|adcode|admin|district|province|city|"
    r"county|town|行政区|区划)",
    re.I,
)
_CODE_RE = re.compile(r"^\d{6}$")
_CJK_NAME_RE = re.compile(r"^[\u4e00-\u9fa5]{1,12}$")

_QUANTITY_HINT_RE = re.compile(
    r"(面积|人口|产量|产值|金额|价格|总价|单价|收入|支出|总量|长度|距离|"
    r"里程|蓄量|重量|gdp|area|population|amount|price|revenue|length|"
    r"distance|weight)",
    re.I,
)
_EXPLICIT_UNIT_RE = re.compile(
    r"(km2|km²|平方公里|平方千米|公顷|hectare|m2|平方米|亩|万元|亿元|"
    r"百万元|元|美元|吨|千克|公斤|kg|t/|km|米|cm|mm|升|毫升|度|千瓦时|"
    r"kwh|人|户|件|percent|%|pct)",
    re.I,
)
_RATIO_HINT_RE = re.compile(r"(率|比例|占比|比重|percent|pct|ratio|share|fraction)", re.I)
_TIME_COMPONENT_RE = re.compile(r"[T ]\d{1,2}:\d{2}")
_TZ_OFFSET_RE = re.compile(r"(?:Z|z|[+-]\d{2}:?\d{2})$")


def _issue(
    code: QualityIssueCode,
    *,
    message: str,
    field: str = "",
    severity: str = "info",
    repairable: bool = True,
    evidence: Optional[Dict[str, Any]] = None,
) -> QualityIssue:
    return QualityIssue(
        code=code,
        severity=severity,
        field=field,
        message=str(message)[:300],
        repairable=repairable,
        evidence=dict(list((evidence or {}).items())[:8]),
    )


# ── 1) 时区欠定 ──────────────────────────────────────────────────────


def detect_timezone_missing(fp: FieldProfile) -> List[QualityIssue]:
    """带时刻的时间戳缺少时区证据 → ``timezone_missing``。

    口径：纯日期（无时刻）没有时区语义，不判；epoch 数值按约定即 UTC，
    不判；全部样本带 offset → 不判；存在 naive 时刻样本（含与 aware 混杂）
    → 判。解析失败的样本归 INVALID_DATES 口径，这里不管。
    """
    if not fp.temporal_hint or str(fp.dtype) not in ("string", "mixed", "unknown"):
        return []
    naive = aware = 0
    example = ""
    for v in fp.samples[:_MAX_VALUE_SAMPLES]:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if not _TIME_COMPONENT_RE.search(s):
            continue  # 纯日期或非时刻形态
        if _TZ_OFFSET_RE.search(s):
            aware += 1
        else:
            naive += 1
            example = example or s[:40]
    if naive == 0:
        return []
    return [
        _issue(
            QualityIssueCode.TIMEZONE_MISSING,
            field=fp.name,
            severity="warning",
            message=(
                f"时间字段 {fp.name} 的时刻样本无时区证据（naive {naive} / aware {aware}，"
                f"示例 {example}）：按 UTC 静默解释会整体偏移，先声明时区或确认 UTC"
            ),
            evidence={
                "naive_count": naive,
                "aware_count": aware,
                "sampled": min(len(fp.samples), _MAX_VALUE_SAMPLES),
            },
        )
    ]


# ── 2) 单位欠定 / 名实矛盾 ───────────────────────────────────────────


def detect_unit_ambiguity(fp: FieldProfile) -> List[QualityIssue]:
    """数值字段的单位证据检查（只判名称线索 × 值域证据，不折算）。

    - 名称/``unit_hint`` 带显式单位标记 → 证据充分，不判；
    - 比例类名称（率/ratio…）值域超出 [0,1] → 名实矛盾 ``inconsistent_unit``
      （疑似百分制）；
    - 数量类名称（面积/人口/金额…）无单位标记 → 单位欠定 ``unit_ambiguous``
      （m²/km²/公顷、元/万元 等常见单位量级差 ≥100×，欠定必须披露）；
    - 无数量语义 → 不判。
    """
    if str(fp.dtype) not in ("number", "integer", "int", "float", "double"):
        return []
    if fp.unit_hint:
        return []
    name = fp.name or ""
    nums = [
        float(v)
        for v in fp.samples[:_MAX_VALUE_SAMPLES]
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    nums = [v for v in nums if v == v]  # 去 NaN
    if not nums:
        return []

    if _RATIO_HINT_RE.search(name):
        mx = max(nums)
        if mx > 1.0:
            return [
                _issue(
                    QualityIssueCode.INCONSISTENT_UNIT,
                    field=name,
                    severity="warning",
                    message=(
                        f"字段 {name} 名称提示比例（fraction）语义，但值域最大 {mx:g} "
                        "疑似百分制：先声明刻度（0-1 或 0-100）再做比率/加权运算"
                    ),
                    evidence={"max": mx, "min": min(nums), "sampled": len(nums)},
                )
            ]
        return []

    if _EXPLICIT_UNIT_RE.search(name):
        return []
    if not _QUANTITY_HINT_RE.search(name):
        return []
    return [
        _issue(
            QualityIssueCode.UNIT_AMBIGUOUS,
            field=name,
            severity="info",
            message=(
                f"字段 {name} 有数量语义但无单位标记：m²/km²/公顷、元/万元等常见"
                "单位量级差异大且值域无法消歧；先声明单位再做跨数据集比较或折算"
            ),
            evidence={
                "min": min(nums),
                "max": max(nums),
                "sampled": len(nums),
            },
        )
    ]


# ── 3) 字段角色歧义 ──────────────────────────────────────────────────

#: 度量族角色（绑定错误的代价：统计/制图语义直接失真）。
_MEASURE_ROLES = frozenset({
    "count_measure", "ratio_measure", "continuous_measure",
    "population_measure", "area_measure", "distance_measure",
    "weight_measure", "normalization_denominator",
})


def detect_field_role_ambiguity(
    fields: Mapping[str, FieldProfile],
    *,
    semantic_profile: Optional[Any] = None,
) -> List[QualityIssue]:
    """度量族角色的绑定歧义披露 → ``field_role_ambiguous``。

    三种歧义（每字段至多一条）：
    a) 绑定置信度为 ``metadata_derived``（仅名称/dtype 推断，值样本未印证）
       —— 低置信绑定 measure/rate/count 的直接来源，必须披露；
    b) 同字段 ≥2 个度量族角色竞争；
    c) 样本反证已绑定角色（count 出现负数/非整数；ratio 出现 [0,1] 之外）。
    非度量族角色（label/category/admin…）名称级绑定不在本检查口径内。
    无语义画像 → 无绑定事实 → 不虚构歧义。
    """
    if semantic_profile is None:
        return []
    assignments = getattr(semantic_profile, "field_roles", None) or []
    out: List[QualityIssue] = []
    for asg in assignments:
        name = str(getattr(asg, "field", "") or "")
        roles = [str(r) for r in (getattr(asg, "roles", None) or [])]
        measure_roles = [r for r in roles if r in _MEASURE_ROLES]
        if not measure_roles:
            continue
        confidence = str(getattr(getattr(asg, "confidence", None), "value",
                                 getattr(asg, "confidence", "")) or "")
        reasons: List[str] = []
        if confidence == "metadata_derived":
            reasons.append("name_only_binding")
        if len(measure_roles) >= 2:
            reasons.append("competing_roles")
        fp = fields.get(name)
        if fp is not None:
            nums = [
                float(v)
                for v in fp.samples[:_MAX_VALUE_SAMPLES]
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
            nums = [v for v in nums if v == v]
            if nums:
                if "count_measure" in measure_roles and any(
                    v < 0 or v != int(v) for v in nums
                ):
                    reasons.append("samples_contradict_count")
                if "ratio_measure" in measure_roles and any(
                    v < 0.0 or v > 1.0 for v in nums
                ):
                    reasons.append("samples_contradict_ratio")
        if not reasons:
            continue
        out.append(
            _issue(
                QualityIssueCode.FIELD_ROLE_AMBIGUOUS,
                field=name,
                severity="info",
                repairable=False,
                message=(
                    f"字段 {name} 的度量角色绑定存在歧义（roles={measure_roles[:4]}, "
                    f"confidence={confidence or 'unknown'}, reasons={reasons}）："
                    "绑定前需用户声明真实角色，不静默当作 measure/rate/count"
                ),
                evidence={
                    "roles": roles[:6],
                    "confidence": confidence or "unknown",
                    "reasons": reasons,
                    "sampled": min(len(fp.samples), _MAX_VALUE_SAMPLES) if fp is not None else 0,
                },
            )
        )
    return out


# ── 4) 行政区值域投影 ────────────────────────────────────────────────


def _bounded_levenshtein(a: str, b: str, cap: int = 2) -> int:
    """有界编辑距离（cap 截断；与 guardrails 同判据，独立实现避免私有依赖）。"""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _nearest_admin_names(value: str, names: Sequence[str]) -> List[str]:
    # 同距离下更长（更具体）的候选优先：「浙扛省」应建议「浙江」而非别名「浙」。
    scored = sorted(
        ((_bounded_levenshtein(value, n), -len(n), n) for n in names),
        key=lambda t: t[:2],
    )
    return [t[2] for t in scored if t[0] <= 2][:_MAX_EXAMPLES]


def detect_admin_mismatch(field_name: str, samples: Sequence[Any]) -> List[QualityIssue]:
    """行政区字段值能否对上已知码/名 → ``admin_mismatch``（值域投影）。

    口径：
    - 6 位码型样本 → ``verify_code`` 结构校验（guardrails 单一事实源，只读）；
    - 名称型样本 → ``resolve_name`` 解析；
    - **覆盖诚实**：仅当字段被证明属于参考表覆盖层级（≥1 名称解析成功）
      时才对未解析名称判 mismatch；零解析 + 无码型 → 检查不适用（表没收录
      ≠ 数据错），不发 issue；
    - 建议只是建议（最近码/最近名），绝不直接改写。
    """
    if not _ADMIN_NAME_RE.search(field_name or ""):
        return []
    from app.services.spatial_guardrails.admin_division_verifier import (
        resolve_name,
        verify_code,
    )
    from app.services.spatial_guardrails.data.admin_divisions import (
        PROVINCE_CODES_BY_SHORT_NAME,
    )
    from app.services.spatial_guardrails.data import get_prefecture_short_names

    str_values = [
        str(v).strip()
        for v in samples[:_MAX_VALUE_SAMPLES]
        if isinstance(v, str) and str(v).strip()
    ]
    if not str_values:
        return []

    reference_names = list(PROVINCE_CODES_BY_SHORT_NAME.keys()) + list(
        get_prefecture_short_names().keys()
    )

    invalid_codes: List[Tuple[str, Optional[str]]] = []
    unresolved_names: List[str] = []
    resolved_names = 0
    for s in str_values:
        if _CODE_RE.match(s):
            report = verify_code(s)
            if not report.ok:
                invalid_codes.append((s, report.suggestion))
        else:
            if _CJK_NAME_RE.match(s) and resolve_name(s):
                resolved_names += 1
            elif _CJK_NAME_RE.match(s):
                unresolved_names.append(s)

    if invalid_codes:
        suggestions = [sug or "" for _, sug in invalid_codes[:_MAX_EXAMPLES]]
        return [
            _issue(
                QualityIssueCode.ADMIN_MISMATCH,
                field=field_name,
                severity="warning",
                message=(
                    f"字段 {field_name} 有 {len(invalid_codes)} 个行政区码无法对上"
                    f"已知码表（GB/T 2260 快照）：确认真实码值后再聚合"
                ),
                evidence={
                    "invalid_codes": [c for c, _ in invalid_codes][:_MAX_EXAMPLES],
                    "invalid_count": len(invalid_codes),
                    "suggestions": suggestions,
                    "sampled": len(str_values),
                },
            )
        ]

    if resolved_names == 0 or not unresolved_names:
        # 全部解析成功（干净）；或层级超出参考表覆盖（零解析）→ 不判。
        return []

    suggestions: List[str] = []
    for name in unresolved_names[:_MAX_EXAMPLES]:
        nearest = _nearest_admin_names(name, reference_names)
        if nearest:
            suggestions.append(f"{name} → 疑似 {nearest[0]}")
        else:
            suggestions.append(name)
    return [
        _issue(
            QualityIssueCode.ADMIN_MISMATCH,
            field=field_name,
            severity="warning",
            message=(
                f"字段 {field_name} 有 {len(unresolved_names)}/{len(str_values)} 个行政区"
                "值无法对上已知省/市级码名（变体/旧名/错别字）：确认映射后再聚合，"
                "绝不静默丢弃或强行归并"
            ),
            evidence={
                "unresolved": len(unresolved_names),
                "examples": unresolved_names[:_MAX_EXAMPLES],
                "suggestions": suggestions,
                "resolved": resolved_names,
                "sampled": len(str_values),
            },
        )
    ]


# ── 聚合入口 ─────────────────────────────────────────────────────────


def evaluate_semantic_checks(
    fields: Mapping[str, FieldProfile],
    *,
    semantic_profile: Optional[Any] = None,
    check_timezone: bool = True,
    check_units: bool = True,
    check_roles: bool = True,
    check_admin: bool = True,
) -> Tuple[List[QualityIssue], List[str], List[str]]:
    """剖析证据 + 语义画像 → (issues, checks_run, checks_not_run)。

    纯增量聚合：任何开关关闭 → 该检查列入 ``checks_not_run``（诚实披露），
    绝不静默跳过。
    """
    issues: List[QualityIssue] = []
    run: List[str] = []
    not_run: List[str] = []

    if check_timezone:
        for fp in fields.values():
            issues.extend(detect_timezone_missing(fp))
        run.append(CHECK_TIMEZONE)
    else:
        not_run.append(CHECK_TIMEZONE)

    if check_units:
        for fp in fields.values():
            issues.extend(detect_unit_ambiguity(fp))
        run.append(CHECK_UNIT)
    else:
        not_run.append(CHECK_UNIT)

    if check_roles:
        issues.extend(
            detect_field_role_ambiguity(fields, semantic_profile=semantic_profile)
        )
        run.append(CHECK_ROLE)
    else:
        not_run.append(CHECK_ROLE)

    if check_admin:
        for fp in fields.values():
            issues.extend(detect_admin_mismatch(fp.name, fp.samples))
        run.append(CHECK_ADMIN)
    else:
        not_run.append(CHECK_ADMIN)

    return issues, run, not_run


__all__ = [
    "CHECK_TIMEZONE",
    "CHECK_UNIT",
    "CHECK_ROLE",
    "CHECK_ADMIN",
    "detect_timezone_missing",
    "detect_unit_ambiguity",
    "detect_field_role_ambiguity",
    "detect_admin_mismatch",
    "evaluate_semantic_checks",
]
