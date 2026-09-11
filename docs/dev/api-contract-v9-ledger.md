# V9 交付台账（foundation/api-contract-v9 / ADR-0138）

延续 AUDIT_REMEDIATION_REPORT.md 风格：任务 → 文件 → 测试 → 证据。

## P0 勘察

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 端点矩阵 dump | `scripts/api_contract_recon.py`（可重跑） | — | 控制台摘要 + `docs/dev/api-contract-matrix.csv`（220 端点） |
| 勘察报告 | `docs/dev/api-contract-recon.md` | — | 信封/分页分布、销项表、文档漂移实测、#1217 复核 |

实测 vs 任务书：220 端点（书 221）/ 123 无 rm（书 163）/ 65 内联模型（书 69，含
`_BaseModel` 别名扫描）。差异源于 master 演进（PR #123 已部分覆盖 chat 会话面）。

## P1 内联模型迁移（65 个 → 归零）

| 路由文件（迁出模型数） | schema 落点 | 迁移期测试 |
|---|---|---|
| auth.py(4) | `app/schemas/auth_schema.py` | tests/test_auth_routes.py, test_token_refresh.py 32✓ |
| chat.py(7+`_bounded_canvas`) | `app/schemas/chat_schema.py`（verbatim 含校验器） | tests/unit -k chat 56✓; test_chat_api 8✓; test_map_action_acks 37✓ |
| config.py(3) | `app/schemas/config_schema.py` | tests/unit -k config 41✓ |
| data_fabric.py(2) | `app/schemas/data_fabric_schema.py`（追加） | test_data_fabric_routes 7✓ |
| explorer.py(2) | `app/schemas/explorer_schema.py` | -k explorer ✓ |
| geocompute.py(6*) | `app/schemas/geocompute_schema.py`（verbatim） | tests/unit -k geocompute 550✓ |
| health.py(2, `_BaseModel` 别名) | `app/schemas/health_schema.py` | test_health_api + hardening 19✓ |
| knowledge.py(3) | `app/schemas/knowledge_schema.py` | tests/test_knowledge_api 7✓（MAX_CONTENT_LENGTH 保持模块属性） |
| layer.py(0) | `app/schemas/layer_schema.py`（新响应模型） | test_issue_666/670/mvt_whitelist 23✓ |
| map.py(2) | `app/schemas/map_schema.py` | test_export_diagnostics_sidecar 11✓ |
| mapspec_mutations.py(14+union) | `app/schemas/mapspec_mutation_schema.py`（verbatim） | -k mapspec_mut/mutation 58✓ |
| project.py(6) | `app/schemas/project_schema.py`（追加） | tests/unit -k project 211✓（1 失败为基线既有） |
| report.py(3) | `app/schemas/report_schema.py` | test_report_api + offload_426 16✓ |
| task.py(4) | `app/schemas/task_schema.py` | -k task 74✓ |
| templates.py(1) | `app/schemas/template_schema.py`（追加） | test_templates_api 18✓ |
| upload.py(3) | `app/schemas/upload_schema.py` | tests/unit -k task/upload ✓ |
| workflow_runtime.py(8) | `app/schemas/workflow_runtime_schema.py`（verbatim） | -k workflow_runtime 144✓（1 失败为迁移引入后修复，见 P2） |

*geocompute 含 `ClusterSubmitRequest`（`_BaseModel` 别名扫描补计）。

## P2 response_model 全覆盖

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 123 端点挂模 | 16+ 路由文件（签名/schema 行区） | `tests/unit/api_contract/test_response_model_coverage.py` | 204/220 挂模 + 16 豁免（`EXCLUSIONS`，含理由）+ 204 规则；`recon --skip-md` 实测 92%→终态豁免面外 100% |
| 每文件契约测试 | `tests/unit/api_contract/test_subsystem_schemas.py` | 19 模块参数化 | 导入/BaseModel/schema 生成/v1 Config 禁用 |
| 类型修正 | version/extensions_api int→str | — | 先修实现再挂模型（任务书要求） |

wire 形态保真：`response_model_exclude_none` 用于 map /export 与 chat map-action-ack
（历史「键缺席」语义保持，防 additive 字段污染旧客户端）。

