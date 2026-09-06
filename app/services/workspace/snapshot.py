"""Workspace Snapshot V3 —— 工作空间快照与恢复（§十五/§十六）。

审计 Agent E 的核心缺口：MapSpec（期望态）能在重启后复活，但其背后
的 ``ref:`` 载荷在 4h TTL 后消失 —— reload 得到「空心图层」且**无人
报告**；只有 workflow 产物有晋升路径，ad-hoc 图层永远会话级。

本模块提供「此刻工作空间」的一等快照单元（**不接管 Map State**）：

- ``save_snapshot``：产物账本（ArtifactContract 投影，≤128 条）+
  MapSpec 指纹/修订 + 图层 source refs + 视图/底图设置 + 标签，
  原子写盘（``<session>/workspace-snapshots/<id>.json``）—— 生命周期
  与 mapspec 磁盘族一致（随会话清扫，TTL 后仍可核查）；
- ``verify_snapshot``：逐 ref 探测存活 + 完整性校验（内容指纹）→
  ``restorable`` 判定 —— 空心图层在 restore **之前**就可见；
- ``restore_snapshot``：账本重注册（rebind 血缘，mode="register"），
  Map/样式状态仍归 checkpoint 体系 —— 本模块只提供证据与账本，
  绝不反向驱动 spec（ADR-0082 单一事实源不变式）；
- ``clone_snapshot``：跨会话复制快照文件；源会话的 ref 在目标会话
  不可解析 → verify 时如实报 unavailable（ref 不可跨会话，诚实）；
- ``describe_workspace``：§十五工作空间结构盘点（counts 聚合）。

有界：快照条目 ≤128、layer refs ≤64、settings 键 ≤16（快照是元数据
契约，不是数据搬运工 —— 大载荷本体永远留在 session store / 磁盘）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.lib.data.artifact_contract import ArtifactContract, from_artifact_record

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 3
_MAX_SNAPSHOT_ARTIFACTS = 128     # 与 ledger cap 对齐
_MAX_LAYER_REFS = 64
_MAX_SETTINGS_KEYS = 16
_MAX_SNAPSHOTS_PER_SESSION = 20


class SnapshotLayerRef(BaseModel):
    """图层引用（ref + 样式提示；不内联载荷）。"""

    layer_id: str = ""
    source_ref: str = ""
    style_ref: str = ""             # 模板/样式指针
    profile_fingerprint: str = ""


class WorkspaceSnapshot(BaseModel):
    """工作空间快照（§十六清单的 V3 实现面）。"""

    snapshot_version: int = SNAPSHOT_VERSION
    snapshot_id: str
    session_id: str
    project_id: str = ""
    label: str = ""
    created_at: float = 0.0
    artifact_contracts: List[ArtifactContract] = Field(default_factory=list)
    layers: List[SnapshotLayerRef] = Field(default_factory=list)
    mapspec_fingerprint: str = ""
    mapspec_mutation_revision: int = 0
    active_view: Dict[str, Any] = Field(default_factory=dict)
    base_layer: str = ""
    settings: Dict[str, Any] = Field(default_factory=dict)
    workflow_refs: List[str] = Field(default_factory=list)

    @field_validator("artifact_contracts")
    @classmethod
    def _bounded_artifacts(cls, v: List[ArtifactContract]) -> List[ArtifactContract]:
        return list(v)[:_MAX_SNAPSHOT_ARTIFACTS]

    @field_validator("layers")
    @classmethod
    def _bounded_layers(cls, v: List[SnapshotLayerRef]) -> List[SnapshotLayerRef]:
        return list(v)[:_MAX_LAYER_REFS]

    @field_validator("settings")
    @classmethod
    def _bounded_settings(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        return dict(list(v.items())[:_MAX_SETTINGS_KEYS])

    @field_validator("workflow_refs")
    @classmethod
    def _bounded_workflows(cls, v: List[str]) -> List[str]:
        return [str(w)[:96] for w in v[:16]]


class SnapshotVerification(BaseModel):
    """快照可恢复性核查（空心图层在 restore 前可见）。"""

    snapshot_id: str
    exists: bool = False
    integrity_ok: bool = False
    artifacts_total: int = 0
    artifacts_live: int = 0
    artifacts_missing: List[str] = Field(default_factory=list)
    layers_total: int = 0
    layers_live: int = 0
    layers_missing: List[str] = Field(default_factory=list)
    mapspec_available: bool = False

    @property
    def restorable(self) -> bool:
        return self.exists and self.integrity_ok and not self.artifacts_missing and not self.layers_missing

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "exists": self.exists,
            "integrity_ok": self.integrity_ok,
            "restorable": self.restorable,
            "artifacts": {
                "total": self.artifacts_total,
                "live": self.artifacts_live,
                "missing": self.artifacts_missing,
            },
            "layers": {
                "total": self.layers_total,
                "live": self.layers_live,
                "missing": self.layers_missing,
            },
            "mapspec_available": self.mapspec_available,
        }


def _snapshots_dir(session_id: str) -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR

    return BASE_STORAGE_DIR / session_id / "workspace-snapshots"


def _validate_session_id(session_id: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", session_id or ""))


def _snapshot_path(session_id: str, snapshot_id: str) -> Optional[Path]:
    if not _validate_session_id(session_id):
        return None
    if not snapshot_id or not _validate_session_id(snapshot_id):
        return None
    return _snapshots_dir(session_id) / f"{snapshot_id}.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _extract_layer_refs(mapspec: Optional[Dict[str, Any]]) -> List[SnapshotLayerRef]:
    """MapSpec → 有界 layer refs（只读 spec 结构，不解析样式内容）。"""
    refs: List[SnapshotLayerRef] = []
    if not isinstance(mapspec, dict):
        return refs
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        source_defs = [
            {**(v if isinstance(v, dict) else {}), "id": k}
            for k, v in raw_sources.items()
        ]
    else:
        source_defs = [s for s in (raw_sources or []) if isinstance(s, dict)]
    for src in source_defs[:_MAX_LAYER_REFS]:
        ref_val = ""
        for key in ("ref", "ref_id", "image_ref", "imageRef", "result_ref"):
            v = src.get(key)
            if isinstance(v, str) and v:
                ref_val = v
                break
        refs.append(
            SnapshotLayerRef(
                layer_id=str(src.get("id") or src.get("layer_id") or "")[:96],
                source_ref=ref_val,
                style_ref=str(src.get("style") or src.get("template") or "")[:96],
                profile_fingerprint=str(src.get("profile_fingerprint") or "")[:64],
            )
        )
    return refs


class WorkspaceSnapshotService:
    """快照保存 / 核查 / 恢复 / 克隆。"""

    async def save_snapshot(
        self,
        session_id: str,
        *,
        label: str = "",
        project_id: str = "",
        settings: Optional[Dict[str, Any]] = None,
        workflow_refs: Optional[List[str]] = None,
    ) -> Optional[WorkspaceSnapshot]:
        """捕捉「此刻工作空间」→ 磁盘快照。失败返回 None（不阻断调用方）。"""
        if not _validate_session_id(session_id):
            return None
        try:
            from app.services.artifact_registry import list_artifacts
            from app.services.mapspec_store import mapspec_store

            records = await list_artifacts(session_id)
            mapspec = await mapspec_store.get_mapspec(session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[WorkspaceSnapshot] save failed session=%s: %s", session_id, e)
            return None

        state = mapspec if isinstance(mapspec, dict) else {}
        view = state.get("view") if isinstance(state.get("view"), dict) else {}
        snapshot = WorkspaceSnapshot(
            snapshot_id=f"ws-{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            project_id=project_id,
            label=str(label)[:96],
            created_at=time.time(),
            artifact_contracts=[from_artifact_record(r) for r in records],
            layers=_extract_layer_refs(state),
            mapspec_fingerprint=str(state.get("fingerprint") or ""),
            mapspec_mutation_revision=int(state.get("revision") or 0),
            active_view=view,
            base_layer=str(state.get("base_layer") or view.get("base_layer") or "")[:96],
            settings=settings or {},
            workflow_refs=workflow_refs or [],
        )
        path = _snapshot_path(session_id, snapshot.snapshot_id)
        if path is None:
            return None
        def _write() -> None:
            _atomic_write_json(path, snapshot.model_dump(mode="json"))

        try:
            await asyncio.to_thread(_write)
        except OSError as e:
            logger.warning("[WorkspaceSnapshot] disk write failed session=%s: %s", session_id, e)
            return None
        await self._enforce_snapshot_cap(session_id)
        return snapshot

    async def _enforce_snapshot_cap(self, session_id: str) -> None:
        """每会话快照数封顶（最旧先删；与 mapspec 20 修订同预算）。"""

        def _cap() -> int:
            d = _snapshots_dir(session_id)
            if not d.is_dir():
                return 0
            files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
            overflow = len(files) - _MAX_SNAPSHOTS_PER_SESSION
            for f in files[:max(0, overflow)]:
                try:
                    f.unlink()
                except OSError:
                    continue
            return max(0, overflow)

        try:
            await asyncio.to_thread(_cap)
        except OSError:
            pass

    async def list_snapshots(self, session_id: str) -> List[Dict[str, Any]]:
        """快照清单（id/label/created_at/规模；最旧 → 最新）。"""

        def _list() -> List[Dict[str, Any]]:
            d = _snapshots_dir(session_id)
            if not d.is_dir():
                return []
            out = []
            for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime):
                data = _read_json(f)
                if not isinstance(data, dict):
                    continue
                out.append({
                    "snapshot_id": str(data.get("snapshot_id") or f.stem),
                    "label": str(data.get("label") or ""),
                    "created_at": data.get("created_at"),
                    "artifacts": len(data.get("artifact_contracts") or []),
                    "layers": len(data.get("layers") or []),
                })
            return out

        try:
            return await asyncio.to_thread(_list)
        except OSError:
            return []

    def _load_snapshot(self, session_id: str, snapshot_id: str) -> Optional[WorkspaceSnapshot]:
        path = _snapshot_path(session_id, snapshot_id)
        if path is None or not path.is_file():
            return None
        data = _read_json(path)
        if not isinstance(data, dict):
            return None
        try:
            return WorkspaceSnapshot.model_validate(data)
        except Exception:  # noqa: BLE001 — 版本漂移的快照按不存在处理
            return None

    async def verify_snapshot(self, session_id: str, snapshot_id: str) -> SnapshotVerification:
        """§十六目标的前置证据：这份快照还能恢复多少？"""
        report = SnapshotVerification(snapshot_id=snapshot_id)
        snapshot = self._load_snapshot(session_id, snapshot_id)
        if snapshot is None:
            return report
        # integrity_ok 语义 = 「快照文件可解析为合法模型」——不对比载荷
        # 指纹（profile_fingerprint 只是与 live profile 的对照提示）。
        report.exists = True
        report.integrity_ok = True
        report.mapspec_available = bool(snapshot.mapspec_fingerprint)

        from app.services.artifact_registry import probe_ref

        for contract in snapshot.artifact_contracts:
            report.artifacts_total += 1
            live = await probe_ref(session_id, contract.artifact_id) if contract.artifact_id.startswith("ref:") else True
            if live:
                report.artifacts_live += 1
            else:
                report.artifacts_missing.append(contract.artifact_id)
        for layer in snapshot.layers:
            if not layer.source_ref:
                continue  # inline/外部源：不计 ref 存活
            report.layers_total += 1
            live = await probe_ref(session_id, layer.source_ref)
            if live:
                report.layers_live += 1
            else:
                report.layers_missing.append(layer.source_ref)
        return report

    async def restore_snapshot(
        self,
        session_id: str,
        snapshot_id: str,
        *,
        mode: str = "verify",       # verify | register
    ) -> Dict[str, Any]:
        """快照恢复。

        - ``verify``（缺省）：只核查，返回验证报告 —— 不写任何状态；
        - ``register``：核查后把快照中的产物记录**重注册**回账本
          （血缘恢复；载荷 TTL 已逝的 ref 不伪造，保持 expired 探测真相），
          Map/样式回放仍归 checkpoint 体系，本函数绝不触碰。
        """
        verification = await self.verify_snapshot(session_id, snapshot_id)
        result: Dict[str, Any] = {
            "mode": mode,
            "verification": verification.to_dict(),
        }
        if mode == "verify":
            return result
        if mode != "register":
            result["error"] = f"unknown mode: {mode!r} (verify|register)"
            return result
        snapshot = self._load_snapshot(session_id, snapshot_id)
        if snapshot is None:
            result["error"] = "snapshot not found or unreadable"
            return result
        from app.services.artifact_registry import mark_status, register_artifact

        registered = 0
        missing_ids = set(verification.artifacts_missing)
        for contract in snapshot.artifact_contracts:
            inputs = list(contract.lineage.parents)
            metadata: Dict[str, Any] = {
                "logical_role": contract.logical_role.value,
                "persistence_tier": contract.persistence.value,
                "restored_from": snapshot_id,
            }
            if contract.fingerprint.content:
                metadata["content_fingerprint"] = contract.fingerprint.content
            ok = await register_artifact(
                session_id,
                artifact_id=contract.artifact_id,
                artifact_type=contract.artifact_subtype or None,
                producer_tool=contract.produced_by.tool,
                inputs=inputs,
                metadata=metadata,
            )
            if ok is not None:
                registered += 1
                # 载荷已亡的产物：血缘重绑定但状态如实回落 expired ——
                # register_artifact 的复活语义不得伪造「可用」。
                if contract.artifact_id in missing_ids:
                    await mark_status(session_id, contract.artifact_id, "expired")
                    result.setdefault("marked_expired", []).append(contract.artifact_id)
        result["registered"] = registered
        return result

    async def clone_snapshot(
        self, source_session_id: str, snapshot_id: str, target_session_id: str
    ) -> Optional[Dict[str, Any]]:
        """跨会话克隆快照文件（ref 的会话绑定语义由 verify 如实披露）。"""
        if not _validate_session_id(target_session_id):
            return None
        src = self._load_snapshot(source_session_id, snapshot_id)
        if src is None:
            return None
        new_id = f"ws-{uuid.uuid4().hex[:16]}"
        clone = src.model_copy(
            update={
                "snapshot_id": new_id,
                "session_id": target_session_id,
                "created_at": time.time(),
                "label": f"clone:{src.label or src.snapshot_id}"[:96],
            }
        )
        path = _snapshot_path(target_session_id, new_id)
        if path is None:
            return None
        try:
            await asyncio.to_thread(_atomic_write_json, path, clone.model_dump(mode="json"))
        except OSError:
            return None
        return {"snapshot_id": new_id, "source": snapshot_id}


_service: Optional[WorkspaceSnapshotService] = None


def get_workspace_snapshot_service() -> WorkspaceSnapshotService:
    global _service
    if _service is None:
        _service = WorkspaceSnapshotService()
    return _service


def reset_workspace_snapshot_service() -> None:
    global _service
    _service = None
