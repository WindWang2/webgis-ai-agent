# 0104. GIS Extension Platform V1 — Plugin / SDK / OGC Integration Ecosystem

**Date:** 2026-09-08
**Status:** Accepted
**Branch:** `feat/gis-extension-platform-v1`

## Context

Goal E 要求让内部与第三方开发者以统一、可验证、可版本化、可限制权限的
方式扩展 WebGIS AI Agent 的六类能力（tools / algorithms / data
providers / cartography / workflow recipes / 外部 GIS 服务），并把
OGC / STAC 一并纳入扩展体系。

Phase 0 调查（`.agent-work/extension-platform-v1/01-08`）确认的结构事实：

1. **权威 registry 唯一且形态各异**：`ToolRegistry`（执行唯一真相，
   43 字段描述符，同名 warn+overwrite）、`AlgorithmRegistry`（纯语义
   目录，重复 id raise）、`AdapterRegistry`（append-only + 别名 rebinding
   raise）、`RecipeRegistry`（keep-first）、cartography 六个单例目录
   （重复策略不一致：raise / 静默 / last-wins）。任何平行事实源都会
   立即漂移。
2. **没有任何第三方装载机制**：工具靠硬编码 `_TOOL_MODULES`，算法靠
   `_ALL_MODULES`，recipe 靠 `PACK_MODULES`，数据源靠编译期注册表；
   全仓无 entry-points / plugin discovery。
3. **安全边界清晰但分散**：tier-3 chokepoint、bridge secret + HMAC
   turn token、data_fabric 的 SSRF 层（requests 系）——但 aiohttp 与
   GDAL `/vsicurl` 路径不受 SSRF 约束，WMS 适配器伪造 `EPSG:3857`。
4. **Python in-process import 无法沙箱**：任何「untrusted-code
   sandbox」的宣称都是虚假的。

## Decisions

1. **投影而非平行（不制造第二事实源）。** 新包 `app/extensions_platform/`
   是一个生命周期宿主 + 投影层：扩展激活时经 `ExtensionContext` 把
   SDK spec 翻译为对既有权威 registry 的注册调用。六个 registry 各获得
   **additive** 的 `unregister`/投影入口方法（ToolRegistry、
   AlgorithmRegistry、AdapterRegistry、RecipeRegistry、
   ComponentRegistry、MapModelRegistry、CartographicThemeRegistry），
   由 `ProjectionLedger` 记账、逆序回放——卸载零僵尸条目、激活原子。
2. **GisExtensionManifest 是唯一扩展契约。** pydantic v2 `extra="forbid"`
   fail-closed；`schema_version` 高于宿主认知即拒绝；声明节
   （tools/algorithms/data_providers/cartography/workflow_packs）与
   activate() 实际注册强制对账：未声明注册 = error（回滚），声明未注册
   = warning（degraded，兼容 feature-flag 门控）。
3. **命名空间强制隔离。** id 必须 `<ns>.<name>`；投影名强制加前缀
   （工具 `<ns>_`、算法 `<ns>.`、source type `<ns>_`、cartography/recipe
   `<ns>_`）；保留命名空间表（core/webgis/app/pi/builtin/internal/gis/
   lib/tools/vendor）。核心条目物理上不可被遮蔽——碰撞 = typed error。
4. **权限：声明 != 授权。** 词表 9 项固定；`EXTENSION_PERMISSION_GRANTS`
   显式授权，缺省全无；工具执行前由 SDK 包裹层检查（typed
   `ExtensionPermissionDenied`，经 ToolRegistry 标准错误面呈现给 LLM）；
   子代理/嵌套上下文只能 `intersect` 收窄。工具 `required_permissions`
   ⊆ manifest 声明（继承规则）。
5. **信任：trusted-code boundary，诚实不宣称沙箱。** 五级
   （core/trusted_builtin/trusted_extension/local_untrusted/blocked）；
   manifest 的自声明无授权效力；blocklist > allowlist > builtin >
   local_untrusted；BLOCKED 在 import 之前隔离。扩展代码与核心同
   解释器同文件系统权限——文档与 CLI 均如实声明。
6. **版本三轴。** `CORE_API_VERSION`（宿主扩展接口）、manifest
   `api_version`（主版本相等 + 次版本 ≤ 宿主）、core 版本窗口
   `[minimum_core_version, maximum_core_version)`（上界排他）。
   `manifest_schema_version` 超前即拒绝。全部纯函数、确定性。
7. **生命周期。** discover→inspect/validate→resolve deps→load→register
   →activate→health→deactivate→unload→reload；状态
   discovered/compatible/incompatible/disabled/loading/active/degraded/
   failed/quarantined；依赖环三色 DFS 检测；发现有界（64 扩展、manifest
   256KiB、条目 ≤128）；内容指纹（sha256，排除 `__pycache__`）参与
   模块名——内容变更必然获得全新模块命名空间，杜绝陈旧模块。
8. **SDK 形态。** 五个声明式 spec（tool/algorithm/provider/cartography/
   workflow），SDK 做词表校验 + 宿主规则（tier≤2、destructive 禁用、
   network⇒network 权限、capabilities/algorithms 引用存在性）+ 生成与
   核心 register 完全同构的 kwargs。扩展只 import `app.extensions_platform.sdk`
   与自身依赖，拿不到核心 registry 对象。
9. **OGC/STAC 硬化为 core additive 修复**：WMS/WMTS describe 不再伪造
   CRS/bbox；GDAL `/vsicurl` href 过 SSRF 门；STAC API base 可配 +
   asset href SSRF 门（详见 docs/extension-platform/ogc-stac.md）。
10. **默认关闭。** `EXTENSIONS_ENABLED=false` 缺省；不配置 roots 时
    启动行为与旧版完全一致。激活发生在 lifespan 中 runtime manifest
    编译之前，扩展产物进入同一份 manifest 与 cross-registry 校验。

## Consequences

- 扩展不再依赖修改内部代码（`_TOOL_MODULES` 等硬编码表保持 core 专用）。
- 六个权威 registry 仍是唯一事实源；frozen seams（CapabilityRegistry、
  MapSpec、SessionPlan、ArtifactContract、ExecutionPlan）零修改。
- 一致性语料库 2014 个确定性 case（`test_conformance_corpus.py`）钉死
  契约：manifest 矩阵、权限矩阵、策略×manifest 评估、生命周期不变量。
- V1 明确不做：model_provider 扩展类型（词表保留、激活期 typed 拒绝）、
  任意代码沙箱、marketplace、recipe 路由权重外部化、tile/流式 provider
  API（ABC 未含，文档声明）。

## Non-goals

重写 Pi runtime；重构 GIS Harness 主状态机；大规模新增科学算法；
第二 ToolRegistry / algorithm registry / artifact registry / session
truth / map truth / durable job system；虚假 sandbox 宣称。

## Verification

- `pytest tests/unit/extensions_platform/ -q --no-cov`（2100+ tests，
  含 2014 case 语料库）
- `ruff check app/ tests/`（相对 master 零新增告警）
- registry parity / load-unload-reload 幂等由语料库与
  `test_sdk_projections.py` 双重钉死
