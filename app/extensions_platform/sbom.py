"""扩展包 SBOM（软件物料清单）与 provenance（ADR-0105 / Wave 7）。

对单个扩展包产出**确定性**清单：同包同指纹 → 逐字节相同的 JSON（无
时间戳、无集合序 hazard，一切排序）。消费方（发布审计 / 漏洞比对 /
合规留痕）可对 SBOM 做差异比较而不必担心噪声。

清单内容与边界：
- ``files``：与 ``compute_fingerprint`` 同覆盖面（排除 ``__pycache__``/
  ``*.pyc``/``signature.json``——签名覆盖内容，SBOM 描述的正是签名所
  覆盖的内容；signature.json 里的 signed_at 时间戳若进清单会破坏确定性），
  每文件带字节数与 sha256，按相对路径排序；512 文件 / 8 MiB 上界沿用
  指纹边界，超界 typed 拒绝（fail closed，不出半份清单）；
- ``python_imports``：全部 ``*.py`` AST 解析的顶层模块名，排除标准库
  （``sys.stdlib_module_names``）、平台 SDK（``app``，经授权的通道面而非
  第三方依赖）与相对导入（包内兄弟模块）；语法残缺的文件跳过（SBOM 是
  尽力清单，不阻断发现/激活语义）；
- ``dependencies``：镜像 manifest 依赖声明（含 ``optional`` 标志与版本
  约束原串）；
- ``secret_scan``：高置信 secret 形状扫描（AWS key / 私钥块 / Slack /
  GitHub / OpenAI 风格 token），只报「形状命中」这一事实，不做网络验证；
  纯文本可解码的文件全扫（含 manifest.json / signature.json——它们同样
  可能携带泄漏），二进制文件跳过。
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .discovery import (
    FINGERPRINT_MAX_FILES,
    FINGERPRINT_MAX_TOTAL_BYTES,
    SIGNATURE_FILENAME,
)
from .manifest import GisExtensionManifest

SBOM_VERSION = "1"

# 平台 SDK 顶层包名：`import app...` 是平台授权的通道面，不算第三方依赖。
_SDK_TOP_LEVEL = "app"

# 高置信 secret 形状（只收低误报模式；宽松形状会淹没审计者）。
# key = secret_scan.findings[].kind（稳定词表，测试与外部脚本依赖）。
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack_token", re.compile(r"xox[abprs]-[0-9A-Za-z-]{10,}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("openai_style_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
)


def _content_files(pack_dir: Path) -> list[Path]:
    """清单覆盖的文件集合（与指纹同覆盖面），确定性排序。"""
    files: list[Path] = []
    for root, dirs, names in os.walk(pack_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            if name.endswith(".pyc") or name == SIGNATURE_FILENAME:
                continue
            path = Path(root) / name
            if path.is_file():
                files.append(path)
    files.sort()
    return files


def _top_level_imports(source: str) -> set[str]:
    """单个 .py 源码的顶层导入模块名（相对导入不计）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()  # 语法残缺的兄弟模块跳过（尽力清单，不阻断）
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue  # 相对导入 = 包内兄弟模块
            if node.module:
                tops.add(node.module.split(".")[0])
    return tops


def _scan_secrets(findings: list[dict[str, str]], rel: str, text: str) -> None:
    """对一份已解码文本做形状扫描；命中按 (file, kind) 记录。"""
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append({"file": rel, "kind": kind})


def build_sbom(pack_dir: Path, manifest: GisExtensionManifest, fingerprint: str) -> dict:
    """构建确定性 SBOM；包体超界 → typed ExtensionPlatformError（fail closed）。

    ``fingerprint`` 由调用方供给（通常来自 ``compute_fingerprint``），SBOM
    不重算——清单是对「该指纹所覆盖内容」的展开描述。
    """
    pack_dir = Path(pack_dir)
    files: list[dict[str, Any]] = []
    imports: set[str] = set()
    findings: list[dict[str, str]] = []
    total_bytes = 0
    for path in _content_files(pack_dir):
        size = path.stat().st_size
        total_bytes += size
        if len(files) >= FINGERPRINT_MAX_FILES or total_bytes > FINGERPRINT_MAX_TOTAL_BYTES:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.FINGERPRINT_CHANGED,
                    f"pack exceeds SBOM bounds "
                    f"({FINGERPRINT_MAX_FILES} files / {FINGERPRINT_MAX_TOTAL_BYTES} bytes)",
                )
            )
        data = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(pack_dir).as_posix(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue  # 二进制文件：无 imports / secret 扫描面
        if path.suffix == ".py":
            imports |= _top_level_imports(text)
        _scan_secrets(findings, files[-1]["path"], text)
    # signature.json 不进清单（不在指纹覆盖面内），但仍在 secret 扫描面
    # ——签名文件同样可能携带泄漏。此处补扫包根的签名文件。
    sig_path = pack_dir / SIGNATURE_FILENAME
    if sig_path.is_file():
        try:
            _scan_secrets(findings, SIGNATURE_FILENAME, sig_path.read_bytes().decode("utf-8"))
        except UnicodeDecodeError:
            pass  # 二进制噪声不构成扫描面
    findings.sort(key=lambda f: (f["file"], f["kind"]))
    stdlib = sys.stdlib_module_names
    python_imports = sorted(
        name for name in imports if name not in stdlib and name != _SDK_TOP_LEVEL
    )
    dependencies: list[dict[str, Any]] = [
        {
            "id": dep.id,
            "required": dep.required,
            "version": dep.version,
            "optional": False,
        }
        for dep in manifest.dependencies
    ] + [
        {
            "id": dep.id,
            "required": dep.required,
            "version": dep.version,
            "optional": True,
        }
        for dep in manifest.optional_dependencies
    ]
    dependencies.sort(key=lambda dep: dep["id"])
    return {
        "sbom_version": SBOM_VERSION,
        "extension_id": manifest.id,
        "name": manifest.name,
        "namespace": manifest.namespace,
        "version": manifest.version,
        "api_version": manifest.api_version,
        "vendor": manifest.vendor,
        "permissions": sorted(manifest.permissions),
        "trust_declared": manifest.trust,
        "fingerprint": fingerprint,
        "files": files,
        "python_imports": python_imports,
        "dependencies": dependencies,
        "secret_scan": {"clean": not findings, "findings": findings},
    }
