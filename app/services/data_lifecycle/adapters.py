"""Data Lifecycle V9 —— 五套过期/回收机制的统一适配器（P3，只读面）。

ADR-0140 的**行为保持**红线在此强制：

- 适配器只做**枚举/观测**（enumerate → LCObject + stats），绝不删除、绝不
  写各机制的内部状态 —— 五套机制的默认行为由其既有路径（retention plan/
  execute、session spill TTL、artifact LRU、coordinator purge）继续承担；
- 删除动作只经统一 GC 计划（policy.action 显式启用 + P5 审批状态机 +
  staging 二段式），默认策略 = 全 observe = 行为等价现状；
- 每个适配器 fail-open：单机制不可用 → 该 kind 计 0 + 诊断，绝不拖垮整体
  评估；枚举全部有界（MAX_OBJECTS 硬帽）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 单机制枚举硬帽（防御无限目录/表）。
MAX_OBJECTS_PER_KIND = 1000
_MAX_INFO_ENTRIES = 16


@dataclass
class LCObject:
    """统一对象视图（adapters → registry upsert 的传输形状）。"""

    kind: str
    object_id: str                 # 机制内自然键（≤255 字符）
    owner_scope: str = ""
    byte_size: int = 0
    last_used_at: Optional[datetime] = None
    info: Dict[str, Any] = field(default_factory=dict)

    def bounded_info(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in list(self.info.items())[:_MAX_INFO_ENTRIES]:
            key = str(k)[:64]
            if isinstance(v, (int, float, bool)) or v is None:
                out[key] = v
            else:
                out[key] = str(v)[:128]
        return out


@dataclass
class AdapterReport:
    """单机制观测报告（assess 的 per-kind 段）。"""

    kind: str
    objects: List[LCObject] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    available: bool = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── 根目录解析缝（monkeypatch 友好；与各机制的路径约定逐字一致） ──────


def _artifact_root() -> Path:
    from app.lib.artifact_cache import ARTIFACT_DIR
    return Path(ARTIFACT_DIR)


def _cog_root() -> Path:
    return Path("data") / "cog"


def _data_root() -> Path:
    from app.core.config import settings
    return Path(getattr(settings, "DATA_DIR", "") or "./data")


def _spill_root() -> Path:
    env_dir = os.getenv("GIS_REF_SPILL_DIR")
    if env_dir:
        return Path(env_dir)
    return _data_root() / "ref_spill"


# ── 1. Lakehouse dataset ─────────────────────────────────────────────


def enumerate_lakehouse(db: Any, limit: int = MAX_OBJECTS_PER_KIND) -> AdapterReport:
    """lakehouse_datasets 注册行（数据集级；字节规模以版本账本聚合）。"""
    report = AdapterReport(kind="lakehouse_dataset")
    try:
        from sqlalchemy import func, select

        from app.models.lakehouse_datasets import LakehouseDataset, LakehouseDatasetVersion

        rows = db.execute(
            select(LakehouseDataset).limit(limit)
        ).scalars().all()
        for row in rows:
            version_count = int(
                db.execute(
                    select(func.count())
                    .select_from(LakehouseDatasetVersion)
                    .where(LakehouseDatasetVersion.dataset_id == row.dataset_id)
                ).scalar() or 0
            )
            report.objects.append(LCObject(
                kind="lakehouse_dataset",
                object_id=str(row.dataset_id)[:255],
                owner_scope=f"{row.owner_type}:{str(row.owner_id)[:64]}",
                byte_size=0,  # 字节规模归 BlobStore 侧车道；此处不虚报
                last_used_at=row.updated_at,
                info={"name": row.name, "versions": version_count,
                      "owner_type": row.owner_type,
                      "default_branch": row.default_branch},
            ))
    except Exception as exc:  # noqa: BLE001 — fail-open（诚实诊断）
        report.available = False
        report.diagnostics.append(f"lakehouse_unavailable: {type(exc).__name__}")
    return report


# ── 2. Fabric 物化 / session spill ───────────────────────────────────


def enumerate_fabric(limit: int = MAX_OBJECTS_PER_KIND) -> AdapterReport:
    """session spill 落盘 + fabric-geoparquet 落盘物化（文件级枚举）。"""
    report = AdapterReport(kind="fabric_materialization")
    try:
        roots = [_spill_root(), _data_root()]

        seen = 0
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if seen >= limit:
                    report.diagnostics.append("bounded_skip: enumeration cap hit")
                    break
                if not path.is_file():
                    continue
                is_spill = path.suffix == ".json" and "ref_spill" in path.parts
                is_fabric = path.suffix == ".parquet" and "fabric-geoparquet" in path.parts
                if not (is_spill or is_fabric):
                    continue
                seen += 1
                try:
                    stat = path.stat()
                except OSError:
                    continue
                report.objects.append(LCObject(
                    kind="fabric_materialization",
                    object_id=path.relative_to(root).as_posix()[:255],  # POSIX 斜杠：跨平台稳定自然键
                    owner_scope=(path.parent.parent.name
                                 if is_fabric and path.parent.parent != root
                                 else path.parent.name)[:128],
                    byte_size=int(stat.st_size),
                    last_used_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    info={"lane": "ref_spill" if is_spill else "fabric_geoparquet"},
                ))
            if seen >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        report.available = False
        report.diagnostics.append(f"fabric_unavailable: {type(exc).__name__}")
    return report


# ── 3. Artifact 磁盘 LRU ─────────────────────────────────────────────


def enumerate_artifacts(limit: int = MAX_OBJECTS_PER_KIND) -> AdapterReport:
    """``data/artifacts`` 内容寻址缓存（与 artifact_cache 同一目录约定）。"""
    report = AdapterReport(kind="artifact_cache")
    try:
        root = _artifact_root()
        if root.exists():
            seen = 0
            for path in sorted(root.glob("*.tif")):
                if seen >= limit:
                    report.diagnostics.append("bounded_skip: enumeration cap hit")
                    break
                seen += 1
                try:
                    stat = path.stat()
                except OSError:
                    continue
                report.objects.append(LCObject(
                    kind="artifact_cache",
                    object_id=path.stem[:255],
                    byte_size=int(stat.st_size),
                    last_used_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    info={"has_meta": path.with_suffix(".meta").exists()},
                ))
    except Exception as exc:  # noqa: BLE001
        report.available = False
        report.diagnostics.append(f"artifact_unavailable: {type(exc).__name__}")
    return report


# ── 4. COG 输出目录 ──────────────────────────────────────────────────


def enumerate_cog(limit: int = MAX_OBJECTS_PER_KIND) -> AdapterReport:
    """``data/cog/<session>/``（raster_tools_cog 的默认 out_dir 约定）。

    现状**无清理机制**（P0 勘察确认的真实缺口）——本适配器先把它纳入
    统一观测；删除仍默认 observe（行为保持），治理动作走显式策略。
    """
    report = AdapterReport(kind="cog_output")
    try:
        root = _cog_root()
        if root.exists():
            seen = 0
            for path in sorted(root.rglob("*.tif")):
                if seen >= limit:
                    report.diagnostics.append("bounded_skip: enumeration cap hit")
                    break
                seen += 1
                try:
                    stat = path.stat()
                except OSError:
                    continue
                session_dir = path.parent.name if path.parent != root else ""
                report.objects.append(LCObject(
                    kind="cog_output",
                    object_id=path.relative_to(root).as_posix()[:255],  # POSIX 斜杠：跨平台稳定自然键
                    owner_scope=session_dir[:128],
                    byte_size=int(stat.st_size),
                    last_used_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    info={},
                ))
    except Exception as exc:  # noqa: BLE001
        report.available = False
        report.diagnostics.append(f"cog_unavailable: {type(exc).__name__}")
    return report


# ── 5. GeoCompute worker_cache（DB 注册表） ───────────────────────────


def enumerate_worker_cache(db: Any, limit: int = MAX_OBJECTS_PER_KIND) -> AdapterReport:
    """``geocompute_worker_cache`` 位置声明注册表（行级枚举）。"""
    report = AdapterReport(kind="worker_cache")
    try:
        from app.models.db_model import GeoComputeWorkerCache

        rows = db.query(GeoComputeWorkerCache).limit(limit).all()
        for row in rows:
            report.objects.append(LCObject(
                kind="worker_cache",
                object_id=f"{row.worker_id}:{row.cache_key}"[:255],
                owner_scope=str(getattr(row, "owner_scope", "") or "")[:128],
                byte_size=int(getattr(row, "size_bytes", 0) or 0),
                last_used_at=getattr(row, "last_hit_at", None),
                info={"worker_id": str(row.worker_id)[:64]},
            ))
    except Exception as exc:  # noqa: BLE001
        report.available = False
        report.diagnostics.append(f"worker_cache_unavailable: {type(exc).__name__}")
    return report


#: kind → 枚举器（db 参数按需传入；封闭注册表）。
def enumerate_all(db: Any) -> Dict[str, AdapterReport]:
    return {
        "lakehouse_dataset": enumerate_lakehouse(db),
        "fabric_materialization": enumerate_fabric(),
        "artifact_cache": enumerate_artifacts(),
        "cog_output": enumerate_cog(),
        "worker_cache": enumerate_worker_cache(db),
    }


__all__ = [
    "LCObject",
    "AdapterReport",
    "enumerate_lakehouse",
    "enumerate_fabric",
    "enumerate_artifacts",
    "enumerate_cog",
    "enumerate_worker_cache",
    "enumerate_all",
    "MAX_OBJECTS_PER_KIND",
]
