"""原子产物提交（规范 §23 / §24）。

长任务产生 raster / vector / image 时的问题：直接写最终路径意味着「取消或崩溃 =
留下半个 GeoTIFF」，而且这个半成品和成功产物长得一模一样，后续步骤会把它当成有效
输入。ADR-0052 统一改成：

    temporary output  →  compute success  →  atomic finalize (os.replace)  →  ready

失败/取消时删除临时文件；已经 finalize 的成功产物**绝不**删除。os.replace 在同一
文件系统上是原子的，所以不存在「读到写了一半的最终文件」的窗口。
"""
from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import Callable, Iterator, Optional

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def atomic_output(
    final_path: str,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
    suffix: str = "",
) -> Iterator[str]:
    """产出 ``final_path`` 的原子写入上下文，yield 临时路径。

    Args:
        final_path: 最终产物路径。只有在 with 块正常结束时才会出现。
        should_abort: 可选回调；块结束时若返回 True 则丢弃产物（用于取消已到达但
            计算刚好完成的竞态 —— 取消优先，绝不 finalize 一个已取消任务的产物）。
        suffix: 临时文件额外后缀（同目录下写多个产物时区分用）。

    Raises:
        FileNotFoundError: 块结束时临时文件不存在（生产者什么都没写）。
        RuntimeError: should_abort 判定应丢弃。
    """
    parent = os.path.dirname(final_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temp_path = f"{final_path}.part-{uuid.uuid4().hex[:8]}{suffix}"

    try:
        yield temp_path
    except BaseException:
        _discard(temp_path)
        raise

    if not os.path.exists(temp_path):
        raise FileNotFoundError(f"artifact producer wrote nothing: {temp_path}")

    if should_abort is not None and should_abort():
        _discard(temp_path)
        raise RuntimeError(f"artifact discarded before finalize: {final_path}")

    os.replace(temp_path, final_path)


def _discard(path: str) -> None:
    """删除临时文件。缺失/权限问题不向上抛 —— 清理不应掩盖真正的失败原因。"""
    with contextlib.suppress(FileNotFoundError, OSError):
        if os.path.isfile(path):
            os.unlink(path)
            logger.debug("[jobs] discarded partial artifact %s", path)


def discard_partial(path: str) -> None:
    """公开的临时产物清理入口（供 finally 块使用）。"""
    _discard(path)
