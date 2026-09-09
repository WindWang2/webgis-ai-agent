# 01 — Architecture（Phase B 冻结）

## 目标态组件图

```
app/main.py lifespan（唯一集成点，改动最小）
  └─ ExtensionHost (host.py)
       ├─ in_process 扩展：V1 路径不变（importlib + ExtensionContext 投影）
       ├─ worker 扩展：WorkerProcess (worker/client.py)
       │     ↕ framed JSON over stdin/stdout（protocol.py，协议版本化）
       │   worker server (worker/server.py，独立进程)
       │     ├─ 加载 pack 模块（指纹化模块名，同 V1 规则）
       │     ├─ WorkerContext（SDK 同形：注册声明收集 + broker stub + secrets stub）
       │     └─ 单线程事件循环：call / health / shutdown / broker_request 交织
       ├─ CapabilityBroker (broker.py，宿主侧唯一执行点)
       │     network(URL allowlist+SSRF gate) / artifact(root-confined) /
       │     secrets(按 id 供给) / model_invoke(需 model_provider 授权) / audit ring
       ├─ spawn 策略 (worker/spawn.py)：rlimit(CPU/AS/NPROC) + 新进程组 + 优雅降级
       ├─ resolver.py：跨扩展版本约束 → 确定性激活序 / 冲突 typed
       ├─ signing.py：HMAC-SHA256 内容签名 + 发布者策略 + 篡改→quarantine
       ├─ sbom.py：确定性 SBOM（文件清单+imports+依赖+secret 扫描）
       ├─ certification.py：确定性认证套件（CLI certify）
       └─ on_projection_change 钩子 → refresh tools args + 重编译 runtime manifest
```

## 权威数据 / 状态所有权（不变式）

- 权威 registry 不变：ToolRegistry / AlgorithmRegistry / AdapterRegistry /
  cartography registries / RecipeRegistry 仍是唯一事实源；扩展平台只经
  ExtensionContext 写入（V1 规则原样保留）。
- worker 模式下 worker 进程内**没有任何权威状态**：WorkerContext 收集声明，
  真正投影发生在宿主侧（proxy 注册进 ToolRegistry）。
- 无新增 DB 表 / 无新增 HTTP 路由 / 无第二 registry。ModelProviderCatalog 是
  平台内投影索引（status/CLI/broker 消费），不是第二事实源（工具投影仍是
  agent 可见的权威面）。
- runtime manifest 仍由 `compile_runtime_manifest` 唯一编译；V2 增加的是
  **投影变化后的重编译钩子**（消除 stale manifest），不是新编译器。

## 契约变更（全部 additive，manifest schema_version 保持 1）

- `CORE_API_VERSION` 1.0.0 → **1.1.0**（minor；1.x 扩展全部继续兼容）。
- manifest 新增可选节（仅 api_version>=1.1 的 manifest 可用，cross-field 校验
  产出 typed 错误）：
  - `execution`: `{mode: "in_process"|"worker", startup_timeout_s, call_timeout_s,
    max_memory_mb, max_cpu_seconds, max_output_bytes}`（mode 缺省 in_process）。
  - `model_providers`: `[{id, description, capabilities ⊆ {streaming,cancellation,batch},
    credentials_ref?}]`；`model_provider` 从 RESERVED_FUTURE_TYPES 移入
    EXTENSION_TYPES。
  - `dependencies[].version` / `optional_dependencies[].version`：约束串
    （`>=1.2,<2.0` 语法，自研确定性解析，零新依赖）。
- worker 模式约束（fail closed）：仅支持 tools(+model_providers→工具投影)+health；
  声明 algorithm/data_provider/cartography/workflow + worker → typed 错误；
  声明 external_process 权限 + worker → typed 错误（worker 无子进程面）。
- worker 工具必须显式 `parameters`（JSON schema dict；args_model 是类型对象
  不可跨进程）——worker 侧握手期校验。
