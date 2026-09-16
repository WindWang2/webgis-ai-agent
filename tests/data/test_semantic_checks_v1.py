"""语义和谐化检测器回归锁（DQH V1）：timezone / unit / role / admin。

不变式：
- 全部确定性：同输入同 issues（无墙钟、无随机）；
- 有界：证据样本数受 MAX 帽约束，绝不携带全量数据；
- 诚实：证据不足 → 不发 issue（unknown ≠ 不满足）；admin 覆盖范围外的
  值域 → 检查声明为未执行（checks_not_run），绝不把「表没收录」当「数据错」；
- 单位：欠定（UNIT_AMBIGUOUS）与名实矛盾（INCONSISTENT_UNIT）分流正确；
- 角色：仅名称级（metadata_derived）绑定 measure 族 → FIELD_ROLE_AMBIGUOUS；
  rule_derived（样本印证）→ 不发；样本反证已绑定角色 → 发；
- guardrails 边界：本检测器只做数据质量侧值域投影，绝不改写
  admin_division_verifier 自身的层级校验语义（verify_code 只读复用）。
"""
from __future__ import annotations

from app.lib.data.quality import QualityIssueCode
from app.services.data_quality.semantic_checks import (
    CHECK_TIMEZONE,
    detect_admin_mismatch,
    detect_field_role_ambiguity,
    detect_timezone_missing,
    detect_unit_ambiguity,
    evaluate_semantic_checks,
)

from app.lib.data.profile import FieldProfile


def _fp(name: str, *, dtype: str = "number", samples=None, unit_hint: str = "",
        temporal_hint: bool = False, mn=None, mx=None) -> FieldProfile:
    return FieldProfile(
        name=name, dtype=dtype, samples=list(samples or []),
        unit_hint=unit_hint, temporal_hint=temporal_hint, min=mn, max=mx,
    )


CODES = {i.code for i in (
    detect_timezone_missing(_fp("ts", temporal_hint=True,
                                samples=["2024-01-01 08:00:00", "2024-01-02 09:30:00"]))
    + detect_unit_ambiguity(_fp("面积", samples=[1234.5, 88.2]))
    + detect_admin_mismatch("省份", ["浙扛省", "江苏省"])
    + detect_field_role_ambiguity(
        {"count": _fp("count", samples=[])},
        semantic_profile=None,
    ))}


class TestTimezoneMissing:
    def test_naive_datetimes_emit(self):
        issues = detect_timezone_missing(_fp(
            "采集时间", dtype="string", temporal_hint=True,
            samples=["2024-01-01 08:00:00", "2024-06-30 23:10:05"]))
        codes = [i.code for i in issues]
        assert QualityIssueCode.TIMEZONE_MISSING in codes
        assert issues[0].field == "采集时间"
        assert issues[0].repairable is True

    def test_all_aware_no_issue(self):
        issues = detect_timezone_missing(_fp(
            "ts", dtype="string", temporal_hint=True,
            samples=["2024-01-01T08:00:00+08:00", "2024-01-01T00:00:00Z"]))
        assert issues == []

    def test_mixed_offset_emit(self):
        issues = detect_timezone_missing(_fp(
            "ts", dtype="string", temporal_hint=True,
            samples=["2024-01-01T08:00:00+08:00", "2024-01-02 09:00:00"]))
        assert [i.code for i in issues] == [QualityIssueCode.TIMEZONE_MISSING]

    def test_date_only_no_issue(self):
        # 纯日期没有时刻语义 → 无时区欠定问题，绝不误报。
        issues = detect_timezone_missing(_fp(
            "日期", dtype="string", temporal_hint=True,
            samples=["2024-01-01", "2024-01-02", "2023/12/31"]))
        assert issues == []

    def test_non_temporal_no_issue(self):
        # 非时间字段（无 temporal_hint）即使样本长得像时间也不在口径内。
        issues = detect_timezone_missing(_fp("备注", dtype="string",
                                             samples=["2024-01-01 08:00:00"]))
        assert issues == []

    def test_epoch_numbers_no_issue(self):
        # epoch 数值按约定即 UTC，不在本检查口径内（不虚构问题）。
        issues = detect_timezone_missing(_fp("ts", temporal_hint=True,
                                             samples=[1700000000, 1700000001]))
        assert issues == []

    def test_deterministic(self):
        fp = _fp("t", dtype="string", temporal_hint=True, samples=["2024-01-01 08:00:00"] * 5)
        assert detect_timezone_missing(fp) == detect_timezone_missing(fp)


