"""Alembic env.py — 桥接 Alembic 与 SQLAlchemy 模型。

审计 I6：之前 env.py 不读 DATABASE_URL，依赖 alembic.ini 里的占位符
`sqlalchemy.url = driver://user:pass@localhost/dbname` —— 任何真实 DB
上跑 `alembic upgrade head` 都会连不上。同时 migrations/versions/ 不存在，
无法生成或应用 revision。生产环境一直靠 init_db() 的 Base.metadata.create_all
+ SQLite-only _apply_runtime_migrations 兜底，没有真正的 schema 版本管理。
"""
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 审计 I6：优先从环境读 DATABASE_URL，覆盖 alembic.ini 的占位符。
# 这让 `alembic upgrade head` 在 docker-compose / k8s 里直接可用。
db_url = os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# add your model's MetaData object here
# for 'autogenerate' support
from app.core.database import Base  # noqa: E402

# 审计 I6：必须显式 import 所有 model 模块，让它们的表注册到 Base.metadata。
# 否则 autogenerate 看到 metadata 是空的 → 生成的 revision upgrade() 是 pass。
import app.models.db_model  # noqa: F401, E402  (registers Organization/User/Layer/etc.)
import app.models.report    # noqa: F401, E402  (registers Report)
import app.models.upload    # noqa: F401, E402  (registers UploadRecord)
import app.models.knowledge_base  # noqa: F401, E402  (registers KnowledgeDocument etc., 若存在)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url and url.startswith("sqlite"),  # SQLite ALTER 兼容
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    url = config.get_main_option("sqlalchemy.url") or ""
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),  # SQLite ALTER 兼容
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
