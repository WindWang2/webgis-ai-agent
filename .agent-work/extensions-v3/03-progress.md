# 03 — Progress

## Wave 1-3（2026-09-10）：依赖 + config + 签名 V3 + trust store ✅
- cryptography>=44 入 requirements.txt + pyproject；venv 安装实测 Ed25519 OK
- config：9 个 V3 新键（全缺省关闭）— EXTENSION_TRUST_STORE_PATH / EXTENSION_REGISTRY_DIR /
  EXTENSION_REGISTRY_URLS / EXTENSIONS_INSTALL_ROOT / EXTENSIONS_ISOLATION_BACKEND /
  EXTENSION_VERSION_PIN / EXTENSIONS_KEEP_VERSIONS / EXTENSION_STREAM_WINDOW / EXTENSION_MAX_STREAM_EVENTS
- HostPolicy：trust_store / isolation_backend / stream_window / max_stream_events / version_pins
- settings_bridge：fail-closed 解析（trust store 加载、后端词表、有界 int、pin 表）
- signing.py：Ed25519 sign/verify（v2 载荷含 publisher）；HMAC v1 载荷逐字节冻结；
  新裁决 signed_retired / revoked；generate_signing_keypair（0600、拒覆盖）
- trust_store.py：publishers/keys(active|retired|revoked)/fingerprints/packages 吊销；
  加载期 fail closed（含非 Ed25519 PEM 拒绝）；运行期零 I/O
- host：验签传入 trust store + package_id/version；revoked → quarantine；
  retired → warning 不提权；diagnostics 追加 12 个 V3 码（append-only）
- 测试：test_signing_v3.py 17 用例（rotation/retired/revoked/forged/tampered/
  HMAC 兼容/fail-closed）；扩展域 2405 passed（基线 2388 + 17 新）；ruff clean
