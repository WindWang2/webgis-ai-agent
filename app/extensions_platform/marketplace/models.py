"""Marketplace registry 数据模型（ADR-0119 / Wave 5）。

全部为不可变 pydantic 模型（fail closed）：registry 索引 `state.json` 的
唯一合法形状。发布（publish）走 `service.publish` 的强制前置（digest/
签名/SBOM/publisher allowlist），这里只负责「已核验事实」的载体。

版本号排序 = semver 三元组（`api_version.parse_version`）；解析失败的
版本记录在加载期即拒绝（不存「半份」记录）。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# digest 形状红线：任何进入 registry 的 blob 引用必须是 sha256 hex。
# 该形状同时是下载 API 的路径穿越防线（blob 文件名 = digest）。
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

PACKAGE_STATUS_ACTIVE = "active"
PACKAGE_STATUS_DEPRECATED = "deprecated"
PACKAGE_STATUS_REVOKED = "revoked"

MAX_PACKAGE_ID_LEN = 128
MAX_DESCRIPTION_CHARS = 2000
MAX_TAGS = 16


def _id_shape(v: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}\.[a-z][a-z0-9_]{0,63}", v or ""):
        raise ValueError(f"package id {v!r} must be a namespaced extension id")
    return v


class VersionRecord(BaseModel):
    """一个已发布版本的不可变事实（blob 引用 + 供应链元数据）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    digest: str  # 包 blob 的 sha256（内容寻址文件名）
    size_bytes: int = Field(ge=1)
    publisher: str
    key_id: str
    signature: dict[str, Any]  # signature.json 副本（验签裁决的输入）
    fingerprint: str  # 包内容指纹（安装后 staging 对账）
    sbom_digest: str  # SBOM 文档自身的 sha256（审计引用，不存全文）
    permissions: list[str] = Field(default_factory=list)
    api_version: str = "1.0.0"
    min_core_version: str = "0.1.0"
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    yanked: bool = False  # 维度性撤回（仍可显式版本安装，不进 latest 解析）
    created_at: str = ""  # ISO8601，纯信息性（不参与任何判定）

    @field_validator("digest", "sbom_digest", "fingerprint")
    @classmethod
    def _digest_shape(cls, v: str) -> str:
        if not DIGEST_RE.match(v or ""):
            raise ValueError(f"digest/fingerprint {v!r} must be sha256 hex")
        return v

    @field_validator("size_bytes")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("size_bytes must be positive")
        return v


class PackageRecord(BaseModel):
    """一个包的注册表条目（版本表 + 生命周期状态）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str = ""
    description: str = ""
    publisher: str
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    status: str = PACKAGE_STATUS_ACTIVE
    deprecation_note: str = ""
    versions: dict[str, VersionRecord] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _pkg_id(cls, v: str) -> str:
        return _id_shape(v)

    @field_validator("status")
    @classmethod
    def _status_vocab(cls, v: str) -> str:
        if v not in {PACKAGE_STATUS_ACTIVE, PACKAGE_STATUS_DEPRECATED, PACKAGE_STATUS_REVOKED}:
            raise ValueError(f"status must be active|deprecated|revoked, got {v!r}")
        return v

    @field_validator("description")
    @classmethod
    def _desc_len(cls, v: str) -> str:
        if len(v) > MAX_DESCRIPTION_CHARS:
            raise ValueError("description exceeds length limit")
        return v

    def latest_version(self) -> Optional[str]:
        """最高非 yanked 版本（semver 序）；全部 yanked/无版本 → None。"""
        from ..api_version import parse_version

        candidates = [
            (parse_version(v), v)
            for v, rec in self.versions.items()
            if not rec.yanked
        ]
        candidates = [c for c in candidates if c[0] is not None]
        if not candidates:
            return None
        return max(candidates)[1]


class RegistryState(BaseModel):
    """`state.json` 的唯一合法形状（generation 单调递增）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation: int = Field(ge=0)
    packages: dict[str, PackageRecord] = Field(default_factory=dict)

    @field_validator("generation")
    @classmethod
    def _gen_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("generation must be >= 0")
        return v
