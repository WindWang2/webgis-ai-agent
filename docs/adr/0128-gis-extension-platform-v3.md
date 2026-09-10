# ADR 0128 — GIS Extension Platform V3 (Secure Ecosystem)

日期：2026-09-10
状态：Proposed（随 feat/extensions-v3-secure-ecosystem 分支交付）
前置：ADR-0104（V1）、ADR-0105（V2）

## 背景

V2 把扩展平台推进到「进程隔离 + 权限代理 + 供应链」，但把一批边界如实
写进了 limitations：HMAC 共享密钥（publisher ≈ operator）、无 key
rotation/revocation、无 marketplace/分发通道、worker 网络面只靠 SDK 纪律
（syscall 可直连）、四类投影无法 worker 化、流式不可用、data fabric 不
消费 provider mixin、无 drain/pin/分布式刷新。

## 决策

### D1 非对称签名与信任根（A）

- `cryptography` Ed25519（成熟库，禁自研 crypto）；`signature.json` v2
  载荷绑定 `(publisher, key_id, fingerprint)`。V2 HMAC v1 载荷逐字节
  冻结（改动 = 全部既有包变 tampered）。
- 信任根 = 运维维护 JSON（`EXTENSION_TRUST_STORE_PATH`）：publishers/
  keys（active|retired|revoked）+ revoked fingerprints/packages。
- **retired ≠ verified**：retired 密钥的签名是独立裁决 `signed_retired`
  ——数学有效但绝不提权（堵「泄露密钥 retire 绕过 revoke」的降级攻击）。
- 吊销（key/fingerprint/package(id,version)）先于验签，生死语义 =
  quarantine；registry revoke 写回 trust store 原子传播。

### D2 Registry / Marketplace（B）

- 服务端 registry = content-addressed blob + `state.json` 索引（包 ≤
  4096、每包 ≤ 256 版本）。POSIX 无原子 CAS-rename → **publish 全程持
  文件锁串行**（O_EXCL + 超时 + 陈旧锁接管），generation 单调计数仅作
  观测，不冒充 CAS。
- publish 全前置：digest 对账 + Ed25519 验签（active key）+ SBOM secret
  扫描 + publisher allowlist + **claim-once**（跨 publisher 同 id 拒绝 =
  dependency confusion 防线）。
- HTTP 面只读（search/detail/version/download；revoked → 410）；写路径
  仅运维 CLI——签名私钥天然 operator 面，不开带 auth 复杂度的 HTTP 写口。

### D3 分发（C）

- **统一 preflight**（install/upgrade/rollback 共用）：digest/验签/
  revocation/pin/downgrade/resolver 冲突。回滚不绕吊销。
- 安全解包：tar 成员白名单（仅 REGTYPE；拒 hardlink/symlink/device/
  FIFO/绝对路径/`..`）+ 前置预算（≤512 条目/8MiB）+ `filter="data"` 兜底。
- 原子换装固定序（两次 rename，窗口最小化）：active→versions/（唯一
  后缀名防撞名）→ staging→active；**启动恢复例程**（先于 TTL 清扫）
  完成被中断的第 2 步。已激活扩展的升级走 `host.upgrade()`（discover 对
  ACTIVE 记录只警告不换血）。

### D4 强隔离后端（D）

- `EXTENSIONS_ISOLATION_BACKEND = process | bubblewrap`（缺省 process =
  V2 行为不变）。bubblewrap：`--unshare-all`（**net**：socket 直连在 OS
  层不可达，broker 从纪律变强制）+ 最小 ro-bind 面 + tmpfs 可写 + rlimit
  照旧。
- **B-1 红线：repo 根不可见**。只 bind `<repo>/app`→`/opt/webgis/app`、
  解析后真实解释器 + site-packages、系统 lib（/lib64 等符号链接按解析源
  绑定字面路径）；`.env`/数据/夹具在沙箱视野外（恶意语料含沙箱内
  open(repo/.env) 失败断言）。
- **per-spawn bwrap 失败 = typed 激活失败**，绝不静默回退 process；
  探测只服务 status/CLI。诚实命名：namespace 级 OS 隔离，不宣称 kernel
  sandbox。

### D5 流式协议 V3（F）

- `WORKER_PROTOCOL_VERSION = "3.0"`，lockstep 严格相等（worker server 与
  宿主同仓 spawn，无版本偏差面，不做 offer/answer 协商）。
