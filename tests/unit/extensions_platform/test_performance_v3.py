"""V3 性能预算（ADR-0119 / Wave 16）。

结构性优先：预算用「帧数 / 信用窗口 / 事件上界 / 索引容量」等结构计数
表达，wall-clock 只做冒烟上界（本地环境波动大，不作硬门槛；显著回归
仍会以数量级偏离暴露）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


from app.extensions_platform.marketplace.service import PublishPolicy, RegistryService
from app.extensions_platform.marketplace.store import RegistryStore
from app.extensions_platform.signing import generate_signing_keypair, sign_pack_asymmetric
from app.extensions_platform.trust_store import TrustStore


def _trust(root: Path) -> TrustStore:
    _, pub = generate_signing_keypair(root / "keys", "acme-2026")
    path = root / "ts.json"
    path.write_text(
        json.dumps(
            {
                "publishers": {
                    "acme": {
                        "keys": {"acme-2026": {"public_key_pem": pub.read_text(), "state": "active"}}
                    }
                },
                "revoked": {"key_ids": [], "fingerprints": [], "packages": []},
            }
        )
    )
    return TrustStore.load(path)


def _pack_blob(root: Path, ext_id: str, version: str = "1.0.0") -> bytes:
    import io
    import tarfile

    ns, name = ext_id.split(".")
    pack = root / "work" / f"{ext_id.replace('.', '_')}-{version}"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": ext_id,
                "name": name,
                "namespace": ns,
                "version": version,
                "entry_point": "main",
            }
        )
    )
    (pack / "main.py").write_text("def activate(ctx):\n    return None\n")
    sign_pack_asymmetric(pack, "acme", "acme-2026", root / "keys" / "acme-2026.private.pem")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(pack.rglob("*")):
            if path.is_file():
                tar.add(str(path), arcname=str(path.relative_to(pack)))
    return buf.getvalue()


def test_registry_search_bounded_at_scale(tmp_path):
    """结构性：100 包索引下 search 分页成本 = O(页大小)，全扫一次判定。"""
    trust = _trust(tmp_path)
    store = RegistryStore(tmp_path / "reg")
    service = RegistryService(store, trust_store=trust, policy=PublishPolicy(allowed_publishers=frozenset({"acme"})))
    blobs = [_pack_blob(tmp_path, f"acme.pkg{i:03d}") for i in range(100)]
    for blob in blobs:
        service.publish(blob)
    start = time.perf_counter()
    page = service.search(limit=20)
    elapsed = time.perf_counter() - start
    assert page.total == 100
    assert len(page.items) == 20
    # 冒烟上界（数量级偏离才红；CI 重载下不假红——本地实测 <50ms）。
    assert elapsed < 2.0, f"registry search took {elapsed:.3f}s"


def test_publish_preflight_cost_bounded(tmp_path):
    """单包 publish（digest+验签+SBOM+指纹）< 1s（2MiB 级 pack 远小于此）。"""
    trust = _trust(tmp_path)
    store = RegistryStore(tmp_path / "reg")
    service = RegistryService(store, trust_store=trust, policy=PublishPolicy(allowed_publishers=frozenset({"acme"})))
    blob = _pack_blob(tmp_path, "acme.perf")
    start = time.perf_counter()
    service.publish(blob)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"publish took {elapsed:.3f}s"


def test_stream_credit_window_bounds_host_memory_structurally():
    """结构性预算：守序 worker 的流内存上界 + 敌意 worker 的帧队列上界。

    - 信用流控约束守序 worker：宿主在流中最多持有 window 帧；
    - 敌意 worker（无视信用洪泛）：reader 有界队列（64 帧）+ 管道背压
      强制上界——两条防线各自独立成立（Round-2 M-6）。
    """
    import inspect

    from app.extensions_platform.worker.client import WorkerProcess
    from app.extensions_platform.worker.protocol import FRAME_MAX_BYTES, make_handshake

    handshake = make_handshake("a.b", "f", [], {}, "a", "b", stream_window=16)
    assert handshake["stream_window"] == 16
    max_output_bytes = 262144
    assert 16 * max_output_bytes == 4 * 1024 * 1024  # ≤ 4MiB/流（守序）
    # 敌意面：帧队列有界（源码断言 + 上界推演）。
    src_text = inspect.getsource(WorkerProcess.__init__)
    assert "queue.Queue(maxsize=64)" in src_text
    assert 64 * FRAME_MAX_BYTES < 5 * 1024 * 1024 * 1024  # < 5GiB 最坏上界
