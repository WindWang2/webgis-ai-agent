"""Keep the combined API/frontend container alive only while both services live."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Sequence
from types import FrameType


def supervise(commands: Sequence[Sequence[str]], *, grace_seconds: float = 10.0) -> int:
    """Forward shutdown to service groups and fail on any unsolicited child exit."""
    children: list[subprocess.Popen[bytes]] = []
    received_signal = 0

    def request_shutdown(signum: int, frame: FrameType | None) -> None:
        nonlocal received_signal
        received_signal = signum

    previous = {sig: signal.signal(sig, request_shutdown) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for command in commands:
            children.append(subprocess.Popen(list(command), start_new_session=True))
        while not received_signal:
            for child in children:
                status = child.poll()
                if status is not None:
                    # A clean service exit is still an unhealthy combined pod.
                    return status if status > 0 else (128 - status if status < 0 else 1)
            time.sleep(0.05)
        return 128 + received_signal
    finally:
        # Signal groups even if their leaders exited: their descendants must
        # not keep serving after the sibling service has failed.
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        deadline = time.monotonic() + grace_seconds
        for child in children:
            try:
                child.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                continue
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
        for child in children:
            child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    return supervise((
        ("uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "16384"),
        ("node", "frontend/.next/standalone/server.js", "-p", "3000"),
    ))


if __name__ == "__main__":
    raise SystemExit(main())
