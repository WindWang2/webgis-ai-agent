"""
Temporal Aggregation Engine.
Provides temporal group-by and rollup aggregation across hours, days, weeks, months, or years,
computing statistical metrics (count, sum, mean, min, max, std) on target numeric properties.
"""

import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

from app.services.temporal.models import (
    TemporalAggregation,
    TemporalMetric,
    TemporalUnit,
)
from app.services.temporal.profiler import parse_value_to_instant, profile_temporal_dataset

logger = logging.getLogger(__name__)


def get_group_key(dt: datetime, unit: TemporalUnit) -> tuple[str, datetime, datetime]:
    """
    Returns (group_key_str, period_start_datetime, period_end_datetime) for a datetime and unit.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    if unit == TemporalUnit.SECOND:
        start = dt.replace(microsecond=0)
        end = start + timedelta(seconds=1)
        key = start.strftime("%Y-%m-%d %H:%M:%S")
    elif unit == TemporalUnit.MINUTE:
        start = dt.replace(second=0, microsecond=0)
        end = start + timedelta(minutes=1)
        key = start.strftime("%Y-%m-%d %H:%M:00")
    elif unit == TemporalUnit.HOUR:
        start = dt.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        key = start.strftime("%Y-%m-%d %H:00:00")
    elif unit == TemporalUnit.DAY:
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        key = start.strftime("%Y-%m-%d")
    elif unit == TemporalUnit.WEEK:
        # 桶按**周一起算**（回退到本周一 00:00）。key 必须与桶同一套周
        # 定义编号：旧代码 %U 是周日基准的周序号、%Y 是日历年份 —— 与
        # 周一起算的桶错配，含 1 月初数据的桶被标成上一年份（review
        # #1744-5）。ISO 8601 周（周一为首日、含首个周四的周为第 1 周）
        # 正是周一定义，改用 isocalendar() 的 (iso_year, iso_week)。
        start = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        iso_year, iso_week, _ = start.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
    elif unit == TemporalUnit.MONTH:
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        key = start.strftime("%Y-%m")
    elif unit == TemporalUnit.QUARTER:
        month = 3 * ((dt.month - 1) // 3) + 1
        start = dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1, month=1) if month == 10 else start.replace(month=month + 3)
        key = f"{start.year}-Q{(month-1)//3+1}"
    elif unit == TemporalUnit.YEAR:
        start = dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
        key = start.strftime("%Y")
    else:
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        key = start.strftime("%Y-%m-%d")

    return key, start, end


class TemporalAggregationEngine:
    """
    Performs temporal grouping and statistical metric calculations over vector/record datasets.
    """

    def aggregate(
        self,
        features_or_records: Any,
        time_field: Optional[str] = None,
        group_by_unit: Union[TemporalUnit, str] = TemporalUnit.DAY,
        metrics: Optional[List[Union[TemporalMetric, str]]] = None,
        target_fields: Optional[List[str]] = None,
        agg_spec: Optional[TemporalAggregation] = None,
    ) -> List[Dict[str, Any]]:
        """
        Groups data by specified unit (hour, day, week, month, year) and computes metrics.
        Returns a list of bucket dictionaries ordered by time.
        """
        is_geojson = isinstance(features_or_records, dict) and "features" in features_or_records
        records = features_or_records["features"] if is_geojson else (features_or_records if isinstance(features_or_records, list) else [])

        if not records:
            return []

        if agg_spec:
            time_field = time_field or agg_spec.time_field
            group_by_unit = agg_spec.group_by_unit
            metrics = metrics or agg_spec.metrics
            target_fields = target_fields or agg_spec.target_fields

        from app.lib.gis.scientific_errors import UnsupportedMethod
        try:
            group_by_unit = TemporalUnit(group_by_unit)
        except ValueError as exc:
            raise UnsupportedMethod(f"unsupported temporal unit: {group_by_unit}") from exc

        if not metrics:
            metrics = [TemporalMetric.COUNT, TemporalMetric.MEAN]
        try:
            metrics_clean: List[TemporalMetric] = [TemporalMetric({"std": "stddev", "avg": "mean"}.get(m, m)) for m in metrics]
        except ValueError as exc:
            raise UnsupportedMethod(f"unsupported temporal metric: {metrics}") from exc

        if not time_field:
            profile = profile_temporal_dataset(records)
            if profile.primary_time_field:
                time_field = profile.primary_time_field.field_name
            else:
                logger.warning("No temporal field found or specified for aggregation.")
                return []

        target_fields = target_fields or []

        # Bucket group structure
        groups: Dict[str, Dict[str, Any]] = {}

        for item in records:
            props = item.get("properties", item) if isinstance(item, dict) else {}
            val = props.get(time_field) if isinstance(props, dict) else None
            parsed = parse_value_to_instant(val, field_name_hint=time_field)
            if parsed is None:
                continue

            inst, _, _ = parsed
            dt = inst.to_datetime()
            group_key, period_start, period_end = get_group_key(dt, group_by_unit)

            if group_key not in groups:
                groups[group_key] = {
                    "period": group_key,
                    "start_time": period_start.isoformat(),
                    "end_time": period_end.isoformat(),
                    "records": [],
                }
            groups[group_key]["records"].append((inst.epoch_seconds, props))

        # Sort group keys chronologically
        sorted_keys = sorted(groups.keys())
        results: List[Dict[str, Any]] = []

        for key in sorted_keys:
            grp = groups[key]
            items = [record for _, record in sorted(grp["records"], key=lambda entry: entry[0])]
            record_count = len(items)

            bucket: Dict[str, Any] = {
                "period": grp["period"],
                "start_time": grp["start_time"],
                "end_time": grp["end_time"],
                "count": record_count,
                "metrics": {},
            }

            for t_field in target_fields:
                num_vals = []
                for it in items:
                    v = it.get(t_field)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                        num_vals.append(float(v))

                field_metrics: Dict[str, Any] = {"count": len(num_vals)}
                if num_vals:
                    if TemporalMetric.SUM in metrics_clean or "sum" in metrics:
                        field_metrics["sum"] = sum(num_vals)
                    if TemporalMetric.MEAN in metrics_clean or "mean" in metrics:
                        field_metrics["mean"] = sum(num_vals) / len(num_vals)
                    if TemporalMetric.MIN in metrics_clean or "min" in metrics:
                        field_metrics["min"] = min(num_vals)
                    if TemporalMetric.MAX in metrics_clean or "max" in metrics:
                        field_metrics["max"] = max(num_vals)
                    if TemporalMetric.FIRST in metrics_clean or "first" in metrics:
                        field_metrics["first"] = num_vals[0]
                    if TemporalMetric.LAST in metrics_clean or "last" in metrics:
                        field_metrics["last"] = num_vals[-1]
                    if TemporalMetric.STDDEV in metrics_clean or "std" in metrics or "stddev" in metrics:
                        if len(num_vals) > 1:
                            mean_val = sum(num_vals) / len(num_vals)
                            variance = sum((x - mean_val) ** 2 for x in num_vals) / (len(num_vals) - 1)
                            field_metrics["std"] = math.sqrt(max(0.0, variance))
                        else:
                            field_metrics["std"] = 0.0

                    # Also top-level shortcut property e.g. "temperature_mean"
                    for m_name, m_val in field_metrics.items():
                        bucket[f"{t_field}_{m_name}"] = m_val

                bucket["metrics"][t_field] = field_metrics

            results.append(bucket)

        return results
