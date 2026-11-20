"""G44 纯验证内核 oracle 测试 —— 人工真值表（WP12）。

预期值独立手算/按声明推导，绝不从生产 validator 生成预期。
覆盖 F04–F10、F37 的核心语义。
"""
from __future__ import annotations


import pytest

from app.services.field_contracts.validator import (
    ContractDocumentError,
    assess_feature_collection,
    check_feature,
    normalize_document,
    validate_candidate_state,
)


def _doc(fields, cross=None):
    return {"version": 1, "fields": fields, "cross_rules": cross or []}


def _codes(findings):
    return [f["code"] for f in findings]


def _one(fields, cross=None, props=None):
    """单要素检查的便捷封装：返回该要素的违规码列表。"""
    nc = normalize_document(_doc(fields, cross))
    return _codes(check_feature(props or {}, nc))


# ─── F04：required × nullable 组合的三态分离 ─────────────────────────────


class TestPresenceSemantics:
    def test_matrix_hand_computed(self):
        # 手工真值表：required / nullable 对 (missing, null, "") 的固定结果
        # string 字段 empty_string 默认 reject：
        #  required=F, nullable=F → missing:OK null:violation "":empty_string_rejected
        assert _one([{"field": "a", "type": "string"}], props={}) == []
        assert _one([{"field": "a", "type": "string"}], props={"a": None}) == [
            "null_not_allowed"
        ]
        assert _one([{"field": "a", "type": "string"}], props={"a": ""}) == [
            "empty_string_rejected"
        ]
        #  required=T, nullable=F → missing:required_missing null:null_not_allowed
        assert _one([{"field": "a", "type": "string", "required": True}], props={}) == [
            "required_missing"
        ]
        assert _one(
            [{"field": "a", "type": "string", "required": True}], props={"a": None}
        ) == ["null_not_allowed"]
        #  required=T, nullable=T → missing:required_missing null:OK
        assert _one(
            [{"field": "a", "type": "string", "required": True, "nullable": True}],
            props={},
        ) == ["required_missing"]
        assert _one(
            [{"field": "a", "type": "string", "required": True, "nullable": True}],
            props={"a": None},
        ) == []
        #  empty_string=allow + 枚举含 "" → 空串放行
        assert _one(
            [{"field": "a", "type": "string", "empty_string": "allow", "enum": ["", "x"]}],
            props={"a": ""},
        ) == []
        #  empty_string=allow 但枚举不含 "" → 作者期拒绝（文档校验）
        with pytest.raises(ContractDocumentError) as ei:
            normalize_document(
                _doc([{"field": "a", "type": "string", "empty_string": "allow", "enum": ["x"]}])
            )
        assert any("empty_string=allow" in e for e in ei.value.errors)


# ─── F05：number 字段的严格类型 ──────────────────────────────────────────


class TestStrictNumber:
    F = [{"field": "n", "type": "number"}]

    def test_bool_string_nan_inf_rejected_hand_table(self):
        # true → type_mismatch（bool≠number）
        assert _one(self.F, props={"n": True}) == ["type_mismatch"]
        # 字符串 "1" → type_mismatch（无字符串转数字）
        assert _one(self.F, props={"n": "1"}) == ["type_mismatch"]
        # NaN / Inf → non_finite_number
        assert _one(self.F, props={"n": float("nan")}) == ["non_finite_number"]
        assert _one(self.F, props={"n": float("inf")}) == ["non_finite_number"]
        assert _one(self.F, props={"n": float("-inf")}) == ["non_finite_number"]
        # 合法数值（int/float 均可）
        assert _one(self.F, props={"n": 1}) == []
        assert _one(self.F, props={"n": 1.5}) == []
        # integer 声明：1.0 整值 float 可，1.5 拒
        assert _one([{"field": "n", "type": "integer"}], props={"n": 1.0}) == []
        assert _one([{"field": "n", "type": "integer"}], props={"n": 1.5}) == ["not_integer"]
        assert _one([{"field": "n", "type": "integer"}], props={"n": True}) == ["type_mismatch"]


