# G02 TEST_MATRIX

原则：本地针对性验证；全量套件有 #1753 存量挂起，禁止无超时全量跑；新失败必须对照
clean origin/master 基线区分存量/新增。

## 基线（改动前先跑，记录退出码）

| # | 命令 | 覆盖 | 基线结果 |
|---|---|---|---|
| B1 | 同左 | 既有科学契约 | 47P（改动前后一致） |
| B2 | resolver 相邻批：test_capability_registry_parity/test_algorithm_registry_phase2/test_gis_lib_registries/test_cost_model/test_reproducible_gis_runtime | resolver 既有行为 | 85P+26P（前后一致） |
| B3 | 同左 | resolver 事实缝 | 45P（前后一致） |
| B4 | 同左 | 冻结语料回归 | 2272P（W1 后） |
| B5 | ruff check 全部改动路径 | lint | All checks passed |

## 新增（G02）

| # | 文件 | 组 | 内容 | 通过判据 |
|---|---|---|---|---|
| N1 | tests/unit/gis/test_g02_contract_semantics.py | W1 | deferred 标志/pass_evidence_state/not_evaluated 通道/MethodUseContract 投影 | 全绿 |
| N2 | tests/unit/gis/test_g02_gate_chain.py | W2 | planner 转录修复→workflow_compiler transform 可见；执行 fail-closed；三段对账 | 全绿 |
| N3 | tests/unit/gis/test_g02_final_gate.py | W2 | final gate 五类 finding + 畸形输入有界 | 全绿 |
| N4 | tests/unit/gis/test_g02_science_oracles.py | W4 | ≥8 组守门 oracle（含负例，见 PLAN W4 清单） | 全绿 |
| N5 | tests/unit/gis/test_g02_performance.py | W4 | 237 算法 resolve+contract < 2s；无 IO 断言（monkeypatch open/builtins） | 全绿 |

## 回归面（每 wave 后）

- B1/B3/B5 必跑；B4 在 W1 后跑一次；B2 在 W1/W2 后各跑一次
- tools/registry 改动后：`python -m pytest tests/unit/tools/ -q -k "registry or dispatch"`（按实际目录名调整）

## 环境纪律

- pytest 串行（不 -n）；`NODE_OPTIONS` 不适用（本方向零前端改动）；每命令 timeout ≤ 600s
- ruff 逐文件点名，不跑全仓
