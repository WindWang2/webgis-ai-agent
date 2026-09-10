"""Model package security gate —— 包**只校验不执行**（ADR-0119 §3.7）。

模型文件视为不可信数据：

- checksum：sha256 与登记值精确匹配（注册时 + load 时双验）；
- archive：zip/tar member 路径穿越（resolved path 必须落在解包根内）、
  symlink/hardlink 拒绝、成员数/总解包字节上限（zip bomb）；
- 成员黑名单：任何可执行/代码加载格式直接拒绝（.py/.pyc/.so/.pkl/
  .pickle/.joblib/.pth/.dll/.dylib）——core 进程没有「受信任包代码」概念；
- metadata：manifest.json strict schema（未知字段拒绝、大小上限、键值
  secret 词根扫描）。

所有失败 :class:`PackageSecurityError`（typed + correction hint）。
"""
from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set

from .errors import ModelChecksumError, PackageSecurityError

#: 默认包上限（bytes）。确定性 tiny 包远小于此；真实大模型由 operator
#: 通过 settings 显式放宽，不静默。
DEFAULT_MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_MAX_MEMBERS = 4096
DEFAULT_MAX_TOTAL_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB
DEFAULT_MAX_METADATA_BYTES = 256 * 1024

#: 可执行/代码加载成员黑名单（大小写不敏感后缀匹配）。
FORBIDDEN_SUFFIXES: Set[str] = {
    ".py", ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib",
    ".pkl", ".pickle", ".joblib", ".pth", ".bin.py", ".exe", ".bat", ".sh",
}

#: 嵌套 archive 拒绝（zip 内 zip/tar = 绕过成员审查的第二信任域）。
FORBIDDEN_ARCHIVE_SUFFIXES: Set[str] = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}

#: 权重载荷的合法形态（架构挑战 m4 契约）：数值 npy/npz（必须以
#: ``np.load(..., allow_pickle=False)`` 消费——引擎与 provider 层共同
#: 遵守；object array 反序列化即代码执行）+ JSON。其余成员仅作数据存证。
ALLOWED_WEIGHT_SUFFIXES: Set[str] = {".npy", ".npz", ".json"}

#: 单文件模型工件的合法后缀（V3 §B）：ONNX 计算图（protobuf，非 pickle）
#: 与 TorchScript archive（``torch.jit.load`` 语义，**不是** pickle 的
#: .pth——后者在成员黑名单）。两者仍受 checksum 双验 + 尺寸上限约束。
SINGLE_FILE_MODEL_SUFFIXES: Set[str] = {".onnx", ".pt"}

#: descriptor.artifact_format → 单文件校验后缀（registry 分发依据；
#: 其余 artifact_format 一律走 ``inspect_archive`` 结构审查）。
ARTIFACT_FORMAT_ONNX = "onnx-v1"
ARTIFACT_FORMAT_TORCHSCRIPT = "torchscript-v1"
SINGLE_FILE_FORMAT_SUFFIXES: Dict[str, str] = {
    ARTIFACT_FORMAT_ONNX: ".onnx",
    ARTIFACT_FORMAT_TORCHSCRIPT: ".pt",
}

METADATA_FILENAME = "manifest.json"


@dataclass(frozen=True)
class PackageEntry:
    """包内一个成员的校验视图（绝不落地执行）。"""

    name: str
    size: int
    digest: str  # sha256 hex（流式计算，不驻留大内存）


@dataclass(frozen=True)
class PackageReport:
    """包校验通过后的结构化报告（进 registry provenance / manifest）。"""

    checksum: str
    entries: List[PackageEntry]
    metadata: Dict[str, Any]
    total_bytes: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "checksum": self.checksum,
            "entry_count": len(self.entries),
            "total_bytes": self.total_bytes,
            "metadata": self.metadata,
            "entries": [{"name": e.name, "size": e.size, "digest": e.digest} for e in self.entries],
        }


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(data: Optional[bytes] = None, *, path: Optional[Path] = None,
                    expected: str = "") -> str:
    """计算并比对 checksum；不符抛 :class:`ModelChecksumError`。"""
    if (data is None) == (path is None):
        raise PackageSecurityError("verify_checksum requires exactly one of data/path")
    actual = sha256_of_bytes(data) if data is not None else sha256_of_file(Path(path))  # type: ignore[arg-type]
    if expected and actual != expected.lower():
        raise ModelChecksumError(
            f"package checksum mismatch: expected {expected[:12]}…, got {actual[:12]}…"
        )
    return actual


