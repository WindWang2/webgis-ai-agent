"""G44 独立评审修复回归（R1-P0/P1/P2）。

R1-P0：激活竞态 —— 锁外选出的零违反收据在锁内被修订检测拒绝；
R1-P1：compare 规则遇类型不匹配值不再 TypeError（typed 路径）；
R1-P2：REST schema 的数值边界保 int（bigint 无损直达内核）。
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import service as fe_service
from app.services.session_data import MemorySessionStore

FACILITIES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": f"fac-{i}",
         "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
         "properties": {"asset_no": f"A-{i:03d}", "status": "planned", "capacity": 10.0 + i}}
        for i in range(3)
    ],
}

DOC = {
    "version": 1, "title": "设施合同",
    "fields": [
        {"field": "asset_no", "type": "string", "required": True},
        {"field": "status", "type": "string", "enum": ["planned", "active", "retired"]},
        {"field": "capacity", "type": "number", "min": 0},
    ],
    "cross_rules": [{"rule_id": "u", "kind": "unique", "fields": ["asset_no"]}],
}


def sid() -> str:
    return f"sess-g44r1-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _storage(tmp_path: Path, monkeypatch):
    store = MemorySessionStore()
    import app.services.session_data as sd

    monkeypatch.setattr(sd, "session_data_manager", store)
    for mod in (fe_service, fe_executor):
        monkeypatch.setattr(mod, "session_data_manager", store)
    from app.services.field_contracts import service as fc_service

    monkeypatch.setattr(fc_service, "_findings_dir",
                        lambda session_id: tmp_path / "fc" / session_id)
    yield store


class TestR1P0ActivationRace:
    async def test_revision_inside_activation_window_rejected(self, _storage, monkeypatch):
        """锁外收据选择后、迁移前的草稿修订 → activation_race（未审计内容不得上膛）。"""
        from app.services.field_contracts import service as fc_service
        from app.services.field_contracts.store import field_contract_store

        s = sid()
        ref = await _storage.store(s, {"geojson": FACILITIES}, prefix="data")
        created = await fc_service.create_contract(s, ref_id=ref, document=DOC)
        started = await fc_service.start_assessment(
            s, created["contract_id"], background=False)
        assert started["assessment"]["status"] == "completed"

        # 模拟竞态：list_assessments（锁外收据选择）被调用的瞬间，一次修订的
        # 存储效果落盘（digest/内容改变）—— 激活必须在锁内冷读复核时拒绝。
        # 在同步线程回调里直接改行（等价 revise_contract 的落盘效果；不在
        # 工作线程里嵌套事件循环）。
        real_list = field_contract_store.list_assessments
        raced = {"done": False}

        def racing_list(contract_id, limit=20):
            out = real_list(contract_id, limit=limit)
            if not raced["done"]:
                raced["done"] = True
                from app.core.database import SessionLocal
                from app.models.field_contracts import FieldContract as Row
                from datetime import datetime, timezone as _tz

                with SessionLocal() as db:
                    row = db.get(Row, contract_id)
                    row.title = "竞态修订"
                    row.rules = {**DOC, "title": "竞态修订"}
                    import hashlib as _h
                    row.normalized_digest = _h.sha256(b"raced").hexdigest()
                    row.updated_at = datetime.now(_tz.utc)
                    db.commit()
            return out

        monkeypatch.setattr(fc_service.field_contract_store, "list_assessments", racing_list)
        with pytest.raises(Exception) as ei:
            await fc_service.activate_contract(s, created["contract_id"])
        assert getattr(ei.value, "code", "") == "activation_race"
        # 行仍是 draft（未上膛）；数据不受影响
        row = await asyncio.get_running_loop().run_in_executor(
            None, lambda: field_contract_store.get_contract(created["contract_id"]))
        assert row["status"] == "draft"

    async def test_transition_status_digest_cas(self, _storage):
        from app.services.field_contracts.store import field_contract_store

        s = sid()
        ref = await _storage.store(s, {"geojson": FACILITIES}, prefix="data")
        from app.services.field_contracts import service as fc_service

        created = await fc_service.create_contract(s, ref_id=ref, document=DOC)
        cid = created["contract_id"]
        # digest 不匹配 → 迁移拒绝（None）
        out = await asyncio.to_thread(
            field_contract_store.transition_status, cid,
            from_status=("draft",), to_status="active",
            expected_digest="deadbeef" * 8,
        )
        assert out is None
        # digest 匹配 → 迁移成功
        out2 = await asyncio.to_thread(
            field_contract_store.transition_status, cid,
            from_status=("draft",), to_status="active",
            expected_digest=created["normalized_digest"],
        )
        assert out2 is not None and out2["status"] == "active"


class TestR1P1CompareTypeSafety:
    def test_mistyped_right_side_no_crash(self):
        from app.services.field_contracts.validator import check_feature, normalize_document

        doc = {
            "version": 1,
            "fields": [
                {"field": "a", "type": "string"},
                {"field": "b", "type": "string"},
            ],
            "cross_rules": [
                {"rule_id": "c", "kind": "compare", "left": "a", "op": "lt", "right": "b"},
            ],
        }
        nc = normalize_document(doc)
        # 右值为 number：字段规则报 type_mismatch；compare 确定性跳过（不抛）
        findings = check_feature({"a": "zzz", "b": 123}, nc)
        codes = [f["code"] for f in findings]
        assert "type_mismatch" in codes
        assert "compare_violation" not in codes

    def test_mistyped_left_side_no_crash(self):
        from app.services.field_contracts.validator import check_feature, normalize_document

        doc = {
            "version": 1,
            "fields": [{"field": "a", "type": "string"}, {"field": "b", "type": "string"}],
            "cross_rules": [
                {"rule_id": "c", "kind": "compare", "left": "a", "op": "lt", "right": "b"},
            ],
        }
        nc = normalize_document(doc)
        findings = check_feature({"a": 123, "b": "zzz"}, nc)
        assert "type_mismatch" in [f["code"] for f in findings]
        assert "compare_violation" not in [f["code"] for f in findings]


class TestR1P2RestBigint:
    def test_schema_preserves_int_bounds(self):
        from app.schemas.field_contracts_schema import FieldRuleIn

        rule = FieldRuleIn(field="id", type="integer", min=9_007_199_254_740_993,
                           max=9_007_199_254_740_994)
        dumped = rule.model_dump()
        assert dumped["min"] == 9_007_199_254_740_993  # int 未被强转 float
        assert isinstance(dumped["min"], int)
        # 内核边界仍精确（F08 不因 wire 失真）
        from app.services.field_contracts.validator import normalize_document

        nc = normalize_document({"version": 1, "fields": [dumped], "cross_rules": []})
        assert nc.field_map["id"].min == 9_007_199_254_740_993


class TestP2ContractBindingPersistence:
    async def test_binding_survives_reread(self, _storage):
        """P2 service.py:185 —— create 返回的 binding 必须落盘，再读一致。"""
        from app.services.field_contracts import service as fc_service

        s = sid()
        ref = await _storage.store(s, {"geojson": FACILITIES}, prefix="data")
        created = await fc_service.create_contract(s, ref_id=ref, document=DOC)
        assert created["binding"]["ref_id"] == ref
        reread = await fc_service.get_contract(s, created["contract_id"])
        assert reread["binding"] == created["binding"]


class TestP2SqliteLockRetryWiring:
    def _flaky_session_factory(self, monkeypatch, failures: int):
        import app.core.database as db_mod

        real = db_mod.SessionLocal
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] <= failures:
                from sqlalchemy.exc import OperationalError

                raise OperationalError("SELECT 1", {}, Exception("database is locked"))
            return real()

        monkeypatch.setattr(db_mod, "SessionLocal", flaky)
        import app.services.field_contracts.store as store_mod

        monkeypatch.setattr(store_mod, "_SQLITE_LOCK_BACKOFF_S", 0.0)
        return calls

    def test_create_assessment_retries_locked(self, monkeypatch):
        from app.services.field_contracts.store import field_contract_store

        calls = self._flaky_session_factory(monkeypatch, failures=2)
        row = field_contract_store.create_assessment(
            contract_id="c-lock", contract_revision=1, session_id=sid(),
            ref_id="ref-lock", status="queued", verified={}, counts={},
        )
        assert row["status"] == "queued"
        assert calls["n"] == 3

    def test_update_assessment_retries_locked(self, monkeypatch):
        from app.services.field_contracts.store import field_contract_store

        created = field_contract_store.create_assessment(
            contract_id="c-lock-2", contract_revision=1, session_id=sid(),
            ref_id="ref-lock-2", status="queued", verified={}, counts={},
        )
        calls = self._flaky_session_factory(monkeypatch, failures=2)
        row = field_contract_store.update_assessment(created["id"], status="running")
        assert row is not None and row["status"] == "running"
        assert calls["n"] == 3


class TestP2LengthBoundTypes:
    @pytest.mark.parametrize("bound", ["3", 3.0, True, -1])
    def test_non_integer_length_bounds_rejected(self, bound):
        from app.services.field_contracts.validator import (
            ContractDocumentError,
            normalize_document,
        )

        doc = {"version": 1, "fields": [
            {"field": "s", "type": "string", "min_length": bound},
        ]}
        with pytest.raises(ContractDocumentError):
            normalize_document(doc)


class TestP3AuthorBooleanStrictness:
    """validator.py:278 —— 布尔开关非 bool 时作者期拒绝，不静默 truthy 强转。"""

    @pytest.mark.parametrize("key", ["required", "nullable", "exclusive_min", "exclusive_max"])
    def test_non_boolean_flags_rejected(self, key):
        from app.services.field_contracts.validator import (
            ContractDocumentError,
            normalize_document,
        )

        doc = {"version": 1, "fields": [{"field": "a", "type": "string", key: "false"}]}
        with pytest.raises(ContractDocumentError) as ei:
            normalize_document(doc)
        assert any(key in e for e in ei.value.errors)

    def test_real_booleans_still_accepted(self):
        from app.services.field_contracts.validator import normalize_document

        nc = normalize_document({"version": 1, "fields": [
            {"field": "a", "type": "string", "required": True, "nullable": False},
        ]})
        assert nc.field_map["a"].required is True
        assert nc.field_map["a"].nullable is False


class TestP3ConditionalDatetimeInNotIn:
    """validator.py:624 —— datetime 触发值的 in/not_in 按 UTC 瞬时判定成员。"""

    DOC = {
        "version": 1,
        "fields": [
            {"field": "t", "type": "datetime"},
            {"field": "a", "type": "string"},
            {"field": "b", "type": "string"},
        ],
        "cross_rules": [
            {"rule_id": "r_in", "kind": "conditional_required",
             "when": {"field": "t", "op": "in", "values": ["2024-01-01T00:00:00+02:00"]},
             "require": ["a"]},
            {"rule_id": "r_notin", "kind": "conditional_required",
             "when": {"field": "t", "op": "not_in", "values": ["2024-01-01T00:00:00+02:00"]},
             "require": ["b"]},
        ],
    }

    def _codes(self, props):
        from app.services.field_contracts.validator import check_feature, normalize_document

        nc = normalize_document(self.DOC)
        return [(f["code"], f.get("rule_id")) for f in check_feature(props, nc)]

    def test_same_instant_different_offset_is_member(self):
        # 2023-12-31T22:00:00Z == 2024-01-01T00:00:00+02:00（同一瞬时）
        codes = self._codes({"t": "2023-12-31T22:00:00Z"})
        assert ("conditional_required", "r_in") in codes
        assert ("conditional_required", "r_notin") not in codes

    def test_different_instant_is_not_member(self):
        codes = self._codes({"t": "2024-01-02T00:00:00+02:00"})
        assert ("conditional_required", "r_in") not in codes
        assert ("conditional_required", "r_notin") in codes

    def test_string_trigger_keeps_raw_exact_compare(self):
        from app.services.field_contracts.validator import check_feature, normalize_document

        nc = normalize_document({"version": 1, "fields": [
            {"field": "s", "type": "string"}, {"field": "a", "type": "string"},
        ], "cross_rules": [
            {"rule_id": "r", "kind": "conditional_required",
             "when": {"field": "s", "op": "in", "values": ["x"]}, "require": ["a"]},
        ]})
        assert [f["code"] for f in check_feature({"s": "x"}, nc)] == ["conditional_required"]
        assert check_feature({"s": "X"}, nc) == []
