"""#1750-1: SQLite 开发/测试 schema 的 FK 级联 no-op —— 显式契约。

背景：``migrations/versions/e46935cd5dd1`` 的 SQLite 分支刻意只建
``knowledge_chunks`` 表，不施加 ``ON DELETE`` 级联与 CHECK 约束（其
docstring 自承「ON DELETE clauses and CHECK constraints are enforced on
PostgreSQL. We only create the missing knowledge_chunks table here」）；
而 ``app/models/db_model.py`` 大量声明 ``ondelete="CASCADE"/"SET NULL"``。

后果：SQLite 侧 ``Base.metadata.create_all`` 生成的 DDL **文本里带**
``ON DELETE ...`` 子句，但 SQLite 连接默认 ``PRAGMA foreign_keys=0``
不 enforcement —— ORM 关系级联语义与库实际约束系统性分叉（测试绿、
生产 Postgres 走 Alembic 才有真级联）。

本文件把这一已知分叉钉成显式契约：
- 迁移 SQLite 分支不做 FK/CHECK retrofit（无 ``ALTER TABLE``）；
- create_all 的 SQLite DDL 声明级联但连接默认不执行；
- 删父行后子行残留（孤儿）—— 应用层若依赖级联删除必须自行处理。
契约升级（如测试 schema 打开 foreign_keys=ON 并补应用层级联）时
这些测试会红，从而迫使差异被显式重新评估，而不是静默漂移。
"""
import ast
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.models.db_model import Layer, Organization, User

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    REPO_ROOT / "migrations" / "versions"
    / "e46935cd5dd1_add_foreignkey_ondelete_and_check_.py"
)

#: ORM 声明 ondelete 的表（db_model.py）—— 分叉契约的覆盖面。
_TABLES_WITH_ONDELETE = ("users", "layers", "analysis_tasks",
                         "layer_permissions", "conversations", "messages",
                         "cartography_templates")


def _function_source(path: Path, name: str) -> str:
    """函数完整源码（含 docstring）。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{path.name}: 找不到函数 {name}")


def _function_body_source(path: Path, name: str) -> str:
    """函数体的语句源码（剔除 docstring —— 它含描述性 ALTER TABLE 字样）。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            return "\n".join(
                ast.get_source_segment(source, stmt) or "" for stmt in body
            )
    raise AssertionError(f"{path.name}: 找不到函数 {name}")


# ── 1. 迁移侧：SQLite 分支是刻意的 FK no-op ─────────────────────────────


def test_sqlite_migration_branch_skips_fk_retrofit():
    """e46935cd5dd1 的 _upgrade_sqlite 不得做 ALTER TABLE FK/CHECK retrofit。

    Postgres 分支（_upgrade_postgres）对 users/layers/analysis_tasks/
    layer_permissions/conversations/messages 逐表 ADD CONSTRAINT ... ON
    DELETE；SQLite 分支只允许 CREATE TABLE/INDEX（knowledge_chunks）。
    若未来给 SQLite 补上级联，本测试红 = 差异契约需显式升级。
    """
    src = _function_body_source(MIGRATION, "_upgrade_sqlite")
    assert "ALTER TABLE" not in src, (
        "_upgrade_sqlite 出现 ALTER TABLE —— SQLite 分支开始 retrofit "
        "FK/CHECK 约束；请同步更新 #1750 的差异契约测试"
    )
    # docstring 显式自承 no-op 语义（问题描述引用的原文）。
    doc = _function_source(MIGRATION, "_upgrade_sqlite").lower()
    assert "knowledge_chunks" in doc and "enforced on postgresql" in doc, (
        "_upgrade_sqlite docstring 应显式声明 ON DELETE/CHECK 仅 PostgreSQL "
        "强制执行（已知差异的文档化锚点）"
    )


def test_postgres_migration_branch_does_retrofit_fk_cascades():
    """对照锚点：_upgrade_postgres 确实施加 ON DELETE 级联。

    没有这一条，「SQLite 分支没有级联」可能被误读成两个分支都没有级联；
    钉住 PG 侧行为后，分叉本身成为显式受检事实。
    """
    src = _function_body_source(MIGRATION, "_upgrade_postgres")
    assert "ON DELETE CASCADE" in src
    assert "users_org_id_fkey" in src
    assert "messages_conversation_id_fkey" in src


