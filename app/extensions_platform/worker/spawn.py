"""Worker 资源强制（ADR-0105 V2 / Wave 5）。

策略：limits 由 **worker 子进程自身**在入口处先行施加（信任边界内，
先 limit 后加载扩展），而非 preexec_fn——宿主多线程下 preexec_fn 不安全
（fork+线程死锁风险），子进程自施与 our-code-first 语义等价：

- ``RLIMIT_AS``  = max_memory_mb（虚拟内存上界；malloc 失败 → MemoryError
  → 崩溃 → typed WORKER_CRASHED）；
- ``RLIMIT_CPU`` = max_cpu_seconds（软=硬；超时 SIGXCPU/SIGKILL）；
- 输出上限不依赖 OS：server 在序列化应答时按 manifest
  ``execution.max_output_bytes`` 强制（OUTPUT_LIMIT_EXCEEDED）；
- 平台不支持（非 POSIX / setrlimit 失败）→ **typed 降级告警**（不虚假
  承诺），墙钟超时 + killpg 兜底。
"""

from __future__ import annotations

from typing import Optional

# 未声明时的缺省（与 manifest.ExecutionDeclaration 缺省一致）。
DEFAULT_MAX_MEMORY_MB = 512
DEFAULT_MAX_CPU_SECONDS = 60


def apply_resource_limits(
    max_memory_mb: Optional[int],
    max_cpu_seconds: Optional[int],
) -> tuple[list[str], list[str]]:
    """施加 POSIX rlimits。返回 (applied, warnings)——警告是 typed 降级
    的证据（宿主将其转为 RESOURCE_LIMIT_UNAVAILABLE warning 诊断）。"""
    applied: list[str] = []
    warnings: list[str] = []
    try:
        import resource
    except ImportError:
        return [], ["resource module unavailable (non-POSIX); wall-clock kill only"]
    if max_memory_mb:
        limit = int(max_memory_mb) * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            applied.append(f"RLIMIT_AS={limit}")
        except (OSError, ValueError) as exc:
            warnings.append(f"RLIMIT_AS unavailable: {exc}")
    if max_cpu_seconds:
        limit = int(max_cpu_seconds)
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (limit, limit))
            applied.append(f"RLIMIT_CPU={limit}")
        except (OSError, ValueError) as exc:
            warnings.append(f"RLIMIT_CPU unavailable: {exc}")
    return applied, warnings
