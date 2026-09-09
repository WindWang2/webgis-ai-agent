# 01 — Architecture（Phase B 冻结，2026-09-10；ADR-0119；经 Subagent-A 架构挑战修订 v2）

## 目标态

```
Publisher ──sign(ed25519)──▶ Package(.tar.gz, digest, signature.json)
   → Registry（服务端，content-addressed，文件锁串行 publish，search/pagination，
     deprecation/revocation，publisher allowlist；读=HTTP API，写=运维 CLI）
   → Distribution（统一 preflight → 有界流式下载/解包 → staging → 原子换装（含恢复）
     → versions/ → 回滚同走 preflight）
   → Host（discover/verify(trust store) → activate/upgrade）
   → Isolation（process | bubblewrap：最小 bind 面，repo 根不可见）
   → Capability Broker（default deny；artifact 按扩展子命名空间，api 门控）
   → Projection（全部声明节 worker-capable；host 侧帧预算强制）
   → Lifecycle（preflight/drain/pin/revoke/刷新信号）
   → Certification V3 + 恶意语料 + Audit
```

## 权威数据 / 第二事实源禁令

- 五类权威 registry 不变；扩展平台只经 ExtensionContext/WorkerContext 投影。
- Registry store 是**包分发域**的权威：`state.json` 索引唯一事实源，写路径 =
  文件锁（`O_EXCL` lockfile + 超时 + 陈旧锁接管）→ temp+rename；generation 仅
  单调计数（**诚实：POSIX 无原子 CAS-rename，靠锁串行化，不用 rename 竞争做 CAS**）。
- blob 内容寻址 `objects/<sha256>`；GC = 持同一把锁、仅删「不在当前索引且 mtime >
  7 天」的 blob；下载 API 校验 digest 形状 `^[0-9a-f]{64}$`（防路径穿越）。
- 索引容量上界：包 ≤ 4096、每包版本 ≤ 256（超限 publish typed 拒绝——文档写明取舍）。
- 安装后 pack 目录是 host 发现权威；`versions/` 是分发层版本存储。
- 刷新信号 `<install_root>/.refresh`：**纯通知**（reason + 时间戳），host 以各自
  discover 为准；revocation 惰性检查挂在 refresh 信号 mtime + host.health() 周期面
  （最坏暴露窗口如实写入 limitations）。
- 无 DB 表、无 alembic。

## 非对称签名（A）与 trust store（revocation / rotation）

- 依赖：`cryptography>=44`（Ed25519）。**HMAC v1 载荷逐字节保留**（`signing.py:55-57`
  不动），v2 载荷 = `webgis-extension-signature-v2\n || publisher || key_id || fingerprint`。
- signature.json v2：`{algorithm: "ed25519", key_id, publisher, fingerprint, signature, signed_at}`。
- `trust_store.py`：publishers（多 key_id 并存 = rotation）、key state `active|retired|revoked`、
  revoked fingerprints、revoked packages `[{id, version?}]`。裁决序：
  revoked fingerprint/package → **typed 拒绝（quarantine）**；
  revoked key → quarantine；active key 验签通过 → signed_verified（可提权）；
  **retired key 验签通过 → 新 status `signed_retired`：不提权、warning、信任决策回落
  运维 allowlist（堵「retire 绕过 revoke 的降级攻击」）**；unknown publisher → signed_untrusted。
- HostPolicy 增加 `trust_store: Optional[TrustStore]`；无 trust store → V2 HMAC 语义逐字节不变。
- 认证语料新增：retired-key 签名包激活 → 不提权断言。

## Registry / Marketplace（B）

- `marketplace/`：`models.py`（typed 记录）、`store.py`（锁 + content-addressed blob + 分页）、
  `service.py`（publish 强制前置：digest 对账 + trust store 验签 + SBOM + publisher allowlist；
  search 确定性排序 `(id asc, version desc)`；deprecate/revoke/yank 幂等）、
  `api.py`（**只读** 4 路由：search/detail/versions/download；写路径仅 CLI——签名私钥
  天然 operator 面）。additive → OpenAPI 快照显式刷新。
- 并发 publish 语义：文件锁串行 → 无丢失更新；测试含真多进程 publish 竞争用例。

