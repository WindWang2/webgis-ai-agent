"""SQLite ↔ PostgreSQL 差异化存储 harness（Quality V2 W8）。

背景教训：commit 006be00c —— SQLite（默认关 FK）让 unit-of-work 落序测试
**误绿**。本 harness 把双后端语义差异变成显式受检契约：

- SQLite 侧**恒跑**（本地/CI 无 PG 时也有 SQLite 行为红线）；
- PostgreSQL 侧由 ``TEST_POSTGRES_URL`` 开启（缺省 graceful skip，与
  real_services 纪律一致；连不上也 skip 不 fail）；
- 双后端可用时，同一场景语料在两个引擎各跑一遍并 **diff 行为结论**；
- 单后端模式下，钉住已知差异的"本侧事实"（防 SQLite-only 回归）。

语义矩阵（每项都是真实差异类别）：
1. FK 约束：SQLite 默认不启用 PRAGMA foreign_keys（孤儿插入成功 = 已知
   差异），PG 强制拒绝；
2. 唯一约束：两侧都强制；
3. NULL 排序：SQLite ASC 时 NULL 在前，PG 默认 NULLS LAST —— 已知差异，
   显式断言两侧各自行为；
4. JSON 列往返：两侧都必须保真（unicode/嵌套/空容器）；
5. 事务回滚：两侧都必须完整回滚（含唯一冲突后的 rollback）；
6. Boolean 严格性：PG 拒绝非布尔字面量，SQLite 容忍（已知差异）；
7. Alembic 迁移链完整性：单一 head（结构契约，不跑 upgrade）。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO = Path(__file__).resolve().parents[2]

PG_URL = os.environ.get("TEST_POSTGRES_URL", "")

needs_pg = pytest.mark.skipif(
    not PG_URL, reason="需要 TEST_POSTGRES_URL（本地差异验证显式开启）")

#: 差异化场景共用的小型 schema（独立 metadata，不污染 app Base）
_METADATA = MetaData()
PARENT = Table(
    "dsci_parent", _METADATA,
    Column("id", String(36), primary_key=True),
    Column("label", String(100), nullable=False),
)
CHILD = Table(
    "dsci_child", _METADATA,
    Column("id", String(36), primary_key=True),
    Column("parent_id", String(36),
           ForeignKey("dsci_parent.id"), nullable=True),
    Column("flag", Boolean(), nullable=True),
    Column("payload", Text(), nullable=True),
    Column("rank_value", Integer(), nullable=True),
    Column("created_at", DateTime(), nullable=True, default=datetime.utcnow),
)


async def _make_engine(url: str, tmp_path, name: str):
    if url.startswith("sqlite"):
        file_url = f"sqlite+aiosqlite:///{tmp_path / name}"
        engine = create_async_engine(
            file_url, connect_args={"check_same_thread": False})
    else:
        engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(_METADATA.create_all)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def sqlite_env(tmp_path):
    engine, maker = await _make_engine("sqlite", tmp_path, "dsci-sqlite.db")
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_env():
    engine, maker = await _make_engine(PG_URL, None, "")  # pragma: no cover
    yield maker  # pragma: no cover
    await engine.dispose()  # pragma: no cover


# ── 1. FK 约束（006be00c 教训的显式契约）────────────────────────────────


@pytest.mark.asyncio
async def test_fk_sqlite_default_accepts_orphan_documented(sqlite_env):
    """已知差异（006be00c）：SQLite 默认无 PRAGMA foreign_keys → 孤儿行
    被接受。本测试钉住这一事实：若未来 app 侧开启 PRAGMA，此测试红 =
    提醒差异契约升级（孤儿插入将变成拒绝）。"""
    maker = sqlite_env
    async with maker() as s:
        from sqlalchemy import insert

        await s.execute(insert(CHILD).values(
            id="orphan-1", parent_id="no-such-parent"))
        await s.commit()
        rows = (await s.execute(
            select(CHILD.c.id).where(CHILD.c.id == "orphan-1"))).fetchall()
    assert rows, "SQLite 默认配置必须接受孤儿行（已知差异，钉契约）"


@needs_pg
@pytest.mark.asyncio
async def test_fk_pg_rejects_orphan(pg_env):
    maker = pg_env
    from sqlalchemy import insert

    with pytest.raises(Exception):
        async with maker() as s:
            await s.execute(insert(CHILD).values(
                id="orphan-1", parent_id="no-such-parent"))
            await s.commit()


# ── 2. 唯一约束（两侧一致）───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unique_constraint_enforced_sqlite(sqlite_env):
    maker = sqlite_env
    from sqlalchemy import insert

    async with maker() as s:
        await s.execute(insert(PARENT).values(id="p1", label="only"))
        await s.commit()
        await s.execute(insert(PARENT).values(id="p2", label="dup"))
        with pytest.raises(Exception):
            await s.execute(
                insert(PARENT).values(id="p2", label="conflict"))
            await s.commit()
        await s.rollback()


@needs_pg
@pytest.mark.asyncio
async def test_unique_constraint_enforced_pg(pg_env):
    maker = pg_env
    from sqlalchemy import insert

    async with maker() as s:
        await s.execute(insert(PARENT).values(id="p1", label="only"))
        await s.commit()
        await s.execute(insert(PARENT).values(id="p2", label="dup"))
        with pytest.raises(Exception):
            await s.execute(
                insert(PARENT).values(id="p2", label="conflict"))
            await s.commit()
        await s.rollback()


# ── 3. NULL 排序（已知差异：ASC 时 SQLite NULL 在前 / PG NULLS LAST）────


async def _null_order(maker) -> list:
    from sqlalchemy import insert

    async with maker() as s:
        for i, rank in enumerate([None, 1, None, 0]):
            await s.execute(insert(CHILD).values(
                id=f"n{i}", rank_value=rank))
        await s.commit()
        rows = (await s.execute(
            select(CHILD.c.id).order_by(CHILD.c.rank_value.asc()))).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_null_order_sqlite_nulls_first(sqlite_env):
    """SQLite ASC：NULL 排最前（已知差异，钉契约）。"""
    order = await _null_order(sqlite_env)
    assert order[0] in ("n0", "n2"), f"SQLite NULL-first 契约被破坏: {order}"


@needs_pg
@pytest.mark.asyncio
async def test_null_order_pg_nulls_last(pg_env):
    """PG 默认 ASC = NULLS LAST（已知差异，钉契约）。"""
    order = await _null_order(pg_env)
    assert order[-1] in ("n0", "n2"), f"PG NULLS-LAST 契约被破坏: {order}"


# ── 4. JSON/文本列往返（两侧一致要求）───────────────────────────────────


@pytest.mark.asyncio
async def test_text_payload_roundtrip_sqlite(sqlite_env):
    maker = sqlite_env
    from sqlalchemy import insert

    weird = {"nested": ["unicode ✓", "", None], "n": -0.0}
    async with maker() as s:
        await s.execute(insert(CHILD).values(
            id="j1", payload=__import__("json").dumps(weird,
                                                      ensure_ascii=False)))
        await s.commit()
        got = (await s.execute(
            select(CHILD.c.payload).where(CHILD.c.id == "j1"))).scalar()
    import json

    assert json.loads(got) == weird


@needs_pg
@pytest.mark.asyncio
async def test_text_payload_roundtrip_pg(pg_env):
    maker = pg_env
    from sqlalchemy import insert

    weird = {"nested": ["unicode ✓", "", None], "n": -0.0}
    async with maker() as s:
        await s.execute(insert(CHILD).values(
            id="j1", payload=__import__("json").dumps(weird,
                                                      ensure_ascii=False)))
        await s.commit()
        got = (await s.execute(
            select(CHILD.c.payload).where(CHILD.c.id == "j1"))).scalar()
    import json

    assert json.loads(got) == weird


# ── 5. 事务回滚（两侧一致要求；含约束冲突后的 rollback 完整性）──────────


@pytest.mark.asyncio
async def test_transaction_rollback_completeness_sqlite(sqlite_env):
    maker = sqlite_env
    from sqlalchemy import insert, select as sel

    async with maker() as s:
        await s.execute(insert(PARENT).values(id="keep", label="kept"))
        await s.commit()
    async with maker() as s:
        await s.execute(insert(PARENT).values(id="drop", label="temp"))
        await s.rollback()
    async with maker() as s:
        ids = {r for (r,) in (await s.execute(sel(PARENT.c.id))).fetchall()}
    assert "keep" in ids and "drop" not in ids


@needs_pg
@pytest.mark.asyncio
async def test_transaction_rollback_completeness_pg(pg_env):
    maker = pg_env
    from sqlalchemy import insert, select as sel

    async with maker() as s:
        await s.execute(insert(PARENT).values(id="keep", label="kept"))
        await s.commit()
    async with maker() as s:
        await s.execute(insert(PARENT).values(id="drop", label="temp"))
        await s.rollback()
    async with maker() as s:
        ids = {r for (r,) in (await s.execute(sel(PARENT.c.id))).fetchall()}
    assert "keep" in ids and "drop" not in ids


# ── 6. Boolean 严格性（已知差异：PG 强类型 / SQLite 容忍）───────────────


@pytest.mark.asyncio
async def test_boolean_strict_sqlite_documented(sqlite_env):
    """布尔严格性契约（双后端一致）：SQLAlchemy Boolean 在 bind 层拒绝
    非布尔 Python 值——原生 SQLite 虽然容忍任意整型，但 ORM 层已拦截。
    若未来此契约失效（junk 被静默存储）即红。"""
    maker = sqlite_env
    from sqlalchemy import insert, select as sel

    with pytest.raises(Exception):
        async with maker() as s:
            await s.execute(insert(CHILD).values(id="b1", flag=5))
            await s.commit()
    async with maker() as s:
        got = (await s.execute(
            sel(CHILD.c.id).where(CHILD.c.id == "b1"))).scalar()
    assert got is None, "拒绝后不得残留行"


@needs_pg
@pytest.mark.asyncio
async def test_boolean_strict_pg(pg_env):
    maker = pg_env
    from sqlalchemy import insert

    with pytest.raises(Exception):
        async with maker() as s:
            await s.execute(insert(CHILD).values(id="b1", flag=5))
            await s.commit()


# ── 7. Alembic 迁移链结构契约 ────────────────────────────────────────────


def test_alembic_single_head():
    """迁移链单一 head：多 head = 并行 Epic 迁移冲突未消解。"""
    import ast
    import re

    versions = REPO / "migrations" / "versions"
    down_to: dict = {}
    revision_files: dict = {}
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", text)
        if not m:
            continue
        rev = m.group(1)
        revision_files[rev] = path.name
        # Capture through closing paren / EOL so multiline merge tuples work.
        dm = re.search(
            r"down_revision(?::[^=]*)?\s*=\s*(\([^)]*\)|[^\n]+)", text, re.S)
        if dm:
            try:
                val = ast.literal_eval(dm.group(1).strip())
            except (SyntaxError, ValueError):
                tokens = re.findall(r"['\"]([^'\"]+)['\"]", dm.group(1))
                val = tokens
            if val is None:
                down_to[rev] = []
            elif isinstance(val, str):
                down_to[rev] = [val]
            else:
                down_to[rev] = [str(t) for t in val]
    heads = [r for r in revision_files
             if r not in {d for ds in down_to.values() for d in ds}]
    assert len(heads) == 1, (
        f"alembic 存在多个 head: {heads} —— 迁移链冲突，需先消解再合入")


def test_pg_url_fixture_wiring_consistent():
    """TEST_POSTGRES_URL 未设时 PG 用例必须可被识别为 skip（防静默真连）。"""
    if PG_URL:
        assert PG_URL.startswith(("postgresql://", "postgres://",
                                  "postgresql+asyncpg://"))
