# 0105. GIS Extension Platform V2 — 隔离执行 / 能力代理 / 供应链

**Date:** 2026-09-09
**Status:** Accepted
**Branch:** `feat/extensions-v2-isolated-runtime`

## Context

ADR-0104 落地了扩展平台 V1：投影而非平行、manifest 唯一契约、命名空间
强制、权限声明 ≠ 授权、trusted-code boundary、三轴版本化。V1 同时诚实地
留下了一批已知边界（docs/extension-platform/limitations.md Round-1 前的
版本）：

1. **扩展类型词表缺口**：`model_provider` 只是保留字（typed 拒绝），
   GIS 域推理模型（分割/检测/分类服务）没有扩展接入面。
2. **执行模型唯一**：所有扩展都被宿主进程 in-process import——扩展代码
   与核心同解释器、同内存、同 OS 权限，`EXTENSIONS_BLOCK` 是唯一硬遏制。
3. **无供应链面**：没有内容签名（指纹只做 tamper 检测，不表达来源）、
   没有 SBOM、没有 secret 形状扫描；"信任" 完全是运维口头配置。
4. **依赖无版本语义**：依赖声明只有 id + required/flag，`>=1.2,<2.0`
   这类约束无法表达；升级预检与回滚闸不存在。
5. **post-startup 生命周期是 diagnostic-only**：启动后 deactivate 不重编译
   runtime manifest，权威视图悬挂旧值（V1 documented limitation）。
6. **资源面无预算**：工具调用没有内存/CPU/输出上限；in-process health
   检查同步无界（V2 修 worker 路径，in-process 保持原样，见 limitations）。

约束不变：六个权威 registry 仍是唯一事实源；不制造平行事实源；**任何
"untrusted-code sandbox" 的宣称都是虚假的**——V2 的隔离是进程隔离 +
能力代理 + 资源强制，worker 代码仍以服务用户身份运行。

## Decisions

V2 按 11 个 wave 递进实现（全部合入，`tests/unit/extensions_platform/`
2372 tests green）：

### Wave 1 — 版本与 manifest 契约（`api_version.py` / `manifest.py`）

- `CORE_API_VERSION` 1.0.0 → **1.1.0**（纯 additive；次版本兼容规则不变，
  全部 1.0.x 扩展继续兼容）。新增 `V2_FEATURE_API_FLOOR = (1, 1, 0)`：
  使用 V2 特性（`execution` / `model_providers` / 依赖版本约束）的
  manifest 必须 `api_version >= 1.1.0`，跨字段校验 fail closed——旧
  api_version 携带 V2 字段得到精准的结构性拒绝，不是 unknown field。
- manifest 新增可选节（`MANIFEST_SCHEMA_VERSION` 保持 1，文档格式不变）：
  - `execution`：`{mode: in_process|worker, startup_timeout_s(默认 10,
    ≤120), call_timeout_s(默认 30, ≤3600), max_memory_mb(默认 512,
    32..8192), max_cpu_seconds(默认 60, ≤86400), max_output_bytes(默认
    1MiB, ≤64MiB)}`——预算是宿主强制的上界，硬上限防病态声明；
  - `model_providers`：`[{id, description, capabilities ⊆ {streaming,
    cancellation, batch}, credentials_ref?}]`；`model_provider` 从
    `RESERVED_FUTURE_TYPES` 移入 `EXTENSION_TYPES`；
  - `dependencies[].version`：约束串语法 `">=1.2,<2.0"`（操作符
    `>= < <= == != >`，逗号 AND；叶子实现在 `version_constraints.py`，
    纯函数、零依赖、与 PEP 440 无关）。
- worker 模式的 manifest 级结构约束（解析期 typed 拒绝）：仅支持
  `tools` + `model_providers` 声明节（类实例投影必须 in-process）；禁止
  `external_process` 权限（隔离进程内无子进程面）；model provider 不得
  声明 `streaming` 能力（单帧 RPC 投递不了事件流）。

### Wave 2/3 — worker 隔离执行（`worker/` 包 + `host.py`）

- 声明 `execution.mode=worker` 的扩展在子进程中运行：
  `python -m app.extensions_platform.worker.server --pack-dir ...`；
  **宿主进程永不 import 扩展代码**。
- 传输：stdin/stdout 上的行分帧 JSON RPC（协议版本 `"1.0"`，帧上限
  68 MiB 双侧强制）。不监听任何 socket——worker 的唯一 I/O 通道就是
  与宿主的管道。