# ─── F06：min/max 相等、越界、开闭区间 ───────────────────────────────────


class TestRangeBounds:
    def test_closed_interval_hand_table(self):
        f = [{"field": "n", "type": "number", "min": 0, "max": 10}]
        # 闭区间：0 与 10 含边界；-0.0001 / 10.0001 越界
        assert _one(f, props={"n": 0}) == []
        assert _one(f, props={"n": 10}) == []
        assert _one(f, props={"n": -0.0001}) == ["number_range"]
        assert _one(f, props={"n": 10.0001}) == ["number_range"]

    def test_open_interval_exclusive_bounds(self):
        f = [{"field": "n", "type": "number", "min": 0, "max": 10,
              "exclusive_min": True, "exclusive_max": True}]
        # 开区间 (0,10)：端点拒绝，内侧放行
        assert _one(f, props={"n": 0}) == ["number_range"]
        assert _one(f, props={"n": 10}) == ["number_range"]
        assert _one(f, props={"n": 0.0001}) == []
        assert _one(f, props={"n": 9.9999}) == []
        # 半开 [0,10)：exclusive_max only
        f2 = [{"field": "n", "type": "number", "min": 0, "max": 10, "exclusive_max": True}]
        assert _one(f2, props={"n": 0}) == []
        assert _one(f2, props={"n": 10}) == ["number_range"]

    def test_only_min(self):
        f = [{"field": "n", "type": "number", "min": 0}]
        assert _one(f, props={"n": 10**9}) == []
        assert _one(f, props={"n": -1}) == ["number_range"]


# ─── F07：日期闰年 / datetime 时区 ───────────────────────────────────────


class TestDateDatetime:
    def test_leap_year_hand_table(self):
        f = [{"field": "d", "type": "date"}]
        assert _one(f, props={"d": "2024-02-29"}) == []      # 闰年合法
        assert _one(f, props={"d": "2023-02-29"}) == ["invalid_date"]  # 平年 2/29 拒
        assert _one(f, props={"d": "2024-13-01"}) == ["invalid_date"]
        assert _one(f, props={"d": "2024-1-1"}) == ["invalid_date"]    # 非严格格式
        assert _one(f, props={"d": 20240101}) == ["type_mismatch"]

    def test_datetime_offset_required(self):
        f = [{"field": "t", "type": "datetime"}]
        # 无时区 → 拒（不猜测 UTC）
        assert _one(f, props={"t": "2026-11-15T08:00:00"}) == ["datetime_offset_required"]
        # 显式 offset（Z 或 ±HH:MM）合法
        assert _one(f, props={"t": "2026-11-15T08:00:00Z"}) == []
        assert _one(f, props={"t": "2026-11-15T08:00:00+08:00"}) == []
        # 小数秒 ≤6 位合法
        assert _one(f, props={"t": "2026-11-15T08:00:00.123456+00:00"}) == []


# ─── F08：超 JS 安全整数（bigint）无损 ──────────────────────────────────


class TestBigIntLossless:
    BIG = 9_007_199_254_740_993  # 2^53 + 1 —— float 会舍入到 2^53+2

    def test_bigint_integer_passes_and_range_exact(self):
        f = [{"field": "id", "type": "integer"}]
        assert _one(f, props={"id": self.BIG}) == []
        # 边界精确比较：min=2^53+1 含 BigInt 自身；2^53（小 1）越界
        f2 = [{"field": "id", "type": "integer", "min": self.BIG}]
        assert _one(f2, props={"id": self.BIG}) == []
        assert _one(f2, props={"id": self.BIG - 1}) == ["number_range"]

    def test_bigint_uniqueness_not_rounded(self):
        # 2^53+1 与 2^53+2 在 float 下都会舍入到同值；整数键必须区分 → 不冲突
        nc = normalize_document(
            _doc([{"field": "id", "type": "integer"}],
                 [{"rule_id": "u", "kind": "unique", "fields": ["id"]}])
        )
        fc = {"features": [
            {"properties": {"id": 2**53 + 1}},
            {"properties": {"id": 2**53 + 2}},
        ]}
        out = assess_feature_collection(fc, nc)
        assert out.violations_total == 0
        # 相同 BigInt → 冲突
        fc2 = {"features": [
            {"properties": {"id": 2**53 + 1}},
            {"properties": {"id": 2**53 + 1}},
        ]}
        out2 = assess_feature_collection(fc2, nc)
        assert out2.counts_by_code.get("unique_conflict") == 2
        assert out2.unique_conflict_groups == {"u": 1}


