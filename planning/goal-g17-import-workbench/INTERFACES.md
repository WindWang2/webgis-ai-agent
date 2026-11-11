# INTERFACES.md — G17 共享接线与文件所有权

## 新增文件（本任务独占）

- `app/services/data_import/`（`__init__.py` / `models.py` / `staging.py` / `discovery.py` / `interpretation.py` / `plan.py` / `preview.py` / `commit.py` / `service.py`）
- `app/schemas/data_import_schema.py`
- `app/api/routes/data_import.py`
- `app/tools/data_import_tools.py`
- `frontend/components/data-import/`（`import-workbench.tsx` + 子组件 + 测试）
- `frontend/lib/api/data-import.ts`（+测试）
- `frontend/messages/zh-CN/dataImport.json`、`frontend/messages/en-US/dataImport.json`
- `tests/data_import/`（定向测试族）
- `docs/`：格式支持矩阵 + 资源限制文档（交付时）
- planning/goal-g17-import-workbench/*（本账本）

## 共享文件最小 additive 接线（各一处，不重排不格式化）

| 文件 | 改动 | 兼容约定 |
|---|---|---|
| `app/main.py` | import + `include_router(data_import.router, prefix="/api/v1", tags=[...])`（:1102-1160 区域） | 纯新增 |
| `app/tools/__init__.py` | `_TOOL_MODULES` 追加 `("app.tools.data_import_tools", "register_data_import_tools")` | 纯新增一行 |
| `requirements.txt` | `openpyxl>=3.1.5`（生产 XLSX 读取；read_only 模式不执行公式） | additive；requirements.lock 用 pip-compile 同步或手工锁定既有版本（以 lock 现有解析为准） |
| `frontend/lib/i18n/messages.ts` | 登记 dataImport ns（zh/en 各一行 + 类型） | 纯新增 |
| `frontend/components/sidebar/project/dataset-manager.tsx` | 头部加一个"导入工作台"按钮 + 条件渲染 workbench（≤15 行） | #2032 冲突段：dataset-manager.tsx 顶部按钮区；不动主题/布局 |
| `app/services/data_parser.py` | **预期不改**；如需复用 `_atomic_write_json` 等原语，优先 import 私有函数不如复制小助手到本域（保持 parser 稳定）。仅当必须暴露既有白名单常量时做纯 import，不改行为 | 旧契约测试全部保持绿 |

## 消费的既有公共契约（只读）

- `parse` 守卫常量：`VECTOR_FORMATS/_VECTOR_DRIVER_ALLOWLIST/MAX_VECTOR_SIZE`（data_parser）
- `IngestPipeline.ingest(...)`、`IngestResult`
- `session_data_manager.store/get/delete_ref/get_ref_data`
- `register_artifact`、`list_artifacts`
- `verify_session_owner`（auth_history_bridge）、`authorize_session_write`（core/auth）
- `compute_payload_fingerprint`（pipeline）
- `CancellationToken/checkpoint/OperationCancelled`（lib/cancellation）
- `ProjectService.attach_dataset`（若提供项目目标，走既有 API 形态，不改）
- 前端：`apiFetch/fastGet`（transport.ts）、`useBoundedPoll`、`use-virtual-rows`、shared 原子组件

## 边界承诺

- 不新增第二套 agent loop / 工具注册表 / MapSpec store / 图层 ID / 撤销栈 / evidence store。
- 不修改 Pi vendor/RPC/bridge/extension。
- 不做 G18-G21、G09、#2022、#2032 范畴。
- 纯表登记用既有 `ref:table-` 前缀通道；artifact_type="table" 为 additive 字符串（register_artifact 自由串，无 CHECK 约束）。
