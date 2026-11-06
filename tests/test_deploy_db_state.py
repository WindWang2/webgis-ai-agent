"""Regression tests for the entrypoint's PostgreSQL schema-state probe.

Execute the actual embedded Python and catalog query against a small SQLite
catalog fixture. Only PostgreSQL's regclass casts and table visibility function
are adapted; joins and extension-membership predicates run as SQL. No Docker or
live database is needed, and the shell behavior is covered by migration_wiring.
"""

from contextlib import nullcontext
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import sqlalchemy
import yaml


ENTRYPOINT = Path(__file__).resolve().parents[1] / "deploy/docker-entrypoint.sh"


def _extension_member(oid, **overrides):
    return {
        "classid": "pg_catalog.pg_class",
        "objid": oid,
        "objsubid": 0,
        "refclassid": "pg_catalog.pg_extension",
        "deptype": "e",
        **overrides,
    }


@pytest.fixture
def probe(monkeypatch, capsys):
    def run(tables, dependencies=(), dialect="postgresql"):
        # Each table has an OID, name, and visibility in the connection's search
        # path. Distinct OIDs allow a user table to shadow a same-named extension
        # table in another schema without becoming an extension member itself.
        with sqlite3.connect(":memory:") as catalog:
            catalog.execute("ATTACH DATABASE ':memory:' AS pg_catalog")
            catalog.execute(
                "CREATE TABLE pg_catalog.pg_class "
                "(oid INTEGER, relname TEXT, visible BOOLEAN)"
            )
            catalog.executemany(
                "INSERT INTO pg_catalog.pg_class VALUES (?, ?, ?)", tables
            )
            catalog.execute(
                "CREATE TABLE pg_catalog.pg_depend "
                "(classid TEXT, objid INTEGER, objsubid INTEGER, "
                "refclassid TEXT, deptype TEXT)"
            )
            catalog.executemany(
                "INSERT INTO pg_catalog.pg_depend VALUES "
                "(:classid, :objid, :objsubid, :refclassid, :deptype)",
                dependencies,
            )
            visible = {oid for oid, _, is_visible in tables if is_visible}
            catalog.create_function("pg_table_is_visible", 1, lambda oid: oid in visible)

            def execute(statement):
                assert dialect == "postgresql", "Other dialects must not query pg_catalog"
                # SQLite does not have regclass OIDs or schema-qualified
                # functions. The fixture uses catalog names as their IDs.
                sql = str(statement).replace("::regclass", "")
                sql = sql.replace("pg_catalog.pg_table_is_visible", "pg_table_is_visible")
                rows = catalog.execute(sql).fetchall()
                return SimpleNamespace(scalars=lambda: (row[0] for row in rows))

            connection = SimpleNamespace(execute=execute)
            engine = SimpleNamespace(
                dialect=SimpleNamespace(name=dialect),
                connect=lambda: nullcontext(connection),
                dispose=lambda: None,
            )
            inspector = SimpleNamespace(
                get_table_names=lambda: [name for _, name, is_visible in tables if is_visible]
            )
            monkeypatch.setattr(sqlalchemy, "create_engine", lambda url: engine)
            monkeypatch.setattr(sqlalchemy, "inspect", lambda bind: inspector)
            monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
            source = ENTRYPOINT.read_text(encoding="utf-8")
            python = source.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
            exec(compile(python, str(ENTRYPOINT), "exec"), {})
            return capsys.readouterr().out.strip()

    return run


def test_postgres_without_tables_is_fresh(probe):
    assert probe([]) == "fresh"


def test_postgis_extension_tables_are_fresh(probe):
    # Public PostGIS table plus visible tiger/topology extension tables, as
    # initialized by the production PostGIS image before application migrations.
    tables = [(1, "spatial_ref_sys", True), (2, "addr", True), (3, "topology", True)]
    assert probe(tables, [_extension_member(oid) for oid, _, _ in tables]) == "fresh"


def test_extension_ownership_not_a_table_name_allowlist(probe):
    assert probe([(1, "another_extension_table", True)], [_extension_member(1)]) == "fresh"


def test_postgis_with_unversioned_application_table_is_legacy(probe):
    tables = [(1, "spatial_ref_sys", True), (2, "users", True)]
    assert probe(tables, [_extension_member(1)]) == "legacy"


def test_postgis_named_table_without_extension_membership_is_legacy(probe):
    assert probe([(1, "spatial_ref_sys", True)]) == "legacy"


@pytest.mark.parametrize(
    "override",
    [
        {"classid": "pg_catalog.pg_type"},
        {"refclassid": "pg_catalog.pg_class"},
        {"deptype": "n"},
        {"objsubid": 1},
    ],
)
def test_only_table_extension_membership_excludes_a_table(probe, override):
    assert probe([(1, "users", True)], [_extension_member(1, **override)]) == "legacy"


def test_hidden_extension_member_cannot_hide_visible_user_table(probe):
    tables = [(1, "spatial_ref_sys", False), (2, "spatial_ref_sys", True)]
    assert probe(tables, [_extension_member(1)]) == "legacy"


def test_alembic_version_still_marks_database_as_managed(probe):
    tables = [(1, "spatial_ref_sys", True), (2, "alembic_version", True)]
    assert probe(tables, [_extension_member(1)]) == "managed"


def test_non_postgres_table_names_are_not_filtered(probe):
    assert probe([(1, "spatial_ref_sys", True)], dialect="sqlite") == "legacy"


def test_real_postgis_ci_uses_entrypoint_before_alembic_upgrade():
    root = ENTRYPOINT.parent.parent
    workflow = yaml.safe_load((root / ".github/workflows/production.yml").read_text())
    steps = workflow["jobs"]["db-migrations"]["steps"]
    bootstrap = next(step for step in steps if step.get("run") == "sh deploy/docker-entrypoint.sh true")
    upgrade = next(step for step in steps if step.get("run") == "alembic upgrade head")
    assert steps.index(bootstrap) < steps.index(upgrade)
    assert bootstrap["env"]["DATABASE_URL"] == upgrade["env"]["DATABASE_URL"]
    assert bootstrap["env"]["ADOPT_LEGACY_SCHEMA"] == "false"
    assert bootstrap["env"]["SKIP_DB_MIGRATIONS"] == "false"
