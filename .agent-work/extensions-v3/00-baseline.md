# 00 — Baseline（Phase A 只读审计，2026-09-10）

- base SHA：`8a33e3a5`（origin/master，与本地 master 一致）
- worktree：`../webgis-ai-agent-extensions-v3`，branch `feat/extensions-v3-secure-ecosystem`
- 最新 PR：#1178–#1175 OPEN（science-v5 / workflow-v5 / lakehouse-v7 / harness-v6 / data-fabric-v7）；#1171 = Extensions V2（MERGED）——本 Epic 的直接前置
- 并发 Epic 冲突面：`app/main.py`（lifespan+router 注册）、`CHANGELOG.md`、`docs/adr/`（新 ADR 编号）、`app/core/config.py`（Settings 追加）、`pyproject.toml`/`requirements.txt`（依赖）、`tests/quality/snapshots/openapi.json`（additive API 需显式刷新）

## V2 现状（证据）

| 事实 | 证据 |
|---|---|
| 平台 ~9.8k LOC，唯一 app 集成点 = FastAPI lifespan | `app/main.py:75-106`；`app/extensions_platform/`（40 文件） |
| 权威 registry 不变：ToolRegistry/AlgorithmRegistry/AdapterRegistry/cartography/Recipe | `context.py` 全部经 registry 门面写入 |
| 签名 = HMAC-SHA256 共享密钥（publisher≈operator） | `signing.py:40-57`；`limitations.md` Ecosystem 节 |
| 信任 5 级 + 验签信任流 | `trust.py:27-40`、`host.py:_apply_signature_verdict` |
| worker 隔离 = 行分帧 JSON RPC over stdio，协议 1.0，串行调用 | `worker/protocol.py:35`、`worker/client.py:283-324` |
| broker 默认 deny，4 op（network/artifact×2/secret），审计环 256 | `broker.py:107-148`、`broker.py:39` |
| worker 仅支持 tools+model_providers（无 streaming）；类实例投影必须 in-process | `manifest.py:55`、`manifest.py:438-470` |
| 无 marketplace/分发；pack = 运维文件系统目录 | `limitations.md` L44-47 |
| 回滚 = 运维手动恢复目录 + `reload(allow_downgrade=True)` | `host.py:1377-1390`、`limitations.md` L84-87 |
| 单进程宿主，无跨进程协调 | `limitations.md` L71-74 |
| data_fabric 不分发 V2 streaming/tile/raster mixin | `sdk/provider.py:86-92`、`limitations.md` L58-67 |
| 认证 CLI：package/verify/sbom/certify | `cli.py:1057-1089` |
| 测试基线：`pytest tests/unit/extensions_platform/ -q --no-cov` = **2388 passed / 13.44s** | 本地实测 2026-09-10 |
| OpenAPI additive 变更可显式刷新快照 | `tests/quality/test_api_compatibility.py:36-54`（`API_SNAPSHOT_UPDATE=1`） |

## 环境可行性（本机实测）

- Python venv 3.13.15；**无 `cryptography`**，PyJWT `has_crypto=False` → 必须新增 crypto 依赖；`pip download cryptography` 网络可用 ✅
- **bwrap（bubblewrap）可用** ✅（`--unshare-all` 实测通过）；`unshare --user` 可用 ✅；docker 存在但 daemon 化部署超范围
- WASM 运行时（wasmtime/wasmer）未安装 → 不采用（引入新解释器面 + 与 Python SDK 不兼容）

## P0/P1 分级（V3 Must-have 对应）

- **P0（安全边界）**：HMAC 共享密钥非发布者身份证明（→ 非对称签名 A）；无 key rotation/revocation（→ trust store A）；worker 网络面只靠 SDK 纪律，syscall 可直连（→ bubblewrap netns D）
- **P0（生态）**：无分发通道、无原子安装、无版本存储（→ registry B + distribution C）
- **P1（能力）**：4 类投影无法 worker 化（→ 协议 V3 F + worker 投影 E）；流式不可用（→ F）；data fabric 不消费 mixin（→ G）
- **P1（运维）**：无 drain/版本 pin/分布式刷新（→ I）
- **P2**：认证缺供应链深度检查（→ J）；无恶意语料（→ 安全测试）
- **P3**：LLM chat transport——**审计结论：不扩展**。ADR-0102 `ModelDescriptorRegistry` 为配置驱动封闭面，打开它 = 破坏 Pi/core boundary（ADR-0105 明确 non-goal；V3 条款"仅在审计确认不破坏 Pi/core boundary 时"→ 审计确认会破坏，故 H 只做 model registry projection（V2 已有 inventory）+ 诚实记录不实现原因）
