"""Continuously drain worker stderr while retaining only a bounded tail."""
from __future__ import annotations

import os
import threading
from typing import BinaryIO


class StderrCapture:
    def __init__(self, stream: BinaryIO, capacity: int = 65536) -> None:
        self._stream = stream
        self._capacity = max(1, capacity)
        self._tail = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, name="extension-stderr", daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        try:
            fd = self._stream.fileno()
            os.set_blocking(fd, False)
        except (OSError, ValueError):
            return
        selector = None
        try:
            if os.name != "nt":
                # fd ≥ 1024 会让 select.select 抛 ValueError（fd_set 上限，
                # 繁忙 API 进程常见）—— drain 线程静默退出 → stderr 管
                # 道(64KiB)灌满 → worker 阻塞。epoll/poll 无此上限。
                import selectors

                selector = selectors.DefaultSelector()
                selector.register(fd, selectors.EVENT_READ)
            while not self._stop.is_set():
                if selector is not None:
                    if not selector.select(0.1):
                        continue
                try:
                    chunk = os.read(fd, 8192)
                except BlockingIOError:
                    if selector is None:
                        self._stop.wait(0.01)
                    continue
                if not chunk:
                    return
                with self._lock:
                    self._tail.extend(chunk)
                    if len(self._tail) > self._capacity:
                        del self._tail[:-self._capacity]
        except (OSError, ValueError):
            # A failed/closed pipe stops diagnostics, never RPC dispatch.
            return
        finally:
            if selector is not None:
                selector.close()

    def snapshot(self, limit: int = 400) -> str:
        with self._lock:
            return bytes(self._tail[-max(0, limit):] if limit > 0 else b"").decode("utf-8", "replace").strip()

    def finish(self) -> None:
        # After process exit, first allow EOF to drain the final diagnostic.
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=0.2)
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=0.3)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()
