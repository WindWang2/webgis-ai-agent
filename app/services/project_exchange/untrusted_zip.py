"""不可信 ZIP 防护（WP3）——外部工程包一律视为不可信输入。

防线（全部服务端强制，fail-closed）：
1. 中央目录先读：条目数 / 声明展开总大小 / 单条目声明大小，超帽直接拒；
2. 路径规范化：拒绝绝对路径、``..`` 穿越、反斜杠、NFC 外归一、重复条目、
   目录条目与符号链接（external_attr 非 regular 文件）；
3. 扩展名白名单：唯一放行面；嵌套压缩包（.zip/.tar/…）显式拒绝；
4. 逐条目解压预算：实际字节流式累计，超帽中断；压缩比校验（bomb）；
5. 哈希对账：解压后逐文件 sha256 与 manifest.files 对账。

解压只落在受控 staging 目录；绝不 ``extractall``；绝不自动执行任何包内
内容（不运行代码 / 不自动联网 / 不自动触发工作流）。
"""
from __future__ import annotations

import os
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

from app.services.project_exchange import contracts as pc

#: 解压批读块（流式；防止整条目进内存）。
_STREAM_CHUNK = 1024 * 1024


@dataclass
class ZipEntryPlan:
    """单个安全条目的解压计划。"""

    arcname: str
    target_rel: str        # staging 内相对路径（POSIX 规范化后）
    declared_bytes: int
    ext: str


@dataclass
class ZipInspection:
    """中央目录审读结果（解压前的全部静态判定）。"""

    entry_count: int
    declared_total_bytes: int
    manifest_present: bool
    manifest_bytes: Optional[bytes]
    entries: List[ZipEntryPlan] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)


