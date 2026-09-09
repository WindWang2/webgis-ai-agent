# GIS Extension Platform V2 — 隔离执行 · 权限代理 · 供应链 · 纵向深化

## Problem / Motivation

V1（ADR-0104）把扩展平台落成了「可信进程内 Python 扩展 + manifest/lifecycle/SDK」，但把一批边界如实记进了 known limitations：trusted-code boundary 而非任何隔离、无 `model_provider` 类型、provider ABC 无 streaming/tile 面、post-startup deactivate 不重编译 runtime manifest、health 无界、无签名/来源/ SBOM、依赖无版本约束。本 PR 按 ADR-0105 把这些边界逐条推进为可验证的实现，而不是另起第二平台。

## Current-state audit（Phase A）

- V1 平台 ~5.6k LOC（`app/extensions_platform/`），唯一 app 侧集成点是 FastAPI lifespan；无路由/前端/DB；2014 案例一致性语料库钉死契约。
- 权威 registry 五类投影 + ProjectionLedger 逆序回滚；runtime manifest 模块级单例缓存、启动后无刷新钩子；`model_provider` 在类型/权限词表均预留；内容指纹（tamper detection）但无签名。
- 结论：纵向深化，零重复造轮子。证据见 `.agent-work/extensions-v2/00-baseline.md`。

## Architecture

- **worker 隔离执行**（`worker/` 包）：`execution.mode=worker` 的扩展在独立子进程运行（行分帧 JSON RPC over stdio，协议版本 1.0，帧上限），主进程不 import 其代码。握手 fail-closed（协议/id/指纹/worker 模式/api 兼容）；崩溃 → typed 回滚 → COMPATIBLE，连续崩溃 → QUARANTINED；deactivate 有 in-flight 闸。
- **Capability broker**（默认 deny）：worker 的 network/artifact/secrets 全部显式跨 RPC 由宿主代执行；出网 = 授权 + host allowlist + 既有 SSRF gate；文件限 artifact 根内；secrets 供给即授权（权限词表保持 9 词冻结）；有界审计环。
- **资源强制**：RLIMIT_AS/CPU 由 worker 入口自施（规避多线程宿主 preexec_fn）；输出帧按 `execution.max_output_bytes` typed 截停；OS 不支持 → typed 降级告警（墙钟 kill 兜底）。
- **签名/供应链**：HMAC-SHA256 内容签名 + 发布者策略（`EXTENSION_TRUSTED_PUBLISHERS`/`EXTENSIONS_TRUST_SIGNED`）；篡改/无效签名 → quarantine（即使被 allowlist 点名）；确定性 SBOM（文件清单/imports/依赖/secret 扫描）。
- **依赖解析**：版本约束（`>=1.2,<2.0` 语法，自研零依赖）；不满足 fail-closed INCOMPATIBLE；激活序收敛 resolver 单一事实源；升级预检冲突拒绝；降级闸（`allow_downgrade` 显式回滚）。
- **Lifecycle V2**：`on_projection_change` 钩子 → lifespan 接线刷新 tools args 枚举 + 重编译 runtime manifest（消除 V1 stale-manifest limitation；worker 崩溃自动停用同样触发）。
- **`model_provider` 类型**：GIS 域推理模型（非 LLM chat transport——ADR-0102 封闭面不动）；投影为类型化 invoke 工具（agent 真实派发路径）+ `invoke_model_provider`（in-process 流式/协作取消；worker 单帧、streaming typed 拒绝）+ 声明派生的 inventory（无第二事实源）。
- **SDK V2 provider 协议**：可选 mixin（streaming vector / tiles / raster window）；核心 data_fabric ABC 七方法不动，分发接入列为 follow-up（接口边界归 Data Control Plane）。
- **认证 harness + CLI**：`package/verify/sbom/certify` 四命令；corpus 新增 `v2_contract` 族（2014→2032 案例案例，byte-stable）。

诚实边界（docs/extension-platform/security-boundary.md）：worker 是进程隔离 + 能力代理 + 资源强制，**不是内核级 sandbox**——worker 代码仍以服务用户身份运行；in-process 扩展保持 V1 trusted-code 语义；文档与 CLI 全程不使用 sandbox 宣传语。

## Key code paths

`app/extensions_platform/{worker/{protocol,context,server,client,spawn},broker,signing,sbom,resolver,version_constraints,refresh,certification,loader}.py`、`sdk/model.py`、`host.py`（worker 分支/信任集成/钩子/upgrade）、`manifest.py`（V2 字段+门控）、`api_version.py`（1.1.0）、`settings_bridge.py`、`app/main.py`（lifespan 钩子接线，~10 行）、`app/core/config.py`（V2 配置，默认全关）。