- 握手 fail closed：协议版本 / extension_id / namespace / name /
  worker 模式 / api 兼容 / 内容指纹逐一核对；指纹不符 →
  `PACKAGE_TAMPERED` 隔离路径。激活在 worker 内执行（与 in-process 同一
  loader 规则），工具注册只被**收集**，可序列化的注册 kwargs 随
  `handshake_ok` 交回宿主，由宿主投影为 proxy 工具（经 ProjectionLedger，
  失败逆序回滚）。
- worker 工具必须声明显式 `parameters` JSON schema——`args_model` 是类型
  对象，无法跨进程传递（typed `WORKER_MODE_INVALID`）。
- 崩溃语义：crash / 超时 → typed `WORKER_CRASHED` / `WORKER_CALL_TIMEOUT`
  → 投影回滚 → 状态 COMPATIBLE（可重新激活）；连续崩溃达
  `EXTENSIONS_MAX_WORKER_CRASHES`（默认 2）→ `QUARANTINED`（运维介入）。
  in-flight 调用期间 deactivate → typed `OPERATION_IN_FLIGHT`。
- spawn 环境净化：子进程只拿 `PATH / PYTHONPATH / LANG / HOME /
  PYTHONHASHSEED`——不继承宿主环境变量面（无 ambient secrets；由
  `test_worker_integration.py::test_worker_env_is_sanitized` 钉死）。

### Wave 4 — 能力代理（`broker.py`）

worker 扩展的一切宿主能力经 **default-deny capability broker** 在宿主
进程内执行并审计；worker 侧门面 `ctx.broker.http_request /
read_artifact / write_artifact / get_secret`。操作面（v1）：

| op | 授权 | 额外闸门 | 上限 |
| --- | --- | --- | --- |
| `network_request` | `network` 授权 | `EXTENSION_NETWORK_ALLOW`（`"id:host1,host2;id2:*"`，按扩展 id）+ 权威 SSRF gate `DataFabricSecurity.validate_url`（私网/环回/元数据 IP 即使 `"*"` 也拒绝）+ 方法限 GET/HEAD/POST | 响应 4 MiB 截断（带 truncated 旗标） |
| `artifact_read` / `artifact_write` | `project_artifact_read` / `project_artifact_write` | 路径必须落在 `EXTENSION_ARTIFACT_ROOTS` 内（空 = 全拒绝） | 单文件 32 MiB |
| `secret_get` | **供给即授权**（`EXTENSION_SECRETS_JSON` = `{ext_id: {ref: value}}`） | ref 未供给 → typed 拒绝 | 值永不进入审计/状态/日志 |

权限词表保持 9 词冻结（secrets 走供给，不新增权限词 → 无主版本提升）。
审计为每扩展 256 条的有界环，经 `host.broker_audit()` 消费。诚实边界：
**in-process 扩展不经过 broker**（trusted-code 语义不变）；broker 约束
的是 worker 的宿主通道，不是任意系统调用。

### Wave 5 — 资源强制（`worker/spawn.py`）

- `RLIMIT_AS`（= max_memory_mb）与 `RLIMIT_CPU`（= max_cpu_seconds）由
  **worker 进程自身在入口处施加**（先 limit 后加载 pack）。不用
  `preexec_fn`——多线程宿主里 fork+preexec_fn 不安全；子进程自施与
  our-code-first 语义等价。
- 平台不支持 rlimit → typed `RESOURCE_LIMIT_UNAVAILABLE` warning 诊断
  （不虚假承诺沙箱能力）；墙钟超时 + `killpg` 整组兜底。
- 输出上限：worker 序列化工具结果时按 `execution.max_output_bytes`
  强制，超限 → `OUTPUT_LIMIT_EXCEEDED` typed 错误结果（不杀 worker）。

### Wave 6 — 内容签名（`signing.py`）

- `sign_pack()` 写 `signature.json`：`{algorithm: "hmac-sha256", key_id,
  fingerprint, signature, signed_at}`；HMAC-SHA256 载荷 =
  域分隔前缀 `webgis-extension-signature-v1` + key_id + 内容指纹。
  指纹计算排除 `signature.json`（防循环依赖）；`signed_at` 纯信息性，
  永不参与验签（验签确定性、可重放）。
- 验签裁决封闭词表：`signed_verified / signed_untrusted / invalid /
  tampered / missing`。tampered / invalid → **QUARANTINED（即使
  allowlist 点名也不放行）**——既有隔离状态机保证永不 import。