# ─── F09：conditional_required 触发范围 ──────────────────────────────────


class TestConditionalRequired:
    DOC = _doc(
        [
            {"field": "status", "type": "string", "enum": ["planned", "active", "retired"]},
            {"field": "reason", "type": "string", "nullable": True},
        ],
        [{
            "rule_id": "rr", "kind": "conditional_required",
            "when": {"field": "status", "op": "eq", "value": "retired"},
            "require": ["reason"],
        }],
    )

    def test_truth_table(self):
        nc = normalize_document(self.DOC)
        # status=retired 且 reason 缺 → 触发
        assert _codes(check_feature({"status": "retired"}, nc)) == ["conditional_required"]
        # status=retired 且 reason 在场（哪怕 null —— null 由 nullable 政策管）→ 不触发
        assert _codes(check_feature({"status": "retired", "reason": None}, nc)) == []
        # status=active → 不触发
        assert _codes(check_feature({"status": "active"}, nc)) == []
        # status=null（未知）→ 条件不满足（不把未知默认为真，无 conditional_required）；
        # 字段自身 nullable=False → null_not_allowed（字段政策与条件触发分离）
        assert _codes(check_feature({"status": None}, nc)) == ["null_not_allowed"]
        # status 缺失 → 不触发
        assert _codes(check_feature({}, nc)) == []
        # ne 算子：status != retired（在场非 null）→ 触发 require
        doc_ne = _doc(
            [
                {"field": "s", "type": "string", "enum": ["a", "b"]},
                {"field": "r", "type": "string"},
            ],
            [{"rule_id": "ne1", "kind": "conditional_required",
              "when": {"field": "s", "op": "ne", "value": "a"}, "require": ["r"]}],
        )
        nc_ne = normalize_document(doc_ne)
        assert _codes(check_feature({"s": "b"}, nc_ne)) == ["conditional_required"]
        assert _codes(check_feature({"s": "a"}, nc_ne)) == []
        # in 算子
        doc_in = _doc(
            [{"field": "s", "type": "string"}, {"field": "r", "type": "string"}],
            [{"rule_id": "in1", "kind": "conditional_required",
              "when": {"field": "s", "op": "in", "values": ["x", "y"]}, "require": ["r"]}],
        )
        nc_in = normalize_document(doc_in)
        assert _codes(check_feature({"s": "x"}, nc_in)) == ["conditional_required"]
        assert _codes(check_feature({"s": "z"}, nc_in)) == []


# ─── F10：组合唯一键的类型与空值政策 ─────────────────────────────────────


