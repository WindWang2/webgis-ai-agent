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
        self._broker_seq = 0
        self._broker_response: Optional[dict[str, Any]] = None
        # V3 流式：共享信用账本 / 取消集（C-7：任何阻塞读点都可消费
        # stream_credit / stream_cancel，迟到/多余 credit 幂等入账）。
        self._stream_credits: dict[str, int] = {}
        self._stream_cancelled: set[str] = set()
        self._stream_window: int = 16
        # 资源强制报告（main() 入口施加；握手应答回传宿主）。
        self.resource_report: dict[str, Any] = {"applied": [], "warnings": []}

    # ── V3：流状态共享账本 ────────────────────────────────────────────
    def _account_stream_frame(self, frame: dict[str, Any]) -> bool:
        """消费一帧流控制帧；返回 True 表示该帧已被处理（读点继续）。"""
        ftype = frame.get("type")
        if ftype == "stream_credit":
            stream_id = str(frame.get("id"))
            n = frame.get("n")
            if isinstance(n, int) and n > 0:
                self._stream_credits[stream_id] = (
                    self._stream_credits.get(stream_id, 0) + n
                )
            return True
        if ftype == "stream_cancel":
            self._stream_cancelled.add(str(frame.get("id")))
            return True
        return False

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
        # V3：宿主在握手中授予的初始信用窗口（结构性上界的宿主侧一半）。
        window = frame.get("stream_window")
        self._stream_window = window if isinstance(window, int) and window > 0 else 16
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
                # V3（ADR-0119）：worker 化投影的可序列化声明。
                "algorithms": ctx.declared_algorithms(),
                "data_providers": ctx.declared_data_providers(),
                "cartography_items": ctx.declared_cartography(),
                "workflow_packs": ctx.declared_workflow_packs(),
                "resource_limits": dict(self.resource_report),
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

    # ── broker 传输（WorkerContext 回调；C-7 帧白名单）────────────────
    def _broker_transport(self, op: str, payload: dict[str, Any]) -> Any:
        self._broker_seq += 1
        request_id = f"b{self._broker_seq}-{op}"
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
                # C-7：broker 等待循环的合法帧 += {stream_credit, stream_cancel}
                # ——宿主在流式工具经 broker 取数时照常补信用/取消，迟到帧
                # 幂等入账，绝不按协议违规崩溃。
                if self._account_stream_frame(frame):
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
        # V3：空闲期到达的流控帧幂等入账（信用领先/取消先行都合法）。
        if self._account_stream_frame(frame):
            return True
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
        want_stream = bool(frame.get("stream"))
        try:
            if not isinstance(call_id, str) or not isinstance(tool_name, str):
                raise ProtocolError("call frame requires string 'id' and 'tool'")
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise ProtocolError("call frame 'args' must be an object")
            # V3：worker 数据 provider 的 RPC 面（`provider:<st>:<method>`）。
            if tool_name.startswith("provider:"):
                value = self._handle_provider_call(tool_name, args)
                if want_stream and value is not None and hasattr(value, "__next__"):
                    # provider mixin 流（如 stream_features）→ 流帧协议。
                    self._handle_stream_call(call_id, value)
                    return
                write_frame(
                    self._outfile,
                    {"type": "result", "id": call_id, "ok": True, "value": value},
                )
                return
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
            if want_stream and value is not None and hasattr(value, "__next__"):
                # V3：事件迭代器 → 流帧协议（信用流控 + 协作取消）。
                self._handle_stream_call(call_id, value)
                return
            if value is not None and hasattr(value, "__next__"):
                # 非流式调用 + 迭代器结果：worker 侧聚合（与 V2 model
                # provider 语义一致——单帧 result 承载聚合事件）。
                from ..sdk.model import aggregate_stream_events

                value = aggregate_stream_events(value)
            self._check_output_size(value)
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

    # ── V3：流式调用（背压 = 阻塞等信用直至 credit/EOF；M-6）──────────
    def _handle_stream_call(self, call_id: str, events: Any) -> None:
        execution = self._ctx.manifest.execution if self._ctx is not None else None
        max_events = (
            execution.max_stream_events
            if execution is not None and getattr(execution, "max_stream_events", None)
            else 10000
        )
        per_frame_cap = (
            execution.max_output_bytes if execution is not None else None
        )
        self._stream_credits.setdefault(call_id, self._stream_window)
        seq = 0
        cancelled = False
        write_frame(self._outfile, {"type": "stream_start", "id": call_id})
        try:
            for event in events:
                if call_id in self._stream_cancelled:
                    self._stream_cancelled.discard(call_id)
                    cancelled = True
                    break
                while self._stream_credits.get(call_id, 0) <= 0:
                    # 背压阻塞点：合法帧 = stream_credit / stream_cancel；
                    # EOF（read_frame 抛 ProtocolError）= host 死亡，自然失败。
                    frame = read_frame(self._infile)
                    if not self._account_stream_frame(frame):
                        raise ProtocolError(
                            f"unexpected frame while awaiting stream credit: "
                            f"{frame.get('type')!r}"
                        )
                if call_id in self._stream_cancelled:
                    self._stream_cancelled.discard(call_id)
                    cancelled = True
                    break
                payload = event if isinstance(event, dict) else {"type": "raw", "value": event}
                frame_obj = {
                    "type": "stream_frame",
                    "id": call_id,
                    "seq": seq,
                    "more": True,
                    "payload": payload,
                }
                # M-5：worker 侧每帧尺寸强制（与单帧 result 同一上限语义）。
                if per_frame_cap is not None:
                    from .protocol import encode_frame

                    if len(encode_frame(frame_obj)) > per_frame_cap:
                        raise ExtensionPlatformError(
                            ExtensionDiagnostic.error(
                                DiagnosticCode.OUTPUT_LIMIT_EXCEEDED,
                                f"stream frame {seq} exceeds "
                                f"execution.max_output_bytes={per_frame_cap}",
                                extension_id=self._ctx.extension_id,
                            )
                        )
                write_frame(self._outfile, frame_obj)
                self._stream_credits[call_id] = self._stream_credits.get(call_id, 0) - 1
                seq += 1
                if seq >= max_events:
                    raise ExtensionPlatformError(
                        ExtensionDiagnostic.error(
                            DiagnosticCode.STREAM_LIMIT_EXCEEDED,
                            f"stream exceeded max_stream_events={max_events}",
                            extension_id=self._ctx.extension_id,
                        )
                    )
        finally:
            # 生成器提前终止（取消/上限/异常）都要收尾，避免悬挂引用。
            close = getattr(events, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
            self._stream_credits.pop(call_id, None)
            self._stream_cancelled.discard(call_id)
        try:
            write_frame(
                self._outfile,
                {"type": "stream_end", "id": call_id, "cancelled": cancelled},
            )
        except ProtocolError:
            raise SystemExit(EXIT_INTERNAL_ERROR)

    # ── V3：worker 数据 provider 的 RPC 分派（C-6：实例单例随 worker）──
    _PROVIDER_METHODS = frozenset(
        {
            "probe",
            "capabilities",
            "list_datasets",
            "describe",
            "preview",
            "query",
            "health",
            # V3 mixin 面（streaming_vector / tiles / raster_window）。
            "stream_features",
            "get_tile",
            "get_raster_window",
        }
    )

    def _handle_provider_call(self, tool_name: str, args: dict[str, Any]) -> Any:
        parts = tool_name.split(":")
        if len(parts) != 3:
            raise ProtocolError(f"malformed provider call {tool_name!r}")
        _, source_type, method = parts
        if method not in self._PROVIDER_METHODS:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                    f"provider method {method!r} is not part of the adapter contract",
                    extension_id=self._ctx.extension_id if self._ctx else None,
                )
            )
        instance = self._ctx.get_provider_instance(source_type)
        if not hasattr(instance, method):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                    f"provider {source_type!r} does not implement {method!r}",
                    extension_id=self._ctx.extension_id,
                )
            )
        result = getattr(instance, method)(**dict(args or {}))
        # TilePayload（bytes）→ data_hex 传输形态（bytes 不可 JSON 帧）。
        if method == "get_tile" and result is not None and hasattr(result, "data"):
            return {
                "data_hex": result.data.hex(),
                "content_type": getattr(result, "content_type", "image/png"),
                "metadata": _jsonable(getattr(result, "metadata", None)),
            }
        return _jsonable(result)

    def _check_output_size(self, value: Any) -> None:
        """输出上限：序列化字节数超过 manifest execution.max_output_bytes →
        typed 错误（协议帧本身另有硬上限兜底）。"""
        from .protocol import encode_frame

        execution = self._ctx.manifest.execution if self._ctx is not None else None
        cap = execution.max_output_bytes if execution is not None else None
        try:
            size = len(encode_frame({"type": "result", "value": value}))
        except ProtocolError as exc:
            # 不可序列化结果 → typed 错误应答（worker 不崩溃，工具面存活）。
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.WORKER_RESULT_INVALID,
                    f"tool result is not JSON-serializable: {exc}",
                    extension_id=self._ctx.extension_id if self._ctx is not None else None,
                )
            ) from exc
        if cap is not None and size > cap:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.OUTPUT_LIMIT_EXCEEDED,
                    f"tool result serialized to {size} bytes, exceeding "
                    f"execution.max_output_bytes={cap}",
                    extension_id=self._ctx.extension_id,
                )
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