- 信任提权：`EXTENSION_TRUSTED_PUBLISERS`（`"key_id:keyfile,..."`）+
  `EXTENSIONS_TRUST_SIGNED=true` → 验签通过把 `local_untrusted` 提为
  `trusted_extension`（只升不降，info 留痕）。
  `EXTENSIONS_ALLOW_UNSIGNED_DEV=true` → 仅大声告警。
- 诚实定位：HMAC 是**共享密钥认证**（publisher ≈ operator 模型），
  非来源证明；非对称签名等 crypto 依赖落地后的 follow-up。

### Wave 7 — SBOM（`sbom.py`）

`build_sbom()` 对单个包产出**确定性**清单（同包同指纹 → 逐字节相同的
JSON）：文件清单（path/bytes/sha256，与指纹同覆盖面）、`python_imports`
（AST 顶层导入，排除标准库与 `app`）、`dependencies`（含版本约束原串）、
`secret_scan`（AWS key / 私钥块 / Slack / GitHub / OpenAI 风格 token 的
形状命中，只报事实不做网络验证）。CLI `sbom <id>`（`--json`）。

### Wave 8 — 依赖解析器（`resolver.py` / `version_constraints.py`）

- validate 期约束校验：required 依赖不满足约束 → `DEPENDENCY_MISSING`
  error → INCOMPATIBLE；optional → warning → degraded。
- 确定性激活序：Kahn 拓扑 + id 字典序 tie-break，成为
  `host.activate_all` 排序的唯一事实源。
- 升级预检 `check_upgrade_conflicts`：新版本破坏任何依赖者的约束 →
  `DEPENDENCY_CONFLICT`，升级被拒、旧版继续运行。
- 降级闸：reload / upgrade 遇版本回归必须显式
  `allow_downgrade=True`。**回滚 = 运维恢复旧 pack 目录 +
  `reload(allow_downgrade=True)`**——宿主不保存版本副本，不虚假承诺
  自动回滚。

### Wave 9 — 投影变化 → 权威视图刷新（`refresh.py` + host 钩子）

`host.set_projection_change_hook()` 在每次投影提交（activate /
deactivate / rollback / failed）后触发；`app/main.py` lifespan 将其接到
`make_projection_refresher(registry)`：刷新 `list_available_tools` args
枚举 → 重编译 runtime manifest + 缓存替换 → strict 校验（启动后降级为
warning，不把运维操作变成 RuntimeError）。**消除 V1 known limitation
「post-startup deactivate 不重编译 runtime manifest」**——对 in-process
与 worker 扩展同样生效，包括 worker 崩溃自动停用的路径。

### Wave 10 — model_provider 扩展类型（`sdk/model.py` + host 调用面）

- 定位（诚实）：GIS **域推理模型**（分割/检测/分类等服务）的扩展接入
  面。LLM chat transport 由 ADR-0102 的 `ModelDescriptorRegistry`
  配置驱动封闭面管理，扩展**不得也无法**接入。
- 投影：`ModelProviderSpec` → 类型化 invoke 工具 `<ns>_<pid>_invoke`
  进入 `ToolRegistry`（真实 agent dispatch 路径）+ `host.
  invoke_model_provider()`（in-process `stream=True` 返回事件迭代器，
  协作式取消 = 提前 close；worker = 单帧聚合，streaming typed 拒绝）+
  `host.model_provider_inventory()`（派生自 manifest，无第二事实源）。
- 凭据：in-process 经 `ctx.get_secret`，worker 经 broker `secret_get`；
  值永不进入结果/日志/LLM 可见面。
- SDK 扩展能力协议（可选 mixin）：`StreamingVectorProvider`
  （`stream_features(query, page_size)`）、`TileProvider`
  （`get_tile(z,x,y)` → `TilePayload`）、`RasterWindowProvider`
  （`get_raster_window(bbox, crs, w, h)`）；探测经
  `extended_provider_capabilities()`。核心
  `GeospatialDataSourceAdapter` ABC 保持 7 个 sync 方法不变（Data
  Control Plane 所有）；data_fabric 对 mixin 的分发接入是明确 follow-up。
- 示例包：`extensions/examples/extdemo-ml-pack`（确定性离线词频模型，
  演示流式事件 + credentials_ref；认证套件全绿）。

### Wave 11 — 认证 harness（`certification.py` + CLI `certify`）

