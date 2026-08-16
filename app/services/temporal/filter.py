"""
Temporal Filter Engine.
Provides filtering capabilities for temporal GIS datasets by instant, time interval,
or relative windows (e.g. 'last_7_days', 'past_3_months').
"""

import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Union

from app.services.temporal.models import (
    TemporalFilter,
    TemporalOperator,
    TimeInstant,
    TimeInterval,
)
from app.services.temporal.profiler import parse_value_to_instant, profile_temporal_dataset

logger = logging.getLogger(__name__)


class TemporalFilterEngine:
    """
    Filters temporal datasets (GeoJSON FeatureCollections or lists of records/features)
    by discrete time instants, bounded time ranges, or relative time windows.
    """

    @staticmethod
    def _coerce_instant(value: Optional[Union[str, datetime, TimeInstant]]) -> Optional[TimeInstant]:
        """Coerces a str/datetime/TimeInstant bound into a TimeInstant (None
        if absent or unparseable)."""
        if value is None or isinstance(value, TimeInstant):
            return value
        parsed = parse_value_to_instant(value)
        return parsed[0] if parsed else None

    @staticmethod
    def parse_relative_window(window_str: str, ref_time: Optional[datetime] = None) -> TimeInterval:
        """
        Parses relative window strings such as 'last_7_days', 'past_3_months', 'last_24_hours', 'past_1_year'
        into a bounded TimeInterval ending at ref_time (defaults to current UTC time).
        """
        if ref_time is None:
            ref_time = datetime.now(timezone.utc)
        elif ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)

        clean_str = window_str.strip().lower().replace("-", "_")

        # Regex for patterns like last_7_days, past_3_months, last_24_hours, past_1_year.
        # (\d+) is followed directly by _+(unit): a \b here would never match
        # (digit and '_' are both word chars), which made arbitrary N values
        # like last_2_days fall through to the preset fallbacks and raise (#507).
        m = re.match(r"^(last|past)_(\d+)_+(day|days|hour|hours|month|months|year|years|week|weeks)$", clean_str)
        if m:
            val = int(m.group(2))
            unit = m.group(3)
            if unit.startswith("hour"):
                delta = timedelta(hours=val)
            elif unit.startswith("day"):
                delta = timedelta(days=val)
            elif unit.startswith("week"):
                delta = timedelta(weeks=val)
            elif unit.startswith("month"):
                delta = timedelta(days=val * 30)
            elif unit.startswith("year"):
                delta = timedelta(days=val * 365)
            else:
                delta = timedelta(days=val)
        else:
            # Fallbacks for standard presets
            if "24_hour" in clean_str or "1_day" in clean_str:
                delta = timedelta(hours=24)
            elif "7_day" in clean_str or "1_week" in clean_str:
                delta = timedelta(days=7)
            elif "30_day" in clean_str or "1_month" in clean_str:
                delta = timedelta(days=30)
            elif "3_month" in clean_str or "90_day" in clean_str:
                delta = timedelta(days=90)
            elif "1_year" in clean_str or "365_day" in clean_str:
                delta = timedelta(days=365)
            else:
                raise ValueError(f"Unrecognized relative window format: '{window_str}'")

        start_time = ref_time - delta
        return TimeInterval(
            start=TimeInstant.from_datetime(start_time),
            end=TimeInstant.from_datetime(ref_time),
        )

    def filter_dataset(
        self,
        features_or_records: Any,
        time_field: Optional[str] = None,
        operator: Optional[Union[TemporalOperator, str]] = None,
        instant: Optional[Union[str, datetime, TimeInstant]] = None,
        start: Optional[Union[str, datetime, TimeInstant]] = None,
        end: Optional[Union[str, datetime, TimeInstant]] = None,
        relative_window: Optional[str] = None,
        ref_time: Optional[datetime] = None,
        filter_spec: Optional[TemporalFilter] = None,
    ) -> Any:
        """
        Main filtering seam. Returns a filtered dataset matching the structure of input.
        """
        is_geojson = isinstance(features_or_records, dict) and "features" in features_or_records
        records = features_or_records["features"] if is_geojson else (features_or_records if isinstance(features_or_records, list) else [])

        if not records:
            return features_or_records

        # Auto-detect time field if not provided
        if not time_field and filter_spec:
            time_field = filter_spec.field_name

        if not time_field:
            profile = profile_temporal_dataset(records)
            if profile.primary_time_field:
                time_field = profile.primary_time_field.field_name
            else:
                logger.warning("No temporal field found or provided for filtering.")
                return features_or_records

        # Handle relative window, then explicit start/end bounds, then a
        # filter spec, then a discrete instant — in that precedence order.
        target_interval: Optional[TimeInterval] = None
        target_instant: Optional[TimeInstant] = None

        if relative_window:
            target_interval = self.parse_relative_window(relative_window, ref_time=ref_time)
            operator = TemporalOperator.BETWEEN
        elif start is not None or end is not None:
            # #451: explicit bounds must WIN over the filter_spec. The engine's
            # execute_filter forwards BOTH the spec and its start_time/end_time
            # fields; the previous `elif filter_spec:` precedence let the
            # interval-less spec shadow the explicit bounds, so BETWEEN ran
            # with no interval and matched nothing — every explicit-range
            # temporal_filter silently returned an empty success.
            # A single bound filters as an open-ended range.
            operator = TemporalOperator.BETWEEN
            start_inst = self._coerce_instant(start)
            end_inst = self._coerce_instant(end)
            if start_inst and end_inst:
                target_interval = TimeInterval(start=start_inst, end=end_inst)
            elif start_inst:
                target_interval = TimeInterval(
                    start=start_inst,
                    end=TimeInstant.from_datetime(datetime.max.replace(tzinfo=timezone.utc)),
                )
            elif end_inst:
                target_interval = TimeInterval(
                    start=TimeInstant.from_datetime(datetime.min.replace(tzinfo=timezone.utc)),
                    end=end_inst,
                )
        elif filter_spec:
            operator = filter_spec.operator
            target_instant = filter_spec.instant
            target_interval = filter_spec.interval
        elif instant is not None:
            if not operator:
                operator = TemporalOperator.EQUALS
            i_parsed = parse_value_to_instant(instant) if not isinstance(instant, TimeInstant) else (instant, None, True)
            target_instant = i_parsed[0] if i_parsed else None

        if isinstance(operator, str):
            operator = TemporalOperator(operator)

        if not operator:
            operator = TemporalOperator.BETWEEN if target_interval else TemporalOperator.EQUALS

        filtered_records = []
        for item in records:
            props = item.get("properties", item) if isinstance(item, dict) else {}
            val = props.get(time_field) if isinstance(props, dict) else None
            parsed = parse_value_to_instant(val, field_name_hint=time_field)

            if parsed is None:
                if operator == TemporalOperator.IS_NULL:
                    filtered_records.append(item)
                continue
            elif operator == TemporalOperator.IS_NULL:
                continue
            elif operator == TemporalOperator.IS_NOT_NULL:
                filtered_records.append(item)
                continue

            inst, _, _ = parsed
            t_epoch = inst.epoch_seconds

            match = False
            if operator == TemporalOperator.EQUALS and target_instant:
                match = abs(t_epoch - target_instant.epoch_seconds) < 1.0
            elif operator == TemporalOperator.BEFORE and target_instant:
                match = t_epoch < target_instant.epoch_seconds
            elif operator == TemporalOperator.AFTER and target_instant:
                match = t_epoch > target_instant.epoch_seconds
            elif operator in (TemporalOperator.BETWEEN, TemporalOperator.IN_INTERVAL) and target_interval:
                s_epoch = target_interval.start.epoch_seconds
                e_epoch = target_interval.end.epoch_seconds
                if target_interval.start_inclusive and target_interval.end_inclusive:
                    match = s_epoch <= t_epoch <= e_epoch
                elif target_interval.start_inclusive:
                    match = s_epoch <= t_epoch < e_epoch
                elif target_interval.end_inclusive:
                    match = s_epoch < t_epoch <= e_epoch
                else:
                    match = s_epoch < t_epoch < e_epoch

            if match:
                filtered_records.append(item)

        if is_geojson:
            res = dict(features_or_records)
            res["features"] = filtered_records
            return res
        return filtered_records
