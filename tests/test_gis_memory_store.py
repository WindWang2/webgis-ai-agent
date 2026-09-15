"""GIS Spatial Reasoning Memory 契约/写策略/存储测试（方向 9，ADR-0183）。

纪律红线（全部有断言锁定）：
- 写入 evidence-gated fail-closed：无证据/低置信/未知 kind 一律拒；
- 矛盾显式化：supersede 链 + 用户纠正必胜 + 弱证据不落库（无静默 merge）;
- tenancy：org 谓词恒等值过滤，跨租户读空集，org 由调用方烙印；
- 有界：value 预算、预算淘汰、过期失效（无界增长即漏洞）;
- 偏好收敛：项目制图偏好改道 ADR-0069 账本，不建第二套 preference 表。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.services.cartography.project_memory import (
    get_active_facts,
    record_fact,
)
from app.services.gis_memory import contract as c
from app.services.gis_memory import policy as p
from app.services.gis_memory import store as s
from app.services.gis_memory.contract import (
    KIND_DATASET_SEMANTICS,
    KIND_PROVIDER_FAILURE,
    KIND_RESOLVED_PLACE,
    KIND_USER_CARTO_PREF,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
    SOURCE_DATASET_PIN,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_TOOL_FAILURE,
    SOURCE_USER_CORRECTION,
    MemoryEvidence,
    MemoryPolicyError,
    MemoryWriteRequest,
    semantic_fingerprint,
)
from app.services.gis_memory.sanitizer import (
    sanitize_refs,
    sanitize_value,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


def _place_req(**overrides) -> MemoryWriteRequest:
    base = dict(
        kind=KIND_RESOLVED_PLACE,
        scope=SCOPE_SESSION,
        scope_id="sess-1",
        subject="成都市",
        value={"name": "成都市", "level": "city", "bbox": [103.0, 30.1, 104.9, 31.4]},
        evidence=MemoryEvidence(source=SOURCE_INTENT_RESOLUTION, method="gazetteer"),
        confidence=0.86,
        org_id="org-a",
    )
    base.update(overrides)
    return MemoryWriteRequest(**base)


# ── 契约层 ────────────────────────────────────────────────────────────


def test_fingerprint_ignores_presentation_fields():
    v1 = {"name": "成都市", "confidence": 0.7, "observed_at": "2026-01-01"}
    v2 = {"name": "成都市", "confidence": 0.9, "observed_at": "2026-09-14"}
    assert semantic_fingerprint(KIND_RESOLVED_PLACE, "成都市", v1) == (
        semantic_fingerprint(KIND_RESOLVED_PLACE, "成都市", v2)
    )
    v3 = {"name": "高新区", "level": "district"}
    assert semantic_fingerprint(KIND_RESOLVED_PLACE, "成都市", v1) != (
        semantic_fingerprint(KIND_RESOLVED_PLACE, "成都市", v3)
    )


def test_contract_rejects_unknown_kind_and_scope():
    with pytest.raises(MemoryPolicyError):
        MemoryWriteRequest(
            kind="chat_memory", scope=SCOPE_SESSION, scope_id="s", subject="x",
            value={}, evidence=MemoryEvidence(source=SOURCE_INTENT_RESOLUTION),
            confidence=0.9, org_id="o",
        ).validate()
    with pytest.raises(MemoryPolicyError):
        _place_req(scope="tenant").validate()


def test_contract_enforces_scope_identity():
    # user 作用域必须显式 user_id（匿名不写用户记忆）
    with pytest.raises(MemoryPolicyError):
        _place_req(scope=SCOPE_USER, scope_id="u-1", user_id=None).validate()
    # org 必填（fail-closed tenancy）
    with pytest.raises(MemoryPolicyError):
        _place_req(org_id="").validate()


def test_contract_value_budget():
    with pytest.raises(MemoryPolicyError):
        _place_req(value={"blob": "x" * (c.VALUE_CHAR_BUDGET + 10)}).validate()


def test_evidence_matrix_is_closed():
    # provider_failure 不接受 intent_resolution 来源（世界事实≠失败事实）
    with pytest.raises(MemoryPolicyError):
        MemoryEvidence(source=SOURCE_INTENT_RESOLUTION).validate(
            KIND_PROVIDER_FAILURE
        )


# ── 写策略（R2）──────────────────────────────────────────────────────


def test_policy_confidence_gate():
    verdict = p.evaluate(_place_req(confidence=0.4))
    assert not verdict.allowed and "门槛" in verdict.reason
    ok = p.evaluate(_place_req(confidence=0.86))
    assert ok.allowed and ok.ttl_s == 30 * 24 * 3600


def test_policy_explicit_source_lower_threshold():
    verdict = p.evaluate(_place_req(
        confidence=0.55, evidence=MemoryEvidence(source=SOURCE_USER_CORRECTION)
    ))
    assert verdict.allowed


def test_policy_failure_memory_requires_ttl():
    verdict = p.evaluate(MemoryWriteRequest(
        kind=KIND_PROVIDER_FAILURE,
        scope=SCOPE_SESSION, scope_id="sess-1", subject="geocode.provider-a",
        value={"failure_class": "provider_unavailable"},
        evidence=MemoryEvidence(source=SOURCE_TOOL_FAILURE, method="dispatch"),
        confidence=0.8, org_id="org-a", invalidation_rule="ttl", ttl_s=None,
    ))
    # kind 默认 TTL 存在（7d）→ 允许
    assert verdict.allowed and verdict.ttl_s == 7 * 24 * 3600
    # 显式声明 ttl 规则但无 TTL 且 kind 无默认 → 拒绝
    verdict2 = p.evaluate(MemoryWriteRequest(
        kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="s", subject="x",
        value={}, evidence=MemoryEvidence(source=SOURCE_USER_CORRECTION),
        confidence=0.9, org_id="o", invalidation_rule="ttl", ttl_s=-3,
    ))
    assert not verdict2.allowed


def test_policy_routes_project_preference_to_carto_fact(db):
    verdict = p.evaluate(MemoryWriteRequest(
        kind=KIND_USER_CARTO_PREF,
        scope=SCOPE_PROJECT, scope_id="proj-1", subject="basemap",
        value={"value": "dark"},
        evidence=MemoryEvidence(source="explicit_user_decision"),
        confidence=0.9, org_id="org-a",
    ))
    assert verdict.route == p.ROUTE_CARTO_PROJECT_FACT


# ── 存储（R3 supersession / R7 GC / tenancy）──────────────────────────


def test_record_and_get_active(db):
    row = s.record_memory(db, _place_req())
    db.commit()
    assert row is not None and row.status == "active"
    rows = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
    assert len(rows) == 1 and rows[0].subject == "成都市"
    assert rows[0].value["level"] == "city"


def test_revalidation_refreshes_in_place(db):
    first = s.record_memory(db, _place_req(confidence=0.7))
    db.commit()
    again = s.record_memory(db, _place_req(confidence=0.9))
    db.commit()
    assert again.id == first.id
    assert again.version == 2 and again.confidence == 0.9
    assert len(s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")) == 1


def test_contradiction_supersedes_with_chain(db):
    old = s.record_memory(db, _place_req(
        value={"name": "成都市", "level": "city"},
        confidence=0.8,
    ))
    db.commit()
    new = s.record_memory(db, _place_req(
        subject="成都市",
        value={"name": "成都市", "level": "city", "admin_id": "510100"},
        confidence=0.9,
        evidence=MemoryEvidence(source=SOURCE_USER_CORRECTION, method="user_fix"),
    ))
    db.commit()
    assert new is not None and new.supersedes_id == old.id
    db.refresh(old)
    assert old.status == "superseded"
    active = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
    assert len(active) == 1 and active[0].id == new.id


def test_weak_contradiction_not_written(db):
    s.record_memory(db, _place_req(confidence=0.9))
    db.commit()
    rejected = s.record_memory(db, _place_req(
        value={"name": "成都市", "level": "province"}, confidence=0.5,
    ))
    db.commit()
    assert rejected is None
    active = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
    assert len(active) == 1 and active[0].value["level"] == "city"


def test_user_correction_beats_higher_confidence_held(db):
    s.record_memory(db, _place_req(
        value={"name": "成都市", "level": "city"}, confidence=0.95,
    ))
    db.commit()
    corrected = s.record_memory(db, _place_req(
        value={"name": "成都市", "level": "city", "note": "user says district"},
        confidence=0.55,
        evidence=MemoryEvidence(source=SOURCE_USER_CORRECTION),
    ))
    db.commit()
    assert corrected is not None
    assert corrected.value.get("note") == "user says district"


def test_tenancy_isolation(db):
    s.record_memory(db, _place_req(org_id="org-a"))
    db.commit()
    assert s.get_active_memories(db, "org-b", SCOPE_SESSION, "sess-1") == []
    rows = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
    assert len(rows) == 1


def test_scope_separation(db):
    s.record_memory(db, _place_req())
    s.record_memory(db, _place_req(scope=SCOPE_PROJECT, scope_id="proj-1"))
    db.commit()
    assert len(s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")) == 1
    assert len(s.get_active_memories(db, "org-a", SCOPE_PROJECT, "proj-1")) == 1


def test_expired_memory_filtered_and_swept(db):
    s.record_memory(db, _place_req(ttl_s=3600))
    db.commit()
    # 时间未到 → 可见
    assert len(s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")) == 1
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    swept = s.sweep_expired(db, now=future)
    db.commit()
    assert swept == 1
    assert s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1") == []
    # invalidated 行保留可审计
    audit = s.list_memories(db, org_id="org-a")
    assert len(audit) == 1 and audit[0].status == "invalidated"


def test_dataset_version_invalidation(db):
    s.record_memory(db, MemoryWriteRequest(
        kind=KIND_DATASET_SEMANTICS, scope=SCOPE_PROJECT, scope_id="proj-1",
        subject="ds:hospitals", value={"version_token": "v1", "time_field": "year"},
        evidence=MemoryEvidence(source=SOURCE_DATASET_PIN, method="pin"),
        confidence=0.9, org_id="org-a", invalidation_rule="dataset_version",
    ))
    db.commit()
    assert len(s.get_active_memories(db, "org-a", SCOPE_PROJECT, "proj-1")) == 1
    # 同版本 → 无漂移不失效
    assert s.invalidate_for_dataset(
        db, org_id="org-a", dataset_key="ds:hospitals", version_token="v1"
    ) == 0
    # 版本推进 → 失效
    assert s.invalidate_for_dataset(
        db, org_id="org-a", dataset_key="ds:hospitals", version_token="v2"
    ) == 1
    db.commit()
    assert s.get_active_memories(db, "org-a", SCOPE_PROJECT, "proj-1") == []


def test_scope_budget_eviction(db):
    for i in range(s.SCOPE_BUDGET[SCOPE_SESSION] + 10):
        s.record_memory(db, _place_req(
            subject=f"place-{i}", confidence=0.6 + i * 0.001,
        ))
    db.commit()
    rows = s.get_active_memories(
        db, "org-a", SCOPE_SESSION, "sess-1", limit=200
    )
    assert len(rows) <= s.SCOPE_BUDGET[SCOPE_SESSION]


def test_retire_is_org_gated(db):
    row = s.record_memory(db, _place_req())
    db.commit()
    assert not s.retire_memory(db, org_id="org-b", memory_id=row.id)
    assert s.retire_memory(db, org_id="org-a", memory_id=row.id)
    db.commit()
    assert s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1") == []


# ── 消毒（R8 支撑）──────────────────────────────────────────────────


def test_sanitizer_strips_secret_keys_and_paths():
    cleaned = sanitize_value({
        "name": "成都市",
        "api_key": "sk-123",
        "password": "hunter2",
        "notes": "data at C:\\Users\\alice\\secret.csv and /home/bob/x",
    })
    assert "api_key" not in cleaned and "password" not in cleaned
    assert "C:\\Users" not in str(cleaned) and "/home/" not in str(cleaned)
    assert cleaned["name"] == "成都市"


def test_sanitizer_refs_bounded():
    refs = sanitize_refs([
        "artifact:abc", "ref:plan-1", "C:\\Users\\eve\\f.csv",
        None, 42, "artifact:abc",
    ])
    assert refs == ["artifact:abc", "ref:plan-1"]


def test_secret_shaped_value_rejected(db):
    # 凭证形态出现在**普通键**下 → 硬拒绝（键名剥除救不了它）
    wrote = s.safe_record_memory(db, _place_req(
        value={"dsn": "postgres://user:secretpw@host/db"},
    ))
    assert wrote is False
    assert s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1") == []


def test_safe_record_never_raises(db):
    # 未知 kind 等合同违规 → 安全 False，不抛
    assert s.safe_record_memory(db, _place_req(kind="bogus")) is False


# ── 偏好收敛（D4：不建第二套 preference DB）─────────────────────────


def test_project_preference_lands_in_carto_project_facts(db):
    from app.models.project import Project

    db.add(Project(id="proj-1", name="p"))
    db.flush()
    wrote = s.record_memory(db, MemoryWriteRequest(
        kind=KIND_USER_CARTO_PREF, scope=SCOPE_PROJECT, scope_id="proj-1",
        subject="basemap", value={"value": "dark"},
        evidence=MemoryEvidence(source="explicit_user_decision"),
        confidence=0.9, org_id="org-a",
    ))
    db.commit()
    # #1309：路由成功返回非 None（供 safe_record_memory / harvest written 计数）
    assert wrote is not None
    # 新表零行（路由改道，不双写 gis_spatial_memories）
    assert s.list_memories(db, org_id="org-a") == []
    # ADR-0069 账本出现 preference 事实
    facts = get_active_facts(db, "proj-1", kinds=("preference",))
    assert len(facts) == 1 and facts[0].subject == "basemap"


def test_user_scope_preference_stays_in_new_table(db):
    row = s.record_memory(db, MemoryWriteRequest(
        kind=KIND_USER_CARTO_PREF, scope=SCOPE_USER, scope_id="user-9",
        subject="basemap", value={"value": "dark"},
        evidence=MemoryEvidence(source="explicit_user_decision"),
        confidence=0.9, org_id="org-a", user_id="user-9",
    ))
    db.commit()
    assert row is not None
    rows = s.get_active_memories(db, "org-a", SCOPE_USER, "user-9")
    assert len(rows) == 1


def test_record_fact_conflict_semantics_untouched(db):
    """ADR-0069 账本的 conflicted 语义不被本方向改变（回归护栏）。"""
    from app.models.project import Project

    db.add(Project(id="proj-2", name="p2"))
    db.flush()
    record_fact(db, "proj-2", "preference", "palette", {"value": "viridis"},
                fingerprint="fp1")
    fact = record_fact(db, "proj-2", "preference", "palette",
                       {"value": "blues"}, fingerprint="fp2")
    db.commit()
    assert fact.status == "conflicted"
