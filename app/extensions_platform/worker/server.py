"""Worker server：隔离扩展子进程的入口与事件循环（ADR-0105 V2 / Wave 2）。

启动方式（由 host 的 WorkerProcess spawn；开发也可手工运行）::

    python -m app.extensions_platform.worker.server --pack-dir <dir>

职责：
- 读握手帧（协议版本 / extension_id / 期望指纹），fail closed 校验：
  manifest 解析、id/namespace/name 一致、worker 模式、内容指纹一致、
  api 兼容——任何失败回 ``handshake_failed`` 并 exit 3；
- 加载入口模块（与 in-process host 同一 loader 规则）并执行
  ``activate(WorkerContext)``；工具注册被收集（不写任何 registry）；
- 事件循环处理 ``call`` / ``health`` / ``shutdown``；等待 broker 应答期间
  宿主主动帧进入 pending 队列（EOF 即信任失败，立即退出）；
- 任何未捕获异常：先尽力回 error 帧再以非零码退出（宿主据此判定 crash）。

安全性质：进程内没有权威状态；全部宿主能力经 broker 请求（宿主侧
default deny）；凭据不落盘、不打印、不进入结果帧。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

from ..api_version import check_extension_api_compatibility
from ..diagnostics import (
    DiagnosticCode,
    ExtensionDiagnostic,
    ExtensionPlatformError,
)
from ..discovery import MANIFEST_FILENAME, _parse_manifest_file, compute_fingerprint
from ..loader import load_entry_module, resolve_health_report
from ..permissions import grants_for
from ..trust import TrustLevel
from .context import WorkerContext
from .protocol import (
    EXIT_ACTIVATION_FAILED,
    EXIT_INTERNAL_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    WORKER_PROTOCOL_VERSION,
    ProtocolError,
    error_payload,
    read_frame,
    write_frame,
)

_PENDING_FRAME_TYPES = {"call", "health", "shutdown"}


class WorkerServer:
    """单扩展 worker 的服务循环（一个进程只服务一个扩展）。"""

    def __init__(self, pack_dir: Path, infile: Any, outfile: Any) -> None:
        self._pack_dir = pack_dir
        self._infile = infile
        self._outfile = outfile
        self._ctx: Optional[WorkerContext] = None
        self._module: Any = None
        self._pending: list[dict[str, Any]] = []
        self._broker_wait_id: Optional[str] = None
        self._broker_response: Optional[dict[str, Any]] = None

    # ── 握手 ─────────────────────────────────────────────────────────
    def handshake(self) -> bool:
        frame = read_frame(self._infile)
        if frame.get("type") != "handshake":
            self._fail_handshake(
                DiagnosticCode.WORKER_PROTOCOL_MISMATCH,
                f"expected handshake frame, got {frame.get('type')!r}",
            )
            return False
        if frame.get("protocol") != WORKER_PROTOCOL_VERSION:
            self._fail_handshake(
                DiagnosticCode.WORKER_PROTOCOL_MISMATCH,
                f"protocol {frame.get('protocol')!r} != host-supported "
                f"{WORKER_PROTOCOL_VERSION!r}",
            )
            return False
        manifest, diags, _ = _parse_manifest_file(self._pack_dir / MANIFEST_FILENAME)
        if manifest is None:
            detail = "; ".join(d.message for d in diags) or "manifest unreadable"
            self._fail_handshake(DiagnosticCode.MANIFEST_PARSE_FAILED, detail)
            return False
        if manifest.id != frame.get("extension_id"):
            self._fail_handshake(
                DiagnosticCode.MANIFEST_INVALID,
                f"extension id mismatch: pack {manifest.id!r} != handshake "
                f"{frame.get('extension_id')!r}",
            )
            return False
        if manifest.namespace != frame.get("expected_namespace") or manifest.name != frame.get(
            "expected_name"
        ):
            self._fail_handshake(
                DiagnosticCode.MANIFEST_INVALID,
                "namespace/name mismatch between pack manifest and handshake",
            )
            return False
        if not manifest.is_worker_mode:
            self._fail_handshake(
                DiagnosticCode.WORKER_MODE_INVALID,
                "pack manifest does not declare execution.mode=worker; refusing "
                "to serve an in_process pack in an isolated worker",
            )
            return False
        compat = check_extension_api_compatibility(manifest.api_version)
        if not compat.compatible:
            self._fail_handshake(DiagnosticCode.API_VERSION_INCOMPATIBLE, compat.reason or "")
            return False
        fingerprint, fp_diag = compute_fingerprint(self._pack_dir)
        if fp_diag is not None or fingerprint is None:
            self._fail_handshake(
                DiagnosticCode.FINGERPRINT_CHANGED,
                fp_diag.message if fp_diag is not None else "fingerprint unavailable",
            )
            return False
        if fingerprint != frame.get("fingerprint"):
            self._fail_handshake(
                DiagnosticCode.PACKAGE_TAMPERED,
                "pack content fingerprint differs from the host-discovered "
                "fingerprint (tampered in flight?)",
            )
            return False
        try:
            module = load_entry_module(
                self._pack_dir,
                manifest.namespace,
                manifest.name,
                manifest.entry_point,
                fingerprint,
                extension_id=manifest.id,
            )
            activate_fn = getattr(module, "activate", None)
            if not callable(activate_fn):
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.ENTRY_POINT_MISSING,
                        "entry module does not define activate(ctx)",
                        extension_id=manifest.id,
                    )
                )
            ctx = WorkerContext(
                manifest=manifest,
                trust=TrustLevel.LOCAL_UNTRUSTED,  # worker 内信任无权威意义
                grants=grants_for(
                    manifest.id, {manifest.id: frozenset(frame.get("grants") or [])}
                ),
                settings=dict(frame.get("settings") or {}),
                module_dir=self._pack_dir,
                entry_module_name=module.__name__,
                broker_transport=self._broker_transport,
            )
            activate_fn(ctx)
        except ExtensionPlatformError as exc:
            self._fail_handshake(exc.diagnostic.code, exc.diagnostic.message)
            return False
        except Exception as exc:  # noqa: BLE001 - 扩展任意异常不得静默
            self._fail_handshake(
                DiagnosticCode.ENTRY_POINT_FAILED,
                f"activate() raised {type(exc).__name__}: {exc}",
            )
            return False
        self._ctx = ctx
        self._module = module
        write_frame(
            self._outfile,
            {
                "type": "handshake_ok",
                "protocol": WORKER_PROTOCOL_VERSION,
                "extension_id": manifest.id,
                "tools": [
                    {
                        "name": manifest.namespaced_tool_name(tool.name),
                        "description": tool.description,
                        "kwargs": tool.kwargs,
                    }
                    for tool in ctx.declared_tools()
                ],
            },
        )
        return True

    def _fail_handshake(self, code: DiagnosticCode, message: str) -> None:
        write_frame(
            self._outfile,
            {
                "type": "handshake_failed",
                "error": error_payload(code.value, message),
            },
        )

    # ── broker 传输（WorkerContext 回调）──────────────────────────────
    def _broker_transport(self, op: str, payload: dict[str, Any]) -> Any:
        request_id = f"b{id(payload):x}-{op}"
        write_frame(self._outfile, {"type": "broker_request", "id": request_id, "op": op, "payload": payload})
        self._broker_wait_id = request_id
        try:
            while True:
                if self._broker_response is not None:
                    response = self._broker_response
                    self._broker_response = None
                    self._broker_wait_id = None
                    if response.get("ok"):
                        return response.get("value")
                    err = response.get("error") or {}
                    raise ExtensionPlatformError(
                        ExtensionDiagnostic.error(
                            DiagnosticCode.BROKER_DENIED,
                            f"broker op {op!r} denied/failed: "
                            f"{err.get('message', 'unknown')}",
                            extension_id=self._ctx.extension_id if self._ctx else None,
                        )
                    )
                frame = read_frame(self._infile)  # EOF → ProtocolError → crash
                if frame.get("type") == "broker_response" and frame.get("id") == request_id:
                    self._broker_response = frame
                    continue
                if frame.get("type") in _PENDING_FRAME_TYPES:
                    self._pending.append(frame)
                    continue
                # 握手外协议违规：信任失败，立即终止。
                raise ProtocolError(f"unexpected frame while awaiting broker response: {frame.get('type')!r}")
        finally:
            self._broker_wait_id = None

    # ── 事件循环 ──────────────────────────────────────────────────────
    def serve(self) -> int:
        while True:
            try:
                again = self.serve_once()
            except ProtocolError:
                return EXIT_INTERNAL_ERROR
            except BrokenPipeError:
                return EXIT_INTERNAL_ERROR
            except SystemExit as exc:
                code = exc.code
                return code if isinstance(code, int) else EXIT_INTERNAL_ERROR
            if not again:
                return EXIT_INTERNAL_ERROR

    def serve_once(self) -> bool:
        """处理恰好一帧（pending 优先）。EOF → False；其余帧 → True。"""
        if self._pending:
            frame = self._pending.pop(0)
        else:
            try:
                frame = read_frame(self._infile)
            except ProtocolError:
                return False
        ftype = frame.get("type")
        if ftype == "call":
            self._handle_call(frame)
        elif ftype == "health":
            self._handle_health(frame)
        elif ftype == "shutdown":
            write_frame(self._outfile, {"type": "bye"})
            raise SystemExit(EXIT_OK)
        else:
            write_frame(
                self._outfile,
                {
                    "type": "error",
                    "error": error_payload(
                        DiagnosticCode.WORKER_PROTOCOL_MISMATCH,
                        f"unknown frame type {ftype!r}",
                    ),
                },
            )
        return True

    def _handle_call(self, frame: dict[str, Any]) -> None:
        assert self._ctx is not None
        call_id = frame.get("id")
        tool_name = frame.get("tool")
        args = frame.get("args")
        try:
            if not isinstance(call_id, str) or not isinstance(tool_name, str):
                raise ProtocolError("call frame requires string 'id' and 'tool'")
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise ProtocolError("call frame 'args' must be an object")
            func = self._ctx.resolve_tool(tool_name)
            if func is None:
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                        f"tool {tool_name!r} is not registered in this worker",
                        extension_id=self._ctx.extension_id,
                    )
                )
            value = func(**args)
            write_frame(self._outfile, {"type": "result", "id": call_id, "ok": True, "value": value})
        except ExtensionPlatformError as exc:
            self._safe_result_error(
                call_id, exc.diagnostic.code.value, exc.diagnostic.message
            )
        except ProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 - 工具异常归一为 typed result
            self._safe_result_error(
                call_id,
                DiagnosticCode.ENTRY_POINT_FAILED.value,
                f"tool raised {type(exc).__name__}: {exc}",
            )

    def _safe_result_error(self, call_id: Any, code: str, message: str) -> None:
        try:
            write_frame(
                self._outfile,
                {
                    "type": "result",
                    "id": call_id,
                    "ok": False,
                    "error": error_payload(code, message),
                },
            )
        except ProtocolError:
            raise SystemExit(EXIT_INTERNAL_ERROR)

    def _handle_health(self, frame: dict[str, Any]) -> None:
        assert self._ctx is not None
        report = resolve_health_report(self._module, self._ctx.manifest.diagnostics_entry)
        try:
            write_frame(
                self._outfile,
                {
                    "type": "result",
                    "id": frame.get("id"),
                    "ok": True,
                    "value": report,
                },
            )
        except ProtocolError:
            raise SystemExit(EXIT_INTERNAL_ERROR)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.extensions_platform.worker.server",
        description="GIS extension isolated worker (ADR-0105)",
    )
    parser.add_argument("--pack-dir", required=True, help="extension pack directory")
    args = parser.parse_args(argv)
    pack_dir = Path(args.pack_dir)
    if not pack_dir.is_dir():
        print(f"pack dir not found: {pack_dir}", file=sys.stderr)
        return EXIT_USAGE
    server = WorkerServer(pack_dir, sys.stdin, sys.stdout)
    try:
        if not server.handshake():
            return EXIT_ACTIVATION_FAILED
    except ProtocolError as exc:
        # 握手期协议失败：尽力回帧后退出。
        try:
            write_frame(
                sys.stdout,
                {
                    "type": "handshake_failed",
                    "error": error_payload(
                        DiagnosticCode.WORKER_PROTOCOL_MISMATCH.value, str(exc)
                    ),
                },
            )
        except Exception:  # noqa: BLE001
            pass
        return EXIT_ACTIVATION_FAILED
    except Exception:  # noqa: BLE001 - 握手外的意外失败：留痕后崩溃退出
        traceback.print_exc(file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    try:
        return server.serve()
    except ProtocolError:
        return EXIT_INTERNAL_ERROR
    except BrokenPipeError:
        return EXIT_INTERNAL_ERROR
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
