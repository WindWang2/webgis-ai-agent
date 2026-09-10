"""ModelPackageStore —— 校验通过的模型包持久化（V3 §B）。

ADR-0124 的「注册时 + load 时双验 checksum」在真实包路径的落地件：

- ``persist``：**已过安全门**（``inspect_archive``/``inspect_model_file``）
  的包字节原子落盘；
- ``resolve``：load 路径唯一取包口——存在性 + sha256 复验（load 时第二次
  校验），不符 typed 拒绝（防落盘后篡改/截断）；
- **内容寻址布局**：文件名含 checksum 前 16 hex（``id__ver__checksum16.<ext>``）
  ——provider 协议的 ``load(descriptor)`` 拿不到 owner scope，跨 scope 同名
  模型靠 checksum 段天然不碰撞；synthetic 种子（无实体包）不经过本店。

安全边界：本店不做首次结构校验（调用方必须先用 package_security 过门后
再 persist）；persist 复验一次 checksum，resolve 再验一次——落盘前后的
字节完整性各有独立闸。
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from pathlib import Path
from app.lib.data.fingerprints import sha256_of_file
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError
from app.lib.modelops.package_security import SINGLE_FILE_FORMAT_SUFFIXES, sha256_of_bytes

#: 包文件名的 safe 化（与 registry 记录文件名同一 charset 纪律）。
_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-.]")


def _safe(component: str) -> str:
    return _SAFE_RE.sub("_", component)[:160]


class ModelPackageStore:
    """包字节存储（内容寻址；原子写 + load 双验）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()

    def package_path(self, descriptor: GeoModelDescriptor) -> Path:
        suffix = SINGLE_FILE_FORMAT_SUFFIXES.get(descriptor.artifact_format, ".zip")
        name = (
            f"{_safe(descriptor.model_id)}__{_safe(descriptor.model_version)}"
            f"__{descriptor.checksum[:16]}{suffix}"
        )
        return self._root / name

    # ── 操作 ────────────────────────────────────────────────────────
    def persist(self, descriptor: GeoModelDescriptor, data: bytes) -> Path:
        """已过安全门的包字节原子落盘（写前复验 checksum）。"""
        actual = sha256_of_bytes(data)
        if actual != descriptor.checksum:
            raise ProviderError(
                f"package bytes checksum {actual[:12]}… != descriptor "
                f"{descriptor.checksum[:12]}… (run package_security inspection first)"
            )
        path = self.package_path(descriptor)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path

    def resolve(self, descriptor: GeoModelDescriptor) -> Path:
        """load 路径唯一取包口：存在性 + sha256 load 双验，不符 typed 拒绝。"""
        from app.lib.modelops.errors import ModelChecksumError

        path = self.package_path(descriptor)
        if not path.exists():
            raise ProviderError(
                f"model package not found for {descriptor.model_id}@{descriptor.model_version} "
                f"(expected {path.name}); register the model with package_bytes first",
                correction_hint="register the model with its package bytes before inference",
            )
        actual = sha256_of_file(path)
        if actual != descriptor.checksum:
            raise ModelChecksumError(
                f"stored package for {descriptor.model_id}@{descriptor.model_version} "
                f"fails load-time checksum: {actual[:12]}… != {descriptor.checksum[:12]}…"
            )
        return path

    def delete(self, descriptor: GeoModelDescriptor) -> bool:
        path = self.package_path(descriptor)
        if not path.exists():
            return False
        path.unlink()
        return True


__all__ = ["ModelPackageStore"]