class TestCompositeUnique:
    DOC = _doc(
        [
            {"field": "k1", "type": "string"},
            {"field": "k2", "type": "number"},
            {"field": "k3", "type": "number"},
        ],
        [{"rule_id": "u", "kind": "unique", "fields": ["k1", "k2"], "null_policy": "ignore"}],
    )

    def test_type_aware_key_hand_counted(self):
        nc = normalize_document(self.DOC)
        fc = {"features": [
            # 行0：("a", 1)
            {"properties": {"k1": "a", "k2": 1}},
            # 行1：("a", 1.0) —— number 1 与 1.0 同一键 → 冲突（手算：1 组 2 行）
            {"properties": {"k1": "a", "k2": 1.0}},
            # 行2：("a", 2) 不同键
            {"properties": {"k1": "a", "k2": 2}},
        ]}
        out = assess_feature_collection(fc, nc)
        assert out.unique_conflict_groups == {"u": 1}
        assert out.counts_by_code["unique_conflict"] == 2
        rows = {f.get("row") for f in out.violations_materialized
                if f["code"] == "unique_conflict"}
        assert rows == {0, 1}

    def test_string_one_vs_number_one_are_distinct_keys(self):
        # k2 声明 number：值 "1"（字符串）本身 type_mismatch；不与 number 1 合并成同键
        nc = normalize_document(
            _doc([{"field": "k", "type": "number"}],
                 [{"rule_id": "u", "kind": "unique", "fields": ["k"], "null_policy": "participate"}])
        )
        fc = {"features": [
            {"properties": {"k": 1}},
            {"properties": {"k": "1"}},
        ]}
        out = assess_feature_collection(fc, nc)
        assert out.counts_by_code.get("type_mismatch") == 1
        assert out.counts_by_code.get("unique_conflict") is None  # 类型标签不同 → 不同键

    def test_null_policy_ignore_vs_participate_hand_table(self):
        cross_ignore = [{"rule_id": "u", "kind": "unique", "fields": ["k"], "null_policy": "ignore"}]
        cross_part = [{"rule_id": "u", "kind": "unique", "fields": ["k"], "null_policy": "participate"}]
        fc = {"features": [
            {"properties": {"k": None}},     # 行0 null
            {},                              # 行1 missing
            {"properties": {"k": None}},     # 行2 null
        ]}
        # ignore：null/missing 行全部跳过唯一性 → 0 冲突组
        #（字段政策仍各自生效：k nullable=False → 2 条 null_not_allowed）
        out_i = assess_feature_collection(fc, normalize_document(_doc([{"field": "k", "type": "string"}], cross_ignore)))
        assert out_i.unique_conflict_groups == {}
        assert out_i.counts_by_code.get("unique_conflict") is None
        assert out_i.counts_by_code.get("null_not_allowed") == 2
        # participate：null≡missing 归一 → 三行同键 → 1 组 3 行
        out_p = assess_feature_collection(fc, normalize_document(_doc([{"field": "k", "type": "string"}], cross_part)))
        assert out_p.unique_conflict_groups == {"u": 1}
        assert out_p.counts_by_code["unique_conflict"] == 3


# ─── F37：datetime 瞬时规范（同一瞬时即冲突）；string 原文比较 ───────────


class TestDatetimeInstantSemantics:
    def test_same_instant_different_offsets_conflict(self):
        nc = normalize_document(
            _doc([{"field": "t", "type": "datetime"}],
                 [{"rule_id": "u", "kind": "unique", "fields": ["t"]}])
        )
        fc = {"features": [
            {"properties": {"t": "2026-11-15T08:00:00+08:00"}},  # = 00:00:00Z
            {"properties": {"t": "2026-11-15T00:00:00Z"}},
        ]}
        out = assess_feature_collection(fc, nc)
        assert out.counts_by_code.get("unique_conflict") == 2

    def test_precision_beyond_support_rejected_not_truncated(self):
        nc = normalize_document(_doc([{"field": "t", "type": "datetime"}]))
        # 7 位小数秒：拒绝（绝不截断合并 —— .1234567 与 .123456 不是同一值）
        assert _one([{"field": "t", "type": "datetime"}],
                    props={"t": "2026-11-15T00:00:00.1234567Z"}) == ["datetime_precision"]
        # 6 位与 7 位若截断会同键 —— 验证它们是两个不同的违规/合法事实
        fc = {"features": [
            {"properties": {"t": "2026-11-15T00:00:00.123456Z"}},
            {"properties": {"t": "2026-11-15T00:00:00.1234567Z"}},
        ]}
        out = assess_feature_collection(fc, nc)
        assert out.counts_by_code.get("datetime_precision") == 1

    def test_string_field_compares_exact_chars(self):
        # 普通字符串：原字符精确比较 —— "A" 与 "a" 不同键（无 casefold）；
        # 前后空格不同键（无 trim）
        nc = normalize_document(
            _doc([{"field": "s", "type": "string"}],
                 [{"rule_id": "u", "kind": "unique", "fields": ["s"]}])
        )
        fc = {"features": [
            {"properties": {"s": "A"}},
            {"properties": {"s": "a"}},
            {"properties": {"s": "A "}},
        ]}
        out = assess_feature_collection(fc, nc)
        assert out.violations_total == 0


