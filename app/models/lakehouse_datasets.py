"""Lakehouse dataset registry models — Versioned Lakehouse V8 (ADR-0130).

**台账而非字节真相**：字节真相仍只有 BlobStore（CAS）；数据集描述符与
版本 commit record 是**内容寻址 manifest**（id = canonical sha256，与
DataObject 同纪律）；本模块的三张表是它们的**durable 台账**：

- ``lakehouse_datasets``：数据集注册行（描述符 manifest id = dataset_id）；
- ``lakehouse_dataset_versions``：append-only 版本账本（commit manifest
  id = version_id；parent 链 = 版本 DAG）；幂等键
  ``(dataset_row_id, version_id)`` —— 同 dataset 同 commit 只有一行；
- ``lakehouse_dataset_refs``：命名指针。branch = 可变指针（乐观并发：
  ``generation`` 单调，更新走 CAS 比对）；tag = 不可变指针（创建后拒绝
  改写）。指针行让 branch/tag/rollback 成为 O(1) 元数据操作 ——
  **绝不为版本复制数据字节**（CoW 增量仍归 cube store 层）。

GC 契约：三张表引用的全部 64-hex id（dataset_id / version_id /
data_object_id）必须进入 ``lakehouse_gc._protected_references`` ——
仅被 dataset 版本历史引用的 manifest/blob 绝不被回收。

JSON 列与 ``lakehouse_catalog`` 同款（PG JSONB / 其余 JSON variant）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.core.database import Base

#: JSON 列类型：PG 用 JSONB，其余 JSON（与 catalog 投影同款 variant）。
DatasetJSON = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LakehouseDataset(Base):
    """数据集注册行（描述符 manifest 的 durable 台账行）。"""

    __tablename__ = "lakehouse_datasets"

    id = Column(String(36), primary_key=True, default=_uuid)
    #: 描述符 manifest id（64 hex，内容寻址 —— 描述符不可变；改名 = 新 dataset）。
    dataset_id = Column(String(64), nullable=False)
    owner_type = Column(String(20), nullable=False)
    owner_id = Column(String(128), nullable=False)
    #: ADR-0139 租户作用域（organizations.id 字符串原文；明文纪律同
    #: geocompute_runs.org_id）。版本/分支行经 dataset_row_id 继承同一 org。
    org_id = Column(String(255), nullable=False)
    #: 用户可读名（charset 白名单在 service 边界强制；非身份）。
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True, default="")
    default_branch = Column(String(128), nullable=False, default="main")
    #: cube 契约（dims/variables/CRS 等 —— cube 型 dataset 的对齐声明）。
    cube_contract = Column(DatasetJSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("dataset_id", name="uq_lh_dataset_descriptor"),
        Index("idx_lh_ds_owner_created", "owner_type", "owner_id", "created_at"),
        Index("idx_lh_ds_org_created", "org_id", "created_at"),
        CheckConstraint("owner_type IN ('session','project')",
                        name="ck_lh_ds_owner_type"),
    )

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "name": self.name,
            "description": self.description,
            "default_branch": self.default_branch,
            "cube_contract": self.cube_contract,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LakehouseDatasetVersion(Base):
    """数据集版本账本行（append-only；commit manifest 的 durable 台账）。

    ``version_id`` = commit manifest 的 canonical sha256（内容寻址，
    同 dataset 同 commit 幂等去重）；``parent_version_id`` 构成版本 DAG
    （线性 branch 历史 = DAG 的一条链）；``data_object_id`` 指向本版本
    的内容 DataObject（manifest，非分支指针）。
    """

    __tablename__ = "lakehouse_dataset_versions"

    id = Column(String(36), primary_key=True, default=_uuid)
    dataset_row_id = Column(
        String(36),
        ForeignKey("lakehouse_datasets.id"),
        nullable=False,
    )
    #: ADR-0139 租户作用域——随 dataset 行传播（commit_version 写入侧取）。
    org_id = Column(String(255), nullable=False)
    #: commit manifest id（64 hex）。
    version_id = Column(String(64), nullable=False)
    #: 父版本（首个版本为 NULL —— DAG 根）。
    parent_version_id = Column(String(64), nullable=True)
    #: 本版本内容 DataObject id（64 hex）。
    data_object_id = Column(String(64), nullable=False)
    content_sha256 = Column(String(64), nullable=False, default="")
    byte_size = Column(BigInteger().with_variant(Integer(), "sqlite"),
                       nullable=False, default=0)
    #: commit 落点分支（历史记录；分支当前 head 以 refs 表为准）。
    branch = Column(String(128), nullable=False)
    #: commit | rollback | delta | import | workflow_publish。
    action = Column(String(32), nullable=False, default="commit")
    #: 完整 provenance（有界 —— V8 provenance 契约，见 dataset_registry）。
    provenance_json = Column(DatasetJSON, nullable=False, default=dict)
    workflow_run_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("dataset_row_id", "version_id",
                         name="uq_lh_dsv_dataset_version"),
        Index("idx_lh_dsv_dataset_created", "dataset_row_id", "created_at"),
        Index("idx_lh_dsv_org_created", "org_id", "created_at"),
        Index("idx_lh_dsv_run", "workflow_run_id"),
        Index("idx_lh_dsv_object", "data_object_id"),
        CheckConstraint(
            "action IN ('commit','rollback','delta','import','workflow_publish')",
            name="ck_lh_dsv_action",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "parent_version_id": self.parent_version_id,
            "data_object_id": self.data_object_id,
            "content_sha256": self.content_sha256,
            "byte_size": int(self.byte_size or 0),
            "branch": self.branch,
            "action": self.action,
            "provenance": self.provenance_json or {},
            "workflow_run_id": self.workflow_run_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LakehouseDatasetRef(Base):
    """命名指针行：branch（可变，generation CAS）与 tag（不可变）。

    branch 更新走乐观并发：``UPDATE ... WHERE generation = <observed>``
    —— 撞号 = 并发移动 → typed 冲突（调用方重读重试）；tag 创建后
    唯一约束拒绝任何改写（不可变指针）。
    """

    __tablename__ = "lakehouse_dataset_refs"

    id = Column(String(36), primary_key=True, default=_uuid)
    dataset_row_id = Column(
        String(36),
        ForeignKey("lakehouse_datasets.id"),
        nullable=False,
    )
    #: ADR-0139 租户作用域——随 dataset 行传播（branch/tag 创建侧取）。
    org_id = Column(String(255), nullable=False)
    ref_type = Column(String(8), nullable=False)
    ref_name = Column(String(128), nullable=False)
    version_id = Column(String(64), nullable=False)
    #: 乐观并发代数（每次 branch 移动 +1；tag 恒 1）。
    generation = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("dataset_row_id", "ref_type", "ref_name",
                         name="uq_lh_dsr_dataset_ref"),
        Index("idx_lh_dsr_dataset", "dataset_row_id", "ref_type"),
        Index("idx_lh_dsr_org", "org_id", "created_at"),
        CheckConstraint("ref_type IN ('branch','tag')", name="ck_lh_dsr_type"),
    )

    def to_dict(self) -> dict:
        return {
            "ref_type": self.ref_type,
            "ref_name": self.ref_name,
            "version_id": self.version_id,
            "generation": int(self.generation or 0),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
