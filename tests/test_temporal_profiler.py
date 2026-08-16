"""Unit tests for Temporal GIS Domain Models & Profiler."""

import unittest
from datetime import datetime, timezone

from app.services.temporal.models import (
    TimeInstant,
    TimeInterval,
    TemporalDatasetProfile,
    TemporalGranularity,
    TemporalFieldType,
)
from app.services.temporal.profiler import profile_temporal_dataset, parse_value_to_instant, profile_field


class TestTemporalProfiler(unittest.TestCase):

    def test_time_instant_creation(self):
        dt = datetime(2026, 8, 8, 10, 0, 0, tzinfo=timezone.utc)
        instant = TimeInstant.from_datetime(dt)
        self.assertEqual(instant.epoch_seconds, dt.timestamp())
        self.assertIn("2026-08-08", instant.iso_string)
        self.assertEqual(instant.to_datetime(), dt)

    def test_time_interval_validation(self):
        t1 = TimeInstant.from_epoch(1000)
        t2 = TimeInstant.from_epoch(2000)
        interval = TimeInterval(start=t1, end=t2)
        self.assertEqual(interval.duration_seconds, 1000.0)

        with self.assertRaises(ValueError):
            TimeInterval(start=t2, end=t1)

    def test_parse_value_to_instant_iso_string(self):
        res = parse_value_to_instant("2026-05-15T14:30:00Z")
        self.assertIsNotNone(res)
        instant, ftype, has_tz = res
        self.assertEqual(ftype, TemporalFieldType.DATETIME)
        self.assertTrue(has_tz)
        self.assertEqual(instant.to_datetime().year, 2026)

    def test_parse_value_to_instant_epoch(self):
        res = parse_value_to_instant(1700000000.0, field_name_hint="timestamp")
        self.assertIsNotNone(res)
        instant, ftype, has_tz = res
        self.assertEqual(ftype, TemporalFieldType.EPOCH_SEC)
        self.assertTrue(has_tz)

    def test_profile_field_detection(self):
        timestamps = ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"]
        tf = profile_field("recorded_at", timestamps)
        self.assertGreaterEqual(tf.confidence_score, 0.9)
        self.assertEqual(tf.field_type, TemporalFieldType.DATETIME)

        non_temporal = ["apple", "banana", "cherry"]
        tf_non = profile_field("fruit_name", non_temporal)
        self.assertEqual(tf_non.confidence_score, 0.0)

    def test_profile_temporal_dataset_gaps_and_extent(self):
        records = [
            {"properties": {"timestamp": "2026-01-01T00:00:00Z", "val": 10}},
            {"properties": {"timestamp": "2026-01-02T00:00:00Z", "val": 12}},
            {"properties": {"timestamp": "2026-01-03T00:00:00Z", "val": 15}},
            # Gap of 3 missing days
            {"properties": {"timestamp": "2026-01-07T00:00:00Z", "val": 20}},
            {"properties": {"timestamp": "2026-01-08T00:00:00Z", "val": 22}},
        ]

        profile: TemporalDatasetProfile = profile_temporal_dataset(records)
        self.assertIsNotNone(profile.primary_time_field)
        self.assertEqual(profile.primary_time_field.field_name, "timestamp")
        self.assertEqual(profile.granularity, TemporalGranularity.DAY)
        self.assertIsNotNone(profile.temporal_extent)
        self.assertEqual(profile.temporal_extent.total_records, 5)
        self.assertEqual(profile.temporal_extent.valid_time_records, 5)
        self.assertEqual(len(profile.detected_gaps), 1)
        self.assertEqual(profile.detected_gaps[0]["missing_steps"], 3)
        self.assertGreater(profile.overall_confidence, 0.8)

    # ─── #450: sampled vs full count basis ─────────────────────────────────

    def test_profile_large_fully_valid_layer_reports_zero_missing(self):
        """Issue #450: the parse sample is capped at 5000 values but
        missing_time_records was computed against the FULL record count —
        a 6000/6000-valid layer reported missing=1000 and deflated
        confidence. Extrapolating the sampled ratio must report 0 missing."""
        base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        records = []
        for i in range(6000):
            iso = datetime.fromtimestamp(base.timestamp() + i * 60, tz=timezone.utc).isoformat()
            records.append({"properties": {"timestamp": iso, "val": i}})

        profile: TemporalDatasetProfile = profile_temporal_dataset(records)
        extent = profile.temporal_extent
        self.assertEqual(extent.total_records, 6000)
        self.assertEqual(extent.valid_time_records, 6000)
        self.assertEqual(extent.missing_time_records, 0)
        # Confidence consistent with full validity (not scaled by 5000/6000).
        self.assertAlmostEqual(profile.overall_confidence, profile.primary_time_field.confidence_score, places=2)
        # The sample cap is documented in the metadata.
        self.assertTrue(profile.metadata.get("time_sample_truncated"))
        self.assertEqual(profile.metadata.get("time_values_sampled"), 5000)

    def test_profile_large_layer_with_missing_values_extrapolates(self):
        """A layer with real missing timestamps: the sampled ratio is
        extrapolated — missing is proportional, not (total − 5000) + real."""
        base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        records = []
        for i in range(6000):
            props = {"val": i}
            if i % 3 != 0:  # 4000 valid, 2000 invalid timestamps
                props["timestamp"] = datetime.fromtimestamp(
                    base.timestamp() + i * 60, tz=timezone.utc
                ).isoformat()
            else:
                props["timestamp"] = "not-a-date"
            records.append({"properties": props})

        profile: TemporalDatasetProfile = profile_temporal_dataset(records)
        extent = profile.temporal_extent
        self.assertEqual(extent.total_records, 6000)
        # Sampled 5000 → ~2/3 valid → extrapolated ≈ 4000 valid, ≈2000 missing.
        self.assertAlmostEqual(extent.valid_time_records, 4000, delta=100)
        self.assertAlmostEqual(extent.missing_time_records, 2000, delta=100)
        self.assertEqual(extent.valid_time_records + extent.missing_time_records, 6000)

    def test_profile_small_dataset_counts_unchanged(self):
        """Below the sample cap the counts are exact, as before."""
        records = [
            {"properties": {"timestamp": "2026-01-01T00:00:00Z"}},
            {"properties": {"timestamp": "oops"}},
            {"properties": {"other": 1}},  # no timestamp at all
        ]
        profile: TemporalDatasetProfile = profile_temporal_dataset(records)
        extent = profile.temporal_extent
        self.assertEqual(extent.total_records, 3)
        self.assertEqual(extent.valid_time_records, 1)
        self.assertEqual(extent.missing_time_records, 2)
        self.assertFalse(profile.metadata.get("time_sample_truncated", False))


if __name__ == "__main__":
    unittest.main()
