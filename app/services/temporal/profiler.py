"""
Temporal Dataset Profiler.
Provides auto-detection logic for temporal fields (datetime, date, timestamp, year-month, epoch),
timezone metadata, temporal bounds, sample granularity, gap detection, and confidence scoring
without fragile guesses.
"""

import math
import re
import logging
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.temporal.models import (
    TemporalDatasetProfile,
    TemporalExtent,
    TemporalFieldType,
    TemporalGranularity,
    TimeField,
    TimeInstant,
)

logger = logging.getLogger(__name__)

# Name matching hints for temporal fields
TEMPORAL_NAME_REGEX = re.compile(
    r"(timestamp|datetime|date|time|acquired|created|updated|recorded|year|month|t1|t2)",
    re.IGNORECASE,
)

ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$")
DATE_ONLY_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_MONTH_REGEX = re.compile(r"^\d{4}-\d{2}$")
YEAR_ONLY_REGEX = re.compile(r"^\d{4}$")
SLASH_DATE_REGEX = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?$")


def parse_value_to_instant(val: Any, field_name_hint: str = "") -> Optional[Tuple[TimeInstant, TemporalFieldType, bool]]:
    """
    Attempts to parse a raw value into a TimeInstant, TemporalFieldType, and has_tz flag.
    Returns None if parsing fails or value is clearly non-temporal.
    """
    if val is None:
        return None

    # 1. Native datetime / date objects
    if isinstance(val, datetime):
        has_tz = val.tzinfo is not None
        instant = TimeInstant.from_datetime(val)
        return instant, TemporalFieldType.DATETIME, has_tz
    elif isinstance(val, date) and not isinstance(val, bool):
        dt = datetime(val.year, val.month, val.day, tzinfo=timezone.utc)
        instant = TimeInstant.from_datetime(dt)
        return instant, TemporalFieldType.DATE, False

    # 2. String values
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return None

        # Check explicit formats
        if ISO_DATE_REGEX.match(val_str):
            try:
                # Handle 'Z' for python 3.10 if isoformat requires +00:00
                iso_clean = val_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso_clean)
                has_tz = dt.tzinfo is not None
                field_type = TemporalFieldType.DATETIME if "T" in val_str or ":" in val_str else TemporalFieldType.DATE
                return TimeInstant.from_datetime(dt), field_type, has_tz
            except ValueError:
                pass

        if YEAR_MONTH_REGEX.match(val_str):
            try:
                year, month = map(int, val_str.split("-"))
                dt = datetime(year, month, 1, tzinfo=timezone.utc)
                return TimeInstant.from_datetime(dt), TemporalFieldType.YEAR_MONTH, False
            except ValueError:
                pass

        if YEAR_ONLY_REGEX.match(val_str):
            try:
                year = int(val_str)
                if 1800 <= year <= 2100:
                    dt = datetime(year, 1, 1, tzinfo=timezone.utc)
                    return TimeInstant.from_datetime(dt), TemporalFieldType.YEAR, False
            except ValueError:
                pass

        if SLASH_DATE_REGEX.match(val_str):
            try:
                parts = val_str.split(" ")
                date_parts = list(map(int, parts[0].split("/")))
                year, month, day = date_parts[0], date_parts[1], date_parts[2]
                hour, minute, second = 0, 0, 0
                if len(parts) > 1:
                    time_parts = list(map(int, parts[1].split(":")))
                    hour = time_parts[0]
                    minute = time_parts[1]
                    if len(time_parts) > 2:
                        second = time_parts[2]
                dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
                return TimeInstant.from_datetime(dt), TemporalFieldType.DATETIME, False
            except (ValueError, IndexError):
                pass

    # 3. Numeric values (epoch or integer year)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        # Prevent treating generic floats/IDs as epoch unless field name matches or value is in epoch range
        is_name_temporal = bool(TEMPORAL_NAME_REGEX.search(field_name_hint))

        # Year check (1800 - 2100)
        if isinstance(val, int) and 1800 <= val <= 2100:
            dt = datetime(val, 1, 1, tzinfo=timezone.utc)
            return TimeInstant.from_datetime(dt), TemporalFieldType.YEAR, False

        # Epoch seconds check (e.g. 1979 to 2065: 3e8 to 3e9)
        if 3.0e8 <= val <= 3.0e9:
            try:
                return TimeInstant.from_epoch(float(val)), TemporalFieldType.EPOCH_SEC, True
            except (ValueError, OverflowError):
                pass

        # Epoch milliseconds check (e.g. 3e11 to 3e12)
        if 3.0e11 <= val <= 3.0e12:
            try:
                return TimeInstant.from_epoch(float(val) / 1000.0), TemporalFieldType.EPOCH_MS, True
            except (ValueError, OverflowError):
                pass

    return None


