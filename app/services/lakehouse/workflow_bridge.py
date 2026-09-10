"""Workflow / Data Fabric bridges — Versioned Lakehouse V8 (ADR-0130 §6).

workflow run 产物 → dataset 版本的**发布桥**，与 lakehouse dataset
版本 → Data Fabric 描述符的**注册适配**。边界纪律（并发边界）：

- **绝不侵入** workflow_engine / promotion / fabric planner 内部 ——
  本模块只读 Artifact/WorkflowRun 台账模型，写侧只经
  ``dataset_registry`` 的既有原子协议；
- provenance 从 run/artifact 台账投影（redact 口径与 registry 一致），
  绝不伪造未记录的事实（缺失 = 显式 None）。

workflow → version 的可追踪性（验收契约）：版本 provenance 携带
``workflow_run_id`` + ``workflow_step`` + ``algorithm`` +``parameters``
（redacted），台账行有 ``workflow_run_id`` 列与索引 ——
run → dataset version 一跳可查，version → run 反查同链。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

_ID64_RE = re.compile(r"^[a-f0-9]{64}\Z")


class WorkflowBridgeError(ValueError):
    """workflow 产物 → dataset 版本桥的契约违例。"""

    code = "LAKEHOUSE_BRIDGE_INVALID"


def commit_dataset_version_from_artifact(
    db,
    dataset_row,
    *,
    artifact_id: str,
    branch: Optional[str] = None,
    run_id: Optional[str] = None,
    provenance_extra: Optional[Mapping[str, Any]] = None,
):
    """把已晋升的 workflow 产物（Artifact）提交为 dataset 新版本。

    内容解析（诚实边界）：
    - ``Artifact.storage_ref`` 必须是**可解析的 DataObject id**（64-hex
      且 manifest 在场）—— promotion 的 durable blob 通道产物即此形态；
      其余形态（裸路径 / ref cursor）= typed 拒绝（本桥绝不发明内容，
      字节真相仍只有 BlobStore）；
    - owner 链：dataset.owner 与 content manifest owner scope 必须一致
      （dataset_registry 的 owner 校验兜底）。

    provenance 投影：``workflow_run_id``（参数 > artifact metadata）、
    ``workflow_step`` / ``algorithm`` / ``parameters``（artifact
    metadata_json 的 step_id/tool_name/tool_args —— 经 redact）。

    Returns (CommitResult, data_object_id)。只 flush 不 commit。
    """
    from sqlalchemy import select

    from app.models.project import Artifact
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.data_object import resolve_data_object

    artifact = db.execute(
        select(Artifact).where(Artifact.id == str(artifact_id))
    ).scalar_one_or_none()
    if artifact is None:
        raise WorkflowBridgeError(f"artifact not found: {str(artifact_id)[:32]}")
    storage_ref = str(artifact.storage_ref or "")
    if not _ID64_RE.match(storage_ref):
        raise WorkflowBridgeError(
            "artifact storage_ref is not a lakehouse data object id — "
            "promote via the durable blob channel first"
        )
    if resolve_data_object(storage_ref) is None:
        raise WorkflowBridgeError(
            f"data object not resolvable: {storage_ref[:12]}"
        )

    meta = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
    provenance: Dict[str, Any] = {
        "workflow_step": meta.get("step_id"),
        "algorithm": meta.get("tool_name") or meta.get("capability"),
    }
    run = str(run_id or meta.get("workflow_run_id") or "")
    if run:
        provenance["workflow_run_id"] = run
    args = meta.get("tool_args")
    if isinstance(args, Mapping):
        provenance["parameters"] = dict(args)
    if provenance_extra:
        if not isinstance(provenance_extra, Mapping):
            raise WorkflowBridgeError("provenance_extra must be a mapping")
        provenance.update(dict(provenance_extra))

    result = reg.commit_version(
        db,
        dataset_row,
        branch=branch or dataset_row.default_branch,
        data_object_id=storage_ref,
        action="workflow_publish",
        provenance=provenance,
        workflow_run_id=run or None,
    )
    return result, storage_ref


def version_to_fabric_descriptor(
    *,
    dataset_row,
    version_dict: Mapping[str, Any],
) -> Any:
    """dataset 版本 → Data Fabric :class:`DatasetDescriptor`（只读适配）。

    不注册进任何 fabric 内部状态 —— 调用方（Data Fabric 注册通道）决定
    生命周期。诚实默认：CRS/bbox/feature_count 从 manifest payload 有界
    投影，缺席 = None（绝不伪造 EPSG:4326 / 全球 extent / 0 行 ——
    fabric 契约的 C2 纪律）。
    """
    from app.schemas.data_fabric_schema import DatasetDescriptor

    manifest = version_dict.get("manifest") or {}
    payload = manifest.get("payload") or {}
    bbox = payload.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) != 4:
        bbox = None
    feature_count = payload.get("feature_count")
    provenance = version_dict.get("provenance") or {}
    return DatasetDescriptor(
        id=f"lakehouse:{dataset_row.dataset_id}@{version_dict['version_id'][:12]}",
        source_type="lakehouse",
        source_id=str(version_dict.get("data_object_id") or ""),
        title=str(dataset_row.name),
        name=str(dataset_row.name),
        description=str(dataset_row.description or ""),
        data_type=payload.get("data_type") or "unknown",
        feature_type=payload.get("feature_type") or "unknown",
        srs=payload.get("crs"),
        crs=payload.get("crs"),
        bbox=[float(v) for v in bbox] if bbox else None,
        feature_count=int(feature_count) if feature_count is not None else None,
        metadata={
            "version_id": str(version_dict.get("version_id")),
            "dataset_id": str(dataset_row.dataset_id),
            "branch": str(version_dict.get("branch") or ""),
            "action": str(version_dict.get("action") or ""),
            "workflow_run_id": provenance.get("workflow_run_id"),
            "kind": str(manifest.get("kind") or ""),
        },
    )
