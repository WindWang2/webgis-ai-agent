"""Workspace Snapshot V4 —— 持久工作空间（§十五/§十六；audit 02 §6）。

审计 Agent E 的核心缺口：MapSpec（期望态）能在重启后复活，但其背后
的 ``ref:`` 载荷在 4h TTL 后消失 —— reload 得到「空心图层」且**无人
报告**；只有 workflow 产物有晋升路径，ad-hoc 图层永远会话级。

V3 的两个结构性缺口（audit 02 §5.2/§5.4）在 V4 关闭：

- **快照家搬出会话目录**：``DATA_DIR/workspaces/<project_id>/
  workspace-snapshots/``（project_id 白名单 ``[A-Za-z0-9_-]{1,64}``，
  与 session id 同款 charset 纪律）—— 不再随会话 purge/TTL 清扫蒸发；
  旧会话域快照目录仍可读（list 合并两域，诚实 ``home`` 字段）；
- **载荷物化 / 重物化**：``save_snapshot(materialize=...)`` 把存活载荷
  经 Wave-1 BlobStore（唯一持久内容后端）写为 digest 寻址内容并在
  manifest 记**指针**（绝不内联字节）；``restore_snapshot(mode="register")``
  对带指针的契约做 digest 校验读 → 原位写回同一 ref。指针读取失败
  如实回落 ``expired`` + ``degraded`` 披露 —— 绝不把死 ref 标成 valid。

本模块提供「此刻工作空间」的一等快照单元（**不接管 Map State**）：

- ``save_snapshot``：产物账本（ArtifactContract 投影，≤128 条）+
  MapSpec 指纹/修订 + 图层 source refs + layout chartRef/tableRef +
  视图/底图设置 + 标签 + 逐产物持久指针，原子写盘；
- ``verify_snapshot``：逐 ref 探测存活 + 完整性校验（含指针摘要比对：
  ``verified | digest_mismatch | pointer_missing | no_pointer``）→
  ``restorable`` 判定 —— 空心图层在 restore **之前**就可见；
- ``restore_snapshot``：账本重注册（rebind 血缘，mode="register"）+
  指针重物化载荷，Map/样式状态仍归 checkpoint 体系 —— 本模块只提供
  证据与账本，绝不反向驱动 spec（ADR-0082 单一事实源不变式）；
- ``clone_snapshot`` / ``delete_snapshot``：跨会话克隆 / 定点删除；
- ``describe_workspace``：§十五工作空间结构盘点（counts + 持久覆盖率）。

有界：快照条目 ≤128、ref ≤64、settings 键 ≤16、指针 ≤128、每项目快照
≤50（快照是元数据契约 + 指针集合，不是数据搬运工 —— 字节永远只在
BlobStore / session store 一份）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from app.lib.data.artifact_contract import ArtifactContract, from_artifact_record

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 4
_MAX_SNAPSHOT_ARTIFACTS = 128     # 与 ledger cap 对齐
_MAX_LAYER_REFS = 64
_MAX_SETTINGS_KEYS = 16
_MAX_SNAPSHOTS_PER_SESSION = 20
_MAX_SNAPSHOTS_PER_PROJECT = 50   # 项目域保留上限（audit §6.8 retention）
_MAX_SNAPSHOT_LIST = 50           # list 合并两域后的输出硬上限
_MAX_DURABLE_POINTERS = 128

#: project_id 白名单（与 session id 同款 charset，限长 64）
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: save 物化的默认字节预算（audit §6.3：每 save 物化总量有界）
_DEFAULT_MATERIALIZE_BUDGET_BYTES = 256 * 1024 * 1024

_WORKSPACES_DIRNAME = "workspaces"


class SnapshotLayerRef(BaseModel):
    """引用（ref + 样式提示 + 来源面；不内联载荷）。"""

    layer_id: str = ""
    source_ref: str = ""
    style_ref: str = ""             # 模板/样式指针
    profile_fingerprint: str = ""
    ref_kind: str = "source"        # source | chart | table（layout 组件面）


class SnapshotDurablePointer(BaseModel):
    """逐产物持久指针（manifest 引用，绝不复制字节/来源真相）。"""

    content_location: str = ""      # BlobStore 相对位置（与 Artifact 行同语义）
    content_payload_sha256: str = ""
    content_type: str = "json"      # json | binary
    byte_size: int = 0
    workflow_run_id: str = ""
    map_product_version_no: Optional[int] = None


class WorkspaceSnapshot(BaseModel):
    """工作空间快照（§十六清单的 V4 实现面）。"""

    snapshot_version: int = SNAPSHOT_VERSION
    snapshot_id: str
    session_id: str
    project_id: str = ""
    label: str = ""
    created_at: float = 0.0
    artifact_contracts: List[ArtifactContract] = Field(default_factory=list)
    layers: List[SnapshotLayerRef] = Field(default_factory=list)
    durable_pointers: Dict[str, SnapshotDurablePointer] = Field(default_factory=dict)
    mapspec_fingerprint: str = ""
    mapspec_mutation_revision: int = 0
    active_view: Dict[str, Any] = Field(default_factory=dict)
    base_layer: str = ""
    settings: Dict[str, Any] = Field(default_factory=dict)
    workflow_refs: List[str] = Field(default_factory=list)
    created_by: str = ""            # 服务侧所有权记录（路由侧强制鉴权）
    #: 预算跳过披露（每条 reason="budget"）：预算耗尽后仍活的 ref，以及
    #: 单个载荷超过剩余预算的 ref（pre-size gate，写 BlobStore 之前跳过）。
    materialize_skipped: List[str] = Field(default_factory=list)

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

    @field_validator("durable_pointers")
    @classmethod
    def _bounded_pointers(
        cls, v: Dict[str, SnapshotDurablePointer]
    ) -> Dict[str, SnapshotDurablePointer]:
        return dict(list(v.items())[:_MAX_DURABLE_POINTERS])

    @field_validator("materialize_skipped")
    @classmethod
    def _bounded_skipped(cls, v: List[str]) -> List[str]:
        return [str(s)[:160] for s in v[:_MAX_LAYER_REFS]]


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
    #: 逐产物指针完整性（audit §6.7）：artifact_id → verified |
    #: digest_mismatch | pointer_missing | no_pointer
    integrity: Dict[str, str] = Field(default_factory=dict)

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
            "integrity": dict(list(self.integrity.items())[:_MAX_DURABLE_POINTERS]),
        }


# ── 路径派生（唯一路径真相；charset 白名单在边界强制）──────────────────


def _validate_session_id(session_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", session_id or ""))


def _validate_project_id(project_id: str) -> bool:
    return bool(_PROJECT_ID_RE.match(project_id or ""))


def _snapshots_dir(session_id: str) -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR

    return BASE_STORAGE_DIR / session_id / "workspace-snapshots"


def _workspaces_root() -> Path:
    """项目级快照根：``DATA_DIR/workspaces``（调用时解析，测试可覆写）。"""
    from app.core.config import settings

    return Path(str(getattr(settings, "DATA_DIR", "./data") or "./data")) / _WORKSPACES_DIRNAME


def _project_snapshots_dir(project_id: str) -> Optional[Path]:
    if not _validate_project_id(project_id):
        return None
    return _workspaces_root() / project_id / "workspace-snapshots"


def _snapshot_path(session_id: str, snapshot_id: str) -> Optional[Path]:
    if not _validate_session_id(session_id):
        return None
    if not snapshot_id or not _validate_session_id(snapshot_id):
        return None
    return _snapshots_dir(session_id) / f"{snapshot_id}.json"


def _resolve_snapshot_file(
    session_id: str, snapshot_id: str, project_id: str = ""
) -> Optional[Tuple[Path, str]]:
    """快照文件解析：项目域优先，回退会话域（向后兼容可读）。"""
    if not snapshot_id or not _validate_session_id(snapshot_id):
        return None
    pdir = _project_snapshots_dir(project_id) if project_id else None
    if pdir is not None:
        candidate = pdir / f"{snapshot_id}.json"
        if candidate.is_file():
            return candidate, "project"
    if _validate_session_id(session_id):
        candidate = _snapshots_dir(session_id) / f"{snapshot_id}.json"
        if candidate.is_file():
            return candidate, "session"
    return None


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
    """MapSpec → 有界 refs（sources + layout 组件 chartRef/tableRef）。

    audit §5.6 盲区修复：layout 组件的 chartRef/tableRef 是 GC 活引用
    （artifact_registry.load_records_for_plan 同面）—— 不捕获则恢复出的
    工作空间连图表/表格绑定都会丢。只读 spec 结构，不解析样式内容。
    """
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
                ref_kind="source",
            )
        )
    layout = mapspec.get("layout")
    if isinstance(layout, dict):
        components = layout.get("components")
        comp_list = (
            [c for c in components if isinstance(c, dict)]
            if isinstance(components, list)
            else (
                [v for v in components.values() if isinstance(v, dict)]
                if isinstance(components, dict)
                else []
            )
        )
        for comp in comp_list[:_MAX_LAYER_REFS]:
            opts = comp.get("options") or {}
            if not isinstance(opts, dict):
                continue
            for key, kind in (("chartRef", "chart"), ("tableRef", "table")):
                v = opts.get(key)
                if isinstance(v, str) and v.startswith("ref:"):
                    refs.append(
                        SnapshotLayerRef(
                            layer_id=str(comp.get("id") or comp.get("type") or "")[:96],
                            source_ref=v[:160],
                            ref_kind=kind,
                        )
                    )
    return refs[:_MAX_LAYER_REFS]


# ── 持久指针（Wave-1 promotion 真相的只读引用；绝不复制来源真相）──────


def _fetch_promoted_pointers_sync(
    project_id: str, refs: List[str]
) -> Dict[str, Dict[str, Any]]:
    """DB（Artifact.metadata_json 的 promoted 头指针）→ ref 指针投影。

    只读、best-effort：DB 不可用 / 项目无行 → {}（诚实缺指针，绝不阻塞
    保存）。workflow_run_id 取 head 修订行；map_product_version_no 仅在
    产物 metadata 显式携带时透传（不虚构关联）。
    """
    if not project_id or not refs:
        return {}
    try:
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models.project import Artifact

        if SessionLocal is None:
            return {}
        with SessionLocal() as db:
            rows = db.execute(
                select(Artifact).where(
                    Artifact.project_id == project_id,
                    Artifact.storage_ref.in_(list(refs)[:_MAX_SNAPSHOT_ARTIFACTS]),
                )
            ).scalars().all()
            out: Dict[str, Dict[str, Any]] = {}
            from app.services.artifact_revisions import head_revision

            for row in rows:
                meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
                if meta.get("content_status") != "promoted":
                    continue
                location = str(meta.get("content_location") or "")
                if not location:
                    continue
                rev = head_revision(db, row.id)
                version_no = meta.get("map_product_version_no")
                # head 修订的 content_type 是真相（binary lane 的 raster 晋升
                # 不得被投影成 json —— round-1 review MINOR）；无修订行的旧
                # 晋升行只有 json 语义 head 指针。
                content_type = (
                    str(rev.content_type)
                    if rev is not None and rev.content_type
                    else "json"
                )
                out[str(row.storage_ref)] = {
                    "content_location": location,
                    "content_payload_sha256": str(meta.get("content_payload_sha256") or ""),
                    "content_type": content_type,
                    "byte_size": int(rev.byte_size or 0) if rev is not None else 0,
                    "workflow_run_id": str(rev.workflow_run_id or "") if rev is not None else "",
                    "map_product_version_no": int(version_no) if isinstance(version_no, int) else None,
                }
            return out
    except Exception as e:  # noqa: BLE001 — 指针查找是增值，不是保存的前提
        logger.info("[WorkspaceSnapshot] durable pointer lookup skipped: %s", e)
        return {}


# ── 服务 ─────────────────────────────────────────────────────────────


async def _probe_ref_live(session_id: str, ref: str) -> bool:
    """ref 存活探测：descriptor/raster 探测 miss 时的 ``store.get()`` 兜底。

    round-1 review MAJOR（alias-mode restore）：``write_back_session_payload``
    的 alias 分支把载荷放回会话 store 的**新 ref** 并 ``set_alias`` 指回原
    ref —— descriptor 索引键在新 ref 名下，按原 ref 走 descriptor 探测会
    miss，而 ``get()`` 经别名命中。verify 绝不与事实自相矛盾：探测 miss 时
    对同一 session 做一次有界读兜底（只读；LRU recency 副作用与
    ``ref_exists`` 同款）。raster ref 的探测本就是磁盘 stat，无别名语义，
    不兜底。
    """
    from app.services.artifact_registry import is_raster_ref, probe_ref

    if await probe_ref(session_id, ref) is not None:
        return True
    if is_raster_ref(ref):
        return False
    try:
        from app.services.session_data import session_data_manager

        return await session_data_manager.get(session_id, ref) is not None
    except Exception:  # noqa: BLE001 — store 故障按不存活（诚实保守）
        return False


class WorkspaceSnapshotService:
    """快照保存 / 核查 / 恢复 / 克隆 / 删除 / 盘点。"""

    async def save_snapshot(
        self,
        session_id: str,
        *,
        label: str = "",
        project_id: str = "",
        settings: Optional[Dict[str, Any]] = None,
        workflow_refs: Optional[List[str]] = None,
        materialize: str = "none",
        max_materialize_bytes: Optional[int] = None,
        owner_id: str = "",
        retention_cap: Optional[int] = None,
    ) -> Optional[WorkspaceSnapshot]:
        """捕捉「此刻工作空间」→ 磁盘快照（+ 可选载荷物化）。

        - ``materialize="none"``（缺省）：纯 manifest（含已有 promoted
          指针的引用，V3 兼容行为）；
        - ``"claimed"``：把账本契约的存活载荷经 BlobStore 物化并盖
          ``persistence_tier="workspace"`` 章（GC interlock，零 GC 改动）；
        - ``"all"``：另加图层 source / layout 组件 ref。
        失败返回 None（不阻断调用方）。
        """
        if not _validate_session_id(session_id):
            return None
        if materialize not in ("none", "claimed", "all"):
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
            created_by=str(owner_id or "")[:64],
        )
        await self._claim_durability(
            session_id, snapshot, materialize=materialize,
            project_id=project_id, budget_bytes=max_materialize_bytes,
        )
        destination = _destination_for(session_id, snapshot.snapshot_id, project_id)
        if destination is None:
            return None
        path, home = destination

        def _write() -> None:
            _atomic_write_json(path, snapshot.model_dump(mode="json"))

        try:
            await asyncio.to_thread(_write)
        except OSError as e:
            logger.warning("[WorkspaceSnapshot] disk write failed session=%s: %s", session_id, e)
            return None
        if home == "project":
            cap = max(1, min(int(retention_cap or _MAX_SNAPSHOTS_PER_PROJECT), 500))
        else:
            cap = _MAX_SNAPSHOTS_PER_SESSION
        await self._enforce_snapshot_cap(session_id, project_id=project_id, cap=cap)
        return snapshot

    async def _claim_durability(
        self,
        session_id: str,
        snapshot: WorkspaceSnapshot,
        *,
        materialize: str,
        project_id: str,
        budget_bytes: Optional[int],
    ) -> None:
        """装配逐产物持久指针 + （声明式）物化 + GC interlock 章。

        - 先引用既有 promoted 指针（Wave-1 promotion 真相，绝不重物化）；
        - ``materialize != "none"`` 时对尚无指针的声明 ref 写通 BlobStore
          （总量受 ``budget_bytes`` 约束，超出如实 ``materialize_skipped``）；
        - 全部声明 ref 盖 ``persistence_tier="workspace"`` 章 ——
          ``collect_orphan_refs`` 的保护规则零改动即生效（audit §6.5）。
        """
        from app.services.artifact_registry import (
            get_artifact,
            probe_ref,
            update_record_metadata,
        )
        from app.services.workspace.durability import (
            WORKSPACE_TIER,
            materialize_ref_payload,
        )

        contract_refs = [
            c.artifact_id
            for c in snapshot.artifact_contracts
            if c.artifact_id.startswith("ref:")
        ]
        claimed = list(contract_refs)
        if materialize == "all":
            extra = [
                layer.source_ref
                for layer in snapshot.layers
                if layer.source_ref.startswith("ref:")
                and layer.source_ref not in set(contract_refs)
            ]
            claimed.extend(extra)
        promoted = (
            await asyncio.to_thread(_fetch_promoted_pointers_sync, project_id, contract_refs)
            if project_id and contract_refs
            else {}
        )
        pointers: Dict[str, SnapshotDurablePointer] = {}
        skipped: List[str] = []
        remaining = int(budget_bytes) if budget_bytes else _DEFAULT_MATERIALIZE_BUDGET_BYTES
        for ref in claimed[:_MAX_SNAPSHOT_ARTIFACTS + _MAX_LAYER_REFS]:
            pre = promoted.get(ref)
            if pre is not None:
                pointers[ref] = SnapshotDurablePointer(**pre)
                continue
            if materialize == "none":
                continue
            if remaining <= 0:
                # 预算耗尽：只有**载荷仍活**的 ref 才算被预算跳过（死载荷
                # 本就无可物化，不该混进 skipped 披露 —— 诚实计数）。
                if await probe_ref(session_id, ref) is not None:
                    skipped.append(ref)
                continue
            # pre-size gate（round-1 review MAJOR）：载荷先量尺再落盘 ——
            # 单个载荷超过**剩余**预算即在写 BlobStore 之前跳过并披露，
            # 绝不让一个巨型 payload 透支预算（materialize_skipped 全部
            # 条目的语义都是 reason="budget"）。预算不被透支消耗：
            # 后续小载荷仍有物化机会。
            ptr, skip_reason = await materialize_ref_payload(
                session_id, ref, budget_bytes=remaining
            )
            if skip_reason == "budget":
                skipped.append(ref)
                continue
            if ptr is None:
                continue  # 载荷不存活/写盘失败 —— 如实缺指针
            pointers[ref] = SnapshotDurablePointer(**ptr)
            remaining -= int(ptr.get("byte_size") or 0)
        snapshot.durable_pointers = pointers
        snapshot.materialize_skipped = skipped
        # GC interlock：被快照声明的 ref 一律盖 workspace 章（含"已有持久
        # 指针"与"载荷仍活但本轮未物化"的 —— 防的是 GC 提前删，TTL 老化
        # 仍由物化路径对抗）。best-effort：无账本记录的 ref 更新即 no-op。
        # 持久层只升不降（round-1 review INFO）：已是 persistent 章的 ref
        # 绝不被本方法降级回 workspace。
        tier_rank = {"session": 0, WORKSPACE_TIER: 1, "persistent": 2}
        stamped = set(pointers.keys())
        if materialize != "none":
            stamped.update(claimed)
        for ref in sorted(stamped)[:_MAX_SNAPSHOT_ARTIFACTS + _MAX_LAYER_REFS]:
            try:
                rec = await get_artifact(session_id, ref)
                existing_tier = str(
                    ((rec.metadata if rec is not None else None) or {}).get(
                        "persistence_tier") or "")
            except Exception:  # noqa: BLE001 — 账本缺席按未盖章处理
                existing_tier = ""
            if tier_rank.get(existing_tier, -1) >= tier_rank[WORKSPACE_TIER]:
                continue
            await update_record_metadata(
                session_id, ref, metadata={"persistence_tier": WORKSPACE_TIER}
            )

    async def _enforce_snapshot_cap(
        self,
        session_id: str,
        *,
        project_id: str = "",
        cap: Optional[int] = None,
    ) -> None:
        """快照数封顶入口（兼容 V3 签名：只传 session_id → 会话域 cap 20）。

        非法 session/project id 直接返回（不触碰文件系统 —— round2 review
        回归的契约）；项目域走 ``cap`` 参数（缺省 50，audit §6.8 retention）。
        """
        if project_id:
            directory = _project_snapshots_dir(project_id)
            limit = max(1, min(int(cap or _MAX_SNAPSHOTS_PER_PROJECT), 500))
        else:
            if not _validate_session_id(session_id):
                return
            directory = _snapshots_dir(session_id)
            limit = _MAX_SNAPSHOTS_PER_SESSION
        if directory is None:
            return
        await self._cap_dir(directory, limit)

    async def _cap_dir(self, directory: Path, cap: int) -> None:
        """快照数封顶（最旧先删；dry-run-safe：只删完整 ``*.json``）。

        顺带清扫崩溃遗留的 ``*.json.tmp`` 半成品（原子写中断 → replace
        未发生；超过 1h 的视为垃圾 —— 活跃写入不会那么久）。
        """

        def _cap() -> int:
            d = directory
            if not d.is_dir():
                return 0
            now = time.time()
            for tmp in d.glob("*.json.tmp"):
                try:
                    if now - tmp.stat().st_mtime > 3600:
                        tmp.unlink()
                except OSError:
                    continue
            files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
            overflow = len(files) - cap
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

    async def list_snapshots(
        self, session_id: str, *, project_id: str = ""
    ) -> List[Dict[str, Any]]:
        """快照清单（项目域 + 会话域合并；最旧 → 最新；≤50 条）。

        每条带诚实 ``home`` 字段（``"project" | "session"``）—— 会话域
        快照仍随会话清扫（向后兼容可读，不承诺长寿）。
        """

        def _summary(f: Path) -> Optional[Dict[str, Any]]:
            data = _read_json(f)
            if not isinstance(data, dict):
                return None
            return {
                "snapshot_id": str(data.get("snapshot_id") or f.stem),
                "label": str(data.get("label") or ""),
                "created_at": data.get("created_at"),
                "artifacts": len(data.get("artifact_contracts") or []),
                "layers": len(data.get("layers") or []),
                "project_id": str(data.get("project_id") or ""),
            }

        def _list() -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            pdir = _project_snapshots_dir(project_id) if project_id else None
            if pdir is not None and pdir.is_dir():
                for f in pdir.glob("*.json"):
                    item = _summary(f)
                    if item is not None:
                        item["home"] = "project"
                        out.append(item)
            if _validate_session_id(session_id):
                sdir = _snapshots_dir(session_id)
                if sdir.is_dir():
                    for f in sdir.glob("*.json"):
                        item = _summary(f)
                        if item is not None:
                            item["home"] = "session"
                            out.append(item)
            out.sort(key=lambda x: (x.get("created_at") or 0.0))
            return out[:_MAX_SNAPSHOT_LIST]

        try:
            return await asyncio.to_thread(_list)
        except OSError:
            return []

    def _load_snapshot(
        self, session_id: str, snapshot_id: str, *, project_id: str = ""
    ) -> Optional[WorkspaceSnapshot]:
        resolved = _resolve_snapshot_file(session_id, snapshot_id, project_id)
        if resolved is None:
            return None
        data = _read_json(resolved[0])
        if not isinstance(data, dict):
            return None
        try:
            return WorkspaceSnapshot.model_validate(data)
        except Exception:  # noqa: BLE001 — 版本漂移的快照按不存在处理
            return None

    async def verify_snapshot(
        self, session_id: str, snapshot_id: str, *, project_id: str = ""
    ) -> SnapshotVerification:
        """§十六目标的前置证据：这份快照还能恢复多少？"""
        report = SnapshotVerification(snapshot_id=snapshot_id)
        snapshot = self._load_snapshot(session_id, snapshot_id, project_id=project_id)
        if snapshot is None:
            return report
        # integrity_ok 语义 = 「可解析为合法模型」+「已记录指针的摘要与
        # BlobStore 重算摘要一致」（audit §6.7：profile_fingerprint 只是与
        # live profile 的对照提示，不参与）。
        report.exists = True
        report.integrity_ok = True
        report.mapspec_available = bool(snapshot.mapspec_fingerprint)

        from app.services.workspace.durability import (
            INTEGRITY_DIGEST_MISMATCH,
            INTEGRITY_NO_POINTER,
            verify_durable_pointer,
        )

        contract_ids = {c.artifact_id for c in snapshot.artifact_contracts}
        for contract in snapshot.artifact_contracts:
            report.artifacts_total += 1
            live = (
                await _probe_ref_live(session_id, contract.artifact_id)
                if contract.artifact_id.startswith("ref:") else True
            )
            if live:
                report.artifacts_live += 1
            else:
                report.artifacts_missing.append(contract.artifact_id)
        for layer in snapshot.layers:
            if not layer.source_ref:
                continue  # inline/外部源：不计 ref 存活
            report.layers_total += 1
            live = await _probe_ref_live(session_id, layer.source_ref)
            if live:
                report.layers_live += 1
            else:
                report.layers_missing.append(layer.source_ref)
        # 指针完整性（有指针才比对；无指针如实 no_pointer —— 不虚构证据）
        verified_ids = contract_ids | set(snapshot.durable_pointers.keys())
        for artifact_id in sorted(verified_ids)[:_MAX_DURABLE_POINTERS]:
            pointer = snapshot.durable_pointers.get(artifact_id)
            if pointer is None:
                report.integrity[artifact_id] = INTEGRITY_NO_POINTER
                continue
            status = await asyncio.to_thread(verify_durable_pointer, pointer.model_dump())
            report.integrity[artifact_id] = status
            if status == INTEGRITY_DIGEST_MISMATCH:
                report.integrity_ok = False
        return report

    async def restore_snapshot(
        self,
        session_id: str,
        snapshot_id: str,
        *,
        mode: str = "verify",       # verify | register
        project_id: str = "",
    ) -> Dict[str, Any]:
        """快照恢复。

        - ``verify``（缺省）：只核查，返回验证报告 —— 不写任何状态；
        - ``register``：核查后把快照中的产物记录**重注册**回账本
          （血缘恢复），并对带持久指针且载荷已失的契约做 **digest 校验
          读 → 原位写回同一 ref**（audit §6.4 verify-before-write）——
          写回失败保持诚实 ``expired`` 并进 ``degraded`` 披露，绝不把
          死 ref 标成 valid。Map/样式回放仍归 checkpoint 体系。
        """
        verification = await self.verify_snapshot(
            session_id, snapshot_id, project_id=project_id
        )
        result: Dict[str, Any] = {
            "mode": mode,
            "verification": verification.to_dict(),
        }
        if mode == "verify":
            return result
        if mode != "register":
            result["error"] = f"unknown mode: {mode!r} (verify|register)"
            return result
        snapshot = self._load_snapshot(session_id, snapshot_id, project_id=project_id)
        if snapshot is None:
            result["error"] = "snapshot not found or unreadable"
            return result
        from app.services.artifact_registry import (
            is_cube_ref,
            is_fabric_parquet_ref,
            mark_status,
            register_artifact,
        )
        from app.services.workspace.durability import (
            RestoredBinary,
            read_back_payload,
            restore_cube_store,
            restore_fabric_parquet_file,
            restore_raster_png,
            write_back_session_payload,
        )

        registered = 0
        restored_payloads = 0
        degraded: List[Dict[str, str]] = []
        marked_expired: List[str] = []
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
            if ok is None:
                continue
            registered += 1
            if contract.artifact_id not in missing_ids:
                continue  # 载荷仍活：无需重物化
            pointer = snapshot.durable_pointers.get(contract.artifact_id)
            restored = False
            if pointer is not None:
                payload = await asyncio.to_thread(
                    read_back_payload, pointer.model_dump()
                )
                if payload is not None:
                    if isinstance(payload, RestoredBinary):
                        if is_fabric_parquet_ref(contract.artifact_id):
                            # V6：GeoParquet 磁盘工件（binary lane）。
                            restored = await restore_fabric_parquet_file(
                                session_id, contract.artifact_id, payload.data
                            )
                        else:
                            restored = await restore_raster_png(
                                session_id, contract.artifact_id, payload.data
                            )
                    elif is_cube_ref(contract.artifact_id):
                        # V6：cube manifest lane（blob 在场才可物化 ——
                        # manifest-only 指针如实 degraded）。
                        restored = await restore_cube_store(
                            session_id,
                            contract.artifact_id,
                            payload,
                            data_object_id=str(
                                pointer.content_payload_sha256 or ""
                            ),
                        )
                    else:
                        restored, _write_mode = await write_back_session_payload(
                            session_id, contract.artifact_id, payload
                        )
            if restored:
                restored_payloads += 1
                # 载荷已验真并写回：register 的探针 miss 不得留在账本里
                # 把活载荷说成死 —— 状态如实回到 valid。
                await mark_status(session_id, contract.artifact_id, "valid")
            else:
                # 载荷已亡的产物：血缘重绑定但状态如实回落 expired ——
                # register_artifact 的复活语义不得伪造「可用」。
                await mark_status(session_id, contract.artifact_id, "expired")
                marked_expired.append(contract.artifact_id)
                degraded.append({
                    "artifact_id": contract.artifact_id,
                    "reason": (
                        "durable_read_failed" if pointer is not None
                        else "no_durable_pointer"
                    ),
                })
        result["registered"] = registered
        result["restored_payloads"] = restored_payloads
        result["marked_expired"] = marked_expired
        result["degraded"] = degraded[:_MAX_SNAPSHOT_ARTIFACTS]
        return result

    async def clone_snapshot(
        self,
        source_session_id: str,
        snapshot_id: str,
        target_session_id: str,
        *,
        project_id: str = "",
        owner_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """跨会话克隆快照文件（ref 的会话绑定语义由 verify 如实披露）。

        项目域快照克隆回项目域（``project_id`` 给出时）；所有权：源快照
        记录了 ``created_by`` 且调用方身份不符 → 拒绝（None）。
        """
        if not _validate_session_id(target_session_id):
            return None
        resolved = _resolve_snapshot_file(source_session_id, snapshot_id, project_id)
        if resolved is None:
            return None
        data = _read_json(resolved[0])
        if not isinstance(data, dict):
            return None
        created_by = str(data.get("created_by") or "")
        if created_by and owner_id and created_by != str(owner_id):
            return None  # 非本人创建的项目域快照：服务侧拒绝（路由侧已鉴权）
        try:
            src = WorkspaceSnapshot.model_validate(data)
        except Exception:  # noqa: BLE001 — 版本漂移按不存在处理
            return None
        new_id = f"ws-{uuid.uuid4().hex[:16]}"
        clone = src.model_copy(
            update={
                "snapshot_id": new_id,
                "session_id": target_session_id,
                "created_at": time.time(),
                "label": f"clone:{src.label or src.snapshot_id}"[:96],
                "created_by": str(owner_id or src.created_by or "")[:64],
                "project_id": project_id or src.project_id,
            }
        )
        destination = _destination_for(target_session_id, new_id, project_id or src.project_id)
        if destination is None:
            return None
        path, _home = destination
        try:
            await asyncio.to_thread(_atomic_write_json, path, clone.model_dump(mode="json"))
        except OSError:
            return None
        return {"snapshot_id": new_id, "source": snapshot_id, "home": _home}

    async def delete_snapshot(
        self,
        session_id: str,
        snapshot_id: str,
        *,
        project_id: str = "",
        owner_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """定点删除：只 unlink 解析到的那个快照文件（有界、dry-run-safe）。"""
        resolved = _resolve_snapshot_file(session_id, snapshot_id, project_id)
        if resolved is None:
            return None
        path, home = resolved
        data = _read_json(path)
        created_by = str((data or {}).get("created_by") or "")
        if created_by and owner_id and created_by != str(owner_id):
            return None  # 非本人创建：拒绝（不删别人的项目资产）
        try:
            await asyncio.to_thread(path.unlink)
        except OSError:
            return None
        return {"snapshot_id": snapshot_id, "home": home, "deleted": True}

    async def describe_workspace(
        self, session_id: str, *, project_id: str = ""
    ) -> Dict[str, Any]:
        """§十五工作空间盘点：快照计数 / 产物生命周期分布 / 图层 refs /
        持久覆盖率（audit §6.7 的 surface 面）。只读。"""
        from app.services.artifact_registry import list_artifacts as registry_list
        from app.services.mapspec_store import mapspec_store

        out: Dict[str, Any] = {
            "project_id": project_id,
            "session_id": session_id,
        }
        try:
            records = await registry_list(session_id)
        except Exception:  # noqa: BLE001 — 账本缺席按空盘点（诚实）
            records = []
        try:
            mapspec = await mapspec_store.get_mapspec(session_id)
        except Exception:  # noqa: BLE001 — spec 缺席按空盘点（诚实）
            mapspec = None
        contracts = [from_artifact_record(r) for r in records[:_MAX_SNAPSHOT_ARTIFACTS]]
        by_lifecycle: Dict[str, int] = {}
        by_persistence: Dict[str, int] = {}
        for c in contracts:
            by_lifecycle[c.lifecycle.value] = by_lifecycle.get(c.lifecycle.value, 0) + 1
            by_persistence[c.persistence.value] = (
                by_persistence.get(c.persistence.value, 0) + 1
            )
        layers = _extract_layer_refs(mapspec if isinstance(mapspec, dict) else None)
        ref_ids = [c.artifact_id for c in contracts if c.artifact_id.startswith("ref:")]
        layer_refs = [lyr.source_ref for lyr in layers if lyr.source_ref.startswith("ref:")]
        all_refs = list(dict.fromkeys(ref_ids + layer_refs))
        # 持久覆盖 = 已有持久指针的 ref：DB promoted 头指针 ∪ 项目域快照
        # manifest 里记录的指针（快照物化的内容 DB 行未必有 —— manifest 就
        # 是它的指针真相；最多读 50 份小 JSON，线程内执行）。
        durable_refs: set[str] = set()

        def _durable_from_manifests() -> set[str]:
            found: set[str] = set()
            pdir = _project_snapshots_dir(project_id) if project_id else None
            if pdir is not None and pdir.is_dir():
                for f in sorted(pdir.glob("*.json"))[:_MAX_SNAPSHOT_LIST]:
                    data = _read_json(f)
                    pointers = data.get("durable_pointers") if isinstance(data, dict) else None
                    if isinstance(pointers, dict):
                        found.update(str(k) for k in pointers.keys())
            return found

        if all_refs:
            promoted = (
                await asyncio.to_thread(_fetch_promoted_pointers_sync, project_id, all_refs)
                if project_id
                else {}
            )
            durable_refs.update(ref for ref in all_refs if ref in promoted)
            if project_id:
                durable_refs.update(await asyncio.to_thread(_durable_from_manifests))
        durable = sum(1 for ref in all_refs if ref in durable_refs)
        total = len(all_refs)
        coverage = round(100.0 * durable / total, 1) if total else 100.0
        snapshots = await self.list_snapshots(session_id, project_id=project_id)
        # 指针完整性披露（round-1 review CRITICAL 接线，additive 字段）：
        # 项目域快照 manifest 里指向已删 blob 的持久指针如实上报
        # （quota.snapshot_pointer_integrity —— 单一 manifest 读取器）。
        try:
            from app.services.data_lifecycle.quota import snapshot_pointer_integrity

            pointer_integrity: Optional[Dict[str, Any]] = (
                await asyncio.to_thread(snapshot_pointer_integrity, project_id)
            )
        except Exception:  # noqa: BLE001 — 披露是增值，绝不阻断盘点
            pointer_integrity = None
        out.update({
            "snapshots": {"count": len(snapshots), "items": snapshots[:10]},
            "artifacts": {
                "total": len(contracts),
                "by_lifecycle": by_lifecycle,
                "by_persistence": by_persistence,
            },
            "layers": {
                "total": len(layers),
                "refs": [lyr.source_ref for lyr in layers if lyr.source_ref][:_MAX_LAYER_REFS],
            },
            "durable": {
                "refs_total": total,
                "refs_durable": durable,
                "coverage_pct": coverage,
            },
            "pointer_integrity": pointer_integrity or {
                "project_id": project_id,
                "snapshots_checked": 0,
                "pointers_missing_total": 0,
                "items": [],
            },
        })
        return out


def _destination_for(
    session_id: str, snapshot_id: str, project_id: str = ""
) -> Optional[Tuple[Path, str]]:
    """写入目的：合法 project_id → 项目域；否则回退会话域（V3 兼容）。"""
    pdir = _project_snapshots_dir(project_id) if project_id else None
    if pdir is not None:
        return pdir / f"{snapshot_id}.json", "project"
    path = _snapshot_path(session_id, snapshot_id)
    if path is None:
        return None
    return path, "session"


_service: Optional[WorkspaceSnapshotService] = None


def get_workspace_snapshot_service() -> WorkspaceSnapshotService:
    global _service
    if _service is None:
        _service = WorkspaceSnapshotService()
    return _service


def reset_workspace_snapshot_service() -> None:
    global _service
    _service = None