- 权限词表**保持 9 词冻结**（secrets 走"供给即授权"，不加词、不升主版本）。

## 安全边界（诚实声明，写入文档与 CLI）

- in_process 扩展：仍是 trusted-code boundary（V1 语义，无变化，不宣称 sandbox）。
- worker 扩展：进程隔离（不共享解释器/内存）、默认无环境变量继承（最小 env）、
  新进程组（超时/失控可 killpg）、POSIX rlimit（CPU/AS；不可用时 typed 降级告警）、
  输出帧上限、全部 I/O 能力经 broker（默认 deny；网络 URL allowlist + 既有 SSRF
  gate；文件限 artifact 根内）。
- **不宣称**：不是内核级 sandbox；worker 内代码仍以服务用户身份运行，可发起
  系统调用。防御目标=故障隔离+能力最小化，不是敌意代码的强约束。
  威胁模型测试钉死默认 deny / 越权 typed / 篡改 quarantine。

## 签名 / 供应链

- `signature.json`（pack 内，指纹计算排除）：`{algorithm: "hmac-sha256",
  key_id, publisher, fingerprint, signature}`；HMAC over domain-separated
  `publisher || fingerprint`。诚实定位：共享密钥认证（操作者=签发者场景），
  非对称签名等 crypto 依赖落地后升级（follow-up）。
- 信任策略：`EXTENSION_TRUSTED_PUBLISHERS`（key_id→key file）+
  `EXTENSIONS_TRUST_SIGNED=true` → 验签通过可提权 trusted_extension；
  指纹不符/验签失败 → **quarantined**（即使被 allowlist 点名）；未签名 dev
  模式需 `EXTENSIONS_ALLOW_UNSIGNED_DEV=true` 且大声告警。

## 失败 / 取消 / 超时语义

- worker 启动：握手 deadline（startup_timeout_s）→ 超时 killpg + typed
  `WORKER_STARTUP_TIMEOUT`。
- 调用：call_timeout_s；超时 → killpg + typed `WORKER_CALL_TIMEOUT`。
- 崩溃：EOF/非零退出 → typed `WORKER_CRASHED`；连续 ≥EXTENSIONS_MAX_WORKER_CRASHES
  → quarantine + 投影回滚 + manifest 刷新。
- in-flight deactivate：调用进行中 → typed `OPERATION_IN_FLIGHT`（拒绝而非悬挂）。
- 取消：worker call 带预算，超时即 kill（无协作式取消通道——诚实、简单、可测）。

## 性能预算

- worker 冷启动 P50 < 300ms（空 pack，本地合成测试）；调用开销 P95 < 15ms
  （小 payload echo）；常驻 worker 无轮询（阻塞读）。
- in_process 路径零新增开销（默认 mode=in_process，V1 行为字节级不变）。
- 结构性预算：帧上限、声明条目上界（复用 128）、broker audit ring 上界（1024/扩展）。

## 测试 oracle

- 单元：新模块逐个（协议帧编解码、签名向量、SBOM 确定性、resolver 序）。
- 集成：真实子进程 worker pack（tmp 目录）激活→调用→health→crash→quarantine→
  重启→deactivate→unload 全生命周期。
- 契约：conformance corpus 扩展 V2 families（worker 拒绝组合、签名篡改、
  resolver 冲突、model_provider 接受/拒绝矩阵），byte-stable id。
- 负路径：默认 deny broker、SSRF 字面量 IP、越权 typed、篡改 quarantine、
  未签名 dev 告警。
- 资源：内存超限崩溃→typed；输出超限→typed；降级（无 rlimit 平台）告警路径
  用注入 fake 覆盖（不在 CI 造 OOM）。

## 回滚 / 兼容

- 默认零行为变化：EXTENSIONS_ENABLED=false；启用后无 worker/签名配置 → V1 语义。
- 所有新 config 缺省值 = 关闭/空；tests/conftest 基线补齐同值。
- in_process 扩展路径代码不动（只加钩子调用点）。
