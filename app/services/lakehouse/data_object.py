"""Durable DataObject identity — Spatial Lakehouse V6 (Wave 1, ADR-0118).

单一事实源边界：本模块是 lakehouse 数据对象的**身份层**，不是第二个
store —— 字节真相只有 BlobStore（``durable_blob_store``，唯一内容持久
后端）一份；会话台账仍归 artifact_registry；durable revision 仍归
artifact_revisions。这里只提供：确定性 manifest 构造、内容寻址发布
（blobs + manifest 经 CAS 幂等去重）、digest 校验解析、owner 范围校验
与物化（blob → 工作目录，DR/restore 复用同一通道）。

身份契约（不可协商）：

- **DataObject id = manifest 的 canonical sha256** —— 内容寻址默认参与
  身份，无 opt-in；
- **manifest 确定性**：绝不含 wall-clock / 随机成分 —— 同
  (kind, owner, 内容, 参数) 必得逐字节相同 manifest ⇒ 同 id ⇒ 免费去重；
- **input_fingerprint** 复用键走 ``app/lib/data.fingerprints.
  compute_reuse_fingerprint``（仓库唯一复用键口径）——"同输入+同参数
  ⇒ 同产物身份"由此可验证（derived artifact dependency hash）；
- **owner 隔离**：owner_scope 参与身份（跨 owner 的同内容是两个不同的
  逻辑对象）；BlobStore 字节级去重跨 owner 共享是安全的 —— 访问永远
  经 owner-checked manifest 解析，绝不跨 owner 泄漏；
- **有界**：blob 数、总字节、manifest canonical 尺寸全部设上限
  （oversized metadata 在成为路径/内存前即 typed 拒绝）。
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple, Union

logger = logging.getLogger(__name__)

#: manifest 契约版本（结构演进 = 升版本，绝不原地改语义）。
MANIFEST_SCHEMA_VERSION = 1

#: 有界上限（oversized metadata / archive-bomb 防线）。
MAX_MANIFEST_BLOBS = 65_536
MAX_MANIFEST_JSON_BYTES = 64 * 1024
DEFAULT_MAX_OBJECT_TOTAL_BYTES = 2 * 1024 ** 3

#: DataObject id = 64 hex sha256。
_DATA_OBJECT_ID_RE = re.compile(r"^[a-f0-9]{64}\Z")
#: owner scope id 白名单（会话/项目 id 同一 charset 纪律 —— 路径段边界）。
_SCOPE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}\Z")

#: DataObject kind 白名单（单点真相 —— 消费方（dr/orphan 扫描等）必须
#: 复用本常量，绝不各自手写清单（评审 R0-5））。
DATA_OBJECT_KINDS = ("vector_parquet", "cog_raster", "zarr_cube", "virtual")


class DataObjectError(ValueError):
    """DataObject 契约违例（非法 scope / 超界 / 身份不符）。"""

    code = "DATA_OBJECT_INVALID"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


class DataObjectTooLargeError(DataObjectError):
    """尺寸闸：blob 数 / 总字节 / manifest 尺寸超界。"""

    code = "DATA_OBJECT_TOO_LARGE"


class DataObjectIdentity(NamedTuple):
    """publish 结果：id 即 manifest digest；deduped = CAS 命中（内容复用）。"""

    data_object_id: str
    manifest_location: str
    content_sha256: str
    byte_size: int
    blob_count: int
    deduped: bool


def is_data_object_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_DATA_OBJECT_ID_RE.match(value))


def normalize_owner_scope(
    *, session_id: Optional[str] = None, project_id: Optional[str] = None
) -> Dict[str, str]:
    """owner scope 规范化（恰好一个维度；charset 白名单在边界即拒绝）。"""
    if bool(session_id) == bool(project_id):
        raise DataObjectError(
            "owner scope requires exactly one of session_id / project_id"
        )
    scope: Dict[str, str] = {}
    if session_id is not None:
        if not _SCOPE_ID_RE.match(session_id):
            raise DataObjectError(f"invalid owner session id: {session_id[:32]!r}")
        scope["session_id"] = session_id
    else:
        if not _SCOPE_ID_RE.match(project_id or ""):
            raise DataObjectError(f"invalid owner project id: {str(project_id)[:32]!r}")
        scope["project_id"] = project_id  # type: ignore[arg-type]
    return scope


def owner_scope_allows(
    manifest: Mapping[str, Any],
    *,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """manifest 是否属于请求者（跨 owner 访问一律 False，绝不泄漏存在性）。"""
    scope = manifest.get("owner_scope") or {}
    if session_id is not None:
        return scope.get("session_id") == session_id and "session_id" in scope
    if project_id is not None:
        return scope.get("project_id") == project_id and "project_id" in scope
    return False


def _redact(value: Any) -> Any:
    """producer/args 防御性脱敏（复用 provenance 唯一口径，绝不在此重写）。"""
    from app.services.provenance.manifest import redact_provenance_args

    return redact_provenance_args(value)


# ── 内容根（merkle）：相对路径 → 有序摘要列表 → 单一根摘要 ──────────────


def compute_content_root(
    entries: List[Tuple[str, str, int]],
) -> Tuple[str, int]:
    """sorted (relative_path, blob_sha256, byte_size) → (root_digest, total_bytes)。

    路径序即身份序（同一对象的重命名 = 新对象，诚实）。空对象 = typed 拒绝。
    """
    if not entries:
        raise DataObjectError("refusing to identity an empty object")
    from app.lib.data.fingerprints import canonical_dumps, sha256_hex

    ordered = [
        {"path": str(p), "sha256": str(d), "byte_size": int(n)}
        for p, d, n in sorted(entries, key=lambda e: e[0])
    ]
    root = sha256_hex(canonical_dumps(ordered))
    return root, sum(int(n) for _p, _d, n in entries)


def _virtual_content_root(children: List[str]) -> str:
    """virtual 内容根 = 有序去重 children ids 的 canonical sha256
    （字节零复制；children 集即逻辑内容的身份）。"""
    from app.lib.data.fingerprints import canonical_dumps, sha256_hex

    return sha256_hex(canonical_dumps(
        [{"child": str(c)} for c in sorted(set(children))]
    ))


# ── manifest 构造（确定性；无 wall-clock）───────────────────────────────


def build_object_manifest(
    *,
    kind: str,
    owner_scope: Mapping[str, str],
    entries: List[Tuple[str, str, int]],
    payload: Optional[Mapping[str, Any]] = None,
    producer: Optional[Mapping[str, Any]] = None,
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
) -> dict:
    """构造确定性 manifest dict（发布前的纯函数形态，测试可独立断言）。

    - ``entries`` 为 (relative_path, blob_sha256, byte_size)；
    - ``producer`` 经 redact（token/secret 不入身份）；
    - ``payload`` canonical 尺寸受 64KiB 闸（consolidated metadata 等大块
      结构必须放 blob，不进 manifest）。
    """
    if kind not in DATA_OBJECT_KINDS:
        raise DataObjectError(f"unknown data object kind: {kind!r}")
    scope = dict(owner_scope)
    if not scope or set(scope) - {"session_id", "project_id"}:
        raise DataObjectError("owner_scope must be exactly one of session/project")
    virtual = kind == "virtual"
    if virtual:
        # Virtual DataObject（评审 R0-4）：零字节复制 —— content root 从
        # 有序去重的 children ids 计算（children 即内容的逻辑身份）。
        children = list((payload or {}).get("virtual", {}).get("children") or [])
        if not children:
            raise DataObjectError(
                "virtual objects require payload.virtual.children (non-empty)"
            )
        unknown = [c for c in children if not is_data_object_id(c)]
        if unknown:
            raise DataObjectError(
                f"virtual children must be data object ids, got {unknown[:3]}"
            )
        unique_children = sorted(set(children))
        if len(unique_children) > MAX_MANIFEST_BLOBS:
            raise DataObjectTooLargeError(
                f"virtual object declares {len(unique_children)} children, "
                f"exceeding the bounded cap {MAX_MANIFEST_BLOBS}"
            )
        payload = dict(payload or {})
        payload["virtual"] = {**payload["virtual"], "children": unique_children}
        entries = []
        root = _virtual_content_root(unique_children)
        total = 0
    else:
        root, total = compute_content_root(entries)
    manifest: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": kind,
        "owner_scope": scope,
        "environment_fingerprint": lakehouse_environment_fingerprint(),
        "content_sha256": root,
        "byte_size": total,
        "content_blobs": [
            {"path": p, "sha256": d, "byte_size": int(n)}
            for p, d, n in sorted(entries, key=lambda e: e[0])
        ],
        "payload": dict(payload or {}),
        "producer": _redact(dict(producer or {})),
        "source_refs": [str(r) for r in (source_refs or [])],
        "input_fingerprint": str(input_fingerprint) if input_fingerprint else "",
    }
    if len(manifest["content_blobs"]) > MAX_MANIFEST_BLOBS:
        raise DataObjectTooLargeError(
            f"object declares {len(manifest['content_blobs'])} blobs, "
            f"exceeding the bounded cap {MAX_MANIFEST_BLOBS}"
        )
    from app.lib.data.fingerprints import canonical_dumps

    if len(canonical_dumps(manifest).encode("utf-8")) > MAX_MANIFEST_JSON_BYTES:
        raise DataObjectTooLargeError(
            f"manifest payload exceeds {MAX_MANIFEST_JSON_BYTES} bytes — "
            "oversized metadata must live in a blob, not the identity record"
        )
    return manifest


# ── 发布 / 解析 / 物化（唯一经 BlobStore 的通道）────────────────────────


def lakehouse_environment_fingerprint() -> str:
    """产出环境的确定性指纹（canonical sha256，有界投影）。

    参与可复现性身份：同输入+同参数在不同运行时环境下产出**不同**的
    DataObject id（诚实 —— 重跑可复现性判定因此可比较环境）。只取版本
    事实（python/geo 栈），绝不包含路径、主机名、时间或凭据。
    """
    import sys

    facts: Dict[str, str] = {"python": "{}.{}.{}".format(*sys.version_info[:3])}
    for name in ("numpy", "rasterio", "pyarrow", "zarr", "geopandas", "shapely"):
        try:
            module = __import__(name)
            facts[name] = str(getattr(module, "__version__", "") or "")
        except Exception:  # noqa: BLE001 — 未安装的可选依赖缺席是事实
            facts[name] = ""
    from app.lib.data.fingerprints import canonical_dumps, sha256_hex

    return sha256_hex(canonical_dumps(facts))


def _store():
    """内容后端选择（env 驱动 —— s3 后端由此真实生效，review C1/M-1）。"""
    from app.services.s3_blob_store import get_object_store

    return get_object_store()


def _digest_bytes(data: bytes) -> str:
    from app.services.durable_blob_store import sha256_of_bytes

    return sha256_of_bytes(data)


def publish_data_object(
    source_files: Mapping[str, Union[Path, bytes]],
    *,
    kind: str,
    owner_scope: Mapping[str, str],
    payload: Optional[Mapping[str, Any]] = None,
    producer: Optional[Mapping[str, Any]] = None,
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
    max_total_bytes: int = DEFAULT_MAX_OBJECT_TOTAL_BYTES,
    store: Optional[Any] = None,
) -> DataObjectIdentity:
    """把一组文件/字节发布为不可变 DataObject（blobs + manifest，全 CAS）。

    同内容重发布 = 全部 CAS 命中（deduped=True，零重写）。执行顺序兑现
    预算承诺（review M3）：**第一遍**流式量尺+摘要（不驻留大内存、不写
    任何字节）→ 预算与 manifest 构造全部通过后 **第二遍**才写 blob ——
    manifest 拒绝（oversized metadata）时零 blob 落盘。
    """
    store = store or _store()
    from app.lib.data.fingerprints import canonical_dumps, sha256_of_file

    # 第一遍：量尺 + 摘要（纯读，无写入）。
    entries: List[Tuple[str, str, int]] = []
    total = 0
    for rel_path in sorted(source_files):
        if not rel_path or rel_path.startswith("/") or ".." in rel_path \
                or "\\" in rel_path or "\x00" in rel_path:
            raise DataObjectError(f"unsafe object member path: {rel_path[:64]!r}")
        src = source_files[rel_path]
        if isinstance(src, bytes):
            digest, size = _digest_bytes(src), len(src)
        else:
            size = Path(src).stat().st_size
            digest = sha256_of_file(src)
        total += size
        if total > max_total_bytes:
            raise DataObjectTooLargeError(
                f"object exceeds {max_total_bytes} bytes — refusing before write"
            )
        entries.append((rel_path, digest, size))

    # manifest 构造（尺寸闸）先于任何 blob 写入。
    manifest = build_object_manifest(
        kind=kind,
        owner_scope=owner_scope,
        entries=entries,
        payload=payload,
        producer=producer,
        source_refs=source_refs,
        input_fingerprint=input_fingerprint,
    )

    # 第二遍：blob 落盘（CAS；路径内容确定性 ⇒ 中断重试自然续齐）。
    # 文件源走流式通道（put_blob_from_path，峰值 O(chunk) —— 评审 R0-27：
    # 大对象发布绝不 read_bytes 全量驻留）；字节源保持原语义。
    from pathlib import Path as _Path

    streamed = getattr(store, "put_blob_from_path", None)
    for rel_path, digest, _size in entries:
        src = source_files[rel_path]
        if isinstance(src, bytes):
            store.put_blob(digest, src, "binary")
        elif streamed is not None:
            streamed(digest, _Path(src), "binary")
        else:
            store.put_blob(digest, Path(src).read_bytes(), "binary")

    blob = canonical_dumps(manifest).encode("utf-8")
    manifest_id = _digest_bytes(blob)
    result = store.put_blob(manifest_id, blob, "json")
    return DataObjectIdentity(
        data_object_id=manifest_id,
        manifest_location=result.location,
        content_sha256=str(manifest["content_sha256"]),
        byte_size=int(manifest["byte_size"]),
        blob_count=len(manifest["content_blobs"]),
        deduped=not result.put_new,
    )


def publish_manifest_only(
    entries: List[Tuple[str, str, int]],
    *,
    kind: str,
    owner_scope: Mapping[str, str],
    payload: Optional[Mapping[str, Any]] = None,
    producer: Optional[Mapping[str, Any]] = None,
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
    store: Optional[Any] = None,
) -> DataObjectIdentity:
    """只发布 manifest（不复制内容 blob）—— 大对象（如超大 cube）的诚实
    降级：身份/完整性/lineage 证据齐备，但 BlobStore 侧无可物化字节
    （调用方必须如实标注 durable="manifest_only"，绝不冒充 published）。"""
    store = store or _store()
    manifest = build_object_manifest(
        kind=kind,
        owner_scope=owner_scope,
        entries=entries,
        payload=payload,
        producer=producer,
        source_refs=source_refs,
        input_fingerprint=input_fingerprint,
    )
    from app.lib.data.fingerprints import canonical_dumps

    blob = canonical_dumps(manifest).encode("utf-8")
    manifest_id = _digest_bytes(blob)
    result = store.put_blob(manifest_id, blob, "json")
    return DataObjectIdentity(
        data_object_id=manifest_id,
        manifest_location=result.location,
        content_sha256=str(manifest["content_sha256"]),
        byte_size=int(manifest["byte_size"]),
        blob_count=0,  # 内容 blob 未进 BlobStore（manifest-only 语义）
        deduped=not result.put_new,
    )


def resolve_data_object(
    data_object_id: str, *, store: Optional[Any] = None
) -> Optional[dict]:
    """digest 校验读 manifest（id ≠ 存储键不符 / 内容被篡改 → None，诚实）。"""
    if not is_data_object_id(data_object_id):
        return None
    store = store or _store()
    raw = store.get_blob(data_object_id, expected_sha256=data_object_id)
    if raw is None:
        return None
    import json

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict) or not manifest.get("content_sha256"):
        return None
    return manifest


def verify_data_object(
    data_object_id: str, *, store: Optional[Any] = None
) -> str:
    """manifest + 全部 blob 的完整性四态（DR 语义，与 workspace durability 同族）：

    ``verified`` / ``manifest_missing`` / ``blob_missing`` / ``digest_mismatch``。
    """
    store = store or _store()
    manifest = resolve_data_object(data_object_id, store=store)
    if manifest is None:
        return "manifest_missing"
    for blob in manifest.get("content_blobs") or []:
        digest = str(blob.get("sha256") or "")
        expected_size = blob.get("byte_size")
        if not digest or not store.exists(digest):
            return "blob_missing"
        size = store.size(digest)
        if expected_size is not None and size is not None and int(size) != int(expected_size):
            return "digest_mismatch"
        raw = store.get_blob(digest, expected_sha256=digest)
        if raw is None:
            return "digest_mismatch"
    return "verified"


def materialize_data_object(
    data_object_id: str,
    target_dir: Union[str, Path],
    *,
    owner_session_id: Optional[str] = None,
    owner_project_id: Optional[str] = None,
    store: Optional[Any] = None,
) -> List[str]:
    """blob → 工作目录（digest 校验逐 blob；原子发布；路径遏制）。

    owner 不符 → :class:`DataObjectError`（绝不物化越权内容）。
    返回写出的相对路径列表。任一 blob 校验失败 → typed 拒绝（绝不写
    半个对象 —— 半对象比无对象更危险）。
    """
    store = store or _store()
    manifest = resolve_data_object(data_object_id, store=store)
    if manifest is None:
        raise DataObjectError(f"data object not found or corrupt: {data_object_id[:16]}")
    if not owner_scope_allows(
        manifest, session_id=owner_session_id, project_id=owner_project_id
    ):
        raise DataObjectError("data object belongs to a different owner scope")
    base = Path(target_dir)
    written: List[str] = []
    # 先全部校验，后写盘（verify-before-write 纪律）。
    payloads: List[Tuple[str, bytes]] = []
    for blob in manifest.get("content_blobs") or []:
        rel_path = str(blob.get("path") or "")
        if not rel_path or rel_path.startswith("/") or ".." in rel_path \
                or "\\" in rel_path:
            raise DataObjectError(f"unsafe manifest member path: {rel_path[:64]!r}")
        raw = store.get_blob(str(blob.get("sha256") or ""),
                             expected_sha256=str(blob.get("sha256") or ""))
        if raw is None:
            raise DataObjectError(
                f"content blob missing or corrupt: {rel_path[:64]}"
            )
        payloads.append((rel_path, raw))
    for rel_path, data in payloads:
        dest = (base / rel_path)
        resolved = dest.resolve()
        try:
            resolved.relative_to(base.resolve())
        except ValueError as e:
            raise DataObjectError(
                f"member path escapes target dir: {rel_path[:64]!r}"
            ) from e
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp-{uuid.uuid4().hex[:8]}")
        try:
            tmp.write_bytes(data)
            tmp.replace(dest)  # 原子发布
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        written.append(rel_path)
    return written


# ── 复用键（dedup/cache identity 的第一等入口）──────────────────────────


def compute_object_reuse_fingerprint(
    *,
    operation: str,
    operation_version: str,
    input_fingerprints: Mapping[str, str],
    normalized_args: Any = None,
    owner_scope: Optional[Mapping[str, str]] = None,
) -> str:
    """确定性产物的复用键（``app/lib/data.fingerprints`` 唯一口径）。

    owner scope 参与键（跨 owner 永不共享复用判定 —— 字节可共享，身份
    判定不可）。非确定性操作不得调用（调用方契约，同 fingerprints 模块）。
    """
    from app.lib.data.fingerprints import compute_reuse_fingerprint

    return compute_reuse_fingerprint(
        operation=operation,
        operation_version=operation_version,
        input_fingerprints=dict(input_fingerprints),
        normalized_args=normalized_args,
        runtime_semantic_version=canonical_owner_tag(owner_scope),
    )


def canonical_owner_tag(owner_scope: Optional[Mapping[str, str]]) -> str:
    """owner scope → 单一字符串（键排序，稳定）。"""
    if not owner_scope:
        return ""
    return ",".join(f"{k}={owner_scope[k]}" for k in sorted(owner_scope))
