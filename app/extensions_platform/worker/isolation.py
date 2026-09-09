"""worker 隔离后端（ADR-0119 / Wave 7）。

两个后端（诚实命名，绝不宣称 kernel sandbox）：

- ``process``（缺省）：V2 语义——独立解释器 + 最小 env + rlimit + 进程组。
  worker 代码仍以服务用户身份运行，socket/open 等 syscall 不受限（只有
  SDK/broker 通道被门控）。
- ``bubblewrap``：namespace 级 OS 隔离——``bwrap --unshare-all``（含
  **net**：worker 内 socket 直连在 OS 层不可达，broker 成为唯一出网通道，
  从「纪律」变「强制」）+ 只读 bind 的最小文件系统视图 + tmpfs 可写面。

**B-1 红线：repo 根不可见于沙箱**。V2 的 secrets 卫生（worker 不读
``.env``、cwd 移出 repo）依赖「worker 代码不主动读」；bwrap 把它升级为
「读不到」：只 bind ``<repo>/app`` 包、venv、stdlib 前缀与 pack 目录，
repo 根（.env、数据、测试夹具）在沙箱视野外。

失败语义（M-9）：``probe`` 只服务 status/CLI 与启动前的显式降级决策；
**per-spawn bwrap 失败 = typed 激活失败**（ISOLATION_UNAVAILABLE），
绝不静默回退 process——静默降级 = 隔离承诺失效。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError

BACKEND_PROCESS = "process"
BACKEND_BUBBLEWRAP = "bubblewrap"
ISOLATION_BACKENDS = frozenset({BACKEND_PROCESS, BACKEND_BUBBLEWRAP})

# 沙箱内的固定挂载点（与宿主路径解耦；PYTHONPATH 指向挂载根）。
SANDBOX_APP_PARENT = "/opt/webgis"
SANDBOX_PACK_DIR = "/opt/ext/pack"

# bwrap 命令可用性探测结果缓存（进程级；探测是昂贵 smoke run）。
_probe_cache: dict[str, Optional[str]] = {}


class IsolationUnavailable(ExtensionPlatformError):
    """隔离后端不可用/启动失败（typed；绝不静默降级）。"""


def probe_bubblewrap(force: bool = False) -> Optional[str]:
    """返回 bwrap 可执行路径；不可用 → None（缓存 smoke run 结果）。"""
    if not force and BACKEND_BUBBLEWRAP in _probe_cache:
        return _probe_cache[BACKEND_BUBBLEWRAP]
    path = shutil.which("bwrap")
    ok: Optional[str] = None
    if path:
        import subprocess

        try:
            smoke = subprocess.run(
                [
                    path,
                    "--unshare-all",
                    "--dev",
                    "/dev",
                    "--proc",
                    "/proc",
                    "--tmpfs",
                    "/tmp",
                    "/bin/sh",
                    "-c",
                    "exit 0",
                ],
                capture_output=True,
                timeout=10,
            )
            # 部分 smoke 环境（容器内无 /bin/sh）会失败但 bwrap 本体可用：
            # 退出码 125 是 bwrap 自身报错，其余非零也可能是沙箱内命令失败；
            # 只把「bwrap 不存在/不可执行」判为不可用。
            if smoke.returncode != 126 and smoke.returncode != 127:
                ok = path
        except (OSError, subprocess.TimeoutExpired):
            ok = None
    _probe_cache[BACKEND_BUBBLEWRAP] = ok
    return ok


def _bind_args() -> list[str]:
    """系统级只读 bind（动态链接器/基础库；缺了 python 无法在沙箱内启动）。

    顶层 symlink（Arch 风格 ``/lib64 -> usr/lib``、``/bin -> usr/bin``）：
    按解析源绑定到**字面路径**——沙箱内 ELF interpreter
    ``/lib64/ld-linux-x86-64.so.2`` 与 ``/bin/sh`` 因此可达（loader 缺失
    时 execvp 报 ENOENT，极易误判为二进制不存在）。
    """
    args: list[str] = ["--ro-bind", "/usr", "/usr"]
    for extra in ("/lib", "/lib64", "/bin", "/sbin"):
        p = Path(extra)
        if not p.exists():
            continue
        try:
            source = str(p.resolve())
        except OSError:
            continue
        if source == extra:
            continue
        args += ["--ro-bind", source, extra]
    return args


def _python_bind_args() -> list[str]:
    """解释器运行所需的最小只读 bind（容器经典模型）。

    不再追符号链接链（中间 symlink 在沙箱内断裂会让 venv 探测退化到
    编译期前缀）：exec **解析后的真实解释器** + ``PYTHONHOME`` 指向
    base prefix + venv site-packages 显式进 PYTHONPATH。
    """
    import sysconfig

    binds: list[str] = []
    seen: set[str] = set()

    def _bind_real(path: str) -> None:
        if not path:
            return
        try:
            real = Path(path).resolve()
        except OSError:
            return
        key = str(real)
        if key in seen or not real.exists():
            return
        seen.add(key)
        binds.extend(["--ro-bind", key, key])

    _bind_real(sys.base_prefix)
    paths = sysconfig.get_paths()
    _bind_real(paths.get("stdlib", ""))
    _bind_real(paths.get("platstdlib", ""))
    _bind_real(paths.get("purelib", ""))
    _bind_real(paths.get("platlib", ""))
    return binds


def _site_packages_paths() -> list[str]:
    import sysconfig

    paths = sysconfig.get_paths()
    out = []
    for key in ("purelib", "platlib"):
        value = paths.get(key, "")
        if value and value not in out:
            out.append(value)
    return out


def build_bwrap_command(
    *,
    python_executable: str,
    server_argv: list[str],
    repo_app_dir: Path,
    pack_dir: Path,
) -> tuple[list[str], dict[str, str]]:
    """构造 bwrap 命令与沙箱内 env 调整。

    返回 ``(argv, env_overrides)``：
    - argv[0] = bwrap；exec 的是**解析后的真实解释器**（symlink 链不进
      沙箱）；pack 目录重映射到沙箱内固定路径（``--pack-dir`` 同步替换）；
    - env_overrides：``PYTHONHOME`` = base prefix（stdlib 定位不依赖
      argv0 探测）；``PYTHONPATH`` = app 挂载根 + venv site-packages
      （repo 根不可见，B-1）。

    断言 repo 根不被 bind：仅 ``<repo>/app`` 子目录进沙箱。
    """
    import sys

    repo_app_dir = Path(repo_app_dir).resolve()
    pack_dir = Path(pack_dir).resolve()
    app_parent = repo_app_dir.parent
    if app_parent == Path(app_parent.anchor):
        raise IsolationUnavailable(
            ExtensionDiagnostic.error(
                DiagnosticCode.ISOLATION_UNAVAILABLE,
                f"repo app dir {str(repo_app_dir)!r} layout unexpected "
                "(refusing to bind filesystem root)",
            )
        )
    argv: list[str] = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--chdir",
        "/tmp",
    ]
    argv += _bind_args()
    argv += _python_bind_args()
    # B-1：只 bind app 子目录（挂到 /opt/webgis/app），绝不 bind repo 根。
    argv += ["--ro-bind", str(repo_app_dir), f"{SANDBOX_APP_PARENT}/app"]
    argv += ["--ro-bind", str(pack_dir), SANDBOX_PACK_DIR]
    real_python = Path(python_executable).resolve()
    argv += ["--", str(real_python)]
    mapped_argv = list(server_argv)
    if "--pack-dir" in mapped_argv:
        idx = mapped_argv.index("--pack-dir")
        if idx + 1 < len(mapped_argv):
            mapped_argv[idx + 1] = SANDBOX_PACK_DIR
    argv += mapped_argv
    python_home = str(Path(sys.base_prefix).resolve())
    pythonpath = os.pathsep.join(
        [SANDBOX_APP_PARENT] + [str(Path(p).resolve()) for p in _site_packages_paths()]
    )
    return argv, {"PYTHONHOME": python_home, "PYTHONPATH": pythonpath}


def effective_backend_label(backend: str, bwrap_available: bool) -> str:
    """status/CLI 呈现用（诚实命名：不宣称 kernel sandbox）。"""
    if backend == BACKEND_BUBBLEWRAP:
        if bwrap_available:
            return "bubblewrap (namespace isolation: netns+ro-bind+tmpfs; NOT a kernel sandbox)"
        return "bubblewrap (REQUESTED but unavailable)"
    return "process (V2 semantics: separate interpreter + rlimits; syscalls unfiltered)"
