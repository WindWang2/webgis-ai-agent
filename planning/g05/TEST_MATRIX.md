# G05 TEST_MATRIX

运行口径（RECON §4）：一律定向 lane + 显式超时，规避 #1753 全量挂起。
- 本 lane：`.venv/bin/pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 --timeout-method=thread -q`
- Lint：`.venv/bin/ruff check tests/acceptance/gis_agent scripts/g05_task_acceptance.py`
- 契约钉：`tests/test_ci_local_gate_contract.py` 不得因 G05 变红

## 场景家族 × 断言面矩阵（W2 起填充实测结果）

| # | family | 正/负 | intent | plan/method | data | MapSpec | render/export | final answer | 多轮 | 恢复/取消 | 注入 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F01 | 学校分布点图 | 正 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – | ☐ |
| F02 | 点图 vs 热力（方法选择） | 正+负 | ✓ | ✓ | – | ✓ | ✓ | ✓ | – | – | method | ☐ |
| F03 | 行政区统计 | 正 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – | ☐ |
| F04 | 定量密度（每平方公里） | 正+负 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | unit | ☐ |
| F05 | 时间变化 | 正 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – | ☐ |
| F06 | 遥感栅格指数 | 正+负 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | nodata | ☐ |
| F07 | 两数据空间连接 | 正 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – | ☐ |
| F08 | 没有经纬度（拒绝/澄清） | 负+澄清 | ✓ | ✓ | ✓ | – | ✓(拒绝) | ✓ | ✓ | – | – | ☐ |
| F09 | 样本不足（科学资格） | 负 | ✓ | ✓ | ✓ | – | ✓(降级) | ✓ | – | – | sample | ☐ |
| F10 | nodata/单位冲突 | 负 | ✓ | – | ✓ | – | ✓ | ✓ | – | – | unit/nodata | ☐ |
| F11 | 错误 CRS | 负 | – | – | ✓ | – | ✓(拒绝) | ✓ | – | – | crs | ☐ |
| F12 | 用户改图层/色带保护（user-wins） | 正 | ✓ | – | – | ✓ | ✓ | ✓ | ✓ | – | – | ☐ |
| F13 | 跨会话续绘与导出不一致 | 正+负 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ | revision | ☐ |
| F14 | 长任务取消/恢复 | 正+负 | – | – | – | ✓ | ✓ | ✓ | – | ✓ | cancel | ☐ |
| F15 | 权限拒绝 | 负 | ✓ | – | ✓ | – | ✓(拒绝) | ✓ | – | – | authz | ☐ |
| F16 | 导出能力不支持 | 负 | – | – | – | – | ✓(unsupported) | ✓ | – | – | – | ☐ |
| F17 | stale render / 重复 SSE | 负 | – | – | – | ✓ | ✓ | – | – | ✓ | stale | ☐ |
| F18 | 错误数据集选择归因 | 负 | ✓ | – | ✓ | ✓ | – | – | ✓ | – | dataset | ☐ |

## 测试文件 × 断言强度

- test_contracts.py：schema 校验负例必须红；序列化 roundtrip 确定性（同输入两次 hash 相等）
- test_corpus.py：≥20 场景、≥12 family、≥6 负例；负例"行为反转"抽样（把 expect_fail 场景的注入移除后断言不再红——防恒绿）
- test_task_e2e.py：每个正例断言链含：dispatch receipt 存在、MapSpec 层角色/可见性、goal_satisfaction 终态、导出 manifest verify_status
- test_task_failures.py：注入→红 + taxonomy 归因到预期 family + first_failing_stage 正确
- test_multiturn / test_recovery：见家族 F12/F13/F14

## 已知既有失败隔离

- 全量后端 suite：#1753 挂起——不跑全量，只跑本 lane + 契约钉文件
- 本 lane 若挂：对照 `origin/master` 同命令基线（master 上无本目录，天然绿）
