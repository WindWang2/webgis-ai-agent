# AC-V11 W0 波次规划（契约重铸与债清）

> 波次:W0 · 预算 0.9 亿 · 前置:§0 启动规程全部完成 · ADR-0160

## 目标

把 V10 留下的结构性债清掉,为 W1–W9 铺平地面。本波不新增用户可见功能。

## 开工前事实基线（§0.2 产出）

- 首轮门基线:cartography 覆盖 48.55%(本机)/lane 794 passed/门禁覆盖步拦截(floor 50)。
- S1 债扫描:6 个新增孤儿模块、blocking 码位置修正、G3 第四套残壳(map-exporter/)。
- 详见 `ac-v11-review-memo.md` 与 `ac-v11-debt-scan.csv`。

## 任务分解与顺序

| 序 | 任务 | 关键决策 | 移交 |
|---|---|---|---|
| 1 | W0.2 label_typography 合并 | keep-upright 双语义并存(TS parity 冻结面不可单侧改) | W4 统一 |
| 2 | W0.4 defaults.py 单点 | 注册表词表/种子 payload 豁免;只收兜底缺省位 | W3 扩阈值清单 |
| 3 | W0.3 孤儿接线 | ts 缺失不判(打包部署);golden 自检内存合成;composition 契约先定 | W5 接线 |
| 4 | W0.1 C2 IR | 升级非重写(v1 publication 不动);同层 z 并列合法 | W6 三渲染器 |
| 5 | W0.5 门禁 --smoke | 冒烟不伪造全量结论;PR 证据必须完整形态 | — |
| 6 | W0.6 44 码契约矩阵 | 契约 JSON + 源码扫描双向对拍;not_evaluated 化解表 | W1/W2/W3/W7 |

## 风险预案(§8.2 对应)

- 双实现合并回归 → 行为零变化红线:golden corpus/既有测试全部不动而全绿才收。
- 覆盖率不足 → 新增契约测试抬升;floor 不调低。
- 契约冻结 → 44 码/IR schema/C1–C4 定稿后单波内不再变更。

## 完成定义

任务书 W0 验收 5 项全绿(见 `ac-v11-w0-ledger.md` 验收对照),milestone tag `ac-v11-w0`。