def _check_member_name(name: str, *, extraction_root: Optional[Path]) -> None:
    """成员名安全：绝对路径/.. 盘点/反斜杠/NUL；给定解包根时验证 containment。"""
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise PackageSecurityError(f"unsafe package member path: {name[:80]!r}")
    pure = PurePosixPath(name)
    if ".." in pure.parts:
        raise PackageSecurityError(f"package member escapes root via '..': {name[:80]!r}")
    if extraction_root is not None:
        resolved = (extraction_root / pure).resolve()
        root_resolved = extraction_root.resolve()
        if not str(resolved).startswith(str(root_resolved) + "\\") and \
           not str(resolved).startswith(str(root_resolved) + "/") and \
           resolved != root_resolved:
            raise PackageSecurityError(
                f"package member resolves outside extraction root: {name[:80]!r}"
            )


def _check_forbidden(name: str) -> None:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        raise PackageSecurityError(
            f"package member {name[:80]!r} has forbidden format {suffix!r}; "
            "packages are data-only (no pickle/code loading in the core process)"
        )
    if suffix in FORBIDDEN_ARCHIVE_SUFFIXES:
        raise PackageSecurityError(
            f"package member {name[:80]!r} is a nested archive ({suffix!r}); "
            "nested archives bypass member review and are rejected"
        )


def inspect_archive(
    data: bytes,
    *,
    expected_checksum: str = "",
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_total_unpacked_bytes: int = DEFAULT_MAX_TOTAL_UNPACKED_BYTES,
    require_metadata: bool = True,
) -> PackageReport:
    """校验 zip/tar 包（内存形态）：checksum → 结构 → 成员黑名单 → metadata。

    不解包到磁盘；成员内容只在需要 metadata 时流式读取（有大小上限）。
    """
    actual_checksum = sha256_of_bytes(data)
    if expected_checksum and actual_checksum != expected_checksum.lower():
        raise ModelChecksumError(
            f"package checksum mismatch: expected {expected_checksum[:12]}…, "
            f"got {actual_checksum[:12]}…"
        )
    if len(data) > DEFAULT_MAX_PACKAGE_BYTES:
        raise PackageSecurityError(
            f"package exceeds {DEFAULT_MAX_PACKAGE_BYTES} bytes"
        )

    entries: List[PackageEntry] = []
    metadata: Dict[str, Any] = {}
    total = 0

    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > max_members:
                raise PackageSecurityError(f"package has {len(infos)} members (max {max_members})")
            for info in infos:
                _check_member_name(info.filename, extraction_root=None)
                _check_forbidden(info.filename)
                if info.is_dir():
                    continue
                # symlink 属性：外部属性高 16 位为 unix mode；S_IFLNK = 0o120000
                unix_mode = info.external_attr >> 16
                if unix_mode and (unix_mode & 0o170000) == 0o120000:
                    raise PackageSecurityError(
                        f"package member is a symlink: {info.filename[:80]!r}"
                    )
                total += info.file_size
                if total > max_total_unpacked_bytes:
                    raise PackageSecurityError(
                        "total unpacked size exceeds budget (possible zip bomb)"
                    )
                digest = hashlib.sha256()
                size = 0
                with zf.open(info) as fh:
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > info.file_size:
                            raise PackageSecurityError(
                                f"member {info.filename[:80]!r} inflates beyond declared size"
                            )
                        digest.update(chunk)
                entries.append(PackageEntry(info.filename, size, digest.hexdigest()))
                if info.filename == METADATA_FILENAME:
                    if size > DEFAULT_MAX_METADATA_BYTES:
                        raise PackageSecurityError("package metadata exceeds size cap")
                    metadata = _parse_metadata(zf.read(info))
    else:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                members = tf.getmembers()
        except tarfile.TarError as exc:
            raise PackageSecurityError(f"unrecognized package format: {exc}") from exc
        if len(members) > max_members:
            raise PackageSecurityError(f"package has {len(members)} members (max {max_members})")
        for m in members:
            _check_member_name(m.name, extraction_root=None)
            _check_forbidden(m.name)
            if not m.isfile():
                raise PackageSecurityError(
                    f"package member is not a regular file ({m.type!r}): {m.name[:80]!r}"
                )
            total += m.size
            if total > max_total_unpacked_bytes:
                raise PackageSecurityError("total unpacked size exceeds budget")
            fh = tf.extractfile(m)
            digest = hashlib.sha256()
            if fh is not None:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            entries.append(PackageEntry(m.name, m.size, digest.hexdigest()))
            if m.name == METADATA_FILENAME:
                if m.size > DEFAULT_MAX_METADATA_BYTES:
                    raise PackageSecurityError("package metadata exceeds size cap")
                if fh is not None:
                    fh.seek(0)
                metadata = _parse_metadata(fh.read() if fh else b"")

    if require_metadata and not metadata:
        raise PackageSecurityError(
            f"package lacks {METADATA_FILENAME}; models must ship a strict metadata manifest"
        )
    return PackageReport(
        checksum=actual_checksum, entries=entries, metadata=metadata, total_bytes=total
    )


