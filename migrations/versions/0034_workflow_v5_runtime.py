"""Workflow Runtime V5: packages / instances / nodes / reuse index

Revision ID: 0034_workflow_v5_runtime
Revises: 0033_geocompute_v6_cluster
Create Date: 2026-09-09

Epic workflow-v5（架构 .agent-work/workflow-v5-executable-runtime/
01-architecture.md §3）—— 四个 additive 新表，无数据改写：

1. ``workflow_packages`` — durable WorkflowPackage registry（semver 发布态；
   (package_id, version) 唯一）。包内容真相仍是 V4 编译器（注册时 re-emit
   比对指纹）—— 本表只管「存在/发布」。
2. ``workflow_instances`` — 运行实例行（实例级 CAS：status/revision/租约/
   pending 变更/决策环）。
3. ``workflow_instance_nodes`` — **每节点一行**（节点级 state CAS，消除
   整行 JSON 争用 [R1-C3]）。
4. ``workflow_node_reuse`` — 复用索引（缓存 fail-open；(owner, fingerprint)
   唯一，每 owner LRU 剪枝；shape 级只记录不复用 [R1-M3]）。

全部 create_all-coexistence guard 保护的**可重入** DDL（repo convention，
0022-0033 同款）；索引按模型声明逐一建出（漂移守卫
tests/test_deploy_migration_wiring.py 按列元组比对）。downgrade 反序回滚。

撞号注记 [R1-M1]（#1221/D-10 修订）：多分支撞号有两种被接受的消解方式 ——
(a) 重编号为下一空号并改 down_revision（01-architecture.md §16 rebase 协议）；
(b) merge revision 收敛（先例：c0d8322aa2cb 合并 4 个 0034 head、e7a51c9d2f04
合并 3 个 0035 head）。本仓实际采用 (b)：图完整性由 preflight migration_heads
守护，人工阅读 versions/ 目录时以 down_revision 图为准，NNNN 前缀不蕴含顺序。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0034_workflow_v5_runtime"
down_revision: Union[str, Sequence[str], None] = "0033_geocompute_v6_cluster"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PKG_TABLE = "workflow_packages"
_INST_TABLE = "workflow_instances"
_NODE_TABLE = "workflow_instance_nodes"
_REUSE_TABLE = "workflow_node_reuse"

_PKG_INDEXES = {
    "idx_wf_pkg_owner": ["owner_scope", "package_id"],
}
_INST_INDEXES = {
    "idx_wf_inst_owner": ["owner_scope", "instance_id"],
    "idx_wf_inst_session": ["session_id", "status"],
    "idx_wf_inst_parent": ["parent_instance_id"],
}
_NODE_INDEXES = {
    "idx_wf_node_inst_state": ["instance_id", "state"],
}
_REUSE_INDEXES = {
    "idx_wf_reuse_owner_created": ["owner_scope", "created_at"],
}

_PKG_CHECKS = (
    ("ck_wf_pkg_status", "status IN ('draft','published','deprecated')"),
)
_INST_CHECKS = (
    ("ck_wf_inst_status",
     "status IN ('running','succeeded','failed','cancelled','superseded')"),
)
_NODE_CHECKS = (
    ("ck_wf_node_state",
     "state IN ('PENDING','READY','RUNNING','SUCCEEDED','FAILED',"
     "'BLOCKED','SKIPPED','CANCELLED','STALE')"),
)


def _table_exists(table: str) -> bool:
    """create_all-coexistence guard（repo convention，0022-0033 同款）。"""
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _constraint_exists(table: str, name: str) -> bool:
    return name in {
        ck["name"]
        for ck in sa.inspect(op.get_bind()).get_check_constraints(table)
    }


def _create_table(table: str, *columns, checks=(), indexes=None) -> None:
    """建表（CHECK 内联 —— SQLite 不可 ALTER 约束；新表无存量改装场景）。

    create_all 共存守卫：表已存在时跳过 —— create_all 表自带模型声明的
    同名约束，语义等价。
    """
    if _table_exists(table):
        return
    args = list(columns) + [
        sa.CheckConstraint(expr, name=name) for name, expr in checks
    ]
    op.create_table(table, *args)
    for name, cols in (indexes or {}).items():
        if not _index_exists(table, name):
            op.create_index(name, table, cols)


def _drop_table(table: str) -> None:
    if _table_exists(table):
        op.drop_table(table)


def upgrade() -> None:
    _create_table(
        _PKG_TABLE,
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  autoincrement=True, primary_key=True),
        sa.Column("package_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("compiler_version", sa.String(length=16), nullable=False),
        sa.Column("methodology_family", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("recipe_fingerprint", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("methodology_fingerprint", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("environment_fingerprint", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("compiled_form", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("owner_scope", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_scope", "package_id", "version",
                            name="uq_wf_pkg_owner_id_ver"),
        checks=_PKG_CHECKS,
        indexes=_PKG_INDEXES,
    )

    _create_table(
        _INST_TABLE,
        sa.Column("instance_id", sa.String(length=64), primary_key=True),
        sa.Column("package_id", sa.String(length=64), nullable=False),
        sa.Column("package_version", sa.String(length=16), nullable=False),
        sa.Column("package_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("owner_scope", sa.String(length=40), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("project_id", sa.String(length=255), nullable=True),
        sa.Column("parent_instance_id", sa.String(length=64), nullable=True),
        sa.Column("parent_node_id", sa.String(length=64), nullable=True),
        sa.Column("run_lease_owner", sa.String(length=64), nullable=True),
        sa.Column("run_lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pending_changes", sa.JSON(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("visited_packages", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(), nullable=True),
        checks=_INST_CHECKS,
        indexes=_INST_INDEXES,
    )

    _create_table(
        _NODE_TABLE,
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  autoincrement=True, primary_key=True),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("bound_ref", sa.String(length=96), nullable=True),
        sa.Column("output_ref", sa.String(length=96), nullable=True),
        sa.Column("output_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.Column("reuse", sa.JSON(), nullable=False),
        sa.Column("attempts_log", sa.JSON(), nullable=False),
        sa.Column("transitions", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("instance_id", "node_id", name="uq_wf_node_inst_node"),
        checks=_NODE_CHECKS,
        indexes=_NODE_INDEXES,
    )

    _create_table(
        _REUSE_TABLE,
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  autoincrement=True, primary_key=True),
        sa.Column("owner_scope", sa.String(length=40), nullable=False),
        sa.Column("reuse_fingerprint", sa.String(length=32), nullable=False),
        sa.Column("session_scope", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("package_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("artifact_ref", sa.String(length=96), nullable=False),
        sa.Column("artifact_session_id", sa.String(length=255), nullable=False),
        sa.Column("fingerprint_level", sa.String(length=24), nullable=False, server_default="shape"),
        sa.Column("input_fingerprints", sa.JSON(), nullable=False),
        sa.Column("algorithm_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("params_fp", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("env_fp", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source_instance_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_scope", "reuse_fingerprint",
                            name="uq_wf_reuse_owner_fp"),
        indexes=_REUSE_INDEXES,
    )


def downgrade() -> None:
    _drop_table(_REUSE_TABLE)
    _drop_table(_NODE_TABLE)
    _drop_table(_INST_TABLE)
    _drop_table(_PKG_TABLE)