# ── 2. 运行时：create_all 的 SQLite schema 声明级联但不执行 ──────────────


def _fk_sqlite_db(tmp_path):
    """独立临时 SQLite 库（只建相关三张表），默认连接参数 = 无 foreign_keys。"""
    db_file = tmp_path / "fk_noop.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(
        engine, tables=[Organization.__table__, User.__table__, Layer.__table__]
    )
    return engine


def test_sqlite_default_connection_does_not_enforce_fk(tmp_path):
    """默认连接的 PRAGMA foreign_keys 为 0 —— SQLite 侧无 DB 级联执行。"""
    engine = _fk_sqlite_db(tmp_path)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 0


def test_sqlite_ddl_declares_ondelete_but_orphans_survive(tmp_path):
    """分叉本体：DDL 文本带 ON DELETE 子句（ORM 声明），删除父行后
    子行残留（库不执行级联）。

    - layers.org_id / layers.creator_id 在 db_model.py:55-56 声明
      CASCADE / SET NULL；
    - SQLite 上删 organization / user 后 layers 行仍在且 creator_id
      未被置 NULL —— 与 Postgres（Alembic 施加约束）语义相反。
    """
    engine = _fk_sqlite_db(tmp_path)
    with engine.connect() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='layers'")
        ).scalar()
        assert "ON DELETE CASCADE" in ddl and "ON DELETE SET NULL" in ddl, (
            "layers DDL 应保留 ORM 声明的 ondelete 子句（声明存在但不被执行）"
        )

        conn.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (1, 'org', 'org')")
        )
        conn.execute(
            text("INSERT INTO users (id, org_id, username, email) "
                 "VALUES ('u1', 1, 'alice', 'alice@example.com')")
        )
        conn.execute(
            text("INSERT INTO layers (id, org_id, creator_id, name, layer_type) "
                 "VALUES (1, 1, 'u1', 'L', 'vector')")
        )
        conn.execute(text("DELETE FROM organizations WHERE id = 1"))
        assert conn.execute(
            text("SELECT COUNT(*) FROM layers WHERE id = 1")
        ).scalar() == 1, "SQLite 无 ON DELETE CASCADE：删 org 后 layers 应残留"
        conn.execute(text("DELETE FROM users WHERE id = 'u1'"))
        assert conn.execute(
            text("SELECT COUNT(*) FROM layers WHERE id = 1")
        ).scalar() == 1, "SQLite 无 ON DELETE SET NULL：删 user 后 layers 应残留"
        assert conn.execute(
            text("SELECT creator_id FROM layers WHERE id = 1")
        ).scalar() == "u1", "SQLite 不执行 SET NULL：creator_id 应保持原值"


def test_ondelete_models_are_covered_by_contract():
    """契约覆盖面自检：db_model.py 里每个声明 ondelete 的表都仍在分叉清单。

    新增带 ondelete 的模型表时必须显式登记到 _TABLES_WITH_ONDELETE，
    否则 _upgrade_postgres 的 retrofit 覆盖面与 ORM 声明会静默分叉。
    """
    import inspect as py_inspect

    import app.models.db_model as dbm

    declared = set()
    for _name, obj in vars(dbm).items():
        if py_inspect.isclass(obj) and issubclass(obj, Base) and hasattr(obj, "__table__"):
            for fk in obj.__table__.foreign_keys:
                if fk.ondelete:
                    declared.add(obj.__tablename__)
    assert declared, "db_model.py 应存在声明 ondelete 的 FK"
    missing = declared - set(_TABLES_WITH_ONDELETE)
    assert not missing, (
        f"新表声明了 ondelete 但未登记进差异契约清单: {sorted(missing)}"
    )


def test_inspector_reports_no_ondelete_metadata_for_sqlite(tmp_path):
    """反射层确认：SQLite inspector 不报告 ondelete（约束只在 DDL 文本里）。

    与 test_sqlite_ddl_declares_ondelete_but_orphans_survive 互补 ——
    文本有、反射无、执行更无，三层证据钉死 no-op 语义。
    """
    engine = _fk_sqlite_db(tmp_path)
    insp = inspect(engine)
    fks = insp.get_foreign_keys("layers")
    assert fks, "layers 应至少有一个 FK"
    assert all(fk.get("ondelete") is None for fk in fks), (
        "SQLite inspector 不应报告 ondelete（默认不 enforcement）"
    )
