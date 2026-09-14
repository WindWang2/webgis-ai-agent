"""行政区划校验：GB/T 2260 结构规则（第一道防线）+ 内嵌表 + 模糊容错
修正（ADR-0195 D6）。

红线：结构合法但表内未收录的码（多为县级、年年增删）一律
UNKNOWN_BUT_PLAUSIBLE 降级警示 —— 未收录 ≠ 虚构；只有结构层可判定的
虚构码（省级段不存在、全零、格式错）才硬阻断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.spatial_guardrails.data import admin_divisions as _admin
from app.services.spatial_guardrails.data import get_prefecture_short_names
from app.services.spatial_guardrails.errors import (
    FabricatedAdminDivisionError,
)
from app.services.spatial_guardrails.types import (
    CODE_ADMIN_PARENT_MISMATCH,
    CODE_FABRICATED_ADMIN_CODE,
    CODE_UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE,
    DefenseMode,
    GuardLevel,
    GuardrailIssue,
)

_SIX_DIGITS = re.compile(r"^\d{6}$")


@dataclass
class AdminCheckReport:
    ok: bool                       # 结构合法
    known: bool = False            # 表内收录
    name: str | None = None
    parent_chain: list[str] = field(default_factory=list)
    suggestion: str | None = None
    issue_code: str | None = None
    level: GuardLevel = GuardLevel.L1_FORMAT_CRS
    mode: DefenseMode = DefenseMode.WARN_DEGRADE


def _province_name(prov_code: str) -> str:
    return _admin.PROVINCES[prov_code][0]


def _levenshtein(a: str, b: str, cap: int = 2) -> int:
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


def _nearest_known_code(code: str) -> str | None:
    """编辑距离 ≤2 的最近已知码（近似码容错建议，绝不直接改写）。"""
    best: tuple[int, str, str] | None = None  # (dist, code, name)
    candidates: list[tuple[str, str]] = [
        (c + "0000", name)
        for c, (name, _aliases, _center) in _admin.PROVINCES.items()
    ] + [
        (c + "00", "%s（%s）" % (name, _province_name(prov)))
        for c, (name, prov) in _admin.PREFECTURES.items()
    ]
    for cand_code, name in candidates:
        d = _levenshtein(code, cand_code)
        if d <= 2 and (best is None or d < best[0]):
            best = (d, cand_code, name)
    if best is None:
        return None
    return f"疑似 {best[2]}（{best[1]}）"


def _structural_issue(code: str, reason: str) -> AdminCheckReport:
    nearest = _nearest_known_code(code)
    suggestion = f"{reason}；{nearest}" if nearest else f"{reason}，请核对统计局最新行政区划代码表"
    return AdminCheckReport(
        ok=False,
        issue_code=CODE_FABRICATED_ADMIN_CODE,
        suggestion=suggestion,
        level=GuardLevel.L1_FORMAT_CRS,
        mode=DefenseMode.BLOCK,
    )


def verify_code(code: str, *, claimed_parent: str | None = None) -> AdminCheckReport:
    """6 位行政区划码三段校验。

    - 结构非法（非 6 位数字 / 省级段不存在 / 地级段或县级段为 00 误配）
      → ok=False + FABRICATED_ADMIN_CODE（第一道防线，零表依赖可硬阻断）；
    - 结构合法 + 表内收录 → ok=True + known=True + 归属链；
    - 结构合法 + 未收录 → ok=True + UNKNOWN_BUT_PLAUSIBLE（降级警示）。
    """
    code = (code or "").strip()
    if not _SIX_DIGITS.fullmatch(code):
        return _structural_issue(code, "非 6 位数字形态")
    prov, city, county = code[:2], code[2:4], code[4:6]
    if prov not in _admin.PROVINCES:
        return _structural_issue(code, f"省级段 {prov} 不是法定省级行政区")
    is_province_level = city == "00" and county == "00"
    is_prefecture_level = city != "00" and county == "00"
    if city == "00" and county != "00":
        return _structural_issue(code, f"地级段为 00 而县级段为 {county}，结构非法")

    if is_province_level:
        name, _aliases, _center = _admin.PROVINCES[prov]
        report = AdminCheckReport(
            ok=True, known=True, name=name, parent_chain=[name],
            level=GuardLevel.L1_FORMAT_CRS, mode=DefenseMode.PASS,
        )
        claimed = _resolve_claimed(claimed_parent)
        if claimed is not None and claimed != prov:
            report.issue_code = CODE_ADMIN_PARENT_MISMATCH
            report.suggestion = f"{code} {name} 应归属 {name} 本级，而非 {_province_name(claimed)}"
            report.mode = DefenseMode.WARN_DEGRADE
        return report

    # 地级 / 县级：查内嵌表（表以 4 位地级段码为键）
    if is_prefecture_level and code[:4] in _admin.PREFECTURES:
        name, parent_prov = _admin.PREFECTURES[code[:4]]
        report = AdminCheckReport(
            ok=True, known=True, name=name,
            parent_chain=[_province_name(parent_prov), name],
            level=GuardLevel.L1_FORMAT_CRS, mode=DefenseMode.PASS,
        )
        claimed = _resolve_claimed(claimed_parent)
        if claimed is not None and claimed != parent_prov:
            report.issue_code = CODE_ADMIN_PARENT_MISMATCH
            report.suggestion = f"{code} {name} 应归属 {_province_name(parent_prov)}"
            report.mode = DefenseMode.WARN_DEGRADE
        return report

    # 结构合法但未收录（县级段体量大且年度增删频繁）
    return AdminCheckReport(
        ok=True, known=False,
        parent_chain=[_province_name(prov)],
        suggestion=_nearest_known_code(code),
        issue_code=CODE_UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE,
        level=GuardLevel.L3_LANDMASK_PLAUSIBILITY,
        mode=DefenseMode.WARN_DEGRADE,
    )


def _resolve_claimed(claimed_parent: str | None) -> str | None:
    """把 claimed_parent（名称或代码）归一为 2 位省级段；无法判定返回 None。"""
    if not claimed_parent:
        return None
    raw = claimed_parent.strip()
    if _SIX_DIGITS.fullmatch(raw):
        return raw[:2] if raw[:2] in _admin.PROVINCES else None
    resolved = resolve_name(raw)
    if resolved:
        return resolved[:2]
    return None


def assert_valid_code(code: str) -> None:
    """第一道防线：结构非法即抛 FabricatedAdminDivisionError。"""
    report = verify_code(code)
    if not report.ok:
        raise FabricatedAdminDivisionError(
            GuardrailIssue(
                level=GuardLevel.L1_FORMAT_CRS,
                mode=DefenseMode.BLOCK,
                code=CODE_FABRICATED_ADMIN_CODE,
                message=f"行政区划代码 {code!r} 在第一道防线被截获：{report.suggestion or '结构非法'}",
                location="$",
                evidence={"code": code, "suggestion": report.suggestion},
            )
        )


def resolve_name(name: str) -> str | None:
    """名称/简称/别名 → 6 位行政区划码（省级优先，其次地级）。"""
    raw = (name or "").strip()
    if not raw:
        return None
    from app.services.spatial_guardrails.data.admin_divisions import (
        PROVINCE_CODES_BY_SHORT_NAME,
    )

    if raw in PROVINCE_CODES_BY_SHORT_NAME:
        return PROVINCE_CODES_BY_SHORT_NAME[raw] + "0000"
    for suffix in ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区",
                   "自治区", "省", "市"):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            stripped = raw[: -len(suffix)]
            if stripped in PROVINCE_CODES_BY_SHORT_NAME:
                return PROVINCE_CODES_BY_SHORT_NAME[stripped] + "0000"
            break
    pref = get_prefecture_short_names()
    if raw in pref:
        return pref[raw] + "00"
    for suffix in ("市", "地区", "盟", "自治州"):
        if raw.endswith(suffix):
            stripped = raw[: -len(suffix)]
            if stripped in pref:
                return pref[stripped] + "00"
            break
    return None
