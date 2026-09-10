# Security Boundary (honest threat model)

Sources: `app/extensions_platform/worker/`, `broker.py`, `spawn.py`（worker
包内）, `host.py`. Authority: `docs/adr/0105-gis-extension-platform-v2.md`.

This page states exactly what the V2 isolation does and does not contain.
The one-sentence version: **worker 模式 = 进程隔离 + 能力代理 + 资源强制；
不是内核沙箱——worker 代码仍以服务用户身份运行。**

## Two execution models, two boundaries

| | `execution.mode=in_process`（默认） | `execution.mode=worker` |
| --- | --- | --- |
| 解释器 | 与宿主共享 | 独立子进程（`python -m app.extensions_platform.worker.server`） |
| 内存 | 共享 | `RLIMIT_AS` 上界 |
| 环境变量 | 完整宿主环境 | 净化后 5 项（见下） |
| 宿主能力 | 直接 SDK/平台通道（权限包裹层检查） | 仅经 default-deny capability broker（RPC） |
| 代码卸载 | `deactivate` 后模块仍驻留 `sys.modules` | 进程退出即消失 |
| 崩溃影响 | 可拖垮宿主进程 | typed 隔离：回滚 → 可恢复 → 连续崩溃 quarantine |
| 本质 | trusted-code boundary（V1 语义，不变） | 隔离，但**非内核沙箱** |

In-process 扩展不经过 broker、不做资源强制、没有超时——它的边界与 V1
完全相同：信任决策（trust levels / grants / blocklist）显式且可审计，
但**不隔离代码**。V2 的一切隔离增强只属于 worker 模式。

## What worker mode ISOLATES（每项都有测试钉死）

- **解释器与内存分离。** 宿主进程永不 import 扩展代码；扩展在独立解释器
  中运行，`RLIMIT_AS = execution.max_memory_mb`（默认 512 MiB）由 worker
  进程自身在入口处施加（先 limit 后加载 pack）。malloc 失败 →
  `MemoryError` → typed `worker_crashed`，宿主不受影响。
  钉死：`test_resource_limits.py`, `test_worker_integration.py`。
- **无 ambient authority。** spawn 环境只含
  `PATH / PYTHONPATH / LANG / HOME / PYTHONHASHSEED / WEBGIS_EXTENSION_WORKER`
  ——宿主的环境变量面（含 API keys）不进入 worker；worker 进程同时以
  临时目录为 CWD 且 `WEBGIS_EXTENSION_WORKER=1` 令 `app.core.config` 跳过
  `.env` 解析（否则 worker 可经 `from app.core.config import settings`
  一次读走宿主全部 secrets，Round-2 审查 C-1 堵住的通道）。钉死：
  `test_worker_integration.py::test_worker_env_is_sanitized`,
  `test_review_r2_fixes.py::TestSecretsNotReadableViaSettings`。
- **CPU 与墙钟预算。** `RLIMIT_CPU = execution.max_cpu_seconds`（SIGXCPU
  杀进程）；宿主侧另有 `call_timeout_s` 墙钟预算 + `killpg` 整组兜底。
  钉死：`test_resource_limits.py::TestCpuLimit`,
  `test_worker_integration.py::test_call_timeout_kills_worker`。
- **输出上限。** 工具结果序列化超过 `execution.max_output_bytes` →
  typed `output_limit_exceeded` 错误结果（worker 不被杀死）；协议帧另有
  68 MiB 硬上限双侧强制。钉死：`test_resource_limits.py::TestOutputLimit`,
  `test_worker_server.py`。
- **Default-deny 能力代理。** worker 的一切宿主能力（网络/工件/凭据）
  显式跨 RPC，由宿主内 broker 执行并审计（矩阵见下）；未知 op、缺授权、
  缺配置一律 typed 拒绝。钉死：`test_broker.py`（单测 + 真实子进程 e2e）。
- **指纹核对的握手。** worker 启动时宿主发送期望内容指纹，worker 重算并
  比对——发现与执行之间包内容被改 → `package_tampered` 隔离路径。
  协议版本 / id / namespace / name / worker 模式 / api 兼容同样在握手期
  fail closed。钉死：`test_worker_server.py::TestHandshake`。
- **崩溃 → 回滚 → quarantine。** 崩溃/超时：投影经台账逆序回滚，状态回
  COMPATIBLE（可重新激活）；连续崩溃达 `EXTENSIONS_MAX_WORKER_CRASHES`
  （默认 2）→ `worker_restart_quarantined` → QUARANTINED。in-flight 调用
  期间 deactivate → typed `operation_in_flight`。
  钉死：`test_worker_integration.py::TestWorkerLifecycle`。
