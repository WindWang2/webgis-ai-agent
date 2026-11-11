# TEST_MATRIX — G12-PI-NATIVE-INTEGRATION

所有证据记录：命令 + 退出码 + 范围 + pin/commit。`heavy` 车道遵守 `-p` 定向运行，不跑全量。

## 基线车道（每波后按改动面重跑）

| 车道 | 命令 | 门 |
|---|---|---|
| vendor 构建 | `bash scripts/build-vendor-pi.sh` | exit 0，bundle 存在 |
| doctor | `python scripts/pi_doctor.py --json` | 无 FAIL；rpc 域 live PASS |
| vendor 契约 | `python -m pytest tests/integration/test_pi_vendor_contract.py -m heavy -q` | 7/7 |
| schema golden | `python -m pytest tests/integration/test_pi_schema_coercion_golden.py -m heavy -q` | 全绿 |
| 动态面/ABI | `tests/test_pi_dynamic_surface.py`、`tests/test_pi_tool_spec*.py`、`tests/test_pi_surface_receipt*.py`（按实际文件名） | 全绿 |
| 扩展边界 | `tests/integration/test_pi_extension_control_boundary.py`、`test_pi_extension_hardening.py` | 全绿 |
| 取消/竞态 | `test_pi_cancellation_unified.py`、`test_runtime_chaos_pi.py`（按需） | 全绿 |
| 旅程 | `PI_JOURNEY_E2E=1 python -m pytest tests/integration/test_pi_stub_llm_journeys.py -m heavy -q` | VERDICT ok |
| lint/type | `ruff check app tests scripts`；mypy ratchet；前端 `pnpm typecheck && pnpm test`（触前端时） | 全绿 |

## 场景 → 测试映射（A1–A16）

| 场景 | 载体 | 类型 |
|---|---|---|
| A1 基础制图 | journeys J1（已有）+ dump v4 回归 | 真实 Pi+stub |
| A2 长尾发现 | 新 journey J8：tool_search 发现→直调（记录声明前后变化） | 真实 Pi+stub |
| A3 中文/$ref | vendor 契约新增测：中文 query 命中 $defs 属性描述；无关定义不误匹配 | 真实 rpc-entry |
| A4 只读组合 | J9：codemode 3 层摘要并行，父子 ID/结果引用/部分失败 | 真实 Pi+stub |
| A5 重放幂等 | 单测（executed-set/操作身份）+ J7 回归 | 单测+真实 |
| A6 撤权/过期 | 单测：discovered≠授权；fingerprint 不符拒绝；scope on 默认 | 单测+集成 |
| A7 非GIS触达 | 契约测 `test_no_builtin_coding_tools_reachable` 扩展 codemode 路径 + nested 不可达断言 | 真实 rpc-entry |
| A8 完全停止 | chaos/单测：clear_queue→abort，迟到 callback 不写；双 session 只取消目标 | 单测+chaos |
| A9 取消竞态 | 回归 `test_pi_cancellation_unified` + 新 stop→新请求用例 | 单测 |
| A10 配置变更 | 新测：双 stub provider 记录真实请求 model/endpoint/key 指纹；换代/失败回滚 | 集成（stub 进程） |
| A11 压缩 | 单测：compaction 钩子保留隐藏层/锁定引用；只改目标样式 | 集成 |
| A12 worker重启 | 单测：respawn 后重建块从权威源；不重放写 | 集成 |
| A13 会话隔离 | 回归 J3 + 重建块变体 | 真实 Pi+stub |
| A14 有序退出 | 单测：EOF+SIGTERM 窗口、工具执行中、无孤儿 | 单测 |
| A15 缺依赖 | 契约测（隔离包删 loader）+ doctor FAIL 断言 | 已有模式扩展 |
| A16 双重结束 | mapper 单测：重复 child end/agent_settled 只结算一次 | 单测 |

## 明确不做/如实标记

- 浏览器真实旅程：沙箱允许则跑 1 条（Playwright journey-real 模式本地等价）；不可行记 NOT_RUN。
- 旧/新 pin 基线对比：一次性（同 fixture 首轮 schema 字节、发现后字节、首工具耗时、取消耗时、RSS）。
- 不跑 3 万级全量 pytest；生产镜像构建本地无 daemon 时记 NOT_RUN（Wave 8 隔离包验证替代）。
