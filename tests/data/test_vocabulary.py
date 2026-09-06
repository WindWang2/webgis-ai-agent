"""Data Vocabulary V3 —— 词表、映射与生命周期状态机测试。"""
import pytest

from app.lib.data import vocabulary as vocab


class TestCategories:
    def test_all_base_categories_present(self):
        cats = set(vocab.all_categories())
        for expected in (
            "vector", "raster", "table", "point_cloud", "network", "timeseries",
            "matrix", "model", "statistics", "chart_data", "report", "map_export",
            "archive", "metadata", "collection", "unknown",
        ):
            assert expected in cats

    def test_register_extension_category(self):
        vocab.register_category("seismic_cube")
        assert "seismic_cube" in vocab.all_categories()
        assert vocab.coerce_category("seismic_cube") == "seismic_cube"
        with pytest.raises(vocab.VocabularyError):
            vocab.register_category("seismic_cube")  # 重复
        with pytest.raises(vocab.VocabularyError):
            vocab.register_category("Has-Dash")  # 非法 token

    def test_coerce_unknown_returns_none(self):
        assert vocab.coerce_category("definitely-not-a-category") is None
        assert vocab.coerce_category(None) is None
        assert vocab.coerce_category("") is None


class TestFineTypeMapping:
    @pytest.mark.parametrize(
        "fine,expected",
        [
            ("feature_collection", "vector"),
            ("poi_feature_set", "vector"),
            ("density_surface", "raster"),
            ("terrain_surface", "raster"),
            ("stats_table", "table"),
            ("od_matrix", "matrix"),
            ("network_graph", "network"),
            ("chart_spec", "chart_data"),
        ],
    )
    def test_fine_to_category(self, fine, expected):
        cat = vocab.category_for_fine_type(fine)
        assert cat is not None and cat.value == expected

    def test_unknown_fine_type_is_none(self):
        assert vocab.category_for_fine_type("mystery_type") is None

    def test_geometry_kind_fallback(self):
        assert vocab.category_for_geometry_kind("point").value == "vector"
        assert vocab.category_for_geometry_kind("raster").value == "raster"
        assert vocab.category_for_geometry_kind("network").value == "network"
        assert vocab.category_for_geometry_kind("unknown") is None

    def test_db_type_mapping(self):
        assert vocab.category_from_any(db_type="raster").value == "raster"
        assert vocab.category_from_any(db_type="analysis").value == "vector"

    def test_evidence_priority(self):
        # 细类型证据优先于 DB 类型
        got = vocab.category_from_any(fine_type="stats_table", db_type="vector")
        assert got.value == "table"
        # 全未知 → None（不虚构）
        assert vocab.category_from_any() is None


class TestLifecycle:
    def test_happy_path_transitions(self):
        path = [
            ("declared", "ingesting"),
            ("ingesting", "available"),
            ("available", "profiling"),
            ("profiling", "ready"),
            ("ready", "materializing"),
            ("materializing", "derived"),
        ]
        for cur, tgt in path:
            assert vocab.can_transition(cur, tgt), f"{cur} → {tgt} 应合法"

    def test_terminal_state_has_no_outgoing(self):
        assert vocab.can_transition("deleted", "deleted")  # 幂等
        assert not vocab.can_transition("deleted", "ready")
        assert vocab.terminal_states() == frozenset({vocab.LifecycleState.DELETED})

    def test_error_recovery_paths(self):
        assert vocab.can_transition("error", "ingesting")
        assert vocab.can_transition("error", "available")
        assert not vocab.can_transition("error", "ready")  # 必须经 available

    def test_stale_revalidation(self):
        assert vocab.can_transition("stale", "ready")
        assert not vocab.can_transition("superseded", "ready")  # 替换不可复活

    def test_invalid_state_names(self):
        assert not vocab.can_transition("bogus", "ready")
        assert not vocab.can_transition("ready", "bogus")

    def test_gc_eligible_states(self):
        eligible = vocab.gc_eligible_states()
        assert vocab.LifecycleState.STALE in eligible
        assert vocab.LifecycleState.READY not in eligible

    def test_session_status_projection(self):
        assert vocab.lifecycle_from_session_status("valid") is vocab.LifecycleState.READY
        assert vocab.lifecycle_from_session_status("stale") is vocab.LifecycleState.STALE
        assert vocab.lifecycle_from_session_status("expired") is vocab.LifecycleState.DELETED
        assert vocab.lifecycle_from_session_status("superseded") is vocab.LifecycleState.SUPERSEDED
        assert vocab.lifecycle_from_session_status("failed") is vocab.LifecycleState.ERROR
        assert vocab.lifecycle_from_session_status("nonsense") is None

    def test_db_state_projection(self):
        assert (
            vocab.lifecycle_from_db_state(has_payload=True, content_fingerprint="fp")
            is vocab.LifecycleState.READY
        )
        assert (
            vocab.lifecycle_from_db_state(has_payload=True, content_fingerprint=None)
            is vocab.LifecycleState.AVAILABLE
        )
        assert (
            vocab.lifecycle_from_db_state(has_payload=False, content_fingerprint=None)
            is vocab.LifecycleState.DECLARED
        )
        assert (
            vocab.lifecycle_from_db_state(has_payload=True, deleted=True)
            is vocab.LifecycleState.DELETED
        )

    def test_promotion_status_projection(self):
        assert vocab.lifecycle_from_promotion_status("promoted") is vocab.LifecycleState.READY
        assert vocab.lifecycle_from_promotion_status("session_expired") is vocab.LifecycleState.STALE


class TestRolesAndQuality:
    def test_all_goal_roles_present(self):
        roles = {r.value for r in vocab.LogicalRole}
        for expected in (
            "source", "reference", "boundary", "mask", "observation", "training",
            "validation", "prediction", "constraint", "factor", "intermediate",
            "derived", "result", "visualization", "export", "temporary",
        ):
            assert expected in roles

    def test_quality_status_composition(self):
        assert vocab.QualityStatus.from_issues(True, False, False) is vocab.QualityStatus.BLOCKED
        assert vocab.QualityStatus.from_issues(False, True, True) is vocab.QualityStatus.REPAIRABLE
        assert vocab.QualityStatus.from_issues(False, False, True) is vocab.QualityStatus.WARNING
        assert vocab.QualityStatus.from_issues(False, False, False) is vocab.QualityStatus.VALID


class TestPersistencePolicies:
    def test_policy_to_tier(self):
        assert vocab.policy_tier(vocab.MaterializationPolicy.EPHEMERAL) is vocab.PersistenceTier.EPHEMERAL
        assert vocab.policy_tier(vocab.MaterializationPolicy.CACHED) is vocab.PersistenceTier.SESSION
        assert (
            vocab.policy_tier(vocab.MaterializationPolicy.WORKSPACE_PERSISTENT)
            is vocab.PersistenceTier.WORKSPACE
        )
        assert (
            vocab.policy_tier(vocab.MaterializationPolicy.USER_PERSISTENT)
            is vocab.PersistenceTier.PERSISTENT
        )
        assert vocab.policy_tier(vocab.MaterializationPolicy.EXPORTED) is vocab.PersistenceTier.PERSISTENT
