# TEST_MATRIX — 验证矩阵（执行时填实际结果）

| 面 | 命令 | 关联里程碑 | 结果（待填） |
|---|---|---|---|
| 提案引擎单测 | `pytest tests/data/test_field_proposal_v1.py -q --no-cov` | M3 | |
| 空间收据单测 | `pytest tests/data/test_spatial_receipt_v1.py -q --no-cov` | M3 | |
| 计划/执行单测 | `pytest tests/data/test_preparation_plan_v1.py -q --no-cov` | M4 | |
| 既有摄入/画像回归 | `pytest tests/data/test_ingest_pipeline.py tests/data/test_profile.py -q --no-cov` | M4 | |
| dataset_semantics 回归 | `pytest tests/unit/dataset_semantics -q --no-cov` | M5 | |
| API 面 + 负例 | `pytest tests/data/test_preparation_api_v1.py -q --no-cov` | M5 | |
| auth AST 闸 | `pytest tests/test_api_auth_required.py -q --no-cov` | M5 | |
| 快照闸（再生后） | `API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py -q --no-cov && API_CONTRACT_FIELDS_UPDATE=1 pytest tests/unit/api_contract/test_field_contract.py -q --no-cov` | M5 | |
| 工具分发真链 | `pytest tests/unit/test_data_preparation_tools_v1.py -q --no-cov` | M6 | |
| 既有工具面回归 | `pytest tests/unit/test_data_fabric_tools.py -q --no-cov`（如受影响） | M6 | |
| E2E 合成城市 | `pytest tests/data/test_preparation_e2e_v1.py -q --no-cov` | M8 | |
| 10 万行受控冒烟 | `pytest tests/data/test_preparation_scale_v1.py -q --no-cov -m heavy`（如归 heavy） | M8 | |
| 前端组件 | `pnpm test components/sidebar/data-preparation` | M7 | |
| 前端类型/lint | `pnpm typecheck && pnpm lint` | M7 | |
| ruff | `ruff check app/ tests/` | 每 commit | |
| mypy 白名单面 | `python -m mypy`（若新增文件落入 files 集） | M9 | |
| import 边界 | `python scripts/check_import_boundaries.py` | M9 | |
| endpoint scope matrix | `python scripts/generate_endpoint_scope_matrix.py --check` | M5 | |

负例清单（每项必须有断言）：跨会话 ref 404、伪造 owner_token 401/404、无 auth 401、AXIS_ORDER_SUSPECT 阻断自动执行、plan_hash 幂等重入、原件不可变（原 ref 内容 sha 前后一致）、llm_payload 不含行数据（长度上限断言）、discarded 计划不可执行、requires_input 不产出 ref。