确定性检查套件（固定顺序、无时间戳、无随机源）：manifest 契约诊断、
api 兼容、依赖约束、签名状态、SBOM secret 扫描、执行模式、lifecycle
smoke（真实 activate → health → deactivate；ACTIVE 记录只做 health，
不扰动运维状态）、deactivate-clean。`certified` = 无 fail 级检查
（warning 如实呈现不阻断）。CLI `certify <id>`（`--json`，exit 0/1）。

### 一致性语料库

`v2_contract` 族新增 20 个确定性 case（execution 接受/拒绝矩阵、预算
越界、model_provider 地板门控、worker+streaming 拒绝、依赖约束门控）；
语料库现共 2032 个 case（`test_conformance_corpus.py` 执行 2034 个测试
= 2032 case + 2 个结构元测试），case id 保持字节稳定、离线、确定性。
注意两处词表事实变化：`INCOMPATIBLE_API_VERSIONS` 不再含 `1.1.0`；
`FUTURE_EXTENSION_TYPES` 演示矩阵不再含 `model_provider`。

## Consequences

- V1 documented limitations 中两项消除：`model_provider` 类型落地；
  post-startup 投影变化立即刷新权威运行时视图（in-process 与 worker 一致）。
- 安全边界诚实分层：in-process 扩展仍是 trusted-code（与 V1 相同）；
  worker 模式提供 **进程隔离 + 能力代理 + 资源强制**——不是内核沙箱，
  worker 代码仍以服务用户身份运行，任意 `os.*` 调用不被平台拦截，只有
  SDK/broker 通道被门控。完整威胁模型见
  [security-boundary.md](../extension-platform/security-boundary.md)。
- 新增诊断码全部 append-only：`worker_mode_invalid`,
  `worker_protocol_mismatch`, `worker_startup_timeout`,
  `worker_call_timeout`, `worker_crashed`, `worker_restart_quarantined`,
  `worker_result_invalid`, `broker_denied`, `output_limit_exceeded`,
  `resource_limit_unavailable`, `signature_invalid`,
  `publisher_untrusted`, `package_tampered`, `signature_verified`(info),
  `dependency_constraint_invalid`, `dependency_conflict`,
  `operation_in_flight`。
- CLI 新增 `package` / `verify` / `sbom` / `certify` 四个子命令（全部
  支持 `--json`，绝不打印密钥/凭据材料；测试直接驱动 `main()`，不经
  subprocess）。既有命令的只读契约不变。
- 配置面新增（全部缺省关闭/为空，未配置时行为与 V1 完全一致）：
  `EXTENSION_SECRETS_JSON`, `EXTENSION_NETWORK_ALLOW`,
  `EXTENSION_ARTIFACT_ROOTS`, `EXTENSION_TRUSTED_PUBLISHERS`,
  `EXTENSIONS_TRUST_SIGNED`, `EXTENSIONS_ALLOW_UNSIGNED_DEV`,
  `EXTENSIONS_MAX_WORKER_CRASHES`。

## Non-goals

内核级沙箱（syscall 过滤 / seccomp / 容器边界）；marketplace 与分发
服务；非对称签名；data_fabric 对流式 mixin 的分发；worker 内类实例投影
与非工具投影；LLM chat transport 扩展接入；分布式宿主协议；vault 集成。
全部列入 follow-up，见下。

## Follow-ups

1. marketplace / 分发与更新通道（签名已就位，缺服务与信任根）。
2. 非对称签名（ed25519 等）——等 crypto 依赖落地。
3. data_fabric 分发接入 `StreamingVectorProvider` / `TileProvider` /
   `RasterWindowProvider` mixin（接口已定型）。
4. worker 内非工具投影（受单帧 RPC 与类实例语义限制，需协议扩展）。
5. LLM transport 扩展类型（需与 ADR-0102 封闭面重新协调，当前明确不做）。
6. 分布式宿主协议（多进程共享激活/registry 状态）。

## Verification

- `pytest tests/unit/extensions_platform/ -q --no-cov`（2372 passed，
  含 2032-case 语料库与真实子进程集成：`test_worker_integration.py` /
  `test_resource_limits.py` / `test_broker.py` e2e /
  `test_model_provider.py` worker 路径）
- `ruff check app/ tests/`（相对 master 零新增告警）
- `EXTENSIONS_BUILTIN_IDS=extdemoml.model python -m
  app.extensions_platform certify extdemoml.model --root
  extensions/examples` exit 0（示例包认证全绿）
- worker 崩溃→回滚→隔离、env 净化、SSRF 拒绝（`"*"` allowlist 亦然）、
  降级闸、刷新钩子等关键不变式均有专属测试钉死（文件名见
  [testing.md](../extension-platform/testing.md)）