def profile_field(field_name: str, values: List[Any], max_samples: int = 100) -> TimeField:
    """
    Profiles a single field across sample values to evaluate temporal confidence and field type.
    """
    non_null_vals = [v for v in values if v is not None]
    if not non_null_vals:
        return TimeField(field_name=field_name, confidence_score=0.0)

    sample_slice = non_null_vals[:max_samples]
    parsed_results = []
    has_tz_count = 0
    type_counts: Dict[TemporalFieldType, int] = {}

    for val in sample_slice:
        res = parse_value_to_instant(val, field_name_hint=field_name)
        if res is not None:
            instant, ftype, has_tz = res
            parsed_results.append(instant)
            type_counts[ftype] = type_counts.get(ftype, 0) + 1
            if has_tz:
                has_tz_count += 1

    success_rate = len(parsed_results) / len(sample_slice)
    if success_rate == 0:
        return TimeField(
            field_name=field_name,
            confidence_score=0.0,
            sample_values=sample_slice[:5],
        )

    # Determine dominant field type
    dominant_type = max(type_counts.items(), key=lambda x: x[1])[0]
    name_bonus = 0.3 if TEMPORAL_NAME_REGEX.search(field_name) else 0.0

    # Base confidence score calculation
    confidence = (success_rate * 0.7) + name_bonus
    confidence = min(1.0, max(0.0, confidence))

    # Penalty for ambiguous low integer years if field name doesn't hint at time
    if dominant_type == TemporalFieldType.YEAR and not TEMPORAL_NAME_REGEX.search(field_name):
        confidence = min(0.6, confidence)

    return TimeField(
        field_name=field_name,
        field_type=dominant_type,
        confidence_score=round(confidence, 3),
        sample_values=sample_slice[:5],
        has_timezone=has_tz_count > 0,
    )


