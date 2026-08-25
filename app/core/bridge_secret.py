"""Pi bridge 共享密钥（E-2 / #893 分层收口）。

`get_bridge_secret` 原先定义在 api/routes/pi_tools.py，被
services/chat/pi_rpc_client.py 反向 import（api→services→api 隐式环）。
密钥装配是 core 基础设施职责，下沉至此（实现原样搬移）；路由层保留
re-export 兼容。
"""
from __future__ import annotations

import fcntl
import logging
import os
import secrets
import tempfile
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_bridge_secret() -> str:
    """返回当前 bridge 共享密钥（供 PiBridge.start 注入 subprocess env），确保多 worker 进程一致。"""
    secret = os.getenv("WEBGIS_BRIDGE_SECRET")
    if secret:
        return secret
    secret_file = Path(settings.DATA_DIR) / ".pi_bridge_secret"
    lock_file = secret_file.with_suffix(".lock")
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        with lock_file.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                val = secret_file.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                val = ""
            if not val:
                val = secrets.token_urlsafe(32)
                fd, tmp_name = tempfile.mkstemp(
                    prefix=".pi_bridge_secret.", dir=str(secret_file.parent)
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as temp:
                        temp.write(val)
                        temp.flush()
                        os.fsync(temp.fileno())
                    os.chmod(tmp_name, 0o600)
                    os.replace(tmp_name, secret_file)
                except BaseException:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
                    raise
    except Exception as e:
        # ADR-0066 同款精神：错误在源头可见比在下游可诊断便宜。
        # 多 worker 部署下回退随机值会导致各 worker 持不同 secret → Pi 回调间歇 401。
        # 要么全拿到同一个 secret，要么全部启动失败，无中间态。
        logger.error(f"Failed to write bridge secret file: {e}")
        raise RuntimeError(
            f"Cannot initialize Pi bridge secret (write failed: {e}). "
            "Multi-worker deployments require a persistent shared secret. "
            "Check file permissions and disk space."
        ) from e
    os.environ["WEBGIS_BRIDGE_SECRET"] = val
    return val