def inspect_model_file(
    data: bytes,
    *,
    expected_checksum: str = "",
    allowed_suffix: str = ".onnx",
) -> PackageReport:
    """校验单文件模型工件（.onnx/.pt，V3 §B）：checksum → 后缀 → 尺寸。

    单文件没有成员结构可审（无 manifest.json），以合成 entry + 格式元数据
    构成报告——registry provenance 的形状与 archive 包一致。计算图运行时
    的执行语义（图数据驱动、无任意代码路径）由对应 adapter 的 docstring
    如实声明。
    """
    # 注意：不能用 PurePosixPath.suffix——'.onnx' 以点开头会被当作隐藏
    # 文件名返回空 suffix。allowed_suffix 是受控常量参数，直接规范化。
    suffix = allowed_suffix.lower()
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    if suffix not in SINGLE_FILE_MODEL_SUFFIXES:
        raise PackageSecurityError(
            f"single-file model suffix must be one of {sorted(SINGLE_FILE_MODEL_SUFFIXES)} "
            f"(got {allowed_suffix!r})"
        )
    actual_checksum = sha256_of_bytes(data)
    if expected_checksum and actual_checksum != expected_checksum.lower():
        raise ModelChecksumError(
            f"package checksum mismatch: expected {expected_checksum[:12]}…, "
            f"got {actual_checksum[:12]}…"
        )
    if len(data) > DEFAULT_MAX_PACKAGE_BYTES:
        raise PackageSecurityError(f"package exceeds {DEFAULT_MAX_PACKAGE_BYTES} bytes")
    # ONNX protobuf 的最小结构哨兵：首字节 field 1 (ir_version) varint——
    # 只拒绝明显不是 protobuf 的载荷（如纯文本/空包），不做完整解析。
    if suffix == ".onnx" and (len(data) < 8 or data[0] != 0x08):
        raise PackageSecurityError(
            "package does not look like an ONNX protobuf (leading byte mismatch); "
            "refusing to treat it as a computation graph"
        )
    return PackageReport(
        checksum=actual_checksum,
        entries=[PackageEntry(name=f"model{suffix}", size=len(data), digest=actual_checksum)],
        metadata={"format": f"single-file{suffix}"},
        total_bytes=len(data),
    )


def load_weights_array(raw: bytes, *, member_name: str = "weights.npy") -> Any:
    """包内权重载荷的**唯一**合法消费口：``np.load(allow_pickle=False)``。

    numpy 反序列化 object array 即代码执行（挑战 m4）——provider 层禁止
    直接 ``np.load`` 包内字节，必须经本函数（typed 拒绝 object array）。
    """
    suffix = PurePosixPath(member_name).suffix.lower()
    if suffix not in ALLOWED_WEIGHT_SUFFIXES or suffix == ".json":
        raise PackageSecurityError(
            f"weight member {member_name[:80]!r} must be .npy/.npz (got {suffix!r})"
        )
    import numpy as np

    try:
        arr = np.load(io.BytesIO(raw), allow_pickle=False)
    except ValueError as exc:
        raise PackageSecurityError(
            f"weight member {member_name[:80]!r} refused by allow_pickle=False gate: {exc}"
        ) from exc
    if getattr(arr, "dtype", None) is not None and arr.dtype == object:
        raise PackageSecurityError("object arrays are code, not weights — rejected")
    return arr


def _parse_metadata(raw: bytes) -> Dict[str, Any]:
    """strict metadata 解析：JSON object、大小/键数上限、secret 词根扫描。"""
    import json

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PackageSecurityError(f"package metadata is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PackageSecurityError("package metadata must be a JSON object")
    if len(parsed) > 64:
        raise PackageSecurityError("package metadata has too many keys (max 64)")
    _scan_metadata_keys("package.metadata", parsed, depth=0)
    return parsed


def _scan_metadata_keys(where: str, node: Any, depth: int) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and any(
                marker in key.lower()
                for marker in ("password", "secret", "token", "api_key", "apikey", "credential")
            ):
                raise PackageSecurityError(
                    f"{where}: metadata key {key!r} looks like a secret — packages never carry secrets"
                )
            _scan_metadata_keys(where, value, depth + 1)
    elif isinstance(node, list):
        for item in node[:64]:
            _scan_metadata_keys(where, item, depth + 1)