## Data / persistence / API / UI

- 无新表、无 alembic、无新 HTTP 路由（OpenAPI 快照 byte-identical）；全部状态仍在权威 registry + 进程内。
- manifest 新增可选节（schema_version 保持 1）：`execution` / `model_providers` / `dependencies[].version`；V2 特性要求 `api_version >= 1.1.0`（CORE_API_VERSION 1.0.0→1.1.0 additive，1.0.x 扩展全兼容）。
- 新配置全部默认关闭/为空 = V1 行为字节级不变（有钉死测试）。
- 无 UI 变更。

## Security implications

- worker 扩展不再默认 import 主进程；crash/超时不带崩宿主；permissions default deny 落到 broker 执行层；篡改可检测（签名+指纹双通道）并强制隔离；secrets 不进入状态/日志/LLM 可见面，且 Round-2 封闭了 `.env`/settings 读取通道（worker 侧 Settings 短路 env_file + cwd 移出 repo root + 最小 env）。
- 两轮独立 review 的全部 CRITICAL/MAJOR/MINOR 已修复（详见 `.agent-work/extensions-v2/05-review-findings.md`）。

## Performance baseline / results

- worker 冷启动（spawn+握手+激活）P50 ≈ 209ms（预算 <300ms）；调用往返 1KB payload p50 0.07ms / p95 0.19ms。
- in-process 路径默认零新增开销（扩展关闭时启动路径零成本，懒加载）。
- 结构性预算：帧上限 68MiB、输出上限默认 1MiB、broker 响应 4MiB / artifact 32MiB 有界读取、审计环 256、corpus 全量 <6s。

## Local test matrix（本任务不等待/不依赖线上 CI/CD，以下为本地实测）

| 层 | 结果 |
|---|---|
| 扩展域全量（`tests/unit/extensions_platform/` + pi hardening，含 2032 案例 corpus、真实子进程集成） | **2397 passed** |
| 全仓 ruff（与 CI lint 同面） | All checks passed |
| 契约层（tool meta / subagent isolation / ci gates） | 39 passed |
| OpenAPI 快照 | byte-identical ✓ |
| broader unit 回归（除 perf/cartography/real_services） | 6107 passed, 13 skipped（config 变更后复跑待本 PR 附注） |
| 负路径/隔离 | broker deny 矩阵、SSRF 字面 IP、篡改 quarantine、env 消毒、`.env` 通道封闭、崩溃隔离、超时 kill、in-flight 拒绝 — 全 typed ✓ |

## Review rounds

- Round 1（架构/正确性）：2 CRITICAL（stderr 无界阻塞挂起宿主线程；invoke wrapper 丢弃扁平 kwargs）+ 2 MAJOR + 6 MINOR → 全修复 + 8 回归测试。
- Round 2（性能/安全/UX）：1 CRITICAL（worker 经 settings/.env 读走全部宿主 secrets，实测证实）+ 2 MAJOR（broker 无界读×2）+ 7 MINOR/NOTE → 全修复 + 7 回归测试。
- 明细：`.agent-work/extensions-v2/05-review-findings.md`。

## Rebase / integration verification

`origin/master` @ `445ad30e` 无新提交，rebase no-op；rebase 后复跑扩展域 2397 绿 + 契约层 39 绿 + 全仓 ruff 绿 + OpenAPI 快照一致。

## Backward compatibility

默认配置下 V1 行为字节级不变（签名检查缺省静默、约束检查对无约束声明零诊断、扩展关闭零启动成本）；1.0.x 扩展 manifest 全部继续解析兼容。

## Known limitations（如实保留，详见 docs/extension-platform/limitations.md）

- worker 隔离不是内核级 sandbox（worker 代码仍可发起系统调用）；in-process 扩展仍是 trusted-code。
- in-process health 检查仍 sync 无界（worker 路径已带超时）；in-process deactivate 代码仍留 sys.modules。
- 类实例投影（algorithm/provider/cartography/recipe）仍须 in-process；worker model provider 不支持 streaming；broker 出网 allowlist 是 host 粒度（DNS rebinding 防护同核心 = pre-connect）。
- 签名为 HMAC 共享密钥模型（发布者≈操作者）；回滚需运维恢复旧 pack 目录；无 marketplace/分发服务。

## Follow-up candidates（超出本 Epic）

- 非对称签名（待 crypto 依赖落地）；marketplace/分发网络；分布式 host 协议。
- data_fabric 对 streaming/tile/raster-window mixin 的分发接入（归 Data Control Plane）。
- LLM chat transport 扩展化（ADR-0102 Model Registry 阶段）；非工具型投影的 worker 化；扩展粒度 artifact 根命名空间。
