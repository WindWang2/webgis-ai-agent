"""Spatial Data Lakehouse & Cube V6 (ADR-0118).

组合层（composition layer）：本包**不是**第二个 registry/store —— 字节
真相只有 ``durable_blob_store.BlobStore`` 一份，会话台账归
``artifact_registry``，durable revision 归 ``artifact_revisions``，
哈希/复用口径归 ``app/lib/data.fingerprints``。本包只提供数据对象的
身份、cube 运行时、lazy 读取、DR 与 retention 组合逻辑。
"""
from app.services.lakehouse.data_object import (  # noqa: F401
    DataObjectError,
    DataObjectIdentity,
    DataObjectTooLargeError,
    build_object_manifest,
    compute_content_root,
    compute_object_reuse_fingerprint,
    is_data_object_id,
    materialize_data_object,
    normalize_owner_scope,
    owner_scope_allows,
    publish_data_object,
    resolve_data_object,
    verify_data_object,
)
from app.services.lakehouse.dataset_registry import (  # noqa: F401
    CommitResult,
    DatasetNotFound,
    DatasetRegistryError,
    build_commit_record,
    build_dataset_descriptor,
    commit_version,
    create_branch,
    create_dataset,
    create_tag,
    dataset_descriptor_id,
    list_versions,
    resolve_commit_record,
    resolve_dataset_descriptor,
    resolve_version,
    rollback_branch,
)
