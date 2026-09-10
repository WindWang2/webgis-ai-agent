"""Workflow Runtime V5 —— store/registry/reuse 持久层测试（Wave 5+6）。

临时 SQLite 工厂（StaticPool 保 :memory: 跨连接存活）；全部断言基于真实
SQLAlchemy 行为（CAS rowcount / 唯一约束 / LRU 剪枝）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import registry as RG
from app.services.workflow_runtime import reuse as RU
from app.services.workflow_runtime import store as ST


def _sqlite_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def factory():
    return _sqlite_factory()


@pytest.fixture
def store(factory):
    return ST.InstanceStore(factory=factory)


@pytest.fixture
def reg(factory):
    return RG.PackageRegistry(factory=factory)


@pytest.fixture
def index(factory):
    return RU.ReuseIndex(factory=factory)


_NODE_SPECS = [
    {"node_id": "data:subject", "optional": False},
    {"node_id": "cap:buffer", "optional": False},
    {"node_id": "output:zone", "optional": False},
]


def _make(store, owner="u:abc", session="s1"):
    return store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="fp" * 16, owner_scope=owner,
        session_id=session, node_specs=_NODE_SPECS)


# ── store：创建 / 读取 / owner 隔离 ─────────────────────────────────────

def test_create_and_get_instance(store):
    inst = _make(store)
    assert inst["instance_id"].startswith("wi-")
    assert inst["status"] == C.InstanceStatus.RUNNING
    states = store.get_node_states(inst["instance_id"])
    assert states == {n["node_id"]: C.NodeState.PENDING
                      for n in _NODE_SPECS}
    # owner 过滤：他人读 → None
    assert store.get_instance(inst["instance_id"], "u:other") is None
    assert store.get_instance(inst["instance_id"], "u:abc") is not None


def test_create_rejects_over_cap_and_empty(store):
    with pytest.raises(ValueError):
        store.create_instance(
            package_id="p", package_version="1.0.0", package_fingerprint="f",
            owner_scope="u:1", node_specs=[])
    with pytest.raises(ValueError):
        store.create_instance(
            package_id="p", package_version="1.0.0", package_fingerprint="f",
            owner_scope="u:1",
            node_specs=[{"node_id": f"n{i}", "optional": False}
                        for i in range(C.MAX_INSTANCE_NODES + 1)])


# ── store：节点级 CAS 转移 ───────────────────────────────────────────────

def test_transition_happy_path_and_illegal(store):
    inst = _make(store)
    iid = inst["instance_id"]
    r = store.transition_node(iid, "data:subject", C.NodeState.READY,
                              expected_from=C.NodeState.PENDING,
                              reason="deps_ok", event="dispatch")
    assert r.ok and r.state == C.NodeState.READY
    # PENDING→SUCCEEDED 跳级非法
    r2 = store.transition_node(iid, "cap:buffer", C.NodeState.SUCCEEDED)
    assert not r2.ok and r2.code == "ILLEGAL_TRANSITION"
    # 终态 CANCELLED 不可再转
    store.transition_node(iid, "cap:output:zone".replace("cap:", ""),
                          C.NodeState.READY, expected_from=C.NodeState.PENDING)
    r3 = store.transition_node(iid, "output:zone", C.NodeState.CANCELLED)
    assert r3.ok
    r4 = store.transition_node(iid, "output:zone", C.NodeState.READY)
    assert not r4.ok


def test_cas_conflict_detected(store):
    """expected_from 不匹配 → CAS_CONFLICT（而非静默覆盖）。"""
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    r = store.transition_node(iid, "data:subject", C.NodeState.RUNNING,
                              expected_from=C.NodeState.PENDING)
    assert not r.ok and r.code == "CAS_CONFLICT" and r.state == C.NodeState.READY


def test_claim_and_completion_token(store):
    """READY→RUNNING 认领；完成校验 claim token（双执行者协调）。"""
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    r = store.transition_node(iid, "data:subject", C.NodeState.RUNNING,
                              expected_from=C.NodeState.READY,
                              claim=True, claimed_by="rt-1")
    assert r.ok
    node = store.get_node(iid, "data:subject")
    assert node["claimed_by"] == "rt-1"
    # 他人 token 完成被拒
    r_bad = store.transition_node(
        iid, "data:subject", C.NodeState.SUCCEEDED,
        require_claim=True, claimed_by="rt-2",
        patch={"output_ref": "ref:geojson-x"})
    assert not r_bad.ok and r_bad.code == "CLAIM_MISMATCH"
    # 正主完成成功（幂等重复完成 → OK_IDEMPOTENT）
    r_ok = store.transition_node(
        iid, "data:subject", C.NodeState.SUCCEEDED,
        require_claim=True, claimed_by="rt-1",
        patch={"output_ref": "ref:geojson-x"})
    assert r_ok.ok
    r_again = store.transition_node(
        iid, "data:subject", C.NodeState.SUCCEEDED,
        require_claim=True, claimed_by="rt-1", complete=True,
        patch={"output_ref": "ref:geojson-x"})
    assert r_again.ok and r_again.code == "OK_IDEMPOTENT"


def test_transition_ring_bounded(store):
    inst = _make(store)
    iid = inst["instance_id"]
    nid = "data:subject"
    # 反复 READY/STALE 往返（SUCCEEDED 经 STALE 回 READY）
    for _ in range(10):
        store.transition_node(iid, nid, C.NodeState.READY,
                              expected_from=C.NodeState.PENDING)
        store.transition_node(iid, nid, C.NodeState.SUCCEEDED)  # STALE→? no: READY 无→SUCCEEDED
        break
    # 直接验证环形上界：连续多次合法转移
    store.reset = None
    for i in range(12):
        cur = store.get_node(iid, nid)["state"]
        if cur == C.NodeState.READY:
            store.transition_node(iid, nid, C.NodeState.STALE)
        elif cur == C.NodeState.STALE:
            store.transition_node(iid, nid, C.NodeState.READY)
        elif cur == C.NodeState.PENDING:
            store.transition_node(iid, nid, C.NodeState.READY,
                                  expected_from=C.NodeState.PENDING)
    node = store.get_node(iid, nid)
    assert len(node["transitions"]) <= C.MAX_NODE_TRANSITIONS


# ── store：租约 / 孤儿清扫 / 会话实例 ────────────────────────────────────

def test_run_lease_mutual_exclusion(store):
    inst = _make(store)
    iid = inst["instance_id"]
    assert store.acquire_run_lease(iid, owner_scope="u:abc", token="drv-1")
    assert not store.acquire_run_lease(iid, owner_scope="u:abc", token="drv-2")
    assert store.acquire_run_lease(iid, owner_scope="u:abc", token="drv-1")


def test_orphan_recovery_requires_expired_lease(store):
    inst = _make(store)
    iid = inst["instance_id"]
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING)
    store.transition_node(iid, "data:subject", C.NodeState.RUNNING,
                          expected_from=C.NodeState.READY, claim=True,
                          claimed_by="rt-1")
    # 无租约（从未 acquire）→ 孤儿
    assert store.find_orphan_running_nodes(iid) == ["data:subject"]
    # 活租约 → 非孤儿
    store.acquire_run_lease(iid, owner_scope="u:abc", token="drv-1")
    assert store.find_orphan_running_nodes(iid) == []


def test_session_listing_and_supersede_target(store):
    _make(store, session="s-shared")
    inst2 = _make(store, session="s-shared")
    rows = store.list_session_instances("s-shared", owner_scope="u:abc")
    assert len(rows) == 2
    updated = store.update_instance(
        inst2["instance_id"], owner_scope="u:abc",
        fields={"status": C.InstanceStatus.SUPERSEDED, "terminal_at": None})
    # update_instance 不做状态词表校验（由 service 层裁决）——但 owner 隔离生效
    assert updated["status"] == C.InstanceStatus.SUPERSEDED
    assert store.update_instance(inst2["instance_id"], owner_scope="u:other",
                                 fields={"status": "running"}) is None


# ── registry ─────────────────────────────────────────────────────────────

class _FakePkg:
    def __init__(self, fp="aa" * 32, version="1.0.0", pid="recipe-x"):
        self.package_id = pid
        self.version = version
        self.schema_version = "1.0.0"
        self.compiler_version = "4.0.0"
        self.methodology_family = "proximity"
        self.recipe_fingerprint = "rf"
        self.methodology_fingerprint = "mf"
        self.fingerprint = fp

    def to_bounded_dict(self):
        return {"compiled_form": {"typed_dag": {"nodes": [], "edges": []}}}


def test_register_idempotent_and_conflict(reg):
    p = _FakePkg()
    row = reg.register(p, owner_scope="u:abc")
    assert row["status"] == RG.PKG_DRAFT
    again = reg.register(_FakePkg(), owner_scope="u:abc")
    assert again["fingerprint"] == row["fingerprint"]
    with pytest.raises(RG.PackageConflict):
        reg.register(_FakePkg(fp="bb" * 32), owner_scope="u:abc")


def test_publish_and_resolve_semver(reg):
    reg.register(_FakePkg(version="1.0.0"), owner_scope="u:abc")
    reg.register(_FakePkg(version="1.1.0"), owner_scope="u:abc")
    assert reg.publish("recipe-x", "1.0.0", owner_scope="u:abc")
    assert reg.publish("recipe-x", "1.1.0", owner_scope="u:abc")
    latest = reg.resolve("recipe-x", owner_scope="u:abc")
    assert latest["version"] == "1.1.0" and latest["status"] == RG.PKG_PUBLISHED
    explicit = reg.resolve("recipe-x", owner_scope="u:abc", version="1.0.0")
    assert explicit["version"] == "1.0.0"
    # 旧版本重放可解析（reproducibility）
    assert explicit["compiled_form"] is not None
    # 他人 owner 不可解析
    assert reg.resolve("recipe-x", owner_scope="u:other") is None


# ── reuse index ──────────────────────────────────────────────────────────

def _rec(owner="u:abc", fp="aa" * 16, level="content", session_scope=""):
    return RU.ReuseRecord(
        owner_scope=owner, reuse_fingerprint=fp, session_scope=session_scope,
        node_id="cap:buffer", package_fingerprint="pkg" * 16,
        artifact_ref="ref:geojson-1", artifact_session_id="s1",
        fingerprint_level=level,
        input_fingerprints={"input": {"level": "content", "fp": "x" * 32,
                                      "content_revision": "3"}},
        algorithm_id="buffer", params_fp="p" * 32, env_fp="e" * 32,
        source_instance_id="wi-1")


def test_reuse_record_find_and_lru_prune(index):
    assert index.record(_rec())
    found = index.find("u:abc", "aa" * 16)
    assert found is not None and found.artifact_ref == "ref:geojson-1"
    # LRU：超过上限后最旧被剪（指纹 32 位内互异，避免截断碰撞）
    for i in range(RU.MAX_ENTRIES_PER_OWNER + 8):
        assert index.record(_rec(fp=f"w{i:030x}")), i
    # 最旧 9 条 = aa + i=0..7（137 - 128）
    assert index.find("u:abc", "aa" * 16) is None
    assert index.find("u:abc", "w000000000000000000000000000000") is None  # i=0
    assert index.find("u:abc", f"w{8:030x}") is not None  # 幸存边界
    # owner 隔离
    assert index.find("u:other", f"w{8:030x}") is None


def test_eligibility_rules(index):
    rec = _rec()
    probe = lambda sid, ref: {"ok": True}  # noqa: E731
    ok, why = RU.evaluate_eligibility(
        rec, package_fingerprint="pkg" * 16,
        current_inputs={"input": {"fp": "x" * 32, "content_revision": "3"}},
        descriptor_probe=probe)
    assert ok and why == "ok"
    # shape 级不复用
    ok2, why2 = RU.evaluate_eligibility(
        _rec(level="shape"), package_fingerprint="pkg" * 16,
        current_inputs={"input": {"fp": "x" * 32, "content_revision": "3"}},
        descriptor_probe=probe)
    assert not ok2 and "shape" in why2
    # 输入内容变化 → miss
    ok3, why3 = RU.evaluate_eligibility(
        rec, package_fingerprint="pkg" * 16,
        current_inputs={"input": {"fp": "y" * 32, "content_revision": "3"}},
        descriptor_probe=probe)
    assert not ok3 and why3 == "input_changed:input"
    # revision 变化（同 ref 原地覆写）→ miss
    ok4, why4 = RU.evaluate_eligibility(
        rec, package_fingerprint="pkg" * 16,
        current_inputs={"input": {"fp": "x" * 32, "content_revision": "4"}},
        descriptor_probe=probe)
    assert not ok4 and why4 == "input_revision_changed:input"
    # 包指纹变化 → miss
    ok5, why5 = RU.evaluate_eligibility(
        rec, package_fingerprint="other" * 16,
        current_inputs={"input": {"fp": "x" * 32, "content_revision": "3"}},
        descriptor_probe=probe)
    assert not ok5 and why5 == "package_changed"
    # ref 失效 → miss
    ok6, why6 = RU.evaluate_eligibility(
        rec, package_fingerprint="pkg" * 16,
        current_inputs={"input": {"fp": "x" * 32, "content_revision": "3"}},
        descriptor_probe=lambda sid, ref: None)
    assert not ok6 and why6 == "artifact_unresolvable"


def test_anonymous_session_scope():
    assert RU.anonymous_session_scope("anonymous", "s1") != ""
    assert RU.anonymous_session_scope("u:abc", "s1") == ""


# ── 迁移单 head + up/down/up ─────────────────────────────────────────────

def test_migration_single_head():
    """全链无双 head（R1-M1 撞号守卫；引号风格双兼容）。"""
    import re as _re
    from pathlib import Path

    revs: dict = {}
    downs: set = set()
    for f in Path("migrations/versions").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        m = _re.search(
            r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", text, _re.M)
        d = _re.search(
            r"^down_revision(?::[^=]*)?\s*=\s*"
            r"(?:Union\[[^\]]*,\s*)?['\"]([^'\"]+)['\"]", text, _re.M)
        if m:
            revs[m.group(1)] = f.name
        if d:
            downs.add(d.group(1))
    heads = [r for r in revs if r not in downs]
    assert heads == ["0034_workflow_v5_runtime"], heads


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    """真实 alembic 链（in-process command；DATABASE_URL 覆写防 CI PG 漂移）。"""
    from alembic.config import Config

    db_path = tmp_path / "wf_v5_runtime.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg, str(db_path)


def _tables(db_path):
    import sqlite3

    con = sqlite3.connect(db_path)
    tabs = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    return tabs


def test_migration_upgrade_downgrade_upgrade(alembic_cfg):
    """0034 up→down(0033)→up 可重入；CHECK/唯一约束随表建出。"""
    from alembic import command

    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    tabs = _tables(db_path)
    for t in ("workflow_packages", "workflow_instances",
              "workflow_instance_nodes", "workflow_node_reuse"):
        assert t in tabs, t
    command.downgrade(cfg, "0033_geocompute_v6_cluster")
    tabs2 = _tables(db_path)
    assert "workflow_packages" not in tabs2
    assert "workflow_instance_nodes" not in tabs2
    command.upgrade(cfg, "head")
    for t in ("workflow_packages", "workflow_instances",
              "workflow_instance_nodes", "workflow_node_reuse"):
        assert t in _tables(db_path), t