# ─── 枚举 / 长度 / compare ────────────────────────────────────────────────


class TestEnumLengthCompare:
    def test_enum_declared_allows_unobserved(self):
        # F02 语义基础：枚举含 retired（数据从未出现过）—— 声明域允许新值
        nc = normalize_document(
            _doc([{"field": "status", "type": "string", "enum": ["planned", "retired"]}])
        )
        assert _codes(check_feature({"status": "retired"}, nc)) == []
        assert _codes(check_feature({"status": "unknown"}, nc)) == ["enum_violation"]

    def test_length_codepoints(self):
        f = [{"field": "s", "type": "string", "min_length": 2, "max_length": 3}]
        assert _one(f, props={"s": "ab"}) == []
        assert _one(f, props={"s": "a"}) == ["string_length"]
        assert _one(f, props={"s": "abcd"}) == ["string_length"]
        # 码点计数：中文 1 字 = 1 码点
        assert _one(f, props={"s": "中"}) == ["string_length"]
        assert _one(f, props={"s": "中文"}) == []

    def test_compare_same_type_ordered(self):
        doc = _doc(
            [{"field": "start", "type": "date"}, {"field": "end", "type": "date"}],
            [{"rule_id": "c", "kind": "compare", "left": "start", "op": "lte", "right": "end"}],
        )
        nc = normalize_document(doc)
        assert _codes(check_feature({"start": "2024-01-01", "end": "2024-02-01"}, nc)) == []
        assert _codes(check_feature({"start": "2024-03-01", "end": "2024-02-01"}, nc)) == [
            "compare_violation"
        ]
        # 一侧缺失/null → compare 不适用（由字段规则管辖）
        assert _codes(check_feature({"start": "2024-01-01"}, nc)) == []
        # 跨类型 compare 是作者期错误
        with pytest.raises(ContractDocumentError):
            normalize_document(_doc(
                [{"field": "a", "type": "date"}, {"field": "b", "type": "number"}],
                [{"rule_id": "c", "kind": "compare", "left": "a", "op": "lt", "right": "b"}],
            ))


# ─── 完整评估：预算 / 物化上限 / 定位 ────────────────────────────────────


class TestAssessmentBounding:
    def test_materialization_limit_and_overflow_counted(self):
        from app.services.field_contracts.contracts import MAX_MATERIALIZED_FINDINGS as CAP

        nc = normalize_document(_doc([{"field": "a", "type": "string", "required": True}]))
        fc = {"features": [{"properties": {}} for _ in range(CAP + 150)]}
        out = assess_feature_collection(fc, nc)
        assert out.violations_total == CAP + 150
        assert len(out.violations_materialized) == CAP
        assert out.overflow_count == 150
        assert out.counts_by_code["required_missing"] == CAP + 150

    def test_row_locator_without_wfid(self):
        # 只读评估（无 _wfid）用 source row index 定位，不伪造 wfid
        nc = normalize_document(_doc([{"field": "a", "type": "string", "required": True}]))
        fc = {"features": [
            {"properties": {"a": "x"}},
            {"properties": {}},
        ]}
        out = assess_feature_collection(fc, nc)
        v = out.violations_materialized[0]
        assert v["row"] == 1 and "wfid" not in v

    def test_size_gate_incomplete(self):
        from app.services.field_contracts.contracts import MAX_ASSESSABLE_FEATURES as GATE

        nc = normalize_document(_doc([{"field": "a", "type": "string"}]))
        fc = {"features": [{"properties": {"a": "x"}} for _ in range(GATE + 1)]}
        out = assess_feature_collection(fc, nc)
        assert out.complete is False
        assert out.stop_reason == "dataset_exceeds_assessable_size"
        assert out.violations_total == 0  # 未检查 ≠ 零违反


