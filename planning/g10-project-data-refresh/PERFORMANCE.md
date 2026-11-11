# G10 · 增量刷新 / 全量重算对拍与性能报告

> 契约 UPD-07 / O12：**先证明结果等价，再谈更快**。更快但漏算不算优化；
> 不通过跳过未知依赖获得漂亮指标。

## 1. 验证方法

- 固定合成输入：2 个来源（`schools` 从 v1→v2，`population` 保持 `pop-2024`），
  学校点 5→6 个要素（+1，稳定 feature ID），行政边界/分母不变。
- 分析图 6 节点：`data:schools`、`data:population`、`analysis:heat`、
  `analysis:normalized`、`output:legend`、`output:map`。
- 差异判定走生产 `compute_source_diff`（`EXACT`，+1 要素），影响计划走生产
  `build_refresh_plan` / `compute_affected_subgraph`（非保守）。
- 节点输出 Oracle：`canonical_fingerprint({node, 数据闭包内来源版本})` ——
  闭包外节点的输出在新旧版本下**逐字段相同**，因此可证明复用安全。
- 全量 = 对所有节点重算；增量 = 只对 `recompute` 节点重算、其余复用基线输出。

## 2. 等价结论（前置条件）

| 项 | 值 |
|---|---|
| 声明容差 | **0（逐字段完全相同）** |
| 结果是否相同 | ✅ `identical = true` |
| 重算节点 | `analysis:heat`, `data:schools`, `output:map` |
| 复用节点 | `analysis:normalized`, `data:population`, `output:legend` |
| 未知证据场景 | ✅ 自动退化为保守全量（`recompute = 全部 6 节点`），不靠跳过未知依赖换指标 |

## 3. 前后对照（相同输入、相同进程、相同环境）

| 指标 | 全量重算 | 增量刷新 | 变化 |
|---|---|---|---|
| 工具/节点执行次数 | 6 | 3 | −50% |
| IO 字节（按每节点 512B 估算） | 3072 | 1536 | −50% |
| wall time（40 次循环合计） | 0.001738 s | 0.000924 s | −46.8% |
| max RSS（进程峰值） | 356896 KB | 356896 KB | 同进程测量，非节省量 |

## 4. 环境

| 项 | 值 |
|---|---|
| Python | 3.13.17 |
| 平台 | Linux 7.3.1-1-cachyos x86_64 glibc 2.44 |
| CPU | 16 |
| 测试命令 | `pytest tests/data/test_project_data_refresh_parity.py --no-cov -q -s` |
| 退出码 | 0（3 passed） |

## 5. 诚实披露（不得外推）

- ⚠️ wall time 是**纯计算微基准**（canonical fingerprint），不含真实 GIS 分析、
  Redis/DB IO、网络或渲染耗时；**不能**用作线上性能承诺。
- ⚠️ max RSS 是**进程峰值**（含 Python 运行时 + pytest + 应用导入），增量与全量
  在同一进程内测量，因此两列相同；本轮不宣称内存节省。
- ⚠️ IO 字节是按节点数的**估算**，不是内核级 IO 计数。
- ⚠️ 科学重算节点的实际执行由既有 Workflow Runtime 承担；本功能只产出权威
  重算计划与复用裁决，不新建第二执行器。
- ✅ 本轮结论只主张：**在固定合成输入下，增量计划与全量重算结果逐字段等价，
  且执行节点数/估算 IO 减半**；方法依赖域未知时自动转保守全量。