class TestUnitAmbiguity:
    def test_quantity_without_marker_emits_ambiguous(self):
        issues = detect_unit_ambiguity(_fp("面积", samples=[1234.5, 88.2], mn=88.2, mx=1234.5))
        assert [i.code for i in issues] == [QualityIssueCode.UNIT_AMBIGUOUS]
        assert issues[0].severity == "info"
        assert issues[0].repairable is True

    def test_explicit_marker_no_issue(self):
        issues = detect_unit_ambiguity(_fp("面积_km2", samples=[1234.5, 88.2]))
        assert issues == []

    def test_unit_hint_no_issue(self):
        issues = detect_unit_ambiguity(_fp("面积", unit_hint="km2", samples=[1.0]))
        assert issues == []

    def test_ratio_contradiction_is_inconsistent_not_ambiguous(self):
        # 名称说比例（率/ratio），值域是百分制 → 名实矛盾（既有码收获生产者）。
        issues = detect_unit_ambiguity(_fp("老龄化率", samples=[12.3, 87.3], mn=12.3, mx=87.3))
        codes = [i.code for i in issues]
        assert QualityIssueCode.INCONSISTENT_UNIT in codes
        assert QualityIssueCode.UNIT_AMBIGUOUS not in codes

    def test_ratio_in_unit_interval_no_issue(self):
        issues = detect_unit_ambiguity(_fp("绿化率", samples=[0.12, 0.87], mn=0.12, mx=0.87))
        assert issues == []

    def test_non_numeric_no_issue(self):
        issues = detect_unit_ambiguity(_fp("面积", dtype="string", samples=["大", "小"]))
        assert issues == []

    def test_no_quantity_semantics_no_issue(self):
        issues = detect_unit_ambiguity(_fp("备注编号", dtype="number", samples=[1.0, 2.0]))
        assert issues == []


class TestFieldRoleAmbiguity:
    def _sem(self, roles_by_field, conf_by_field):
        """构造 SemanticDatasetProfile 鸭子（detector 只读 field_roles）。"""
        from app.lib.gis.semantic_profile import (
            FieldRoleAssignment, RoleConfidence, SemanticDatasetProfile,
        )

        assignments = [
            FieldRoleAssignment(
                field=f,
                roles=[r for group in rs for r in group],
                confidence=RoleConfidence(conf_by_field[f]),
            )
            for f, rs in roles_by_field.items()
        ]
        return SemanticDatasetProfile(field_roles=assignments)

    def test_name_only_measure_binding_emits(self):
        sem = self._sem(
            {"count": (["count_measure"],)},
            {"count": "metadata_derived"},
        )
        fields = {"count": _fp("count", samples=[])}
        issues = detect_field_role_ambiguity(fields, semantic_profile=sem)
        assert [i.code for i in issues] == [QualityIssueCode.FIELD_ROLE_AMBIGUOUS]
        assert issues[0].repairable is False  # 澄清语义，不是数据变换

    def test_rule_derived_binding_no_issue(self):
        sem = self._sem(
            {"count": (["count_measure"],)},
            {"count": "rule_derived"},
        )
        fields = {"count": _fp("count", samples=[1, 2, 3])}
        assert detect_field_role_ambiguity(fields, semantic_profile=sem) == []

    def test_sample_contradicts_name_bound_role(self):
        # 名字暗示 count，但样本有负数/非整数 → 名实冲突，必须披露。
        sem = self._sem(
            {"num": (["count_measure"],)},
            {"num": "metadata_derived"},
        )
        fields = {"num": _fp("num", samples=[3, -2, 1.5])}
        issues = detect_field_role_ambiguity(fields, semantic_profile=sem)
        assert [i.code for i in issues] == [QualityIssueCode.FIELD_ROLE_AMBIGUOUS]

    def test_competing_measure_roles_emit(self):
        sem = self._sem(
            {"v": (["count_measure", "ratio_measure"],)},
            {"v": "rule_derived"},
        )
        fields = {"v": _fp("v", samples=[1, 2])}
        issues = detect_field_role_ambiguity(fields, semantic_profile=sem)
        assert [i.code for i in issues] == [QualityIssueCode.FIELD_ROLE_AMBIGUOUS]

    def test_non_measure_roles_not_flagged(self):
        sem = self._sem(
            {"name": (["label"],), "城市": (["admin_dimension"],)},
            {"name": "metadata_derived", "城市": "metadata_derived"},
        )
        fields = {"name": _fp("name", dtype="string", samples=["a"]),
                  "城市": _fp("城市", dtype="string", samples=["杭州"])}
        assert detect_field_role_ambiguity(fields, semantic_profile=sem) == []

    def test_no_semantic_profile_no_issue(self):
        # 无语义画像 → 无绑定事实 → 不虚构歧义（unknown ≠ 不满足）。
        fields = {"count": _fp("count", samples=[])}
        assert detect_field_role_ambiguity(fields, semantic_profile=None) == []


