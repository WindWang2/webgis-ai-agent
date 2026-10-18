"""扩展入口模块加载（ADR-0104/0105 共享基础设施）。

从 host.py 抽出的唯一加载事实源：入口解析、指纹化模块名、import、
按前缀清理。in-process host 与隔离 worker server 必须使用同一套规则，
保证「同一 pack、同一加载语义」：
- 模块名含指纹前 12 位：内容变化必然得到全新命名空间（杜绝陈旧模块）；
- 入口必须落在 pack 目录内（指纹覆盖范围，防路径逃逸）；
- 兄弟模块挂在入口模块命名空间下（``<模块名>.<sibling>``）。
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError

MODULE_PREFIX = "webgis_ext_"


# ── CP-01：源码专用加载 ─────────────────────────────────────────────
# 指纹/签名只覆盖源码（``__pycache__``/``*.pyc`` 是解释器副产物，必须排除
# 否则首次导入即漂移）。因此扩展代码**绝不能**从字节码加载：标准
# SourceFileLoader 会优先读取 ``__pycache__/*.pyc``（UNCHECKED_HASH pyc 甚至
# 不与源码校验），被篡改的字节码可在签名仍然有效的情况下执行。这里的
# loader 总是从源码编译、从不读写字节码缓存；finder 对扩展命名空间
# 权威化——只认 ``.py`` 源码，sourceless ``.pyc`` / 原生扩展一律不可导入。


class SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """总是从 ``.py`` 源码编译；不读、不写 ``__pycache__``。"""

    def get_code(self, fullname: str) -> Any:  # type: ignore[override]
        path = self.get_filename(fullname)
        source = self.get_data(path)
        return self.source_to_code(source, path)

    def set_data(self, path: str, data: bytes, *, _mode: int = 0o666) -> None:  # type: ignore[override]
        return None  # 绝不写字节码


def source_only_spec(
    module_name: str,
    path: Path,
    submodule_search_locations: Optional[list[str]] = None,
) -> Any:
    """为扩展源码文件构造使用 :class:`SourceOnlyLoader` 的 ModuleSpec。"""
    loader = SourceOnlyLoader(module_name, str(path))
    return importlib.util.spec_from_file_location(
        module_name,
        path,
        loader=loader,
        submodule_search_locations=submodule_search_locations,
    )


class _ExtensionSourceFinder(importlib.abc.MetaPathFinder):
    """扩展命名空间（``webgis_ext_*``）下子模块的权威 finder：仅源码。"""

    def find_spec(  # noqa: D401
        self, fullname: str, path: Any, target: Any = None
    ) -> Any:
        if not fullname.startswith(MODULE_PREFIX) or "." not in fullname or not path:
            return None
        for entry in path:
            finder = importlib.machinery.FileFinder(
                entry,
                (SourceOnlyLoader, importlib.machinery.SOURCE_SUFFIXES),
            )
            spec = finder.find_spec(fullname, target)
            if spec is None:
                continue
            if spec.loader is None and not spec.submodule_search_locations:
                continue
            return spec
        # 权威：不让后续 PathFinder 回落到 sourceless .pyc / 原生扩展。
        raise ModuleNotFoundError(
            f"extension module {fullname!r} has no .py source "
            "(bytecode-only / native modules are not loadable)",
            name=fullname,
        )


_FINDER = _ExtensionSourceFinder()
_FINDER_LOCK = threading.Lock()


def install_source_finder() -> None:
    """幂等地把扩展源码 finder 装到 ``sys.meta_path`` 最前。"""
    with _FINDER_LOCK:
        if not any(f is _FINDER for f in sys.meta_path):
            sys.meta_path.insert(0, _FINDER)


def resolve_entry_path(pack_dir: Path, entry_point: str) -> Optional[Path]:
    """解析 entry_point → pack 目录内的入口文件；不存在/逃逸返回 None。"""
    entry = entry_point.strip()
    if not entry or entry == "__init__":
        if (pack_dir / "__init__.py").is_file():
            return pack_dir / "__init__.py"
        return None
    candidate = pack_dir / f"{entry}.py"
    package_init = pack_dir / entry / "__init__.py"
    resolved: Optional[Path] = None
    if candidate.is_file():
        resolved = candidate.resolve()
    elif package_init.is_file():
        resolved = package_init.resolve()
    if resolved is None:
        return None
    # Round-1 审计 F1：入口必须落在包目录内（指纹覆盖范围）。
    if not resolved.is_relative_to(pack_dir.resolve()):
        return None
    return candidate if resolved == candidate.resolve() else package_init


def module_name_for(namespace: str, name: str, fingerprint: Optional[str]) -> str:
    fingerprint = fingerprint or "unknown"
    return f"{MODULE_PREFIX}{namespace}_{name}_{fingerprint[:12]}"


def load_entry_module(
    pack_dir: Path,
    namespace: str,
    name: str,
    entry_point: str,
    fingerprint: Optional[str],
    extension_id: Optional[str] = None,
) -> Any:
    """加载（或返回已加载的）入口模块；失败抛 typed ExtensionPlatformError。"""
    module_name = module_name_for(namespace, name, fingerprint)
    if module_name in sys.modules:
        return sys.modules[module_name]
    entry_path = resolve_entry_path(pack_dir, entry_point)
    if entry_path is None:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.ENTRY_POINT_MISSING,
                f"entry_point {entry_point!r} not found in {str(pack_dir)!r}",
                extension_id=extension_id,
            )
        )
    is_package = entry_path.name == "__init__.py"
    install_source_finder()
    spec = source_only_spec(
        module_name,
        entry_path,
        submodule_search_locations=[str(pack_dir)] if is_package else None,
    )
    if spec is None or spec.loader is None:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.ENTRY_POINT_FAILED,
                f"cannot build import spec for {str(entry_path)!r}",
                extension_id=extension_id,
            )
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def purge_modules(namespace: str, name: str, *fingerprints: Optional[str]) -> None:
    """按指纹化前缀清理模块（Round-1 审计 M4：只清本记录实际加载过的代次）。"""
    base = f"{MODULE_PREFIX}{namespace}_{name}"
    prefixes = {f"{base}_{fp[:12]}" for fp in fingerprints if fp}
    prefixes.add(f"{base}.")
    for prefix in prefixes:
        for mod_name in list(sys.modules):
            if mod_name == prefix or mod_name.startswith(prefix + "."):
                sys.modules.pop(mod_name, None)


def resolve_health_report(module: Any, diagnostics_entry: Optional[str]) -> dict[str, Any]:
    """解析 manifest.diagnostics_entry（``module:fn``）并执行健康检查。

    唯一事实源：in-process host 与 worker server 共用同一解析/执行/归一
    规则。任何失败都归一为 ``degraded`` 报告——健康检查失败 ≠ 宿主失败。
    """
    if not diagnostics_entry:
        return {"status": "healthy", "messages": []}
    module_name, _, fn_name = diagnostics_entry.partition(":")
    if not fn_name:
        fn_name = module_name
        owner = module
    else:
        owner = getattr(module, module_name, None)
    health_fn = getattr(owner, fn_name, None) if owner is not None else None
    if not callable(health_fn):
        return {
            "status": "degraded",
            "messages": [f"diagnostics entry {diagnostics_entry!r} not resolvable"],
        }
    try:
        result = health_fn()
    except Exception as exc:  # noqa: BLE001 - 健康检查失败 ≠ 宿主失败
        return {
            "status": "degraded",
            "messages": [f"health check raised {type(exc).__name__}: {exc}"],
        }
    if isinstance(result, dict) and "status" in result:
        return result
    status = getattr(result, "status", None)
    if status:
        return {
            "status": str(status),
            "messages": list(getattr(result, "messages", []) or []),
        }
    return {"status": "healthy", "messages": []}
