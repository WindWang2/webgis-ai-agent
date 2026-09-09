# 04-test-matrix — 验证矩阵与结果

## 新增测试面（Quality V2）

| 层 | 文件 | 数量 | 覆盖 |
|---|---|---|---|
| 棘轮/行为闸 | tests/quality/test_findings_ratchet_gate.py | 13 | baseline 棘轮、waiver 过期、behavior 词表一致性 |
| 安全矩阵 | tests/quality/test_delete_ownership_matrix.py | 13 | SEC-KG-01 AST 契约 + SEC-KG-02 owner 11 案 |
| realtime 契约 | tests/quality/test_realtime_contract.py | 11 | WS/SSE 快照字节一致 + breaking/additive 分类器 |
| property/fuzz | tests/quality/test_property_geometry.py | 13 | parse_bbox/safe_parse 崩溃面 + 幂等 |
| property | tests/quality/test_property_mapspec.py | 4 | MapSpec 序列不变量（revision 单调/可序列化/类型化拒绝） |
| property | tests/quality/test_property_cache_key.py | 8 | 缓存键确定性/区分性/ref 禁缓存/owner 隔离 |
| fuzz 语料 | tests/quality/test_fuzz_parsers.py | 15 | corpus 14 样本逐条契约 |
| 差异存储 | tests/quality/test_storage_differential.py | 8+6skip | FK/NULL 排序/布尔/唯一/回滚/单 head（PG graceful skip） |
| chaos | tests/quality/test_chaos_storage.py | 2 | STORAGE_TRANSIENT_FAIL 显式拒绝/无半截提交 |
| 行为工具 | tests/unit/tools/test_behavioral_*.py | 30 | 30 个 TOOL_UNTESTED 真实 dispatch 三段式 |
| 生成物账本 | tests/quality/test_generated_artifact_graph.py | 5 | staleness + gitignore 回归闸 |
| 性能稳定化 | tests/fixtures/perf_budget.py + 5 文件迁移 | 6 处 | median/floor/factor + 结构比值 |

## 验证命令（本地一键）

```
python scripts/quality_runner.py quick        # 红线闸 + 漂移 + 生成物一致性（~1min）
python scripts/quality_runner.py changed      # git diff 受影响面 + 顺序轮换
python scripts/quality_runner.py full-local   # 全量本地验收（perf 隔离）
```

## 结果记录（wave 完成时点）

- W1 闸 25 passed；W3 行为测试 30 passed（+回归 129 passed）；W5 安全 44 passed；
- W6 realtime 11 passed；W7 property/fuzz 40 passed；W8 差异存储 8 passed/6 skip；
- W9 chaos 套件 41 passed；W10 quick lane PASS（54s）；W11 迁移文件 43 passed+1 skip；
- W12 账本闸 5 passed。
- Phase D 汇总：见 06-pr-summary。