- **可信面最小化。** worker 内代码即使主动注册未声明工具，宿主侧声明
  对账仍然拒绝（`undeclared_registration`）；类实例投影 API 在
  `WorkerContext` 上 typed 拒绝（纵深防御，manifest 层已先拒绝一次）。
  钉死：`test_worker_integration.py::test_undeclared_tool_fails_activation`,
  `test_worker_integration.py::test_worker_packs_cannot_project_class_instances`。

## Capability broker ops matrix

| op | 所需授权（`EXTENSION_PERMISSION_GRANTS`） | 额外闸门 | 硬上限 |
| --- | --- | --- | --- |
| `network_request` | `network` | `EXTENSION_NETWORK_ALLOW` 出网 allowlist（按扩展 id；`"*"` = 全放行）→ 仍须通过权威 SSRF gate `DataFabricSecurity.validate_url`（私网/环回/链路本地/元数据 IP 一律拒绝，**即使 allowlist 是 `"*"`**）；方法限 `GET / HEAD / POST`；`host`/`content-length`/`connection` 头剥离；不跟随重定向 | 响应体 4 MiB（超出截断 + `truncated` 旗标）；超时 ≤ broker 上限 |
| `artifact_read` | `project_artifact_read` | 路径必须落在 `EXTENSION_ARTIFACT_ROOTS`（`os.pathsep` 分隔；**空 = 一律拒绝**）；相对/绝对路径统一 resolve 后 confinement 检查 | 单文件 32 MiB（截断 + 旗标） |
| `artifact_write` | `project_artifact_write` | 同上路径 confinement | 单文件 32 MiB（超限 → `output_limit_exceeded`） |
| `secret_get` | 无权限词——**供给即授权**：ref 必须由运维经 `EXTENSION_SECRETS_JSON` 按本扩展 id 显式供给 | 未供给 ref → typed 拒绝；权限词表保持 9 词冻结 | 值永不写入审计环 / 状态 / 日志（审计只记 ref） |

审计：每扩展 256 条有界环（FIFO），`host.broker_audit(id)` 消费；成功与
拒绝都留痕，内部故障只泄故障类型名。钉死：`test_broker.py::TestAudit`,
`test_broker.py::TestBrokerLimits`, `test_broker.py::TestDefaultDeny`,
`test_broker.py::TestWorkerBrokerEndToEnd`。

## What worker mode does NOT isolate（诚实清单）

- **不是内核级沙箱。** 没有 seccomp / syscall 过滤 / namespace / 容器
  边界。worker 代码是服务用户身份的普通 Python 进程，操作系统对该用户
  的一切授权它都有。
- **任意 `os.*` 调用不被拦截。** broker 门控的是 **SDK/broker 通道**；
  worker 代码内直接 `open()` 未授权路径、直接 `socket` 出网、`os.fork`
  等系统调用不经过任何平台检查（进程内甚至没有平台代码可被绕过——
  检查点全在宿主侧）。rlimit 是进程级资源账目，不是能力系统——它限制
  「能用多少」，不限制「能碰什么」。
- **文件系统面 = 服务用户的文件系统。** `EXTENSION_ARTIFACT_ROOTS` 只
  约束 `ctx.broker.read_artifact/write_artifact` 这一条 SDK 通道；它不
  是文件系统 jail。
- **SSRF 防御是 pre-connect 的。** broker 与核心 data fabric 共用同一
  姿态：连接前解析全部地址并拒绝私网/环回/元数据 IP；不做连接期 IP
  pinning，因此 DNS rebinding（首查公网、连接时改解析为私网）在理论
  上仍在——与核心 gate 相同的缺口，不因 broker 而更强。
- **网络 allowlist 是 hostname 级粗过滤。** `EXTENSION_NETWORK_ALLOW`
  匹配 URL hostname（或 `"*"`），不是 egress 防火墙；它的价值是把
  「worker 可能不能出网」变成显式运维决策，而不是精细网络策略。
- **握手指纹核对发生在加载前，但 pack 仍是磁盘内容。** 带有 pack 目录
  写权限的攻击者仍然拥有 V1 全部攻击面（包括 `.pyc` 字节码盲区——
  worker 不 import 进宿主进程缓解的是宿主暴露，不是盲区本身）。
- **崩溃隔离不等于资源记账完备。** `RLIMIT_CPU`/`RLIMIT_AS` 之外的资源
  （文件描述符、子进程数、磁盘写入 via 非 broker 通道）没有预算。
- **in-process 扩展共享一切。** V1 的 trusted-code 边界原文照旧适用于
  默认执行模式；V2 没有让它变强也没有变弱。

## Claims we do NOT make

- 不宣称 "sandbox" 或 "完全隔离"——CLI 输出与代码注释同遵守此措辞禁令。
- 不宣称 worker 内代码被 "最小权限" 运行——它拥有服务用户的完整权限，
  只是平台提供的通道被门控。
