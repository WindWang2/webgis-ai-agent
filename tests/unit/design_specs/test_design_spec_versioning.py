"""WP1 版本化测试 —— 不可变快照 / 继承 / 软废弃 / 命名查重 / 发布门 / scope。

DB 面：conftest 每 worker 隔离 SQLite（Base.metadata.create_all 覆盖
g50 新表，即 create_all-coexistence guard 的真实场景）。
"""
from __future__ import annotations

import pytest

from app.core.database import Base, Engine, SessionLocal
from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
from app.services.design_specs.contract import structural_digest
from app.services.design_specs.errors import (
    DesignSpecConflictError,
    DesignSpecNotFoundError,
    DesignSpecValidationError,
)
from app.services.design_specs.versioning import (
    create_spec,
    create_version,
    deprecate_version,
    design_spec_can_write,
    design_spec_visible,
    get_spec,
    get_version,
    list_specs,
    publish_spec,
    resolve_payload,
    retire_spec,
)


def _payload(name="测试规范A", **kw) -> dict:
    base = {
        "spec_version": "design_spec.v1",
        "name": name,
        "color": {"theme_id": "cartographic.light_interactive"},
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    yield
    with SessionLocal() as db:
        db.query(CartographyDesignSpecVersion).delete()
        db.query(CartographyDesignSpec).delete()
        db.commit()


class TestCreateSpec:
    def test_create_returns_version1_with_digest(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(), creator_id="u1", org_id=None)
            assert spec.current_version == 1
            assert spec.structural_digest == structural_digest(_payload())
            assert spec.status == "draft"
            row = get_version(db, spec.id, 1)
            assert row.payload == _payload()
            assert row.structural_digest == spec.structural_digest

    def test_name_conflict_same_creator_rejected(self):
        with SessionLocal() as db:
            create_spec(db, _payload(name="密度规范"), creator_id="u1")
            with pytest.raises(DesignSpecConflictError):
                create_spec(db, _payload(name="密度规范"), creator_id="u1")

    def test_same_name_different_creator_allowed(self):
        with SessionLocal() as db:
            s1 = create_spec(db, _payload(name="同名规范"), creator_id="u1")
            s2 = create_spec(db, _payload(name="同名规范"), creator_id="u2")
            assert s1.id != s2.id

    def test_invalid_payload_rejected(self):
        with SessionLocal() as db:
            with pytest.raises(DesignSpecValidationError):
                create_spec(db, {"spec_version": "design_spec.v1"}, creator_id="u1")

    def test_leak_payload_rejected_at_creation(self):
        with SessionLocal() as db:
            bad = _payload(name="泄漏规范")
            bad["classification_breaks"] = [1, 2, 3, 4]
            with pytest.raises(DesignSpecValidationError) as exc:
                create_spec(db, bad, creator_id="u1")
            assert any(
                v["reason"] == "DATA_VALUES_FORBIDDEN" for v in exc.value.violations
            )


class TestVersionChain:
    def test_version_monotonic_and_main_forward(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(name="链式规范"), creator_id="u1")
            v2 = create_version(db, spec.id, _payload(name="链式规范", description="v2 desc"))
            assert v2.version == 2
            db.expire(spec)
            spec = get_spec(db, spec.id)
            assert spec.current_version == 2
            assert spec.payload["description"] == "v2 desc"
            # 版本 1 快照不可变
            assert get_version(db, spec.id, 1).payload.get("description", "") == ""

    def test_inheritance_deep_merge_child_wins(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(name="继承规范"), creator_id="u1")
            v1 = get_version(db, spec.id, 1)
            child_payload = _payload(name="继承规范")
            child_payload["color"] = {"palette_sequential": "Blues"}
            create_version(db, spec.id, child_payload, parent_version_id=v1.id)
            row = get_version(db, spec.id, 2)
            effective, chain = resolve_payload(db, row)
            assert effective["color"]["theme_id"] == "cartographic.light_interactive"  # 继承
            assert effective["color"]["palette_sequential"] == "Blues"  # 子覆盖
            assert chain == [spec.id, spec.id]

    def test_cross_spec_parent_rejected(self):
        with SessionLocal() as db:
            s1 = create_spec(db, _payload(name="规范一"), creator_id="u1")
            s2 = create_spec(db, _payload(name="规范二"), creator_id="u1")
            parent = get_version(db, s1.id, 1)
            with pytest.raises(DesignSpecValidationError):
                create_version(db, s2.id, _payload(name="规范二"), parent_version_id=parent.id)

    def test_deep_chain_capped(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(name="深链规范"), creator_id="u1")
            # 逐版 parent 链：v2←v1, v3←v2, … 每落一版链深 +1；
            # 深度帽 16 → 链深 17 的落版必须被拒绝（守卫在 parent 校验期触发）。
            prev = get_version(db, spec.id, 1)
            last = prev
            for _ in range(15):
                last = create_version(
                    db, spec.id, _payload(name="深链规范"),
                    parent_version_id=last.id,
                )
            # 此时 v16 的 parent 链 = 16 节点（≤帽，允许）；v17 落版后链 = 17。
            v17 = create_version(
                db, spec.id, _payload(name="深链规范"), parent_version_id=last.id
            )
            # v18（parent=v17）→ parent 链 17 节点 > 16 → 拒绝。
            with pytest.raises(DesignSpecValidationError):
                create_version(
                    db, spec.id, _payload(name="深链规范"),
                    parent_version_id=v17.id,
                )

    def test_nonexistent_spec_404(self):
        with SessionLocal() as db:
            with pytest.raises(DesignSpecNotFoundError):
                create_version(db, "no-such-spec", _payload())


class TestDeprecateRetirePublish:
    def test_deprecate_soft_and_read_compat(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(name="废弃规范"), creator_id="u1")
            deprecate_version(db, spec.id, 1, note="superseded by v2")
            row = get_version(db, spec.id, 1)
            assert row.deprecated_at is not None
            assert row.deprecation_note == "superseded by v2"
            # 兼容读取：resolve 仍可用
            effective, _ = resolve_payload(db, row)
            assert effective["name"] == "废弃规范"

    def test_publish_gate_blocks_violations(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(name="发布门规范"), creator_id="u1")
            # 直接落一个带引用违规的版本（绕过 create_version 校验不可能，
            # 因此模拟主表 payload 被外部写坏的场景）
            spec.payload = dict(spec.payload, color={"theme_id": "no_such_theme"})
            db.commit()
            with pytest.raises(DesignSpecValidationError):
                publish_spec(db, spec.id)
            assert get_spec(db, spec.id).status == "draft"

    def test_publish_success(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(name="发布规范"), creator_id="u1")
            publish_spec(db, spec.id)
            assert get_spec(db, spec.id).status == "published"

    def test_retire_soft(self):
        with SessionLocal() as db:
            spec = create_spec(db, _payload(name="退役规范"), creator_id="u1")
            retire_spec(db, spec.id, note="replaced")
            row = get_spec(db, spec.id)
            assert row.status == "retired"
            assert row.retired_at is not None
            # 读取兼容：get/resolve 仍可用
            assert get_version(db, spec.id).payload["name"] == "退役规范"


class TestScopeAndAuthz:
    def test_visibility_builtin_creator_org(self):
        with SessionLocal() as db:
            mine = create_spec(db, _payload(name="我的规范"), creator_id="u1")
            theirs = create_spec(db, _payload(name="他人规范"), creator_id="u2", org_id=7)
            builtin = create_spec(
                db, _payload(name="内置规范"), creator_id=None, org_id=None, is_builtin=True
            )
            u1, _org = "u1", 7
            assert design_spec_visible(mine, u1, None)
            assert design_spec_visible(builtin, u1, None)
            assert not design_spec_visible(theirs, u1, None)  # 跨租户不可见
            assert design_spec_visible(theirs, "u2", 7)
            assert design_spec_visible(theirs, "u9", 7)  # 同组织可见

            assert not design_spec_can_write(mine, "u2", None, None)
            assert design_spec_can_write(mine, "u1", None, None)
            assert not design_spec_can_write(builtin, "u1", None, "admin")  # builtin 只读
            admin_same_org = design_spec_can_write(theirs, "uadmin", 7, "admin")
            assert admin_same_org

    def test_list_scoped(self):
        with SessionLocal() as db:
            create_spec(db, _payload(name="列表甲"), creator_id="u1")
            create_spec(db, _payload(name="列表乙"), creator_id="u2", org_id=7)
            create_spec(db, _payload(name="列表内"), creator_id=None, is_builtin=True)
            rows_u1 = list_specs(db, "u1", None)
            names = {r.name for r in rows_u1}
            assert "列表甲" in names and "列表内" in names
            assert "列表乙" not in names
            rows_o7 = list_specs(db, "u9", 7)
            names7 = {r.name for r in rows_o7}
            assert "列表乙" in names7 and "列表甲" not in names7