## Distribution（C）

- `distribution.py` `ExtensionInstaller`，**统一 preflight 函数**（install/upgrade/rollback 共用）：
  `preflight(record, target_version) = [digest 已验] + trust store 验签 + revocation 检查
  （revoked (id, version) 对 install/upgrade/**rollback** 一律 typed 拒绝）+ 版本 pin +
  resolver 依赖冲突 + downgrade 闸`。
- 安装步骤（中断安全）：
  1. 取 VersionRecord（本地 registry 目录或 `EXTENSION_REGISTRY_URLS` http(s)，URL 经
     `DataFabricSecurity.validate_url` + allowlist）；
  2. 有界流式下载（size cap）→ sha256 对账 → trust store 验签 blob 内 signature.json；
  3. 安全解包至 `.staging/<id>-<ver>-<digest[:12]>`：`tarfile` 逐成员白名单
     （仅 REGTYPE；**拒绝 hardlink/linkname/device/FIFO/symlink/绝对路径/`..`**）+
     前置流式预算统计（条目数 ≤ 512、总字节 ≤ 8MiB，与指纹上界一致）→ `extractall(filter="data")` 兜底；
  4. staging 指纹 == 签名指纹；
  5. preflight；
  6. **换装固定序**（两次 rename，窗口最小化 + 可恢复）：
     a. 若 active 存在：`rename(active → versions/<old_ver>)`，目标已存在则改用
        `versions/<old_ver>-<digest[:8]>` 唯一名（防版本来回升级撞名 ENOTEMPTY）；
     b. `rename(staging → active)`。
     **启动恢复例程（先于 staging 清扫执行）**：active 缺失且 `.staging/` 存在合法
     （manifest 可解析 + 验签通过）包 → 完成步骤 b；此后清扫只删「TTL（24h）过期」staging，
     保证崩溃窗口内新版本副本不被误删；
  7. 激活：目标**未激活** → `discover()+activate`；目标**已激活** → 必须走 `host.upgrade()`
     （`host.py:1446` 既有降级闸/冲突预检；C-1：discover 对 ACTIVE 记录只警告不换血，
     activate 幂等 no-op——直接 discover+activate 会静默留在旧版）；
  8. 激活失败 → 自动回滚上一版（回滚同走 preflight）。
- 回滚：`rollback(id, version=None)`；version 缺省 = versions/ 中 **semver 最大**者
  （不依赖 mtime）；preflight 通过才换装。
- 中断恢复测试：`os.rename` 故障注入（第 N 次抛 OSError）+ 恢复例程断言。

## 强隔离后端（D）

- `worker/isolation.py`：`EXTENSIONS_ISOLATION_BACKEND = process | bubblewrap`（默认 process）。
- **bubblewrap 最小 bind 面（B-1 修复：repo 根不可见）**：
  - 只读 bind：`<repo>/app` → `/opt/webgis/app`、venv `site-packages`、stdlib prefix
    （`sys.prefix`/`sys.base_prefix` 探测）、worker 需要的最小系统路径（`/usr`：动态链接器等，ro）；
  - **不 bind repo 根**：`.env`、数据、测试夹具在沙箱视野外；
  - `--tmpfs /tmp`（cwd 用沙箱内 tmpfs）、`--dev /dev --proc /proc`（pidns 内）、
    `--unshare-all`（**net**：socket 直连 OS 层不可达，broker 成为唯一出网通道）、
    `--die-with-parent --new-session`；
  - PYTHONPATH=/opt/webgis（指向沙箱内 app 挂载点）。
- 语义：探测（`shutil.which` + smoke）只服务 status/CLI 与**显式**降级决策；
  **per-spawn bwrap 失败 → typed 激活失败，绝不静默回退 process**（M-9）；
  effective backend 记录在 record/status。诚实命名：namespace 级 OS 隔离，不宣称 kernel sandbox。
- 恶意语料验收（有 bwrap 机器）：沙箱内 `open(repo_root/.env)` 必须失败；无 bwrap CI
  → skipped-unless-bwrap 车道 + typed 降级断言车道。
