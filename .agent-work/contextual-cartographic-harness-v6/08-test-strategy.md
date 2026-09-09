# 08 — Test Strategy（Wave 16 及全程）

## 总原则（§36/§55/§56）

- 核心 Harness correctness 零 LLM 可重复；LLM/VLM 仅 optional evaluation lane。
- 本地验证为准，不以线上 CI 为门槛；控制并行（pytest workers / vitest --maxWorkers / node heap），机器：16C/62G，当前可用 ~39G。
- 每个 wave：unit + integration + commit；不积累到最后。

## 既有门（必须保持绿）

- `pytest -m cartography`（release-blocking；基线 708 passed/7 skipped）
- findings 棘轮（新 code 上限 0）、quality manifest 字节门、workflow catalog `--check`、BENCHMARK_MANIFEST 再生成对齐
- `-m perf`（结构预算回归）、`-m real_services`（自跳过）、`-m heavy`（pip-only 依赖）
- 前端：vitest（parity/contract）、tsc、eslint

## 三类新 corpus（§34）

### A. Semantic Workflow Corpus（intent → methodology → DAG）
- 落 `tests/unit/gis_harness/`（比照 `test_methodology_corpus_v4.py` 先例）：金标 query → 期望 family/方法资格/DAG 形状断言

### B. Runtime Failure Corpus（failure → classification → repair）
- 11 类 `HarnessFailureClass` × 注入点；断言 remediation 动作、预算、disclosure；`classify_and_remediate` 生产接入后回归

### C. Cartographic Closed-loop Corpus（≥100 场景，§35）
- 主题：学校分布、行政区统计、热点分析、人口公平性、插值、DEM 地形、水文、遥感指数、变化检测、SAR、网络可达性、选址、风险图、分类结果、时序对比、双变量地图、不确定性地图（17 主题 × 变体 ≥100）
- 每主题叠加故障：missing data、wrong CRS、empty result、tool timeout、stale artifact、missing source、render failure、style failure、chart failure、visual overlap、user edit mid-run、resume
- 形态：`tests/cartography/` 闭环（无 Node/Chromium/LLM，fake telemetry dict 注入比照 `test_render_telemetry_v5.py`）+ golden_corpus 扩展

## 关键 E2E（§57，全部确定性可重复）

1. NL 专题地图 → 分析 → MapSpec → (fake) telemetry → READY
2. CRS 错误 → typed failure → remediation → 重投影 → 继续
3. 改参数 → 只重算受影响子图
4. 仅改色带 → 科学零重跑
5. source 加载失败 → render finding → repair/retry → verified
6. chart data_points=0 → 不得 READY
7. 组件重叠 → deterministic finding → layout repair → re-render
8. 用户锁图层 → agent repair 不得修改（三路径）
9. 中断 → resume → 验证 artifact → 继续 DAG
10. 重复 repair 无进展 → NO_PROGRESS/REPAIR_EXHAUSTED → disclosure

## Visual Harness 测试（§37）

structural geometry tests / render scene semantic tests / deterministic synthetic layouts / golden finding corpus / optional image-diff（软门）/ optional VLM eval（软门）

## 性能验证（§58，synthetic structural benchmark）

500/1k/10k layers、large DAG、deep lineage、long chat、large registry、multiple findings；hard cap：workflow graph size、context bytes、retrieval candidates、finding count、repair depth、observation payload、trace size、visual snapshot size（§38）；重点防 nested scan/repeated stringify/full graph rebuild/full context serialization（§39）。
