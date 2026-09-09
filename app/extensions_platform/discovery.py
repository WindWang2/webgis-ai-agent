"""扩展包发现与指纹（ADR-0104 / Wave 2）。

目录布局约定::

    <extension_dir>/
        manifest.json          # 唯一入口（无 manifest 的目录被忽略并留痕）
        main.py                # entry_point 指向的模块（含 activate）
        ...

发现边界（bounded discovery，防御病态目录）：
- 最多扫描 ``max_extensions`` 个子目录（默认 64），超出产出诊断并停止；
- 目录深度固定一层（<dir>/<ext>/manifest.json），不递归；
- manifest.json 大小上界 256 KiB，超界按解析失败处理；
- 单个扩展解析失败只影响自身（fail closed per extension），不拖垮扫描。

指纹（fingerprint）：对扩展目录内全部文件做确定性 sha256（相对路径排序
后逐文件 hash），用于检测「发现后内容被篡改」。文件数 / 总字节上界
（512 / 8 MiB）内拒算指纹并产出诊断。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic
from .manifest import GisExtensionManifest, manifest_from_dict

MANIFEST_FILENAME = "manifest.json"
MANIFEST_MAX_BYTES = 256 * 1024
FINGERPRINT_MAX_FILES = 512
FINGERPRINT_MAX_TOTAL_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_EXTENSIONS = 64
# 签名文件（ADR-0105 / Wave 6，常量正体在 signing.py 语境中使用）。签名
# 覆盖的是包内容，而内容指纹不得覆盖签名本身（循环依赖 → 指纹永不收敛），
# 故指纹计算必须排除它。常量定义在本模块（discovery 是更底层），signing.py
# 从此处导入，避免 signing → discovery → signing 循环导入。
SIGNATURE_FILENAME = "signature.json"


@dataclass(frozen=True)
class DiscoveredExtension:
    manifest: GisExtensionManifest
    path: Path
    diagnostics: tuple[ExtensionDiagnostic, ...] = ()
    fingerprint: Optional[str] = None

    @property
    def extension_id(self) -> str:
        return self.manifest.id


@dataclass(frozen=True)
class DiscoveryFailure:
    """发现阶段就失败的扩展（无/坏 manifest），单独成类以便 CLI 呈现。"""

    path: Path
    diagnostics: tuple[ExtensionDiagnostic, ...] = ()


@dataclass(frozen=True)
class DiscoveryResult:
    extensions: tuple[DiscoveredExtension, ...] = ()
    failures: tuple[DiscoveryFailure, ...] = ()
    diagnostics: tuple[ExtensionDiagnostic, ...] = field(default_factory=tuple)


def compute_fingerprint(ext_dir: Path) -> tuple[Optional[str], Optional[ExtensionDiagnostic]]:
    """目录内容确定性指纹；超界返回 (None, diagnostic)。

    ``__pycache__``/``*.pyc`` 是解释器导入的副产物而非扩展内容，必须
    排除——否则首次激活生成字节码后指纹必然漂移。``signature.json``
    （Wave 6 内容签名）同理排除：签名覆盖的是包内容，内容指纹若覆盖
    签名自身则循环依赖、永不收敛。
    """
    ext_dir = Path(ext_dir)
    files: list[Path] = []
    total_bytes = 0
    for root, dirs, names in os.walk(ext_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        # 确定性：os.walk 顺序依赖文件系统，这里收集后统一排序。
        for n in names:
            if n.endswith(".pyc") or n == SIGNATURE_FILENAME:
                continue
            p = Path(root) / n
            if not p.is_file():
                continue
            files.append(p)
            if len(files) > FINGERPRINT_MAX_FILES:
                return None, ExtensionDiagnostic.error(
                    DiagnosticCode.FINGERPRINT_CHANGED,
                    f"extension dir has more than {FINGERPRINT_MAX_FILES} files",
                )
    files.sort()
    hasher = hashlib.sha256()
    hasher.update(b"webgis-extension-fingerprint-v1\n")
    for p in files:
        size = p.stat().st_size
        total_bytes += size
        if total_bytes > FINGERPRINT_MAX_TOTAL_BYTES:
            return None, ExtensionDiagnostic.error(
                DiagnosticCode.FINGERPRINT_CHANGED,
                f"extension dir exceeds {FINGERPRINT_MAX_TOTAL_BYTES} bytes",
            )
        rel = p.relative_to(ext_dir).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        with p.open("rb") as fh:
            hasher.update(fh.read())
        hasher.update(b"\0")
    return hasher.hexdigest(), None


def _parse_manifest_file(path: Path) -> tuple[Optional[GisExtensionManifest], tuple[ExtensionDiagnostic, ...], Optional[str]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, (
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED, f"manifest unreadable: {exc}"
            ),
        ), None
    if len(raw) > MANIFEST_MAX_BYTES:
        return None, (
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"manifest exceeds {MANIFEST_MAX_BYTES} bytes",
            ),
        ), None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, (
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED, f"manifest is not valid JSON: {exc}"
            ),
        ), None
    manifest, err = manifest_from_dict(data)
    if manifest is None:
        return None, (
            ExtensionDiagnostic.error(DiagnosticCode.MANIFEST_INVALID, err or "invalid manifest"),
        ), None
    return manifest, (), None


def discover_extensions(
    roots: list[Path | str],
    max_extensions: int = DEFAULT_MAX_EXTENSIONS,
) -> DiscoveryResult:
    """扫描多个根目录下的 <dir>/<ext>/manifest.json（深度一层）。"""
    extensions: list[DiscoveredExtension] = []
    failures: list[DiscoveryFailure] = []
    diagnostics: list[ExtensionDiagnostic] = []
    seen_ids: dict[str, Path] = {}
    limit_hit = False

    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            diagnostics.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"extension root {str(root)!r} is not a directory (skipped)",
                )
            )
            continue
        for child in sorted(root.iterdir()):
            if limit_hit:
                break
            if not child.is_dir():
                continue
            manifest_path = child / MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            if len(extensions) + len(failures) >= max_extensions:
                limit_hit = True
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DISCOVERY_LIMIT_EXCEEDED,
                        f"discovery limit of {max_extensions} extensions reached "
                        f"(remaining directories ignored)",
                    )
                )
                break
            manifest, manifest_diags, _ = _parse_manifest_file(manifest_path)
            if manifest is None:
                failures.append(DiscoveryFailure(path=child, diagnostics=manifest_diags))
                continue
            if manifest.id in seen_ids:
                # 同 id 双份：按排序路径先到先得（确定性），后者隔离。
                failures.append(
                    DiscoveryFailure(
                        path=child,
                        diagnostics=(
                            ExtensionDiagnostic.error(
                                DiagnosticCode.ID_COLLISION,
                                f"extension id {manifest.id!r} already discovered at "
                                f"{str(seen_ids[manifest.id])!r}; this copy is quarantined",
                                extension_id=manifest.id,
                            ),
                        ),
                    )
                )
                continue
            seen_ids[manifest.id] = child
            fp, fp_diag = compute_fingerprint(child)
            diags = list(manifest_diags)
            if fp_diag is not None:
                diags.append(fp_diag)
            extensions.append(
                DiscoveredExtension(
                    manifest=manifest,
                    path=child,
                    diagnostics=tuple(diags),
                    fingerprint=fp,
                )
            )
    # 确定性输出：按 extension id 排序。
    extensions.sort(key=lambda e: e.extension_id)
    return DiscoveryResult(
        extensions=tuple(extensions),
        failures=tuple(failures),
        diagnostics=tuple(diagnostics),
    )
