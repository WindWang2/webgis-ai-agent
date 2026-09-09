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

## Wave 7-9（2026-09-10）：bubblewrap 隔离 + 流式协议 V3 ✅
- worker/isolation.py：bwrap `--unshare-all`（netns = socket 直连 OS 层不可达）
  + 最小 bind 面（app 子目录→/opt/webgis/app + 解析后真实解释器 + PYTHONHOME
  + venv site-packages 进 PYTHONPATH + 系统 lib64/bin symlink 按解析源绑定）；
  per-spawn 失败 = typed ISOLATION_UNAVAILABLE，绝不静默回退（M-9）
- B-1 验收测试：沙箱内读 repo 根 marker 文件失败（本机真跑通过）；
  结构断言 bind 面不含 repo 根
- 协议 3.0：stream_start/frame/end/cancel/credit；信用流控（worker 无信用
  阻塞至 credit/EOF，M-6）；逐帧 idle timeout（C-3，不入崩溃计数）；
  host 收帧 per-frame 字节检查 + stream=False 总字节预算（M-5）；
  broker 等待循环白名单 += credit/cancel + 迟到 credit 幂等入账（C-7）
- manifest：execution.max_stream_events/stream_window；CORE_API_VERSION
  1.2.0；worker+streaming 与 worker+类实例节的门控改按 api>=1.2 判定
- host：invoke_model_provider worker 流式（运行期协商协议门控）；
  call 结果 host 侧尺寸强制
- 测试：脚本化确定性帧序列（fake pipe + fake worker）+ 真实子进程冒烟
  （流式往返/中途取消/超限/隔离后端）；2449 passed
- 行为变更（有意图）：worker+streaming 1.1 拒绝 → 1.2+ 允许（版本门控）；
  协议版本 1.0 → 3.0（lockstep，同仓 spawn 无偏差面）

## Wave 10-16（2026-09-10）：worker 化投影/fabric/lifecycle/认证/语料/证明 ✅
- Wave 10-11（5be122b9）：worker 四类投影 + 对账扩展 + 动态代理类
- Wave 12（3d955c3b）：fabric bridge 能力感知分发（additive 委托）
- Wave 13（189565d3）：drain/pin/revoke 传播/refresh 信号
- Wave 14-15（55c67399）：certification V3 + 恶意语料 20 场景
- Wave 16（989c4280 + 本提交）：extdemo-v3-pack 完成证明 + 性能基准 + ADR-0120/docs
- 生成物再生成：CONTRACT_DRIFT_REPORT/QUALITY_MANIFEST/QUALITY_REPORT/
  generated-artifacts.json（输入变化 → 按仓库流程 gen_* + --update）
- quality gates：334 passed, 7 skipped（含 OpenAPI 快照一致性）
- 扩展域：2492 passed（基线 2388 → +104 V3 用例）