- 不宣称 broker 是安全边界对 in-process 扩展的延伸——它只管 worker。
- 不宣称签名是来源证明——HMAC 共享密钥 = publisher ≈ operator，是内容
  认证而非身份认证。
- 不宣称 secrets 系统是 vault——`EXTENSION_SECRETS_JSON` 是运维管理的
  明文配置，无轮换/租约/审计汇。
- 不宣称 conformance corpus 覆盖 worker 运行时行为——语料库是 manifest
  层契约；worker 行为由真实子进程集成测试单独钉死。

## Operational hardening（平台之内与之外）

平台之内（配置即边界）：

1. 不受信扩展一律 `execution.mode=worker`；in-process 模式只给
   trusted_builtin / 显式审查过的包。
2. `EXTENSION_PERMISSION_GRANTS` 按需授予；broker 没有授权时一切 op
   默认拒绝。
3. `EXTENSION_ARTIFACT_ROOTS` 指到专用目录；留空则 artifact 通道全关。
4. `EXTENSION_NETWORK_ALLOW` 逐 host 点名；避免 `"*"`。
5. `EXTENSION_SECRETS_JSON` 只给需要的扩展 id + ref。
6. `EXTENSIONS_BLOCK` 仍是唯一 "永不执行" 控制——隔离不是信任的替代品。

平台之外（OS 层，平台不做也不宣称）：

- 以专用低权限用户运行服务与 worker；用 OS 文件权限收紧 pack 目录与
  artifact 根；如需 syscall/网络硬隔离，用容器/seccomp 在平台之外包裹
  整个服务——这与 V1 的建议一致，V2 不改变它。

## Pinning tests

| Behavior | Test file |
| --- | --- |
| worker 生命周期 / 崩溃隔离 / 超时 kill / env 净化 / 声明对账 | `tests/unit/extensions_platform/test_worker_integration.py` |
| 协议分帧 / 握手矩阵 / 调用循环 | `tests/unit/extensions_platform/test_worker_server.py` |
| rlimits / 输出上限 / typed 降级 | `tests/unit/extensions_platform/test_resource_limits.py` |
| broker 授权矩阵 / SSRF / 路径 confinement / 审计 / e2e | `tests/unit/extensions_platform/test_broker.py` |
| 签名流（含 tampered→quarantine 即使 allowlisted） | `tests/unit/extensions_platform/test_signing.py` |
| V2 manifest 契约（execution/model_providers/约束矩阵） | `tests/unit/extensions_platform/test_v2_contract.py` + `test_conformance_corpus.py` |
| 认证套件（含示例包全绿） | `tests/unit/extensions_platform/test_certification.py` |

## V3 isolation backends (ADR-0120)

The V2 "process" backend remains the default and its semantics are
unchanged. V3 adds an opt-in **bubblewrap** backend
(`EXTENSIONS_ISOLATION_BACKEND=bubblewrap`):

- `bwrap --unshare-all` — including the **network namespace**: direct
  socket syscalls inside the worker fail at the OS level. The capability
  broker becomes the only egress path *by construction*, not by SDK
  discipline.
- Minimal read-only bind set: the repo's `app/` package (mounted at
  `/opt/webgis/app`), the resolved interpreter + stdlib + venv
  site-packages, system libraries, and the pack directory (at
  `/opt/ext/pack`). **The repository root is not in the sandbox** —
  `.env`, local data and fixtures are unreachable (a corpus test opens a
  repo-root marker from inside a real sandbox and asserts failure).
- Writable surface: tmpfs only. rlimits and process-group kill semantics
  are unchanged from V2.

Claims we still do **not** make: bubblewrap confinement is not a kernel
sandbox (no seccomp/LSM filter; the worker still runs as the service user
inside its namespaces). A bwrap failure at spawn time is a typed
activation failure (`isolation_unavailable`) — the platform never
silently falls back to the weaker backend, and the effective backend is
reported in status.

## V3 supply chain

- Signatures: Ed25519 (`cryptography`), payload binding
  `(publisher, key_id, fingerprint)`; HMAC v1 packs keep verifying
  byte-identically. Trust store: multiple keys per publisher (rotation),
  per-key `active|retired|revoked`, plus fingerprint- and
  package-level revocation lists. Revoked ⇒ quarantine regardless of
  allowlists; retired ⇒ `signed_retired` (valid math, no elevation).
- Installer: safe unpack (regular-file whitelist, entry/byte budgets,
  traversal and link rejection), digest re-verification, staged
  fingerprint-vs-signature check, atomic two-rename swap with crash
  recovery, and one shared preflight for install/upgrade/rollback
  (revocation and version pins apply to rollback too).
