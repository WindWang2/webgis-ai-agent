"""Worker RPC 协议（ADR-0105 V2 / Wave 2）。

传输：stdin/stdout 上的 **行分帧 JSON**（UTF-8，``\\n`` 终止）。不监听
任何 socket——worker 的唯一 I/O 通道就是与宿主的管道，宿主能力全部经
broker 往返，没有环境内权威。

消息信封（dict，必有 ``type``）::

    host → worker
      handshake        首帧：协议版本 / extension_id / 期望指纹 / 授权 / 设置
      call             {"id", "tool", "args"}（tool 为命名空间化投影名）
      health           {"id"}
      shutdown         优雅退出请求（worker 回 bye 后 exit 0）

    worker → host
      handshake_ok     激活成功 + 工具声明（含可序列化 register kwargs）
      handshake_failed 激活失败（typed error payload；随后 exit 3）
      result           {"id", "ok", value | error}
      broker_request   {"id", "op", "payload"} —— worker 请求宿主代为执行
      bye               shutdown 确认

    host → worker（对 broker_request 的应答）
      broker_response  {"id", "ok", value | error}

错误 payload 统一为 ``{"code": <DiagnosticCode value>, "message": str}``；
帧大小双侧强制（防输出炸弹 / 防畸形输入拖垮对端）。协议版本不匹配在
握手期 typed 拒绝——不存在「尽力兼容」路径。
"""

from __future__ import annotations

import json
from typing import Any

WORKER_PROTOCOL_VERSION = "1.0"

# 帧上限：必须 ≥ manifest.execution.max_output_bytes 的合法上界
# （64 MiB）+ JSON 序列化余量。
FRAME_MAX_BYTES = 68 * 1024 * 1024

EXIT_OK = 0
EXIT_INTERNAL_ERROR = 1
EXIT_USAGE = 2
EXIT_ACTIVATION_FAILED = 3


class ProtocolError(Exception):
    """协议层失败（帧超限 / 非 JSON / 形状非法）。不携带 typed diagnostic
    ——协议失败即信任失败，调用方直接断开并产出 WORKER_CRASHED。"""


def error_payload(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def make_handshake(
    extension_id: str,
    fingerprint: str,
    grants: list[str],
    settings: dict[str, Any],
    expected_namespace: str,
    expected_name: str,
) -> dict[str, Any]:
    return {
        "type": "handshake",
        "protocol": WORKER_PROTOCOL_VERSION,
        "extension_id": extension_id,
        "expected_namespace": expected_namespace,
        "expected_name": expected_name,
        "fingerprint": fingerprint,
        "grants": list(grants),
        "settings": dict(settings),
    }


def encode_frame(obj: dict[str, Any]) -> bytes:
    try:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"frame not JSON-serializable: {exc}") from exc
    if len(data) + 1 > FRAME_MAX_BYTES:
        raise ProtocolError(f"frame exceeds {FRAME_MAX_BYTES} bytes")
    return data + b"\n"


def decode_frame(line: bytes) -> dict[str, Any]:
    if len(line) > FRAME_MAX_BYTES:
        raise ProtocolError(f"frame exceeds {FRAME_MAX_BYTES} bytes")
    try:
        obj = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"frame is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        raise ProtocolError("frame must be a JSON object with a string 'type'")
    return obj


def write_frame(fileobj: Any, obj: dict[str, Any]) -> None:
    fileobj.write(encode_frame(obj))
    fileobj.flush()


def read_frame(fileobj: Any) -> dict[str, Any]:
    line = fileobj.readline()
    if not line:
        raise ProtocolError("stream closed (EOF)")
    return decode_frame(line)