# ─── 候选态门禁：变更面 + 唯一性全量 ─────────────────────────────────────


class TestCandidateState:
    DOC = _doc(
        [
            {"field": "no", "type": "string"},
            {"field": "cap", "type": "number", "min": 0},
        ],
        [{"rule_id": "u", "kind": "unique", "fields": ["no"]}],
    )

    def _fc(self):
        return {"features": [
            {"properties": {"_wfid": "w1", "no": "A", "cap": 1}},
            {"properties": {"_wfid": "w2", "no": "B", "cap": 2}},
        ]}

    def test_changed_row_recheck_and_full_unique_scan(self):
        nc = normalize_document(self.DOC)
        fc = self._fc()
        # 候选态：w1 的 no 被编辑为 B（与未选中的 w2 冲突）—— 唯一性必须全量扫描
        fc["features"][0]["properties"]["no"] = "B"
        findings = validate_candidate_state(fc, nc, changed_wfids={"w1"})
        # 手算：唯一性冲突 w1/w2 各 1 条（1 组）
        uniq = [f for f in findings if f["code"] == "unique_conflict"]
        assert {f["wfid"] for f in uniq} == {"w1", "w2"}
        # 无冲突基线：no=A/B 互不冲突
        base = validate_candidate_state(self._fc(), nc, changed_wfids={"w1"})
        assert [f for f in base if f["code"] == "unique_conflict"] == []
        # 未变更行不重复字段检查（w2 本来合法）
        assert not [f for f in findings if f.get("field") == "cap"]

    def test_schema_change_forces_full_rescan(self):
        nc = normalize_document(self.DOC)
        fc = self._fc()
        fc["features"][0]["properties"]["cap"] = -1
        fc["features"][1]["properties"]["cap"] = -2
        # schema_changed=True → 全量扫（两行都报 cap 越界，即使只声明变更 w1）
        findings = validate_candidate_state(fc, nc, changed_wfids={"w1"}, schema_changed=True)
        caps = [f for f in findings if f.get("field") == "cap"]
        assert {f["wfid"] for f in caps} == {"w1", "w2"}


# ─── 文档归一化：作者期错误全量披露 ──────────────────────────────────────


class TestDocumentValidation:
    def test_unknown_cross_field_reference(self):
        with pytest.raises(ContractDocumentError) as ei:
            normalize_document(_doc(
                [{"field": "a", "type": "string"}],
                [{"rule_id": "u", "kind": "unique", "fields": ["nope"]}],
            ))
        assert any("not declared" in e for e in ei.value.errors)

    def test_budget_gates(self):
        from app.services.field_contracts.contracts import (
            MAX_FIELDS_PER_CONTRACT,
            MAX_CROSS_RULES,
            MAX_ENUM_VALUES,
        )
        with pytest.raises(ContractDocumentError):
            normalize_document(_doc([{"field": f"f{i}", "type": "string"}
                                     for i in range(MAX_FIELDS_PER_CONTRACT + 1)]))
        with pytest.raises(ContractDocumentError):
            normalize_document(_doc(
                [{"field": "a", "type": "string"}],
                [{"rule_id": f"r{i}", "kind": "unique", "fields": ["a"]}
                 for i in range(MAX_CROSS_RULES + 1)],
            ))
        with pytest.raises(ContractDocumentError):
            normalize_document(_doc(
                [{"field": "a", "type": "string",
                  "enum": [f"v{i}" for i in range(MAX_ENUM_VALUES + 1)]}]
            ))

    def test_digest_deterministic_and_content_sensitive(self):
        d1 = normalize_document(_doc([{"field": "a", "type": "string"}]))
        d2 = normalize_document(_doc([{"field": "a", "type": "string"}]))
        d3 = normalize_document(_doc([{"field": "a", "type": "string", "required": True}]))
        from app.services.field_contracts.validator import contract_digest
        assert contract_digest(d1) == contract_digest(d2)
        assert contract_digest(d1) != contract_digest(d3)