- Broker：词表保持 4 op；artifact 子命名空间 `<root>/<ext_id>/...` **仅对 manifest
  api>=1.2 的扩展生效**（1.1 扩展保持 V2 平铺语义，非隐性破坏，M-8）。

## Streaming Protocol V3（F）

- `WORKER_PROTOCOL_VERSION = "3.0"`，双侧严格相等（lockstep 发布：worker server 与 host
  同 repo、spawn 用 `sys.executable`+同仓模块，版本偏差不存在——**不做 offer/answer 协商**，
  N-3/M-1 从简；扩展面兼容由 api_version 门控，与线协议解耦）。
- 帧：`call{stream:true}` → `stream_start{id}` → `stream_frame{id,seq,more,payload}`* →
  `stream_end{id}` | `result{error}`；host→worker `stream_cancel{id}`、`stream_credit{id,n}`。
- **超时模型（C-3）**：流式调用不使用整 call deadline；逐帧 **idle timeout**（每帧重置，
  缺省 = call_timeout_s）；整流上界 = `execution.max_stream_events`（缺省 10000）+ 可选
  总时长预算；**STREAM_FLOW_CONTROL / 流超时不映射 `_on_worker_death` 崩溃计数**。
- **背压（M-6）**：worker 无 credit → 阻塞等待直至 credit/EOF（EOF = host 死，自然失败）；
  不设人为短超时；STREAM_FLOW_CONTROL 仅 host 显式 abort 慢消费时发生。
- **host 侧预算强制（M-5）**：收帧处按 `execution.max_output_bytes` 检查每帧序列化尺寸
  （超限 typed + cancel）；`stream=False` 聚合路径加总字节预算（超限 cancel + typed）。
  宿主内存上界 ≈ window × max_output_bytes（真实成立）。
- **读循环帧白名单（C-7）**：broker 等待循环合法帧 += {stream_credit, stream_cancel}；
  credit 等待循环合法帧 += {broker_response}；**迟到/多余 credit 幂等入账**（不报错）。
  协议文档列出每个阻塞读点的合法帧集合。
- 取消：host 发 cancel 后 drain 至 stream_end/EOF；worker 侧生成器提前 close。

## Worker 化投影（E）与 Data Fabric（G）

- 门控放宽 **checklist（M-2，四处同步）**（全部要求 api>=1.2）：
  1. `manifest.py` cross-field：worker + algorithms/data_providers/cartography/workflow_packs
     允许；worker + model_provider streaming 允许；
  2. `worker/context.py` 纵深防御 typed 拒绝同步放开（V3 形状：可序列化声明）；
  3. `host._activate_worker` 对账集合扩展（declared 集合含四节投影名）；
  4. `invoke_model_provider(stream=True)` 放行门 = **协商后协议 == 3.0**（运行期检查
     record.worker.protocol_version），非仅 manifest api。
- algorithm：`WorkerAlgorithmSpec`（可序列化）→ host proxy descriptor（run = RPC，结果过
  输出上限；可选 V3 流）。
- data provider（C-6/M-7 语义表）：
  - host 侧 `WorkerDataProviderAdapter(GeospatialDataSourceAdapter)` **动态继承握手申报的
    mixin**（`extended_provider_capabilities` 才能探测到）；
  - 7 方法逐个 RPC，**proxy 层请求队列化**（per-worker 内部锁 + 有界等待/超时），
    绝不向 fabric 消费端抛 `operation_in_flight`；`AdapterSpec.notes` 声明并发退化
    （worker 端点串行）；worker 侧 adapter 实例 = 每 source_type 单例，首用创建、随
    worker 存活；
  - sync()：基类实现走 `self.list_datasets/describe`（proxy 方法）→ host 侧完成目录
    注册，无 worker 内分叉（实现时按 `base_adapter.py` 实际代码核对）；
  - `capabilities_v2` 经握手声明暴露（explain 路径不静默退化）；
  - 返回值 host 侧逐方法 `model_validate` 重校验（DatasetDescriptor/QueryResult/Health）；
  - 错误映射表：worker `{code,message}` → `DataFabricError` 子类
    （UnsupportedSourceError/SourceUnreachableError/…），保住熔断与「fetch failed ≠ empty」语义；
  - streaming/tile/raster mixin 经 V3 流。
