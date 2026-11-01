"""时间表示探测与真实帧推导（GOAL 03 M2）。

唯一帧来源 = 数据里**实际出现**的时间桶：
- 探测复用 ``app/services/temporal/profiler.parse_value_to_instant``（单一
  解析事实源，不另写解析器）；
- 表示（time_representation）按**原始值形态**判定并要求全列一致 —— 混合
  表示（如 ISO 字符串 + epoch 数值）拒绝绑定（typed fallback），因为
  where AST 字面比较无法跨表示保持诚实；
- iso_string 额外要求**字典序 = 时间序**（同格式、同时区偏移），否则拒绝
  —— 这是字符串比较科学性的前提，不是实现细节；
- 分桶边界在契约时区计算（timezone 参数），桶内计数 = 真实要素数。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.temporal.profiler import parse_value_to_instant

#: 探测采样上限（与 profiler _MAX_TEMPORAL_SAMPLE 同口径）：bind 是冷路径，
#: 粒度估计只看样本；分桶计数段是纯字典聚合（无解析），保持全量真实。
_MAX_PROBE_SAMPLE = 5000

_ISO_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")

#: 粒度选择顺序（细 → 粗）。
_GRANULARITY_ORDER = ("hour", "day", "week", "month", "quarter", "year")


@dataclass
class Bucket:
    """一个真实观测桶（frame 的推导中间态）。"""

    key: str          # 稳定 id 片段（"2023"、"2023-04"、"2023-W12"）
    label: str        # 人读标签
    start: datetime   # tz-aware（契约时区）
    end: datetime     # 半开边界
    count: int        # 真实要素计数


@dataclass
class TimeBindingProbe:
    """探测结果 —— bind 决策的全部证据。"""

    ok: bool
    code: str = ""                      # 拒绝码（ok=False 时必填）
    message: str = ""
    time_field: str = ""
    representation: str = ""            # TEMPORAL_REPRESENTATIONS 之一
    format_hint: str = ""               # iso/date 格式样例（表示归一用）
    semantics: str = "event"
    confidence: float = 0.0
    total_values: int = 0
    parsed_values: int = 0
    buckets: List[Bucket] = field(default_factory=list)
    quality: List[str] = field(default_factory=list)
    granularity: str = "year"


def _bucket_start(lt: datetime, granularity: str) -> datetime:
    """本地（契约时区）instant → 所属桶左边界。"""
    if granularity == "year":
        return lt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if granularity == "quarter":
        q_month = ((lt.month - 1) // 3) * 3 + 1
        return lt.replace(month=q_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    if granularity == "month":
        return lt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if granularity == "week":
        # ISO 周：周一为起点。
        days_since_monday = lt.weekday()
        return (lt - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    if granularity == "day":
        return lt.replace(hour=0, minute=0, second=0, microsecond=0)
    # hour
    return lt.replace(minute=0, second=0, microsecond=0)


def _bucket_step(start: datetime, granularity: str) -> datetime:
    """桶左边界 → 右边界（半开）。"""
    if granularity == "year":
        return start.replace(year=start.year + 1)
    if granularity == "quarter":
        m = start.month + 3
        return start.replace(year=start.year + m // 12, month=m % 12 or 12)
    if granularity == "month":
        m = start.month + 1
        return start.replace(year=start.year + m // 12, month=m % 12 or 12)
    if granularity == "week":
        return start + timedelta(weeks=1)
    if granularity == "day":
        return start + timedelta(days=1)
    return start + timedelta(hours=1)


def _bucket_key(start: datetime, granularity: str) -> Tuple[str, str]:
    """桶 (key, label)。"""
    if granularity == "year":
        s = f"{start.year:04d}"
        return s, s
    if granularity == "quarter":
        q = (start.month - 1) // 3 + 1
        return f"{start.year:04d}-Q{q}", f"{start.year:04d}-Q{q}"
    if granularity == "month":
        s = f"{start.year:04d}-{start.month:02d}"
        return s, s
    if granularity == "week":
        iso = start.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}", f"{iso[0]:04d}-W{iso[1]:02d}"
    if granularity == "day":
        s = f"{start.year:04d}-{start.month:02d}-{start.day:02d}"
        return s, s
    return (
        f"{start.year:04d}-{start.month:02d}-{start.day:02d}T{start.hour:02d}",
        f"{start.year:04d}-{start.month:02d}-{start.day:02d} {start.hour:02d}:00",
    )


def _iso_with_tz(dt: datetime) -> str:
    """契约 ISO instant（统一 UTC 渲染 Z，紧凑且可比）。"""
    u = dt.astimezone(timezone.utc)
    return u.strftime("%Y-%m-%dT%H:%M:%SZ")


def granularity_from_span(seconds: float, max_frames: int) -> str:
    """按跨度与帧数上限选默认粒度（跨度过细 → 自动升粗）。

    估计各粒度桶数（近似：月=30 天、季=91 天、年=365 天），取第一个使
    估计桶数 ≤ 4×max_frames 的粒度；真实桶数仍可能超限，由调用方的
    too_many_frames 拒绝兜底（估计只用于初选，不用于截断）。
    """
    span_days = max(seconds / 86400.0, 1e-9)
    approx = {
        "hour": span_days * 24,
        "day": span_days,
        "week": span_days / 7,
        "month": span_days / 30,
        "quarter": span_days / 91,
        "year": span_days / 365,
    }
    for g in _GRANULARITY_ORDER:
        if approx[g] <= max_frames * 4:
            return g
    return "year"


def _detect_representation(
    raw_values: List[Any],
) -> Tuple[str, str, float]:
    """原始值形态 → (representation, format_hint, 置信度)。

    全列一致才接受；混合 → ("", "", 0)。iso_string 要求同偏移（字典序
    = 时间序的前提）。
    """
    non_null = [v for v in raw_values if v is not None]
    if not non_null:
        return "", "", 0.0

    all_int = all(isinstance(v, int) and not isinstance(v, bool) for v in non_null)
    all_float = all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null
    )
    all_str = all(isinstance(v, str) for v in non_null)

    if all_int:
        # 整数三态：纯年份（全列 1800-2100）/ epoch 秒 / epoch 毫秒。
        if all(1800 <= v <= 2100 for v in non_null):
            return "year_int", "", 0.95
        if all(3.0e8 <= v <= 3.0e9 for v in non_null):
            return "epoch_s", "", 0.9
        if all(3.0e11 <= v <= 3.0e12 for v in non_null):
            return "epoch_ms", "", 0.9
        return "", "", 0.0
    if all_float:
        if all(3.0e8 <= v <= 3.0e9 for v in non_null):
            return "epoch_s", "", 0.7
        if all(3.0e11 <= v <= 3.0e12 for v in non_null):
            return "epoch_ms", "", 0.7
        return "", "", 0.0
    if all_str:
        # 字符串子形态：ISO datetime / date / year-month / year。
        iso_dt = [v for v in non_null if _ISO_DT_RE.match(v)]
        date_only = [v for v in non_null if _DATE_RE.match(v)]
        ym = [v for v in non_null if _YEAR_MONTH_RE.match(v)]
        y = [v for v in non_null if _YEAR_RE.match(v)]
        n = len(non_null)
        if len(iso_dt) == n:
            # 偏移检查（字典序 = 时间序的科学前提）：仅接受全列统一 **Z**
            # 偏移 —— 非 Z 的统一偏移（如 +08:00）下，边界渲染（Z）与数据
            # 字面序不一致会错窗（P2 修正：拒绝并建议归一化，不静默错配）。
            offsets = {v[19:] if len(v) > 19 else "" for v in iso_dt}
            if len(offsets) == 1 and ("Z" in offsets or "" in offsets):
                return "iso_string", iso_dt[0], 0.9
            return "", "", 0.0
        if len(date_only) == n:
            return "date_string", date_only[0], 0.9
        if len(ym) == n:
            return "date_string", ym[0], 0.85
        if len(y) == n:
            return "date_string", y[0], 0.8
    return "", "", 0.0


def probe_fc_time_field(
    fc: Dict[str, Any],
    time_field: Optional[str] = None,
) -> Tuple[str, List[Any], List[Any]]:
    """FC → (字段名, 值样本, 全量值)。自动探测复用 profiler 评分。"""
    from app.services.temporal.profiler import profile_field

    features = fc.get("features", []) if isinstance(fc, dict) else []
    field_values: Dict[str, List[Any]] = {}
    for f in features:
        props = f.get("properties") if isinstance(f, dict) else None
        if isinstance(props, dict):
            for k, v in props.items():
                field_values.setdefault(k, []).append(v)

    if time_field is not None:
        if time_field not in field_values:
            return "", [], []
        return time_field, field_values[time_field][:_MAX_PROBE_SAMPLE], field_values[time_field]

    # 自动探测：profiler 评分（confidence ≥ 0.4）取最高分字段。
    best_field, best_score = "", 0.0
    for fname, vals in field_values.items():
        tf = profile_field(fname, vals[:_MAX_PROBE_SAMPLE])
        if tf.confidence_score >= 0.4 and tf.confidence_score > best_score:
            best_field, best_score = fname, tf.confidence_score
    if not best_field:
        return "", [], []
    return best_field, field_values[best_field][:_MAX_PROBE_SAMPLE], field_values[best_field]


def _gap_steps(a: datetime, b: datetime, granularity: str) -> float:
    """相邻桶起点间的粒度步数（≥1；>1 = 中间有整期缺测）。"""
    if granularity == "year":
        return b.year - a.year
    if granularity == "quarter":
        return (b.year - a.year) * 4 + ((b.month - 1) // 3) - ((a.month - 1) // 3)
    if granularity == "month":
        return (b.year - a.year) * 12 + b.month - a.month
    if granularity == "week":
        return (b - a).total_seconds() / (7 * 86400)
    if granularity == "day":
        return (b - a).total_seconds() / 86400
    return (b - a).total_seconds() / 3600


def _natural_granularity(representation: str, format_hint: str) -> str:
    """表示的自然精度（缺省粒度的第一候选）。

    year_int / 定宽日期串自带精度信息（年值→年桶、YYYY-MM→月桶、
    YYYY-MM-DD→日桶）；epoch / iso datetime 自带时刻精度，由跨度决定。
    """
    if representation == "year_int":
        return "year"
    if representation == "date_string":
        h = (format_hint or "").strip()
        if _YEAR_RE.match(h):
            return "year"
        if _YEAR_MONTH_RE.match(h):
            return "month"
        return "day"
    return ""


def _coarsen(granularity: str) -> Optional[str]:
    """粒度粗化一步；已最粗（year）→ None。"""
    idx = _GRANULARITY_ORDER.index(granularity)
    if idx + 1 >= len(_GRANULARITY_ORDER):
        return None
    return _GRANULARITY_ORDER[idx + 1]


def detect_time_binding(
    fc: Dict[str, Any],
    *,
    time_field: Optional[str] = None,
    granularity: Optional[str] = None,
    tz_name: str = "UTC",
    semantics: str = "event",
    max_frames: int = 512,
) -> TimeBindingProbe:
    """FC → 帧桶推导（bind 的唯一决策函数）。

    拒绝路径全部 typed（probe.ok=False + code），调用方转成工具/服务的
    typed fallback，绝不静默降级、绝不截断真实桶。
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return TimeBindingProbe(ok=False, code="invalid_timezone",
                                message=f"时区 {tz_name!r} 不是合法 IANA 名")

    field, sample, all_values = probe_fc_time_field(fc, time_field)
    if not field:
        return TimeBindingProbe(ok=False, code="no_time_field",
                                message="数据中探测不到可用时间字段（或指定字段不存在）")

    representation, format_hint, conf = _detect_representation(all_values)
    if not representation:
        return TimeBindingProbe(
            ok=False, code="non_uniform_time_format",
            message="时间列表示不统一（混合格式/偏移），字面比较无法保持诚实",
            time_field=field,
        )

    # 第一段：全量解析（单一解析事实源）。
    parsed_pairs: List[Tuple[Any, Any]] = []  # (raw, TimeInstant)
    for raw in all_values:
        res = parse_value_to_instant(raw, field_name_hint=field)
        if res is not None:
            parsed_pairs.append((raw, res[0]))
    if not parsed_pairs:
        return TimeBindingProbe(
            ok=False, code="unparseable_time_field",
            message="时间列存在但全部值无法解析为时间", time_field=field,
        )

    # 粒度：显式优先；缺省 = 表示自然精度（year_int/日期串自带精度），
    # epoch/iso 由观测跨度估计。
    explicit_granularity = granularity
    if explicit_granularity:
        eff_granularity = explicit_granularity
    else:
        eff_granularity = _natural_granularity(representation, format_hint)
        if not eff_granularity:
            sample_epochs = [inst.epoch_seconds for _, inst in parsed_pairs[:500]]
            span = max(sample_epochs) - min(sample_epochs)
            eff_granularity = granularity_from_span(span, max_frames)

    def _bucket_all(g: str) -> Dict[str, Bucket]:
        by_key: Dict[str, Bucket] = {}
        for _, inst in parsed_pairs:
            local = inst.to_datetime().astimezone(tz)
            start = _bucket_start(local, g)
            key, label = _bucket_key(start, g)
            if key not in by_key:
                by_key[key] = Bucket(
                    key=key, label=label, start=start,
                    end=_bucket_step(start, g), count=1,
                )
            else:
                by_key[key].count += 1
        return by_key

    buckets_by_key = _bucket_all(eff_granularity)
    # 自动粒度下真实桶仍超限 → 逐级粗化重桶（仍只有真实桶，绝不截断）；
    # 到 year 仍超限才拒绝。显式粒度超限 → 直接诚实拒绝（带改粗建议）。
    while len(buckets_by_key) > max_frames:
        if explicit_granularity:
            break
        nxt = _coarsen(eff_granularity)
        if nxt is None:
            break
        eff_granularity = nxt
        buckets_by_key = _bucket_all(eff_granularity)

    buckets = sorted(buckets_by_key.values(), key=lambda b: b.start)

    if len(buckets) > max_frames:
        return TimeBindingProbe(
            ok=False, code="too_many_frames",
            message=f"真实时间桶 {len(buckets)} 超过播放上限 {max_frames}；"
                    f"请改用更粗粒度（当前 {eff_granularity}）",
            time_field=field, representation=representation,
            granularity=eff_granularity,
        )

    quality: List[str] = []
    if len(buckets) == 1:
        quality.append("single_period")
    if len(buckets) >= 2:
        # 缺期检测：相邻桶起点步进 ≥2 个粒度步（如 year 桶 2023→2025 跳过
        # 2024）—— 不内插、只披露（missing_policy 消费）。
        if any(_gap_steps(a.start, b.start, eff_granularity) >= 1.5
               for a, b in zip(buckets, buckets[1:])):
            quality.append("missing_period_gap")
    if len(buckets) >= 3:
        # 不规则间隔：桶间距中位数 ±50% 之外的异质性。
        starts = [b.start for b in buckets]
        gaps = [(b - a).total_seconds() for a, b in zip(starts, starts[1:])]
        gaps_sorted = sorted(gaps)
        median = gaps_sorted[len(gaps_sorted) // 2]
        if median > 0 and any(g > median * 1.5 + 1e-9 or g < median * 0.5 - 1e-9
                              for g in gaps):
            quality.append("irregular_spacing")
    if all_values and len(parsed_pairs) / len(all_values) < 0.95:
        quality.append("time_field_partial_null")

    # 快照语义推断：整期对齐（所有值落在桶起点）→ snapshot，否则 event。
    eff_semantics = semantics
    if semantics == "event":
        aligned = all(
            _bucket_start(inst.to_datetime().astimezone(tz), eff_granularity)
            == inst.to_datetime().astimezone(tz)
            for _, inst in parsed_pairs[:200]
        )
        if aligned:
            eff_semantics = "snapshot"

    return TimeBindingProbe(
        ok=True,
        time_field=field,
        representation=representation,
        format_hint=format_hint,
        semantics=eff_semantics,
        confidence=conf,
        total_values=len(all_values),
        parsed_values=len(parsed_pairs),
        buckets=buckets,
        quality=quality,
        granularity=eff_granularity,
    )


def buckets_to_contract_frames(buckets: List[Bucket]) -> List[Dict[str, Any]]:
    """Bucket 列表 → 契约 frames dict 列表（MapSpecTemporalConfig.frames）。"""
    return [
        {
            "id": f"f-{b.key}",
            "label": b.label,
            "start": _iso_with_tz(b.start),
            "end": _iso_with_tz(b.end),
            "feature_count": b.count,
        }
        for b in buckets
    ]