- 信用流控：host 初始授 `stream_window`；worker 无信用**阻塞至 credit/
  EOF**（背压本义是安全地慢，不设人为短超时）；宿主内存上界 ≈
  window × max_output_bytes（host 收帧处强制每帧尺寸，M-5）。
- 超时模型：流式调用逐帧 idle timeout（**不**入 worker 崩溃计数，C-3）；
  整流上界 = `execution.max_stream_events`。
- 每个阻塞读点的合法帧集合入协议文档（broker 等待循环 += credit/cancel，
  C-7）；迟到 credit 幂等入账。取消 = stream_cancel + 有界 drain。

### D6 worker 化投影（E）+ Data Fabric（G）

- api>=1.2 门控放开 worker + algorithms/data_providers/cartography/
  workflow_packs（四处同步：manifest 解析、worker context 纵深防御、
  host 对账集合、invoke 运行期协议门控）。
- 算法 = 可序列化描述符（元数据面；执行体 = worker 工具，
  `tool_candidates` 规约为宿主投影名）。
- 数据 provider = 工厂 + 动态代理类（按申报 mixin 真继承 →
  `extended_provider_capabilities` 可探测）；7 sync 方法逐个 RPC；
  **proxy 串行锁把 data_fabric 线程池并发排队**（绝不泄漏
  operation_in_flight，C-6）；返回值 host 侧重校验；worker 错误 →
  `DataFabricError` 子类映射表（保住熔断与 fetch-failed≠empty 语义）。
- cartography/recipe = payload dict/model_dump 经既有 ExtensionContext
  投影管线（零活对象跨进程）。
- `fabric_bridge`：能力感知分发（无 mixin → 诚实降级 sync query 或 typed
  UnsupportedSourceError）；`DataFabricManager.stream_catalog_item_features`
  为 additive 委托，既有路径逐字节不变。

### D7 LLM transport（H）——不实现

审计确认 ADR-0102 `ModelDescriptorRegistry` 为配置驱动封闭面，扩展接入
LLM chat transport 会破坏 Pi/core boundary（ADR-0105 non-goal 维持）。
V3 的 `model_provider` 仍是 GIS 域推理模型；worker streaming 经 D5 落地。

### D8 Lifecycle（I）

- `deactivate(drain=True, drain_timeout_s)`：worker in-flight 有界等待，
  超时 typed `DRAIN_TIMEOUT`；默认 False = V2 拒绝语义；in-process drain
  为显式 no-op。
- 版本 pin（`EXTENSION_VERSION_PIN`）在 activate/upgrade/install/rollback
  统一 preflight 消费。
- revoke 传播 = trust store 写回 + `.refresh` 信号（纯通知）+ 宿主
  `refresh_revocations()` 惰性复查（mtime 快路径；最坏暴露窗口在
  limitations 如实记录）。

### D9 认证（J）

certification 新增：signature（trust store/revocation/retired）、
package_layout（symlink/hardlink/设备/stat 元数据）、
resource_budget_declared、protocol_compat、provider_conformance。

## 兼容

- `CORE_API_VERSION` 1.1.0 → 1.2.0（additive）；1.0/1.1 manifest 全兼容
  （含 V2 平铺 artifact 语义、worker+streaming 1.1 仍拒绝）。
- 新配置全部缺省关闭/为空；未配置时 V2 行为逐字节不变（基线钉死测试）。
- marketplace HTTP 路由 additive，OpenAPI 快照显式刷新。
- 线协议 1.0 → 3.0：lockstep（同仓 spawn），无外部消费者。

## 性能预算

- 流式宿主内存 ≤ window × max_output_bytes；事件 ≤ max_stream_events；
  帧 68MiB 硬顶。
- registry：索引有界（4096×256）、分页确定性、下载流式 size cap。
- publish/验签成本 < 1s（小包实测 ~20ms 级）。
- 结构性基准：`tests/unit/extensions_platform/test_performance_v3.py`。

## 完成证明

`tests/unit/extensions_platform/test_v3_completion_proof.py`：
签名 → 发布 → 安装 → 隔离激活（bwrap 可用即真沙箱）→ broker deny 审计
→ 流式矢量 100 帧 → 流式 model provider → 升级 → 回滚 → 吊销版本不可
再装/回滚。全真实生产路径。