- cartography/recipe：声明即 JSON payload，握手回传，宿主走既有投影路径（零活对象跨进程）。
- **Fabric 接入（G）**：`fabric_bridge.py` extended dispatch helpers +
  `DataFabricManager.query_catalog_item_async` additive 分支（adapter 具备
  StreamingVectorProvider 且请求流式才走 mixin；否则既有路径逐字节不变）。

## Lifecycle（I）

- drain：`deactivate(id, drain=False, drain_timeout_s=10)`；worker 模式有界等待 in_flight
  清零（50ms poll）→ 超时 typed `DRAIN_TIMEOUT`；默认 False = V2 语义；in-process 扩展
  drain 为显式 no-op（诊断注明「drain 仅 worker 模式有效」，Mi-3）。
- 版本 pin：`EXTENSION_VERSION_PIN`（`"id==1.2.0;..."`）→ activate/upgrade/install/rollback
  预检统一消费。
- revoke 传播：registry revoke → `.refresh` 信号 → host 在 refresh 信号 mtime 变化 +
  health() 周期面惰性复查（最坏窗口写入 limitations，Mi-1）；已装且 revoked →
  deactivate + quarantine。
- rolling upgrade：preflight + drain + 原子换装 = 单宿主滚动升级；多宿主逐台 + 信号。

## Certification V3（J）与恶意语料

- 新检查：signature_trust（trust store + revocation + retired）、package_layout（symlink/
  hardlink/设备条目/`..`/stat 元数据断言，Mi-5）、resource_budget_declared、
  protocol_compat（声明流式但线协议 <3.0 → fail）、provider_conformance（7 方法 +
  streaming smoke）。
- 恶意语料 15+ 场景含：retired-key 提权、rollback 绕吊销、换装中断恢复、hardlink tar、
  bwrap repo-root 不可见（条件车道）。

## 兼容与发布

- `CORE_API_VERSION` 1.1.0 → **1.2.0**（additive）；1.0/1.1 扩展零行为变化（含 V2 平铺
  artifact 语义、HMAC v1 载荷逐字节不动）。
- 新配置缺省关闭：`EXTENSION_TRUST_STORE_PATH=""`、`EXTENSION_REGISTRY_DIR=""`、
  `EXTENSION_REGISTRY_URLS=""`、`EXTENSIONS_INSTALL_ROOT=""`、
  `EXTENSIONS_ISOLATION_BACKEND="process"`、`EXTENSION_VERSION_PIN=""`、
  `EXTENSIONS_KEEP_VERSIONS=3`、`EXTENSION_STREAM_WINDOW=16`、
  `EXTENSION_MAX_STREAM_EVENTS=10000`。
- `WORKER_PROTOCOL_VERSION` "1.0"→"3.0"（lockstep，同仓 spawn 无偏差面）。
- marketplace 只读路由 additive → 显式刷新 OpenAPI 快照。
- `cryptography>=44` 入 requirements.txt + pyproject。

## 性能预算（结构性优先）

- registry：索引有界（4096×256）；分页；下载流式 size cap；文件锁串行 publish。
- 流式：宿主内存 ≤ window × max_output_bytes；事件 ≤ max_stream_events；帧 68MiB 硬顶。
- worker 冷启动 budget 不变；bubblewrap 增量实测记录（预算 ≤ +150ms）。
- 验签 = 1×指纹 + 1×Ed25519 verify；install 全程 fingerprint 计算 ≤ 2 次。

## 测试 oracle（含架构挑战指出的盲区钉法）

- 换装中断：`os.rename` 故障注入 + 恢复例程断言（非 sleep 时序）。
- registry 并发 publish：真多进程（multiprocessing + 共享目录 fixture）。
- 流控竞争：**fake worker / fake host 脚本化确定性帧序列**钉协议状态机；真实子进程只做冒烟。
- bwrap：有 bwrap 机器的真沙箱断言车道（skipunless）+ typed 降级车道。
- 双进程 host：进程 A install → 进程 B refresh 信号消费的集成用例。
- 既有 2388 扩展域测试为回归底线。
