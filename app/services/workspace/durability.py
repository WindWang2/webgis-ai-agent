"""Workspace payload durability (Wave 2, Workspace V4 — audit 02 §6.3/§6.4/§6.7).

单一事实源边界：快照是 **manifest（指针集合）**，字节真相只有 Wave-1
BlobStore（``durable_blob_store`` / promotion 同一内容库）一份 —— 本模块
不新建任何存储，只提供快照与那个唯一后端之间的两条受控通道：

- ``materialize_ref_payload``：会话 ref 载荷 → canonical 字节 → sha256 →
  幂等 put（JSON 走 promotion.``materialize_blob`` 同一路径；``ref:raster/*``
  走 Wave-1 promotion binary lane 的同款模式 —— 磁盘 PNG 读字节 →
  sha256 → binary blob + sidecar，因 promotion 的 lane 绑定 DB 行 /
  run 上下文，这里按其审计批准的降级形态直读磁盘文件 + ``put_blob``）；
- ``read_back_payload`` / ``verify_durable_pointer``：digest 校验读
  （verify-before-write 纪律，同 checkpoint :465-500）—— 内容缺失或被
  篡改一律 ``None`` / ``"digest_mismatch"``，绝不静默顶替，绝不把死
  指针恢复成活载荷。

写回会话侧（``write_back_session_payload``）：优先 ``overwrite``（同 ref
原位写回，Redis 后端 SET 天然 create-or-replace）；内存后端的 overwrite
是 replace-only（ref 槽位随 TTL/删除消失即 False）—— 此时以
``store()`` + 别名把载荷放回**同一 ref 的读取语义**
（``get(recorded_ref)`` 经别名命中），并如实返回 ``"alias"`` 模式供
调用方披露。本模块不改写任何 session 后端语义。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

#: 快照声明的持久层（与 artifact_registry._GC_PROTECTED_TIERS 的 "workspace"
#: 同一词表 —— GC 保护规则零改动即生效）。
WORKSPACE_TIER = "workspace"

#: verify 的 integrity 状态（有限集合，快照报告的机器契约）
INTEGRITY_VERIFIED = "verified"
INTEGRITY_DIGEST_MISMATCH = "digest_mismatch"
INTEGRITY_POINTER_MISSING = "pointer_missing"
INTEGRITY_NO_POINTER = "no_pointer"


class RestoredBinary(NamedTuple):
    """binary blob 的读回形态（调用方据此走磁盘 PNG 还原而非 store 写回）。"""

    data: bytes
    content_type: str


def _ref_prefix(ref: str) -> str:
    """``ref:geojson-ab12`` → ``geojson``（store() 前缀沿用原 ref 词头）。"""
    body = ref[4:] if ref.startswith("ref:") else ref
    return (body.split("-", 1)[0] or "data")[:32]


def _sha256_of_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 物化（save 侧；字节只进 BlobStore，绝不进快照）────────────────────


async def materialize_ref_payload(
    session_id: str,
    ref: str,
    *,
    budget_bytes: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """把一个**存活**的会话 ref 载荷物化为 BlobStore 内容（幂等 CAS）。

    ``budget_bytes``（pre-size gate，round-1 review MAJOR）：canonical 字节
    序列化/读盘**之后**、写 BlobStore **之前**量尺 —— 超过预算即返回
    ``(None, "budget")``，绝不落盘，绝不透支预算。None 预算 = 无界（兼容
    旧调用语义）。

    Returns ``(pointer, skip_reason)``：
    - ``({"content_location", "content_payload_sha256", "content_type",
      "byte_size"}, None)`` —— 物化成功；
    - ``(None, "budget")`` —— 载荷存活但超过预算（调用方如实披露）；
    - ``(None, None)`` —— 载荷不存活 / 不可序列化 / 写盘失败（调用方
      如实缺指针，verify 报告真相，绝不伪造）。
    """
    from app.services.artifact_registry import is_raster_ref, raster_png_path
    from app.services.durable_blob_store import (
        get_filesystem_blob_store,
        sha256_of_bytes,
    )

    store = get_filesystem_blob_store()
    if is_raster_ref(ref):
        path = raster_png_path(session_id, ref)
        if path is None:
            return None, None

        # round-2 review INFO：stat() 预检 —— 明显超预算的 PNG 在整读进内存
        # **之前**就跳过（此前先整读再量尺 = 预算闸门前的无谓 O(payload)
        # IO）。行为不变：读后仍以实际字节复核（stat→read 之间文件可能
        # 变化，读后量尺仍是权威闸门）。
        if budget_bytes is not None:
            try:
                pre_size = path.stat().st_size
            except OSError:
                pre_size = None
            if pre_size is not None and pre_size > int(budget_bytes):
                return None, "budget"

        def _read() -> Optional[bytes]:
            try:
                return path.read_bytes()
            except OSError:
                return None

        data = await asyncio.to_thread(_read)
        if not data:
            return None, None
        if budget_bytes is not None and len(data) > int(budget_bytes):
            return None, "budget"
        # round-1 review PERF MINOR-audit: sha256 over the full PNG bytes is
        # O(payload) CPU — same worker-thread discipline as the read itself.
        digest = await asyncio.to_thread(sha256_of_bytes, data)
        try:
            result = await asyncio.to_thread(store.put_blob, digest, data, "binary")
        except Exception as e:  # noqa: BLE001 — 持久化失败由调用方诚实披露
            logger.warning("[workspace.durability] binary put failed for %s: %s", ref, e)
            return None, None
        return {
            "content_location": result.location,
            "content_payload_sha256": digest,
            "content_type": "binary",
            "byte_size": len(data),
        }, None
    # JSON lane：canonical 序列化一次 → 同一批字节回填摘要与写盘
    # （与 promotion 的 single-serialization 契约一致）。
    from app.services.session_data import session_data_manager

    try:
        payload = await session_data_manager.get(session_id, ref)
    except Exception:  # noqa: BLE001 — store 故障按载荷不存活（诚实）
        return None, None
    if payload is None:
        return None, None
    try:
        from app.services.project_artifact_promotion import canonical_dumps

        # round-1 review PERF MINOR-2: canonical serialization is O(payload)
        # CPU (sort-keys JSON dumps over potentially multi-MB refs) — never
        # on the event loop; the digest + materialize steps below were
        # already threaded (single-serialization contract unchanged).
        blob = await asyncio.to_thread(canonical_dumps, payload)
    except Exception:  # noqa: BLE001 — 不可序列化 → 诚实缺指针
        return None, None
    # 同一 O(payload) 纪律：UTF-8 计长（编码即一次全量拷贝）也不上事件循环。
    blob_size = await asyncio.to_thread(lambda: len(blob.encode("utf-8")))
    if budget_bytes is not None and blob_size > int(budget_bytes):
        return None, "budget"
    digest = await asyncio.to_thread(_sha256_of_text, blob)
    try:
        from app.services.project_artifact_promotion import materialize_blob

        location = await asyncio.to_thread(materialize_blob, digest, blob)
    except Exception as e:  # noqa: BLE001
        logger.warning("[workspace.durability] json put failed for %s: %s", ref, e)
        return None, None
    if not location:
        return None, None
    return {
        "content_location": location,
        "content_payload_sha256": digest,
        "content_type": "json",
        "byte_size": blob_size,
    }, None


# ── 校验 / 读回（restore + verify 侧；verify-before-write）────────────


def _resolve_content_path(content_location: str):
    """location → 内容库内的绝对路径（containment 校验，同 promotion.read_content）。"""
    from app.services.project_artifact_promotion import content_store_root

    if not content_location:
        return None
    root = content_store_root()
    try:
        path = (root / content_location).resolve()
        path.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return path


def verify_durable_pointer(pointer: Dict[str, Any]) -> str:
    """指针完整性四态（audit §6.7 的 honesty 口径）：

    - ``verified``       —— 内容在场且 sha256 与记录一致（未记录摘要的
      旧指针仅校验可读性 —— 无摘要可比对，不做超出证据的断言）；
    - ``digest_mismatch``—— 内容在场但字节被篡改/损坏；
    - ``pointer_missing``—— 指针记录了但内容缺失 / 越界 / 不可读。
    """
    from app.services.durable_blob_store import (
        get_filesystem_blob_store,
        sha256_of_bytes,
    )

    digest = str(pointer.get("content_payload_sha256") or "")
    content_type = str(pointer.get("content_type") or "json")
    store = get_filesystem_blob_store()
    if content_type == "binary":
        if not digest or not store.exists(digest):
            return INTEGRITY_POINTER_MISSING
        raw = store.get_blob(digest)
        if raw is None:
            return INTEGRITY_POINTER_MISSING
        return INTEGRITY_VERIFIED if sha256_of_bytes(raw) == digest else (
            INTEGRITY_DIGEST_MISMATCH
        )
    path = _resolve_content_path(str(pointer.get("content_location") or ""))
    if path is None or not path.is_file():
        return INTEGRITY_POINTER_MISSING
    try:
        raw = path.read_bytes()
    except OSError:
        return INTEGRITY_POINTER_MISSING
    if not digest:
        return INTEGRITY_VERIFIED  # 旧指针无摘要：仅证明可读（见 docstring）
    return INTEGRITY_VERIFIED if sha256_of_bytes(raw) == digest else (
        INTEGRITY_DIGEST_MISMATCH
    )


def read_back_payload(pointer: Dict[str, Any]) -> Optional[Any]:
    """digest 校验读回。JSON → 解析后的载荷；binary → RestoredBinary。

    缺失 / 摘要不符 / 越界 → None（调用方诚实降级，绝不写回未经校验的
    字节 —— checkpoint 同款 verify-before-write 纪律）。
    """
    import json

    from app.services.durable_blob_store import get_filesystem_blob_store

    digest = str(pointer.get("content_payload_sha256") or "")
    content_type = str(pointer.get("content_type") or "json")
    if content_type == "binary":
        if not digest:
            return None
        raw = get_filesystem_blob_store().get_blob(digest, expected_sha256=digest)
        if raw is None:
            return None
        return RestoredBinary(data=raw, content_type="binary")
    path = _resolve_content_path(str(pointer.get("content_location") or ""))
    if path is None or not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if digest:
        from app.services.durable_blob_store import sha256_of_bytes

        if sha256_of_bytes(raw) != digest:
            logger.warning(
                "[workspace.durability] digest mismatch reading %s — refusing",
                pointer.get("content_location"),
            )
            return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


# ── 会话侧写回（restore；不改写 session 后端语义）────────────────────


async def restore_raster_png(session_id: str, ref: str, data: bytes) -> bool:
    """binary 指针 → 会话磁盘栅格位（原子写回同一 PNG 路径）。"""
    from app.services.artifact_registry import is_raster_ref, raster_png_path

    if not is_raster_ref(ref):
        return False
    path = raster_png_path(session_id, ref)
    if path is None:
        return False

    def _write() -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
            try:
                tmp.write_bytes(data)
                tmp.replace(path)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            return True
        except OSError:
            return False

    return await asyncio.to_thread(_write)


async def write_back_session_payload(
    session_id: str, ref: str, payload: Any
) -> Tuple[bool, str]:
    """把载荷放回 **同一 ref id** 的读取语义。

    优先 ``overwrite``（同 ref 原位写回；Redis 后端 SET 天然
    create-or-replace，TTL 消失的 ref 直接复活）。内存后端的 overwrite 是
    replace-only —— False 时经 ``store()`` + 别名放回（
    ``get(recorded_ref)`` 经别名命中），返回 ``"alias"`` 模式供调用方披露。
    失败返回 ``(False, reason)``。
    """
    from app.services.session_data import session_data_manager

    try:
        if await session_data_manager.overwrite(session_id, ref, payload):
            return True, "overwrite"
    except Exception as e:  # noqa: BLE001 — store 故障按失败（诚实降级）
        logger.warning("[workspace.durability] overwrite failed for %s: %s", ref, e)
        return False, "overwrite_error"
    try:
        new_ref = await session_data_manager.store(
            session_id, payload, prefix=_ref_prefix(ref)
        )
        if not new_ref:
            return False, "store_failed"
        await session_data_manager.set_alias(session_id, new_ref, ref)
        # round-1 review MAJOR（documented choice）：alias 模式下 descriptor
        # 索引键在 new_ref 名下（session store 无公开的 descriptor 写入口，
        # 从这里伸手进内部 ``_descriptors`` 不是一个干净的选项）—— 因此
        # verify_snapshot 的探测带 ``store.get()`` 兜底（见
        # snapshot._probe_ref_live），别名命中的载荷不会被误报 missing。
        return True, "alias"
    except Exception as e:  # noqa: BLE001
        logger.warning("[workspace.durability] alias restore failed for %s: %s", ref, e)
        return False, "store_error"
