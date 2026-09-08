"""S3-compatible BlobStore backend — Spatial Lakehouse V6 (Wave 2, ADR-0118).

单一事实源边界：这是 ``durable_blob_store.BlobStore`` 的**第二实现**，
不是第二内容后端 —— 接口/键布局/原子发布/digest 校验纪律与
``FilesystemBlobStore`` 完全同族（BlobStore docstring 早在 V4 预留此
seam）。指针（``content_location``）保持 ``<shard>/<key>.json|bin`` 的
后端无关形态：同一快照指针在 FS 与 S3 后端间可移植。

原子发布（S3 无 rename，copy 是唯一原子出场方式）：

    staging:  <prefix>/staging/<uuid8>/<shard>/<key><suffix>   （put_object）
    final:    <prefix>/<shard>/<key><suffix>                   （copy_object ——
                                                  读者只见完整对象或无对象）
    cleanup:  delete_object(staging)

put-if-absent：head final + 读 sidecar（``<key><suffix>.meta``，内容
{content_type, byte_size, sha256}）比对 digest —— 一致 = CAS 命中；不一致
（外部覆写/损坏占位）= 原子重写，绝不把坏字节当命中。

依赖纪律：boto3 是 **optional dependency**（lazy import-at-use；缺失 →
typed ``S3StoreUnavailable``，vector_carrier/zarr 同族诚实降级 —— 绝不
假装成功）。测试经 client factory 注入 fake（无网络）。

安全：endpoint URL 构建期过 ``DataFabricSecurity.validate_url`` SSRF 门
（该门早已支持 http/https/s3 schemes）；secret 只进 client config，绝不进
日志/manifest。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Optional

from app.services.durable_blob_store import (
    BlobKeyError,
    BlobStore,
    PutResult,
    safe_blob_key,
    sha256_of_bytes,
)

logger = logging.getLogger(__name__)

_JSON_SUFFIX = ".json"
_BIN_SUFFIX = ".bin"
_META_SUFFIX = ".meta"
_STAGING_ROOT = "staging"


class S3StoreUnavailable(RuntimeError):
    """boto3 缺失 / 配置不完整时的 typed 降级（诚实，绝不假装成功）。"""

    code = "S3_STORE_UNAVAILABLE"
    correction_hint = "pip install boto3 and configure WEBGIS_S3_* env vars"

    def __init__(self, message: str = "s3 object store is not available"):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {
            "success": False,
            "code": self.code,
            "message": self.message,
            "correction_hint": self.correction_hint,
        }


def _object_store_backend() -> str:
    import os

    return (os.environ.get("WEBGIS_OBJECT_STORE_BACKEND") or "filesystem"
            ).strip().lower()


def s3_config_from_env() -> dict:
    """env → client 配置（secret 不落日志；调用方负责脱敏输出）。"""
    import os

    return {
        "endpoint_url": (os.environ.get("WEBGIS_S3_ENDPOINT_URL") or "").strip(),
        "bucket": (os.environ.get("WEBGIS_S3_BUCKET") or "").strip(),
        "region": (os.environ.get("WEBGIS_S3_REGION") or "").strip(),
        "access_key_id": (os.environ.get("WEBGIS_S3_ACCESS_KEY_ID") or "").strip(),
        "secret_access_key": (
            os.environ.get("WEBGIS_S3_SECRET_ACCESS_KEY") or ""
        ).strip(),
        "prefix": (os.environ.get("WEBGIS_S3_PREFIX") or "").strip().strip("/"),
    }


class S3BlobStore(BlobStore):
    """S3/MinIO 兼容后端（同一 BlobStore 接口；staging→copy 原子发布）。

    ``client_factory``：``() -> client``（DI seam —— 生产走 lazy boto3，
    测试注入 fake）。``prefix`` 允许同 bucket 内多部署隔离（默认空）。
    """

    def __init__(
        self,
        bucket: str,
        client_factory: Callable[[], Any],
        *,
        prefix: str = "",
    ):
        if not bucket or "/" in bucket:
            raise BlobKeyError(f"invalid s3 bucket: {bucket[:32]!r}")
        self._bucket = bucket
        self._client_factory = client_factory
        self._prefix = prefix.strip("/") if prefix and prefix.strip("/") else ""
        self._client: Optional[Any] = None

    @property
    def bucket(self) -> str:
        return self._bucket

    def _require_client(self) -> Any:
        if self._client is None:
            try:
                self._client = self._client_factory()
            except S3StoreUnavailable:
                raise
            except Exception as e:  # noqa: BLE001 — 构建/认证失败 = typed 降级
                raise S3StoreUnavailable(f"s3 client init failed: {e}") from e
            if self._client is None:
                raise S3StoreUnavailable("s3 client factory returned None")
        return self._client

    # ── 键布局（与 FS store 同 shard 约定；指针后端可移植）──────────────

    def _object_key(self, key: str, content_type: str, *, staging: bool = False) -> str:
        key = safe_blob_key(key)
        shard = key[:4]
        suffix = _BIN_SUFFIX if content_type == "binary" else _JSON_SUFFIX
        rel = f"{shard}/{key}{suffix}"
        parts = [self._prefix] if self._prefix else []
        if staging:
            parts += [_STAGING_ROOT, uuid.uuid4().hex[:8], rel]
        else:
            parts.append(rel)
        return "/".join(parts)

    def _meta_key(self, final_key: str) -> str:
        return final_key + _META_SUFFIX

    def location(self, key: str, content_type: str = "json") -> str:
        """后端无关指针形态（与 FS store 相同的相对布局）。"""
        key = safe_blob_key(key)
        shard = key[:4]
        suffix = _BIN_SUFFIX if content_type == "binary" else _JSON_SUFFIX
        return f"{shard}/{key}{suffix}"

    # ── 核心操作 ─────────────────────────────────────────────────────────

    def put_blob(self, key: str, data: bytes, content_type: str = "json") -> PutResult:
        key = safe_blob_key(key)
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("put_blob expects bytes")
        data = bytes(data)
        client = self._require_client()
        digest = sha256_of_bytes(data)
        final_key = self._object_key(key, content_type)
        location = self.location(key, content_type)
        if self._head(client, final_key) is not None:
            # put-if-absent：信任前先验（FS store 同纪律 —— 绝不把坏字节或
            # 外来内容当命中）。sidecar digest 先比（廉价），长度再比（head），
            # 两者都过才整读验证 —— 外部覆写/损坏占位（含同长度覆写，sidecar
            # 陈旧场景）一律原子重写。
            sidecar = self._get_bytes(client, self._meta_key(final_key))
            existing_digest = ""
            if sidecar is not None:
                try:
                    existing_digest = str(json.loads(sidecar.decode("utf-8")).get(
                        "sha256", ""))
                except (ValueError, UnicodeDecodeError):
                    existing_digest = ""
            if existing_digest == digest:
                head = self._head(client, final_key) or {}
                if int(head.get("ContentLength", -1)) == len(data):
                    existing = self._get_bytes(client, final_key)
                    if existing is not None and sha256_of_bytes(existing) == digest:
                        return PutResult(put_new=False, location=location)
            logger.warning(
                "[s3_blob_store] existing object %s failed identity check — "
                "republishing atomically", key,
            )
        self._atomic_publish(client, key, final_key, data, content_type, digest)
        return PutResult(put_new=True, location=location)

    def _atomic_publish(
        self,
        client: Any,
        key: str,
        final_key: str,
        data: bytes,
        content_type: str,
        digest: str,
    ) -> None:
        staging_key = self._object_key(key, content_type, staging=True)
        try:
            client.put_object(Bucket=self._bucket, Key=staging_key, Body=data)
            client.copy_object(
                Bucket=self._bucket,
                Key=final_key,
                CopySource={"Bucket": self._bucket, "Key": staging_key},
            )
            self._put_meta(client, self._meta_key(final_key), content_type,
                           len(data), digest)
        except Exception:
            # staging 残留清理（final 未出场 = 读者只见"无对象"或旧版本）。
            try:
                client.delete_object(Bucket=self._bucket, Key=staging_key)
            except Exception:  # noqa: BLE001 — 清理失败只留 staging 垃圾
                logger.warning("[s3_blob_store] staging cleanup failed for %s", key)
            raise

    def _put_meta(self, client: Any, meta_key: str, content_type: str,
                  byte_size: int, digest: str) -> None:
        body = json.dumps({
            "content_type": content_type,
            "byte_size": byte_size,
            "sha256": digest,
        }).encode("utf-8")
        client.put_object(Bucket=self._bucket, Key=meta_key, Body=body)

    def _head(self, client: Any, object_key: str) -> Optional[dict]:
        try:
            return client.head_object(Bucket=self._bucket, Key=object_key)
        except Exception:  # noqa: BLE001 — 404/网络错误按缺席（诚实保守）
            return None

    def _get_bytes(self, client: Any, object_key: str) -> Optional[bytes]:
        try:
            resp = client.get_object(Bucket=self._bucket, Key=object_key)
            return bytes(resp["Body"].read())
        except Exception:  # noqa: BLE001 — 缺席/读失败按 None（诚实）
            return None

    def get_blob(self, key: str, expected_sha256: Optional[str] = None) -> Optional[bytes]:
        key = safe_blob_key(key)
        client = self._require_client()
        # 读序与 FS store 一致：JSON → binary（指针不记 content_type 时的
        # 后向兼容读）。
        for content_type in ("json", "binary"):
            raw = self._get_bytes(
                client, self._object_key(key, content_type)
            )
            if raw is None:
                continue
            if expected_sha256 and sha256_of_bytes(raw) != expected_sha256:
                logger.warning(
                    "[s3_blob_store] digest mismatch for %s — refusing", key,
                )
                return None
            return raw
        return None

    def exists(self, key: str) -> bool:
        try:
            key = safe_blob_key(key)
        except BlobKeyError:
            return False
        client = self._require_client()
        for content_type in ("json", "binary"):
            if self._head(client, self._object_key(key, content_type)) is not None:
                return True
        return False

    def size(self, key: str) -> Optional[int]:
        try:
            key = safe_blob_key(key)
        except BlobKeyError:
            return None
        client = self._require_client()
        for content_type in ("json", "binary"):
            head = self._head(client, self._object_key(key, content_type))
            if head is not None and "ContentLength" in head:
                return int(head["ContentLength"])
        return None

    def delete_blob(self, key: str) -> bool:
        try:
            key = safe_blob_key(key)
        except BlobKeyError:
            return False
        client = self._require_client()
        deleted = False
        for content_type in ("json", "binary"):
            final_key = self._object_key(key, content_type)
            if self._head(client, final_key) is not None:
                deleted = True
            client.delete_object(Bucket=self._bucket, Key=final_key)
            client.delete_object(Bucket=self._bucket, Key=self._meta_key(final_key))
        return deleted

    def read_meta(self, key: str) -> Optional[dict]:
        try:
            key = safe_blob_key(key)
        except BlobKeyError:
            return None
        client = self._require_client()
        for content_type in ("json", "binary"):
            raw = self._get_bytes(
                client, self._meta_key(self._object_key(key, content_type))
            )
            if raw is None:
                continue
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
        return None


def _default_client_factory() -> Any:
    """生产 client factory：lazy boto3 + SSRF 门 + secret 注入。"""
    import boto3  # noqa: F401 — ImportError → typed 降级

    raise S3StoreUnavailable(
        "configure WEBGIS_S3_* env vars (endpoint/bucket/credentials) to "
        "enable the s3 backend"
    )


def build_s3_client_from_env() -> Any:
    """env 配置 → boto3 client（构建期 SSRF 门；缺配置 → typed 降级）。"""
    try:
        import boto3
    except Exception as e:  # noqa: BLE001 — any import failure = unavailable
        raise S3StoreUnavailable(
            "the optional 'boto3' dependency is not installed"
        ) from e
    cfg = s3_config_from_env()
    if not cfg["bucket"] or not cfg["endpoint_url"]:
        raise S3StoreUnavailable(
            "WEBGIS_S3_BUCKET and WEBGIS_S3_ENDPOINT_URL are required for "
            "the s3 object store backend"
        )
    from app.services.data_fabric.security import DataFabricSecurity

    try:
        DataFabricSecurity.validate_url(cfg["endpoint_url"])
    except Exception as e:  # noqa: BLE001 — SSRF 门拒绝 = 配置错误，typed 暴露
        raise S3StoreUnavailable(f"s3 endpoint rejected by SSRF gate: {e}") from e
    session = boto3.session.Session(
        aws_access_key_id=cfg["access_key_id"] or None,
        aws_secret_access_key=cfg["secret_access_key"] or None,
        region_name=cfg["region"] or None,
    )
    return session.client("s3", endpoint_url=cfg["endpoint_url"])


_S3_STORE: Optional[S3BlobStore] = None


def get_object_store() -> BlobStore:
    """env 驱动的单例选择器（默认 filesystem = 零行为变化）。

    ``WEBGIS_OBJECT_STORE_BACKEND=s3`` 时切到 S3BlobStore；s3 配置不完整
    / boto3 缺失在使用时抛 typed ``S3StoreUnavailable``（诚实降级，绝不
    静默回落 —— 内容后端换目标必须显式）。
    """
    global _S3_STORE
    backend = _object_store_backend()
    if backend == "filesystem":
        from app.services.durable_blob_store import get_filesystem_blob_store

        return get_filesystem_blob_store()
    if backend == "s3":
        if _S3_STORE is None:
            cfg = s3_config_from_env()
            if not cfg["bucket"]:
                raise S3StoreUnavailable(
                    "WEBGIS_OBJECT_STORE_BACKEND=s3 requires WEBGIS_S3_BUCKET"
                )
            _S3_STORE = S3BlobStore(
                cfg["bucket"],
                build_s3_client_from_env,
                prefix=cfg["prefix"],
            )
        return _S3_STORE
    raise S3StoreUnavailable(
        f"unknown WEBGIS_OBJECT_STORE_BACKEND: {backend!r} "
        "(expected 'filesystem' or 's3')"
    )


def reset_object_store_singleton() -> None:
    """Test hook：单例重置（env 变更即刻生效）。"""
    global _S3_STORE
    _S3_STORE = None