class TestAdminMismatch:
    def test_invalid_code_emits_with_suggestion(self):
        issues = detect_admin_mismatch("adcode", ["110004", "110000"])
        assert issues, "结构非法码应产出 admin_mismatch"
        ev = issues[0].evidence
        assert ev["invalid_count"] >= 1
        assert any("疑似" in s for s in ev.get("suggestions", []))

    def test_valid_codes_no_issue(self):
        assert detect_admin_mismatch("adcode", ["110000", "330000", "440100"]) == []

    def test_valid_province_names_no_issue(self):
        issues = detect_admin_mismatch("省份", ["浙江省", "江苏省", "广东省"])
        assert issues == []

    def test_name_typo_emits_with_nearest_suggestion(self):
        issues = detect_admin_mismatch("省份", ["浙扛省", "江苏省"])
        assert [i.code for i in issues] == [QualityIssueCode.ADMIN_MISMATCH]
        assert any("浙江" in s for s in issues[0].evidence.get("suggestions", []))

    def test_unresolvable_level_honest_skip(self):
        # 区县级名称不在省/市级码表覆盖内：零解析 + 无码型 → 检查诚实跳过，
        # 绝不把「表没收录」报成「数据错」。
        issues = detect_admin_mismatch("乡镇", ["西湖街道", "古荡街道", "文新街道"])
        assert issues == []

    def test_non_admin_field_no_issue(self):
        assert detect_admin_mismatch("备注", ["hello", "world"]) == []

    def test_deterministic(self):
        a = detect_admin_mismatch("adcode", ["110004", "110000"])
        b = detect_admin_mismatch("adcode", ["110004", "110000"])
        assert a == b


class TestAggregator:
    def test_aggregate_collects_all_families(self):
        fields = {
            "ts": _fp("ts", dtype="string", temporal_hint=True,
                      samples=["2024-01-01 08:00:00"]),
            "面积": _fp("面积", samples=[12.0, 3.0]),
            "adcode": _fp("adcode", dtype="string", samples=["110004"]),
        }
        issues, run, not_run = evaluate_semantic_checks(fields=fields)
        codes = {i.code for i in issues}
        assert QualityIssueCode.TIMEZONE_MISSING in codes
        assert QualityIssueCode.UNIT_AMBIGUOUS in codes
        assert QualityIssueCode.ADMIN_MISMATCH in codes
        assert CHECK_TIMEZONE in run
        # 空/无证据字段不产生任何族的问题（诚实缺省）。
        assert not any(i.field == "ts" and i.code == QualityIssueCode.ADMIN_MISMATCH
                       for i in issues)

    def test_feature_off_switches(self):
        fields = {"ts": _fp("ts", temporal_hint=True,
                            samples=["2024-01-01 08:00:00"])}
        issues, run, not_run = evaluate_semantic_checks(
            fields=fields, check_timezone=False)
        assert issues == []
        assert CHECK_TIMEZONE in not_run

    def test_empty_fields_clean(self):
        issues, run, not_run = evaluate_semantic_checks(fields={})
        assert issues == []


class TestReviewAdminMixedLevel:
    """review P3-2：混层级列（省/市/区/街道并存）不得因单一解析成功而误报。"""

    def test_mixed_level_column_not_flagged(self):
        # 一个省级名解析成功 ≠ 整列是省/市级：区县/街道值在参考表覆盖外，
        # 解析比例过低时检查诚实跳过（表没收录 ≠ 数据错）。
        issues = detect_admin_mismatch(
            "区县名称", ["浙江省", "西湖区", "文新街道", "杭州市"])
        assert issues == []

    def test_majority_resolved_with_variant_still_flagged(self):
        # 解析占多数（列层级已证明）时，变体名仍是真发现。
        issues = detect_admin_mismatch(
            "省份", ["浙江省", "江苏省", "广东省", "浙扛省"])
        assert [i.code for i in issues] == [QualityIssueCode.ADMIN_MISMATCH]
