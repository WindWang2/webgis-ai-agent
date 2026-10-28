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
# disable_existing_loggers=False：默认 True 会把进程内已配置的 logger 全部
# 置 disabled（app.* 全静音）。对 CLI 迁移无感，但对同进程调用
# `alembic.command.upgrade` 的单元测试是顺序污染源——之后所有 caplog
# 断言都收不到 app.* 记录。
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 审计 I6：优先从环境读 DATABASE_URL，覆盖 alembic.ini 的占位符。
# 这让 `alembic upgrade head` 在 docker-compose / k8s 里直接可用。

# add your model's MetaData object here
# for 'autogenerate' support
from app.core.database import Base, _to_sync_url  # noqa: E402

db_url = os.getenv("DATABASE_URL")
if db_url:
    # #1592：2.1.x 裸 postgres URL 默认方言改走 psycopg v3（lock 只有 psycopg2-binary），
    # 与 get_engine() 同源同纪律，显式钉 psycopg2 驱动。
    db_url = _to_sync_url(db_url)
    # #1638：alembic Config 底层是 configparser（默认 BasicInterpolation），
    # set_main_option 写入时即校验插值语法——URL 里的裸 %（百分号编码密码如
    # p%40ss；RDS/Azure 托管库强密码含 @/%/: 是常态）会抛 ValueError:
    # invalid interpolation syntax，upgrade head 崩在 env.py 入口、部署拒绝
    # 启动（而 create_engine 原生接受 % URL，故障形态不对称、难排查）。
    # 按 configparser 协议写 %% 转义；读回（get_main_option/get_section）时
    # 自动还原为单 %，online/offline 两个 mode 与 drift check 消费的都是原 URL。
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

# 审计 I6：必须显式 import 所有 model 模块，让它们的表注册到 Base.metadata。
# 否则 autogenerate 看到 metadata 是空的 → 生成的 revision upgrade() 是 pass。
import app.models.db_model  # noqa: F401, E402  (registers Organization/User/Layer/etc.)
import app.models.report    # noqa: F401, E402  (registers Report)
import app.models.upload    # noqa: F401, E402  (registers UploadRecord)
import app.models.knowledge_base  # noqa: F401, E402  (registers KnowledgeDocument etc., 若存在)
import app.models.project   # noqa: F401, E402  (registers Project/Workflow/WorkflowRun/Artifact etc.)
import app.models.data_fabric  # noqa: F401, E402  (registers DataSource/CatalogItem/Materialization)
import app.models.story_map_asset  # noqa: F401, E402  (registers StoryMapAsset, G3D05)
import app.models.data_quality  # noqa: F401, E402  (V9: registers QualityReport/QualityRuleResult)
import app.models.data_lifecycle  # noqa: F401, E402  (V9: registers LifecycleObject/LifecyclePolicy/GcPlan)
import app.models.template_version  # noqa: F401, E402  (V9: registers TemplateVersion)
import app.models.cartography_quality  # noqa: F401, E402  (AC-10/ADR-0159: CartographyQualityRun/Metric)
import app.models.mission  # noqa: F401, E402  (ADR-0197: GIS Mission Runtime)
import app.models.project_knowledge  # noqa: F401, E402  (Project Knowledge Projection 读模型)
import app.models.lakehouse_catalog  # noqa: F401, E402  (ADR-0119: lakehouse_catalog_items)
import app.models.lakehouse_datasets  # noqa: F401, E402  (lakehouse_datasets/versions/refs)
import app.models.intent_learning  # noqa: F401, E402  (V11 W1/ADR-0161: carto intent 学习三表)
import app.models.spatial_memory  # noqa: F401, E402  (方向 9/ADR-0255: gis_spatial_memories)
import app.models.spatial_events  # noqa: F401, E402  (空间事件控制平面四表)
import app.models.harness_journal  # noqa: F401, E402  (H04/ADR-0268: turn_events 会话账本)
import app.models.gis_context  # noqa: F401, E402  (ADR-0206: gis_working_contexts)
import app.models.composition_template_asset  # noqa: F401, E402  (G04: composition_template_assets(_versions))
import app.models.ads_fabric  # noqa: F401, E402  (ADR-0175/0178: ads_* 三张账本镜像)
# 注意：漏 import 任何一个 model 模块，autogenerate 都会把该模块的表当作
# 「metadata 里不存在」→ 对已迁移库生成 drop_table（数据丢失风险）。
# security F-09：手工清单已漏掉 ads_fabric / gis_context / harness_journal
# （5 张线上表）。兜底遍历 app.models 下全部子模块，新增模块无需再登记。
# walk_packages 递归展开子包：iter_modules 只列一层，将来若建
# app/models/<子包>/，包内模块不会被兜底 import（漏注册 = drop_table
# 数据丢失风险的潜在入口）。
import importlib  # noqa: E402
import pkgutil  # noqa: E402

import app.models  # noqa: E402

for _mod in pkgutil.walk_packages(app.models.__path__):
    importlib.import_module(f"app.models.{_mod.name}")

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
