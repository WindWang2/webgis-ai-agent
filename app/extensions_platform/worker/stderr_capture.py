"""Continuously drain worker stderr while retaining only a bounded tail."""
from __future__ import annotations

import os
import select
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
            while not self._stop.is_set():
                if os.name != "nt":
                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if not ready:
                        continue
                try:
                    chunk = os.read(fd, 8192)
                except BlockingIOError:
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