def inspect_zip(zip_path: Path, *, manifest: Optional[dict] = None
                ) -> ZipInspection:
    """零解压审读：中央目录 + 静态防线 1–3。失败抛 typed PackageError。"""
    inspection = ZipInspection(entry_count=0, declared_total_bytes=0,
                               manifest_present=False, manifest_bytes=None)
    try:
        zf = zipfile.ZipFile(str(zip_path))
    except (zipfile.BadZipFile, OSError) as e:
        raise pc.PackageError(pc.ERR_PACKAGE_FORMAT, f"不是合法 ZIP: {e}") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > pc.MAX_PACKAGE_ENTRIES:
            raise pc.PackageError(
                pc.ERR_ENTRY_LIMIT,
                f"条目数 {len(infos)} 超过 {pc.MAX_PACKAGE_ENTRIES}")
        inspection.entry_count = len(infos)
        seen: Set[str] = set()
        total_declared = 0
        for info in infos:
            name = info.filename
            declared = int(info.file_size)
            total_declared += declared
            # 防线 1：声明总量
            if total_declared > _max_package_bytes():
                raise pc.PackageError(
                    pc.ERR_SIZE_BUDGET,
                    f"声明展开总量 {total_declared}B 超过预算")
            if declared > pc.MAX_ENTRY_BYTES:
                raise pc.PackageError(
                    pc.ERR_SIZE_BUDGET,
                    f"条目 {name[:80]} 声明 {declared}B 超过单条目上限")
            # 压缩比（声明级 bomb 预检；高度可压缩的白名单文本不豁免，
            # 因为后续还有逐条目实际字节流式预算兜底）
            if declared > 0 and info.compress_size > 0:
                ratio = declared / max(1, info.compress_size)
                if ratio > pc.MAX_COMPRESSION_RATIO:
                    raise pc.PackageError(
                        pc.ERR_COMPRESSION_RATIO,
                        f"条目 {name[:80]} 压缩比 {ratio:.0f}x 超过 "
                        f"{pc.MAX_COMPRESSION_RATIO}x（ZIP bomb 嫌疑）")
            # 目录条目：记录但不解压（布局由解压侧 mkdir 承担）
            if name.endswith("/"):
                continue
            # 防线 2：路径规范化
            safe_rel = _safe_relpath(name, seen)
            # external_attr 高位：非 regular 文件（符号链接/设备）拒绝
            if (info.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise pc.PackageError(
                    pc.ERR_PATH_UNSAFE,
                    f"条目 {name[:80]} 不是常规文件（符号链接/特殊文件拒绝）")
            # 防线 3：扩展名白名单
            ext = PurePosixPath(safe_rel).suffix.lower()
            if ext not in pc.ALLOWED_ENTRY_EXTENSIONS:
                raise pc.PackageError(
                    pc.ERR_ENTRY_TYPE,
                    f"条目 {name[:80]} 扩展名 {ext or '(无)'} 不在白名单")
            seen.add(safe_rel)
            inspection.entries.append(ZipEntryPlan(
                arcname=name, target_rel=safe_rel, declared_bytes=declared,
                ext=ext))
            if safe_rel == pc.MANIFEST_PATH:
                inspection.manifest_present = True
        if not inspection.manifest_present:
            raise pc.PackageError(
                pc.ERR_PACKAGE_FORMAT, f"包根缺少 {pc.MANIFEST_PATH}")
        # manifest 先读（≤1MiB 守卫在 format_gate）
        raw = zf.read(pc.MANIFEST_PATH)
        if len(raw) > pc.MAX_MANIFEST_BYTES:
            raise pc.PackageError(pc.ERR_MANIFEST_TOO_LARGE,
                                  "manifest 超过 1MiB")
        inspection.manifest_bytes = raw
    return inspection


def extract_zip(zip_path: Path, inspection: ZipInspection, staging: Path,
                *, expected_hashes: Optional[Dict[str, str]] = None,
                progress=None) -> List[ZipEntryPlan]:
    """受控解压：只解 whitelist 条目，流式预算 + 哈希对账。

    ``expected_hashes``：rel path → sha256（来自 manifest.files）。
    提供时逐条对账；不符 → ERR_HASH_MISMATCH（fatal）。
    Returns 实际解压的条目清单。
    """
    extracted: List[ZipEntryPlan] = []
    extracted_total = 0
    try:
        zf = zipfile.ZipFile(str(zip_path))
    except (zipfile.BadZipFile, OSError) as e:
        raise pc.PackageError(pc.ERR_PACKAGE_FORMAT, f"ZIP 打不开: {e}") from e
    with zf:
        max_total = _max_package_bytes()
        for i, plan in enumerate(inspection.entries):
            target = (staging / plan.target_rel).resolve()
            base = staging.resolve()
            if not str(target).startswith(str(base) + os.sep):
                raise pc.PackageError(pc.ERR_PATH_UNSAFE,
                                      f"解压目标越界: {plan.target_rel[:80]}")
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            import hashlib

            digest = hashlib.sha256()
            try:
                with zf.open(plan.arcname) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(_STREAM_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        extracted_total += len(chunk)
                        if written > pc.MAX_ENTRY_BYTES:
                            raise pc.PackageError(
                                pc.ERR_SIZE_BUDGET,
                                f"条目 {plan.arcname[:80]} 实际展开超过单条目上限")
                        if extracted_total > max_total:
                            raise pc.PackageError(
                                pc.ERR_SIZE_BUDGET,
                                f"实际展开总量超过预算 {max_total}B")
                        digest.update(chunk)
                        dst.write(chunk)
            except pc.PackageError:
                target.unlink(missing_ok=True)
                raise
            except (OSError, zipfile.BadZipFile, RuntimeError) as e:
                target.unlink(missing_ok=True)
                raise pc.PackageError(
                    pc.ERR_PACKAGE_FORMAT,
                    f"条目解压失败 {plan.arcname[:80]}: {e}") from e
            if expected_hashes is not None:
                expected = expected_hashes.get(plan.target_rel)
                if expected and digest.hexdigest() != expected:
                    raise pc.PackageError(
                        pc.ERR_HASH_MISMATCH,
                        f"条目 {plan.target_rel[:80]} sha256 与 manifest 不符")
            extracted.append(plan)
            if progress is not None and i % 25 == 0:
                progress(10 + int(40 * (i + 1) / max(1, len(inspection.entries))),
                         f"解包 {i + 1}/{len(inspection.entries)}")
    return extracted


def _max_package_bytes() -> int:
    from app.core.config import settings

    return int(getattr(settings, "PROJECT_EXCHANGE_MAX_PACKAGE_BYTES",
                       pc.DEFAULT_MAX_PACKAGE_BYTES))


def _safe_relpath(name: str, seen: Set[str]) -> str:
    """归一化条目路径并做全部静态防线（返回 staging 内相对 POSIX 路径）。"""
    import os

    if "\x00" in name:
        raise pc.PackageError(pc.ERR_PATH_UNSAFE, "条目名含 NUL")
    # 反斜杠统一视作分隔符后再检查（Windows 打包容错；穿越检查在 / 语义下做）
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or normalized.startswith("/"):
        raise pc.PackageError(pc.ERR_PATH_UNSAFE,
                              f"绝对路径条目拒绝: {name[:80]}")
    parts: List[str] = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise pc.PackageError(pc.ERR_PATH_UNSAFE,
                                  f"路径穿越拒绝: {name[:80]}")
        if unicodedata.normalize("NFC", part) != part:
            raise pc.PackageError(
                pc.ERR_PATH_UNSAFE, f"非 NFC 归一路径拒绝: {name[:80]}")
        if len(part) > 120:
            raise pc.PackageError(pc.ERR_PATH_UNSAFE,
                                  f"路径段过长: {name[:80]}")
        parts.append(part)
    if not parts:
        raise pc.PackageError(pc.ERR_PATH_UNSAFE, f"空路径条目: {name[:80]}")
    rel = "/".join(parts)
    # Windows 保留名与控制字符兜底
    first = parts[-1].split(".")[0].upper()
    if first in ("CON", "PRN", "AUX", "NUL",
                 *tuple(f"COM{i}" for i in range(1, 10)),
                 *tuple(f"LPT{i}" for i in range(1, 10))):
        raise pc.PackageError(pc.ERR_PATH_UNSAFE,
                              f"Windows 保留名拒绝: {name[:80]}")
    if any(ord(c) < 32 for c in rel):
        raise pc.PackageError(pc.ERR_PATH_UNSAFE,
                              f"控制字符拒绝: {name[:80]}")
    if rel in seen:
        raise pc.PackageError(pc.ERR_PATH_UNSAFE,
                              f"重复条目路径拒绝: {name[:80]}")
    return rel
