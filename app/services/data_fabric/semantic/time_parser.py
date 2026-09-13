"""Relative time expression parser (DS6, ADR-0176, gap A8).

"近五年" / "去年三季度" / "2015年以来" / "last 3 years" / "since 2015" →
``TimeRange{start, end, granularity, confidence}`` (ISO dates, inclusive).

Deterministic and pure: the reference "now" is injectable (tests pin it;
production defaults to UTC today). Unparseable input returns ``None`` —
never a silently-widened range. Confidence ≥0.9 for explicit patterns,
lower for loose ones (the retrieval layer treats <0.5 as "no time filter").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Tuple


@dataclass(frozen=True)
class TimeRange:
    start: str          # ISO date (inclusive)
    end: str            # ISO date (inclusive)
    granularity: str    # year / quarter / month / week / day / range
    confidence: float

    def as_tuple(self) -> Tuple[str, str]:
        return (self.start, self.end)


_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num(text: str) -> Optional[int]:
    text = text.strip()
    if text.isdigit():
        return int(text)
    if text in _CN_NUM:
        return _CN_NUM[text]
    if text == "十":
        return 10
    m = re.fullmatch(r"十([一二三四五六七八九])", text)
    if m:  # 十一 … 十九
        return 10 + _CN_NUM[m.group(1)]
    m = re.fullmatch(r"([一二两二三四五六七八九])十([一二三四五六七八九])?", text)
    if m:
        tens = _CN_NUM[m.group(1)]
        ones = _CN_NUM[m.group(2)] if m.group(2) else 0
        return tens * 10 + ones
    return None


def _iso(d: date) -> str:
    return d.isoformat()


def _quarter_months(q: int) -> Tuple[int, int]:
    return (3 * (q - 1) + 1, 3 * q)


def parse_time_expr(text: str, *, now: Optional[date] = None) -> Optional[TimeRange]:
    """Parse a (possibly Chinese) time expression embedded in a query."""
    if not text or not text.strip():
        return None
    today = now or date.today()
    t = text.strip()

    # ── explicit absolute ranges ────────────────────────────────────────────
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?至到?(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?", t)
    if m:
        y1, mo1, d1, y2, mo2, d2 = map(int, m.groups())
        return TimeRange(_iso(date(y1, mo1, d1)), _iso(date(y2, mo2, d2)), "day", 0.95)
    m = re.search(r"(\d{4})[-/年.](\d{1,2})\s*(?:至|到|~|—|-|和|、)\s*(\d{4})[-/年.](\d{1,2})", t)
    if m:
        y1, m1, y2, m2 = map(int, m.groups())
        return TimeRange(_iso(date(y1, m1, 1)), _iso(_month_end(date(y2, m2, 1))), "month", 0.9)
    m = re.search(r"between\s+(\d{4})(?:-(\d{1,2}))?\s+and\s+(\d{4})(?:-(\d{1,2}))?", t, re.I)
    if m:
        y1 = int(m.group(1))
        m1 = int(m.group(2) or 1)
        y2 = int(m.group(3))
        m2 = int(m.group(4) or 12)
        return TimeRange(_iso(date(y1, m1, 1)), _iso(_month_end(date(y2, m2, 1))), "month", 0.9)

    # ── anchored ranges: 以来 / since / 截至 / until ────────────────────────
    m = re.search(r"(\d{4})\s*年?\s*(?:以来|之后|以后|及以后|起)", t)
    if m:
        y = int(m.group(1))
        return TimeRange(_iso(date(y, 1, 1)), _iso(today), "year", 0.9)
    m = re.search(r"since\s+(\d{4})", t, re.I)
    if m:
        return TimeRange(_iso(date(int(m.group(1)), 1, 1)), _iso(today), "year", 0.9)
    m = re.search(r"(?:截至|截止到?|直到)\s*(\d{4})\s*年?(?:底|末|12月)?", t)
    if m:
        y = int(m.group(1))
        return TimeRange("0001-01-01", _iso(date(y, 12, 31)), "year", 0.85)
    m = re.search(r"(\d{4})\s*年?之前", t)
    if m:
        return TimeRange("0001-01-01", _iso(date(int(m.group(1)), 12, 31)), "year", 0.85)
    m = re.search(r"until\s+(\d{4})", t, re.I)
    if m:
        return TimeRange("0001-01-01", _iso(date(int(m.group(1)), 12, 31)), "year", 0.85)

    # ── relative windows: 近N年/月/周/日/季度 ──────────────────────────────
    m = re.search(r"(?:近|最近|过去|前)\s*([一二两二三四五六七八九十\d]+)\s*年", t)
    if m:
        n = _num(m.group(1)) or 1
        return TimeRange(_iso(date(today.year - n + 1, 1, 1)), _iso(today), "year", 0.9)
    m = re.search(r"last\s+(\d+)\s+years?", t, re.I)
    if m:
        n = int(m.group(1))
        return TimeRange(_iso(date(today.year - n + 1, 1, 1)), _iso(today), "year", 0.9)
    m = re.search(r"(?:近|最近|过去|前)\s*([一二两二三四五六七八九十\d]+)\s*个?月", t)
    if m:
        n = _num(m.group(1)) or 1
        start = _month_end(today) - timedelta(days=30 * n)
        return TimeRange(_iso(date(start.year, start.month, 1)), _iso(today), "month", 0.9)
    m = re.search(r"last\s+(\d+)\s+months?", t, re.I)
    if m:
        n = int(m.group(1))
        start = _month_end(today) - timedelta(days=30 * n)
        return TimeRange(_iso(date(start.year, start.month, 1)), _iso(today), "month", 0.9)
    if re.search(r"(?:近|最近|过去)\s*半\s*年", t):
        start = _month_end(today) - timedelta(days=30 * 6)
        return TimeRange(_iso(date(start.year, start.month, 1)), _iso(today), "month", 0.85)
    m = re.search(r"(?:近|最近|过去|前)\s*([一二两二三四五六七八九十\d]+)\s*周", t)
    if m:
        n = _num(m.group(1)) or 1
        return TimeRange(_iso(today - timedelta(days=7 * n)), _iso(today), "week", 0.9)
    m = re.search(r"(?:近|最近|过去|前)\s*([一二两二三四五六七八九十\d]+)\s*天", t)
    if m:
        n = _num(m.group(1)) or 1
        return TimeRange(_iso(today - timedelta(days=n - 1)), _iso(today), "day", 0.9)
    m = re.search(r"last\s+(\d+)\s+days?", t, re.I)
    if m:
        n = int(m.group(1))
        return TimeRange(_iso(today - timedelta(days=n - 1)), _iso(today), "day", 0.9)

    # ── quarters ────────────────────────────────────────────────────────────
    m = re.search(r"(\d{4})\s*年?(上|下)半年", t)
    if m:
        y = int(m.group(1))
        if m.group(2) == "上":
            return TimeRange(_iso(date(y, 1, 1)), _iso(date(y, 6, 30)), "quarter", 0.9)
        return TimeRange(_iso(date(y, 7, 1)), _iso(date(y, 12, 31)), "quarter", 0.9)
    m = re.search(r"(?:近|最近|过去)\s*([一二两二三四五六七八九十\d]+)\s*个?季度", t)
    if m:
        n = _num(m.group(1)) or 1
        cur_q = (today.month - 1) // 3 + 1
        start_q = cur_q - (n - 1)
        start_year = today.year
        while start_q <= 0:
            start_q += 4
            start_year -= 1
        m1, _ = _quarter_months(start_q)
        return TimeRange(_iso(date(start_year, m1, 1)), _iso(today), "quarter", 0.9)
    m = re.search(
        r"(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter(?:\s+of)?\s*,?\s*(\d{4})?", t, re.I
    )
    if m:
        qword = m.group(1).lower()
        q = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4}.get(qword, 1)
        year = int(m.group(2)) if m.group(2) else None
        return _quarter_range(year, q, today)
    m = re.search(r"去年\s*第?\s*([一二三四1-4])\s*季度", t)
    if m:
        q = _num(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
        return _quarter_range(today.year - 1, q, today)
    m = re.search(r"(?:(\d{4})\s*年?\s*)?[第]?\s*([一二三四1-4])\s*季度", t)
    if m:
        year = int(m.group(1)) if m.group(1) else None
        q = _num(m.group(2)) if not m.group(2).isdigit() else int(m.group(2))
        return _quarter_range(year, q, today)
    m = re.search(r"Q([1-4])\s*,?\s*(\d{4})?", t, re.I)
    if m:
        q = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else None
        return _quarter_range(year, q, today)
    if re.search(r"本季度|这个季度|this quarter", t, re.I):
        return _quarter_range(today.year, (today.month - 1) // 3 + 1, today)
    if re.search(r"上季度|上个季度|last quarter", t, re.I):
        qy, qq = today.year, (today.month - 1) // 3 + 1
        qy, qq = (qy - 1, 4) if qq == 1 else (qy, qq - 1)
        return _quarter_range(qy, qq, today)

    # ── months ──────────────────────────────────────────────────────────────
    m = re.search(r"(\d{4})[-/年.](\d{1,2})月?", t)
    if m and not re.search(r"日|day", t):
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return TimeRange(_iso(date(y, mo, 1)), _iso(_month_end(date(y, mo, 1))), "month", 0.9)
    if re.search(r"本月|这个月|this month", t, re.I):
        return TimeRange(_iso(date(today.year, today.month, 1)), _iso(_month_end(today)), "month", 0.9)
    if re.search(r"上个月|上月|last month", t, re.I):
        prev = _month_end(today - timedelta(days=15))
        return TimeRange(_iso(date(prev.year, prev.month, 1)), _iso(_month_end(prev)), "month", 0.9)

    # ── years ───────────────────────────────────────────────────────────────
    m = re.search(r"(\d{4})\s*年?\s*(?:至|到|~|—)\s*(\d{4})(?:\s*年)?", t)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        lo, hi = min(y1, y2), max(y1, y2)
        return TimeRange(_iso(date(lo, 1, 1)), _iso(date(hi, 12, 31)), "year", 0.9)
    m = re.search(r"(\d{4})\s*年?\s*(?:与|和|跟|及|vs|与)\s*(\d{4})\s*年?(?:\s*(?:对比|比较|区间|之间))?", t)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        lo, hi = min(y1, y2), max(y1, y2)
        return TimeRange(_iso(date(lo, 1, 1)), _iso(date(hi, 12, 31)), "year", 0.85)
    m = re.search(r"(\d{4})\s*年\s*(\d{4})\s*年?\s*(?:区间|之间|对比)", t)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        lo, hi = min(y1, y2), max(y1, y2)
        return TimeRange(_iso(date(lo, 1, 1)), _iso(date(hi, 12, 31)), "year", 0.85)
    if re.search(r"今年|this year", t, re.I):
        return TimeRange(_iso(date(today.year, 1, 1)), _iso(today), "year", 0.9)
    if re.search(r"本年度|本年", t):
        return TimeRange(_iso(date(today.year, 1, 1)), _iso(today), "year", 0.9)
    if re.search(r"去年以来", t):
        return TimeRange(_iso(date(today.year - 1, 1, 1)), _iso(today), "year", 0.9)
    if re.search(r"去年|last year", t, re.I):
        y = today.year - 1
        return TimeRange(_iso(date(y, 1, 1)), _iso(date(y, 12, 31)), "year", 0.9)
    if re.search(r"前年", t):
        y = today.year - 2
        return TimeRange(_iso(date(y, 1, 1)), _iso(date(y, 12, 31)), "year", 0.85)
    m = re.search(r"(\d{4})\s*年(?:度)?", t)
    if m:
        y = int(m.group(1))
        return TimeRange(_iso(date(y, 1, 1)), _iso(date(y, 12, 31)), "year", 0.9)
    m = re.search(r"\b(\d{4})\b", t)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= today.year + 1:
            return TimeRange(_iso(date(y, 1, 1)), _iso(date(y, 12, 31)), "year", 0.7)

    return None


def _quarter_range(year: Optional[int], q: int, today: date) -> TimeRange:
    y = year or today.year
    m1, m2 = _quarter_months(q)
    return TimeRange(_iso(date(y, m1, 1)), _iso(_month_end(date(y, m2, 1))), "quarter", 0.9)


def _month_end(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


__all__ = ["TimeRange", "parse_time_expr"]