## P3 错误信封统一

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| HTTPException/422 → 统一信封 | `app/core/exception.py`、`app/main.py` | `tests/unit/api_contract/test_error_envelope.py` 7✓ | 默认信封 / settings 开关 / 请求头覆盖 / 分类字段四类断言 |
| starlette 基类注册键修复 | `app/main.py` | schemathesis 发现 | malformed body 400 此前绕过统一 handler |
| 503/502/504 code 映射 | `app/core/exception.py` | schemathesis 发现 | 503 不再误标 SERVER_ERROR |
| transport.ts 双信封 | `frontend/lib/api/transport.ts`（+5/-1，≤30 红线） | `pnpm --dir frontend typecheck` ✓ | detail（旧）与 message（新）双读 |

## P4 分页

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| additive 补齐 | chat/sessions `has_more` | `tests/unit/api_contract/test_pagination_contract.py` 3✓ | clamp 边界 0/负/超限；Page 信封字段；chat meta |
| alias 弃用推迟 v2 | ADR-0138 D3 | — | 「接口不 breaking」优先；v2 为启用点 |

## P5 幂等

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 中间件 | `app/core/idempotency.py`、main.py 注册 | `tests/unit/api_contract/test_idempotency.py` 9✓ | 重放一致/并发单飞(5线程≤2次)/TTL 过期/Redis fail-open/无头零开销/非 JSON 绕过 |

## P6 文档生成

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 生成器 | `scripts/gen_api_docs.py` | `tests/test_api_docs_drift.py` 3✓ | 限流数值闸（240/60s）+ 三子系统章节在场闸 + 逐字比对 |
| 限流漂移修复 | `docs/api-docs.md:45` | 同上 | 60/min 误载修正为代码实际值 |

## P7 版本策略

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| /api/v2 挂载 | `app/api/v2.py`、main.py | `tests/unit/api_contract/test_api_v2.py` 5✓ | 59 v2 路径、operation_id 唯一、三子系统可达、v1 不受影响 |
| v1 弃用头 + 打点 | main.py `ApiVersionDeprecationMiddleware` | 同上 | Deprecation/Sunset/Link 头 + `webgis_api_version_requests_total` |

## P8 模糊与字段闸

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| Schemathesis shard | `tests/unit/api_contract/test_schemathesis.py` | 6✓（5 fuzz 参数化 + allowlist 防腐化） | max_examples=30；发现并修复 auth 401/403/429/400 未声明、503 误码、starlette 绕过 |
| 字段级契约闸 | `tests/unit/api_contract/test_field_contract.py`、`tests/quality/snapshots/api_contract_fields.json` | 1✓（更新模式 env 刷新） | #1217 后端半边：字段删除/改名/类型变化 fail + 类型化 diff |
| CI contract lane | `.github/workflows/contract.yml` | — | production.yml 零改动 |

## 台账外实现型修正（§8 例外条款）

1. `app/services/report_service.py` + `app/services/publication_export.py`：
   weasyprint 可选依赖守卫 `except ImportError` → `except (ImportError, OSError)`
   —— Windows 缺 GTK 时 cffi dlopen 抛 OSError，22 个测试文件不可收集；
   与 requirements.txt「缺席时运行时诚实降级」声明一致，Linux CI 行为不变。

## 基线既有失败甄别（非本线引入）

- `extensions_platform/` 11 项（rlimit/memory-cap/bwrap/multiprocess/stdout-close，
  Linux 机制在 Windows 本地不可用）：基线抽查同样失败（#1011 同类）。
- `gis/test_backend_sdk_v3`、`gis/test_scientific_contracts_vnext`（conformance
  文件存在性）、`gis_harness/test_chaos_invariants_v6`（正斜杠路径 exists）、
  `gis_harness/test_workflow_guards`（sys.path 找不到 `gen_workflow_catalog`）：
  Windows 环境类失败，基线同类（#1011）。
- `tests/unit/test_mapspec_store.py::test_validate_and_compile`：Node CLI 缺席
  （WinError 2），基线验证失败。
- 覆盖率 ≥75% 门禁：由 CI production.yml 主 lane `--cov-fail-under=75` 执行
  （pytest.ini 注释明确闸在 CI 命令行）。

## 与其他 v9 线协调点

- 路由文件改动限于签名/schema/response_model 行区；B 线 `Depends(...)` 行未触碰。
- D/E/F/H 前端 API 客户端：接口不 breaking（additive 字段 + `exclude_none` 保形；
  错误体变更由 LEGACY 开关兜底 + transport.ts 双信封兼容已交付）。
- TS 类型再生成（#1217 前端半边）未动手：§8 前端边界（仅 transport.ts ≤30 行）
  优先；字段闸已就位，前端镜像可独立跟进。
