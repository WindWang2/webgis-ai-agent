"""构建身份信息（Platform V4，ADR-0131 D7）。

单一事实源：仓库根 ``VERSION`` 文件 + git commit（env 覆盖 → git 查询
best-effort）。模块级缓存，查询失败诚实落 ``unknown``，绝不猜。

漂移修复（review R1-M2）：历史上 /health 硬编码 "0.1.3" 而 VERSION 文件
是 "0.1.0.0"——同一部署多面矛盾。现在 VERSION 文件已对齐到代码发布身份
（0.1.3，与 ``app.__version__`` / FastAPI version / extensions_platform
``CORE_RELEASE_VERSION`` 同源），/health 与 /version 从本模块读取，
三个公开面口径一致。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERSION_FILE = _REPO_ROOT / "VERSION"

_cache_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None


@lru_cache(maxsize=1)
def _read_version_file() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


@lru_cache(maxsize=1)
def _git_commit() -> str:
    """构建身份：WEBGIS_BUILD_SHA env（容器构建时注入）→ git 查询 → unknown。

    git 查询是 best-effort：非 git 部署/只读文件系统一律落 unknown，
    绝不让版本查询拖垮或拖慢请求（2s 上限）。
    """
    env_sha = os.environ.get("WEBGIS_BUILD_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 — 最佳努力
        pass
    return "unknown"


def build_info() -> Dict[str, Any]:
    """构建身份快照（缓存；字段全部非敏感，可直接进公开端点）。"""
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = {
                "version": _read_version_file(),
                "commit": _git_commit(),
                "python": sys.version.split()[0],
            }
        return dict(_cache)


def version_string() -> str:
    return str(build_info()["version"])


def reset_build_info_cache_for_tests() -> None:
    global _cache
    with _cache_lock:
        _cache = None
    _read_version_file.cache_clear()
    _git_commit.cache_clear()


__all__ = [
    "build_info",
    "version_string",
    "reset_build_info_cache_for_tests",
]