def _jsonable(value: Any) -> Any:
    """provider 方法结果 → JSON-able（pydantic 模型 model_dump；其余原样）。"""
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - 序列化失败归一为 typed
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.WORKER_RESULT_INVALID,
                    f"provider result {type(value).__name__} is not serializable",
                )
            )
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.extensions_platform.worker.server",
        description="GIS extension isolated worker (ADR-0105)",
    )
    parser.add_argument("--pack-dir", required=True, help="extension pack directory")
    parser.add_argument(
        "--max-memory-mb",
        type=int,
        default=None,
        help="RLIMIT_AS in MiB (applied in-process before loading the pack)",
    )
    parser.add_argument(
        "--max-cpu-seconds",
        type=int,
        default=None,
        help="RLIMIT_CPU seconds (applied in-process before loading the pack)",
    )
    args = parser.parse_args(argv)
    pack_dir = Path(args.pack_dir)
    if not pack_dir.is_dir():
        print(f"pack dir not found: {pack_dir}", file=sys.stderr)
        return EXIT_USAGE
    from .spawn import apply_resource_limits

    applied, resource_warnings = apply_resource_limits(args.max_memory_mb, args.max_cpu_seconds)
    server = WorkerServer(pack_dir, sys.stdin.buffer, sys.stdout.buffer)
    server.resource_report = {"applied": applied, "warnings": resource_warnings}
    try:
        if not server.handshake():
            return EXIT_ACTIVATION_FAILED
    except ProtocolError as exc:
        # 握手期协议失败：尽力回帧后退出。
        try:
            write_frame(
                sys.stdout.buffer,
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
