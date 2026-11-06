"""MapSpec v1.8 temporal 契约（TemporalViewContract.v1）schema 测试。

科学诚实不变量（GOAL 03 M1 Oracle 1）：
- frames 仅真实观测桶：enabled=True 必须 ≥2 帧（单期拒绝 = 防御纵深）；
- 帧区间 [start,end) 不重叠、cursor/compare 必须指向真实帧；
- 时区必须 IANA 可解析（不得以用户本地时区伪造）；
- extent 必须覆盖帧集合（观测范围不窄于数据）；
- 固定分级 breaks 严格递升；
- 纯 additive：存量 spec（无 temporal）指纹逐位不变、1.7→1.8 identity。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lib.cartography.mapspec_schema import (
    KNOWN_VERSIONS,
    LATEST_VERSION,
    MapSpecTemporalConfig,
    TemporalClassification,
    TemporalCompareConfig,
    TemporalFrame,
    TemporalLayerBinding,
    TemporalPlaybackConfig,
    parse_mapspec,
)
from app.lib.cartography.quality_loop import cartographic_fingerprint


def _frame(fid: str, year: int, count: int = 5) -> TemporalFrame:
    return TemporalFrame(
        id=fid, label=str(year),
        start=f"{year}-01-01T00:00:00+00:00",
        end=f"{year + 1}-01-01T00:00:00+00:00",
        feature_count=count,
    )


def _config(**overrides) -> dict:
    base = dict(
        enabled=True,
        layers=[TemporalLayerBinding(layer_id="schools", source_ref="ref:schools")],
        time_field="year",
        time_semantics="snapshot",
        time_representation="year_int",
        timezone="Asia/Shanghai",
        granularity="year",
        extent=("2021-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
        frames=[_frame("f-2021", 2021), _frame("f-2022", 2022), _frame("f-2023", 2023)],
        cursor_frame="f-2022",
        playback=TemporalPlaybackConfig(playing=True, rate=2.0),
        source_ref="ref:schools",
        content_revision=3,
    )
    base.update(overrides)
    return base


class TestVersionBump:
    def test_known_versions_include_18(self):
        assert "1.8" in KNOWN_VERSIONS
        assert KNOWN_VERSIONS.index(LATEST_VERSION) >= KNOWN_VERSIONS.index("1.8")

    def test_upgrade_path_17_to_18_identity(self):
        from app.lib.cartography import mapspec_schema as ms
        assert ms._find_upgrade_path("1.7", "1.8") == [("1.7", "1.8")]
        spec = {"version": "1.7", "view": {"center": [104.0, 30.6], "zoom": 10}}
        result = parse_mapspec(spec, target_version="1.8")
        assert result.migrated is True
        assert result.effective_version == "1.8"
        # identity 迁移不改写文档字节。
        assert result.document["view"] == spec["view"]

    def test_legacy_spec_fingerprint_unchanged_by_temporal_absence(self):
        # additive 纪律：无 temporal 的 spec，投影**不含** temporal 键 ——
        # 与 master 的 cartographic_projection 输出键集一致（指纹逐位不变；
        # 对照组：加 temporal 后键集出现且指纹变化）。
        from app.lib.cartography.quality_loop import cartographic_projection

        spec = {"version": "1.7", "layers": [{"id": "a", "type": "circle"}]}
        projection = cartographic_projection(spec)
        assert "temporal" not in projection  # 在场才投影（存量指纹稳定）
        spec_with = dict(spec)
        spec_with["temporal"] = {"enabled": True}
        assert "temporal" in cartographic_projection(spec_with)
        assert cartographic_fingerprint(spec) != cartographic_fingerprint(spec_with)


class TestContractInvariants:
    def test_valid_config_roundtrip(self):
        cfg = MapSpecTemporalConfig(**_config())
        assert cfg.schema_version == "temporal_view.v1"
        assert cfg.missing_policy == "strict"
        assert cfg.user_pinned is False
        assert cfg.render_path == "client_filter"

    def test_single_period_enabled_is_refused(self):
        # E2E-3 锚点：单期数据不得伪造动画（schema 层防御纵深）。
        with pytest.raises(ValidationError, match=">=2 observed frames"):
            MapSpecTemporalConfig(**_config(
                frames=[_frame("f-2023", 2023)],
                extent=("2023-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
                cursor_frame="f-2023",
            ))

    def test_single_period_disabled_with_one_frame_allowed_for_audit(self):
        # disabled 状态允许单帧存档（作为探测结果的诚实记录，不驱动动画）。
        cfg = MapSpecTemporalConfig(**_config(
            enabled=False,
            frames=[_frame("f-2023", 2023)],
            extent=("2023-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
            cursor_frame="f-2023",
            playback=TemporalPlaybackConfig(playing=False),
        ))
        assert cfg.enabled is False

    def test_overlapping_frames_refused(self):
        bad = TemporalFrame(
            id="f-x", label="2022", start="2022-01-01T00:00:00+00:00",
            end="2023-06-01T00:00:00+00:00",
        )
        with pytest.raises(ValidationError, match="overlap"):
            MapSpecTemporalConfig(**_config(
                frames=[_frame("f-2021", 2021), bad, _frame("f-2023", 2023)],
            ))

    def test_half_open_interval_required(self):
        with pytest.raises(ValidationError, match="start must be < end"):
            MapSpecTemporalConfig(**_config(frames=[
                TemporalFrame(
                    id="f-zero", label="x",
                    start="2022-01-01T00:00:00+00:00",
                    end="2022-01-01T00:00:00+00:00",
                ),
                _frame("f-2023", 2023),
            ]))

    def test_cursor_must_reference_real_frame(self):
        with pytest.raises(ValidationError, match="cursor_frame"):
            MapSpecTemporalConfig(**_config(cursor_frame="f-2099"))

    def test_compare_frame_must_differ_from_cursor(self):
        with pytest.raises(ValidationError, match="differ from cursor_frame"):
            MapSpecTemporalConfig(**_config(compare=TemporalCompareConfig(
                enabled=True, frame_id_b="f-2022",
            )))

    def test_compare_with_shared_classification_breaks_ascending(self):
        cfg = MapSpecTemporalConfig(**_config(compare=TemporalCompareConfig(
            enabled=True, frame_id_b="f-2023",
            classification=TemporalClassification(
                breaks=[0, 10, 20, 50], scope="shared", method="quantile_k4_union",
            ),
        )))
        assert cfg.compare.classification.scope == "shared"

    def test_compare_breaks_not_strictly_ascending_refused(self):
        with pytest.raises(ValidationError, match="strictly ascending"):
            MapSpecTemporalConfig(**_config(compare=TemporalCompareConfig(
                enabled=True, frame_id_b="f-2023",
                classification=TemporalClassification(breaks=[0, 20, 20, 50]),
            )))

    def test_fake_timezone_refused(self):
        with pytest.raises(ValidationError, match="IANA"):
            MapSpecTemporalConfig(**_config(timezone="Mars/Olympus"))

    def test_extent_must_cover_frames(self):
        with pytest.raises(ValidationError, match="extent must cover"):
            MapSpecTemporalConfig(**_config(
                extent=("2022-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
            ))

    def test_representation_vocabulary_enforced(self):
        with pytest.raises(ValidationError):
            MapSpecTemporalConfig(**_config(time_representation="magic_string"))

    def test_playback_rate_bounds(self):
        with pytest.raises(ValidationError):
            MapSpecTemporalConfig(**_config(
                playback=TemporalPlaybackConfig(playing=True, rate=16.0),
            ))


class TestFingerprintProjection:
    def test_cursor_change_changes_cartographic_fingerprint(self):
        # displayed_at 门控依赖：时间游标变化必须是可观察 spec 变更。
        doc_a = {"version": "1.8", "temporal": MapSpecTemporalConfig(**_config(cursor_frame="f-2021")).model_dump(mode="json")}
        doc_b = {"version": "1.8", "temporal": MapSpecTemporalConfig(**_config(cursor_frame="f-2023")).model_dump(mode="json")}
        assert cartographic_fingerprint(doc_a) != cartographic_fingerprint(doc_b)

    def test_parse_validates_temporal_branch(self):
        cfg = MapSpecTemporalConfig(**_config())
        doc = {"version": "1.8", "temporal": cfg.model_dump(mode="json")}
        result = parse_mapspec(doc)
        assert result.valid is True
        assert not result.unknown_fields

    def test_parse_reports_invalid_temporal(self):
        bad = MapSpecTemporalConfig(**_config(
            frames=[_frame("f-2023", 2023)],
            extent=("2023-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
            cursor_frame="f-2023",
            enabled=False,
            playback=TemporalPlaybackConfig(playing=False),
        )).model_dump(mode="json")
        bad["frames"][0]["feature_count"] = -1  # 负要素数 = invalid
        doc = {"version": "1.8", "temporal": bad}
        result = parse_mapspec(doc)
        assert result.valid is False
        assert any(d.path.startswith("temporal") for d in result.invalid_fields)