def determine_granularity_and_gaps(
    instants: List[TimeInstant]
) -> Tuple[TemporalGranularity, bool, List[Dict[str, Any]]]:
    """
    Analyzes sorted TimeInstants to estimate sample granularity, regularity, and missing gaps.
    """
    if len(instants) < 2:
        return TemporalGranularity.IRREGULAR, False, []

    # Sort instants by epoch seconds
    sorted_instants = sorted(instants, key=lambda x: x.epoch_seconds)
    deltas = [
        sorted_instants[i + 1].epoch_seconds - sorted_instants[i].epoch_seconds
        for i in range(len(sorted_instants) - 1)
    ]

    # Filter out zero deltas (identical timestamps)
    positive_deltas = [d for d in deltas if d > 0]
    if not positive_deltas:
        return TemporalGranularity.IRREGULAR, False, []

    positive_deltas.sort()
    n = len(positive_deltas)
    median_delta = positive_deltas[n // 2]

    # Map median delta to granularity
    granularity = TemporalGranularity.IRREGULAR
    if median_delta < 5:
        granularity = TemporalGranularity.SECOND
    elif 50 <= median_delta <= 70:
        granularity = TemporalGranularity.MINUTE
    elif 3500 <= median_delta <= 3700:
        granularity = TemporalGranularity.HOUR
    elif 80000 <= median_delta <= 90000:
        granularity = TemporalGranularity.DAY
    elif 600000 <= median_delta <= 620000:
        granularity = TemporalGranularity.WEEK
    elif 2.4e6 <= median_delta <= 2.7e6:
        granularity = TemporalGranularity.MONTH
    elif 7.5e6 <= median_delta <= 8.0e6:
        granularity = TemporalGranularity.QUARTER
    elif 3.1e7 <= median_delta <= 3.2e7:
        granularity = TemporalGranularity.YEAR

    # Compute regularity (variance / stddev of deltas relative to median)
    mean_delta = sum(positive_deltas) / len(positive_deltas)
    variance = sum((d - mean_delta) ** 2 for d in positive_deltas) / len(positive_deltas)
    std_dev = math.sqrt(variance)
    is_regular = (std_dev / mean_delta < 0.15) if mean_delta > 0 else False

    # Gap detection logic
    detected_gaps = []
    if median_delta > 0:
        threshold = max(median_delta * 2.0, median_delta + 1.0)
        for i, delta in enumerate(deltas):
            if delta > threshold:
                missing_steps = int(round(delta / median_delta)) - 1
                gap_start = sorted_instants[i].iso_string
                gap_end = sorted_instants[i + 1].iso_string
                detected_gaps.append({
                    "start": gap_start,
                    "end": gap_end,
                    "gap_duration_seconds": delta,
                    "missing_steps": missing_steps,
                })

    return granularity, is_regular, detected_gaps


def profile_temporal_dataset(
    features_or_records: Any,
    primary_field: Optional[str] = None,
    secondary_field: Optional[str] = None,
) -> TemporalDatasetProfile:
    """
    Profiles a collection of vector features or tabular records for temporal properties.
    Automatically detects primary/secondary time fields, temporal extents, gaps, and confidence.
    """
    if isinstance(features_or_records, dict) and "features" in features_or_records:
        records = features_or_records["features"]
    elif isinstance(features_or_records, list):
        records = features_or_records
    else:
        records = []

    if not records:
        return TemporalDatasetProfile(overall_confidence=0.0)

    # Collect property values per field
    field_values: Dict[str, List[Any]] = {}
    for item in records:
        props = item.get("properties", item) if isinstance(item, dict) else {}
        if isinstance(props, dict):
            for k, v in props.items():
                if k not in field_values:
                    field_values[k] = []
                field_values[k].append(v)

    # Profile candidate time fields
    candidate_fields: List[TimeField] = []
    for fname, vals in field_values.items():
        if primary_field and fname != primary_field and fname != secondary_field:
            continue
        tf = profile_field(fname, vals)
        if tf.confidence_score >= 0.4:
            candidate_fields.append(tf)

    candidate_fields.sort(key=lambda x: x.confidence_score, reverse=True)

    selected_primary: Optional[TimeField] = None
    selected_secondary: Optional[TimeField] = None

    if primary_field and primary_field in field_values:
        selected_primary = profile_field(primary_field, field_values[primary_field])
    elif candidate_fields:
        selected_primary = candidate_fields[0]

    if secondary_field and secondary_field in field_values:
        selected_secondary = profile_field(secondary_field, field_values[secondary_field])
    elif len(candidate_fields) > 1 and selected_primary and candidate_fields[1].field_name != selected_primary.field_name:
        selected_secondary = candidate_fields[1]

    if not selected_primary or selected_primary.confidence_score == 0:
        return TemporalDatasetProfile(overall_confidence=0.0)

    # Parse all values for primary field to build extent & detect gaps
    p_vals = field_values.get(selected_primary.field_name, [])
    total_records = len(records)
    valid_instants: List[TimeInstant] = []

    for v in p_vals:
        res = parse_value_to_instant(v, field_name_hint=selected_primary.field_name)
        if res is not None:
            valid_instants.append(res[0])

    valid_count = len(valid_instants)
    missing_count = total_records - valid_count

    if not valid_instants:
        return TemporalDatasetProfile(
            primary_time_field=selected_primary,
            overall_confidence=0.0,
        )

    valid_instants.sort(key=lambda x: x.epoch_seconds)
    min_time = valid_instants[0]
    max_time = valid_instants[-1]

    extent = TemporalExtent(
        min_time=min_time,
        max_time=max_time,
        total_records=total_records,
        valid_time_records=valid_count,
        missing_time_records=missing_count,
    )

    granularity, is_regular, detected_gaps = determine_granularity_and_gaps(valid_instants)
    overall_conf = selected_primary.confidence_score * (valid_count / max(1, total_records))

    return TemporalDatasetProfile(
        primary_time_field=selected_primary,
        secondary_time_field=selected_secondary,
        temporal_extent=extent,
        granularity=granularity,
        is_regular=is_regular,
        detected_gaps=detected_gaps,
        overall_confidence=round(overall_conf, 3),
        metadata={
            "total_candidates_found": len(candidate_fields),
            "parsed_instants_count": len(valid_instants),
        },
    )


class TemporalProfiler:
    """
    Auto-profiles datasets to identify temporal field, time type, extent, resolution, timezone, gaps, and confidence score.
    """
    def __init__(self) -> None:
        pass

    def profile_dataset(
        self,
        features_or_records: Any,
        primary_field: Optional[str] = None,
        secondary_field: Optional[str] = None,
    ) -> TemporalDatasetProfile:
        return profile_temporal_dataset(features_or_records, primary_field=primary_field, secondary_field=secondary_field)

    @classmethod
    def profile(
        cls,
        features_or_records: Any,
        primary_field: Optional[str] = None,
        secondary_field: Optional[str] = None,
    ) -> TemporalDatasetProfile:
        return cls().profile_dataset(features_or_records, primary_field, secondary_field)

