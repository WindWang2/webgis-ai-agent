"""Versioning V3 —— 来源修订与产物版本判定（§九，纯函数层）。

V3 之前（审计）：``RefDescriptor.content_hash`` 永久 None、
``content_revision`` 是无人消费的计数器、``ArtifactRecord.revision``
恒 0、两套 dataset 指纹语义互斥。「数据变了」这件事在整个会话层
**不可感知** —— 图层继续展示过期产物、缓存继续命中旧内容。

本模块定义 V3 的最小版本语义（纯函数、零 I/O）：

- ``SourceRevision``：一个来源在某时刻的修订证据快照
  （revision token = mtime_ns:size 这类便宜证据；富证据 = 四维指纹）；
- ``revision_token_from_stat``：文件 stat → 修订 token（不含路径，
  path 只作 key 不作 token —— 路径重命名 ≠ 内容变化）;
- ``compare_revisions``：旧/新快照 → ChangeClass（复用 fingerprints
  的变更分类；token 证据只能判「变了/没变/不知道」）;
- ``ArtifactVersion``：产物版本条目（version 号 + 指纹 + 时间戳），
  ``build_version_chain`` 把 ``replaces`` 链变成可枚举版本史。

诚实边界：mtime+size 是启发式（同尺寸改写不可见）——凡是能算内容
指纹的场景都必须优先内容指纹；token 只用于「没有更便宜证据」的
文件来源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence

from app.lib.data.fingerprints import (
    ChangeClass,
    FingerprintSet,
    classify_change,
)


@dataclass(frozen=True)
class SourceRevision:
    """来源修订快照（§九 source revision）。证据缺维 → None（诚实）。"""

    source_type: str = ""                     # upload / file / session_ref / fabric:<t>
    source_ref: str = ""                      # 来源 key（upload_id / 路径 / url）
    revision_token: str = ""                  # 便宜证据（mtime_ns:size / etag / 计数器）
    content_fingerprint: Optional[str] = None
    schema_fingerprint: Optional[str] = None
    metadata_fingerprint: Optional[str] = None
    crs: str = ""
    captured_at: Optional[datetime] = None

    def fingerprint_set(self) -> FingerprintSet:
        return FingerprintSet(
            content=self.content_fingerprint,
            schema=self.schema_fingerprint,
            metadata=self.metadata_fingerprint,
            crs=self.crs or None,
        )

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "revision_token": self.revision_token,
            "content_fingerprint": self.content_fingerprint,
            "schema_fingerprint": self.schema_fingerprint,
            "metadata_fingerprint": self.metadata_fingerprint,
            "crs": self.crs,
            "captured_at": (
                self.captured_at.isoformat() if self.captured_at else None
            ),
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SourceRevision":
        d = d or {}
        captured = d.get("captured_at")
        if isinstance(captured, str):
            try:
                captured = datetime.fromisoformat(captured)
            except ValueError:
                captured = None
        return cls(
            source_type=str(d.get("source_type") or ""),
            source_ref=str(d.get("source_ref") or ""),
            revision_token=str(d.get("revision_token") or ""),
            content_fingerprint=d.get("content_fingerprint") or None,
            schema_fingerprint=d.get("schema_fingerprint") or None,
            metadata_fingerprint=d.get("metadata_fingerprint") or None,
            crs=str(d.get("crs") or ""),
            captured_at=captured,
        )


def revision_token_from_stat(stat: Any) -> str:
    """os.stat_result → 修订 token（``mtime_ns:size``）。stat 缺失 → ""。"""
    if stat is None:
        return ""
    try:
        return f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
    except (AttributeError, TypeError, ValueError):
        return ""


def compare_revisions(old: Optional[SourceRevision], new: Optional[SourceRevision]) -> ChangeClass:
    """旧/新来源快照 → 变更类别。

    - 任一快照缺席 → UNKNOWN（不能把「没记录」当「没变」）；
    - 双方都有富指纹 → classify_change（四维分类）；
    - 只有 token → token 相同 NONE、不同 CONTENT（token 变化只能证明
      「值得怀疑」，归 CONTENT 触发重算 —— 保守方向）。
    """
    if old is None or new is None:
        return ChangeClass.UNKNOWN
    fingerprint_verdict = classify_change(old.fingerprint_set(), new.fingerprint_set())
    if fingerprint_verdict is not ChangeClass.UNKNOWN:
        return fingerprint_verdict
    # 指纹证据不足：token 兜底
    if old.revision_token and new.revision_token:
        if old.revision_token == new.revision_token:
            return ChangeClass.NONE
        return ChangeClass.CONTENT
    # 什么证据都没有
    return ChangeClass.UNKNOWN


@dataclass(frozen=True)
class ArtifactVersion:
    """产物版本条目（§九 artifact version）。"""

    artifact_id: str
    version: int = 0
    content_fingerprint: Optional[str] = None
    created_at: Optional[datetime] = None
    replaced_by: Optional[str] = None   # 下一版本（replaces 反向）

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "version": self.version,
            "content_fingerprint": self.content_fingerprint,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "replaced_by": self.replaced_by,
        }


@dataclass
class VersionChain:
    """版本链（旧 → 新；``replaces`` 链的有序投影，§九版本史）。"""

    chain: List[ArtifactVersion] = field(default_factory=list)
    truncated: bool = False

    @property
    def latest(self) -> Optional[ArtifactVersion]:
        return self.chain[-1] if self.chain else None

    def to_dict(self) -> dict:
        return {
            "chain": [v.to_dict() for v in self.chain],
            "truncated": self.truncated,
        }


MAX_VERSION_CHAIN = 32


def build_version_chain(
    records: Sequence[Any],
    *,
    head_artifact_id: str,
    max_length: int = MAX_VERSION_CHAIN,
) -> VersionChain:
    """records（含 replaces 边）→ head 的版本链（旧 → 新）。

    ``records``：任意具备 artifact_id / replaces / revision / created_at /
    metadata.content_fingerprint 的对象（ArtifactRecord 或 dict）。环由
    visited 集合打破；超 max_length 截断并声明。
    """
    by_id: dict = {}

    def _field(r: Any, name: str, default=None):
        if isinstance(r, dict):
            return r.get(name, default)
        return getattr(r, name, default)

    for r in records:
        aid = _field(r, "artifact_id")
        if aid:
            by_id[aid] = r
    chain: List[ArtifactVersion] = []
    visited: set = set()

    def _metadata(r: Any) -> dict:
        md = _field(r, "metadata") or {}
        return md if isinstance(md, dict) else {}

    # 从 head 沿 replaces 反向收集（head → 被替换者链），再倒序成旧→新。
    replaced_by = {
        _field(r, "replaces"): _field(r, "artifact_id")
        for r in records
        if _field(r, "replaces") and _field(r, "artifact_id")
    }
    cur: Optional[str] = head_artifact_id
    truncated = False
    while cur and cur in by_id and cur not in visited:
        visited.add(cur)
        r = by_id[cur]
        created = _field(r, "created_at")
        if isinstance(created, (int, float)):
            created = datetime.fromtimestamp(float(created), tz=timezone.utc)
        chain.append(
            ArtifactVersion(
                artifact_id=cur,
                version=int(_field(r, "revision") or 0),
                content_fingerprint=_metadata(r).get("content_fingerprint"),
                created_at=created,
                replaced_by=replaced_by.get(cur),
            )
        )
        cur = _field(r, "replaces")
        if len(chain) >= max_length and cur:
            truncated = True
            break
    chain.reverse()
    return VersionChain(chain=chain, truncated=truncated)
