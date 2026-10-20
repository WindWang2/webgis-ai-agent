# H02 BASELINE — 基线实证

## 环境

- worktree venv：Python 3.12.x（uv），依赖按 CI 同款 `pip install -c requirements.lock -r requirements-dev.txt httpx psycopg2-binary`
- 系统 Python 3.14.7 超项目声明范围（存量 #1731 登记），一律不用

## 基线测试结果（pristine origin/master 0415ad8d，未含 H02 改动）

| Lane | 命令 | 结果 | 退出码 |
|---|---|---|---|
| cartography（CI 同款） | `pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q` | 2259 passed, 19 skipped, 0 failed（172.62s） | 0 |

## 已知存量阻塞（不属 H02，引用披露）

- #1753：后端全量套件 ~80% 处确定性永久挂起（HEAD 同现）→ H02 只跑定向 lane。
- #1702：tests/unit/spatial_events/test_contracts.py 非包目录同名收集冲突（存量；f4ad732b 已加全仓结构守卫测试）。
- #1707：39 个 p4_test_*.py 不匹配 pytest 默认收集模式，静默不收集（存量）。
- #1754：requirements.lock 漂移门常红（存量）。
- 前端基线：待 worktree pnpm install 后跑 typecheck/lint/test 记录于此（Wave 3 前）。
