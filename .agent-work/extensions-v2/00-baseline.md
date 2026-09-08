# 00 — Baseline（只读审计结论，Phase A 冻结）

- 基线 commit：`origin/master` @ `445ad30e`（worktree `/home/kevin/projects/webgis/extensions-v2`，branch `feat/extensions-v2-isolated-runtime`）。
- 审计方式：Explore subagent 全仓扫描（1.75M tokens 报告）+ 主 agent 精读
  manifest/host/context/tool/provider/discovery/trust/api_version/permissions +
  main.py 生命周期 + model_runtime + ToolRegistry.register + tests/conftest。

## V1 事实图（证据）

| 事实 | 证据 |
|---|---|
| 平台唯一 app 侧集成点 = FastAPI lifespan | `app/main.py:73-117`（extensions → refresh_list_available_tools_args → compile_runtime_manifest） |
| 无 HTTP 路由 / 前端消费者 / DB 持久化 | `app/api/routes/` 无 extensions；grep 前端 0 命中；grep sqlalchemy 0 命中 |
| 投影面 5 类，全走 ExtensionContext 单一门面 + ProjectionLedger 逆序回滚 | `context.py:78-521`，`ledger.py` |
| runtime manifest 是模块级单例缓存，启动后无刷新钩子 | `runtime_manifest.py:264,509-522`；limitations.md:109-112 |
| model_provider 在类型词表与权限词表均预留给词，被 corpus 钉死拒绝 | `manifest.py:43`，`permissions.py:35`，`conformance.py:66` |
| provider ABC 恰 7 个 sync 方法，无 streaming/tile | `base_adapter.py`，limitations.md:40-47 |
| 信任 = trusted-code boundary（非 sandbox）；唯一硬控制是 EXTENSIONS_BLOCK | `trust.py`，limitations.md:16-27 |
| 完整性 = 内容指纹（sha256，排除 `__pycache__`/`*.pyc`），无签名/来源 | `discovery.py:67-108`，limitations.md:30-34,89-93 |
| health 检查 sync 无界 | `host.py:977-1008`，limitations.md:57-60 |
| deactivate 回滚投影但模块留 sys.modules；activate 后无 manifest 重编译 | `host.py:752-804`，limitations.md:62-66,109-112 |
| conformance corpus 2014 案例，byte-stable id，fail-closed 契约 | `conformance.py`，`test_conformance_corpus.py` |
| 测试环境钉 EXTENSIONS_ENABLED=false 等 10 个环境变量 | `tests/conftest.py:104-113` |
| 无 cryptography 依赖（python 环境无该模块） | `python -c "import cryptography"` 失败 |
| CORE_API_VERSION=1.0.0；manifest schema_version=1（extra=forbid） | `api_version.py:23-26`，`manifest.py:167` |
| ToolRegistry.register 接受 parameters/args_model/描述符 kwargs（unknown kwarg 拒绝） | `registry.py:459-521` |
| 模型侧 provider 架构：配置驱动封闭面（ModelDescriptorRegistry 严格拒绝未知来源） | `model_runtime/descriptors.py:1-13,118` |

## Known limitations → 本 Epic 对应

| # | limitation | V2 处理 |
|---|---|---|
| 1 | 无 model_provider 类型 | W1 契约 + W10 实现（工具投影生产路径） |
| 2 | 无 sandbox（trusted-code boundary） | W2-W5 worker 进程隔离 + broker + 资源强制 + 安全边界文档（不宣称完美 sandbox） |
| 3 | 扩展代码=受信代码 | worker 模式下不可信包不再 import 主进程 |
| 4 | 无 marketplace/签名/来源 | W6 签名 + 发布者策略 + W7 SBOM |
| 5 | provider ABC 无 streaming/tile | W10 SDK V2 协议（可选 mixin，核心 ABC 不动） |
| 6 | 单进程 host | worker 隔离单元为单扩展单进程（V2 范围；分布式仍 out of scope） |
| 7 | health 无界 | worker 模式 health 走带超时 RPC（in-process 行为不变，文档明示） |
| 8 | deactivate 留模块 | worker 模式 deactivate=进程退出（in-process 语义保持并文档化） |
| 9 | post-startup deactivate 不重编译 manifest | W9 on_projection_change 钩子（in-process 与 worker 一致） |

## Scope 冻结

- P0：worker 隔离执行（工具+health）、capability broker、资源强制、签名/篡改检测、
  lifecycle 刷新钩子、model_provider 类型、resolver 版本约束、认证 harness、CLI。
- P1：SBOM、SDK streaming/tile/raster mixin、trusted publisher 提权、upgrade/rollback。
- 明确不做（follow-up）：marketplace/分发网络、分布式 host 协议、非工具投影的 worker 化
  （algorithm/provider/cartography/recipe 类实例仍须 in-process）、LLM chat transport 扩展化
  （ADR-0102 封闭面）、data_fabric 对 streaming mixin 的分发接入（归 Data Control Plane）。
- 与并行 Epic 的边界：不改 data_fabric adapter ABC、不改 chat LLM transport、
  不动 alembic（无表）、不动 OpenAPI snapshot（无新路由）、generated catalogs 零接触
  （测试钉 EXTENSIONS_ENABLED=false 保持不变）。
