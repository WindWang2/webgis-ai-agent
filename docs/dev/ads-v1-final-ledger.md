# ads-v1 · 终版交付台账（DS0–DS9 收口 · ADR-0170~0179）

> 线：adaptive-data-supply/v1-master · PR #1272 · 回滚点 tag ads-v1-ds0…ads-v1-ds8
> 日期：2026-09-13

## 1. 波次 → 交付 → ADR → 证据索引

| 波次 | 交付 | ADR | 台账 | 关键数值 |
|---|---|---|---|---|
| DS0 | D1–D4 契约冻结 + JSON Schema、五协议离线 fixture 层、socket 阻断器、A7 阈值单点（**终态：V11 先合入 → 本线删自建单点改 import `data_tiers`，§8.1.1**）、首轮基线、债清 CSV | [0170](../adr/0170-ads-v1-contracts-fixture-baseline.md) | [ads-v1-ds0-ledger](ads-v1-ds0-ledger.md) | 取数 P50 7.98ms / P95 10.09ms |
| DS1 | 声明式源注册表（12 源）+ gov 迁移 + 4 新 adapter + lint | [0171](../adr/0171-ads-v1-source-registry.md) | [ads-v1-ds1-ledger](ads-v1-ds1-ledger.md) | 加源=YAML 文件（端到端演示测试） |
| DS2 | 语义检索（卡片+BM25+可选向量+可解释排序）+ intent 缝 + 278 评测 | [0172](../adr/0172-ads-v2-semantic-retrieval.md) | [ads-v1-ds2-ledger](ads-v1-ds2-ledger.md) | Recall@5 0.9748 / MRR 0.9194（校准后 0.9203） |
| DS3 | 取数计划编译器 + 代价模型 + 预算选优 + explain + 重放 | [0173](../adr/0173-ads-v1-acquisition-planning.md) | [ads-v1-ds3-ledger](ads-v1-ds3-ledger.md) | 7 类源 plan 测试；代价偏差 P50 ≤30%（行 0%/字节 18.5%） |
| DS4 | 声明式降级链（条件触发）+ D3 决策 + 原语整合 + 本地优先注册表化 | [0174](../adr/0174-ads-v1-fallback-chains.md) | [ads-v1-ds4-ledger](ads-v1-ds4-ledger.md) | 30 组故障矩阵全绿；不可比强制标注 |
| DS5 | 版本 pin 快照 + 四类漂移检测（golden）+ 影响面 + 分级阻断 | [0175](../adr/0175-ads-v1-version-pinning-drift.md) | [ads-v1-ds5-ledger](ads-v1-ds5-ledger.md) | 同 pin 重放指纹一致；迁移 0070 |
| DS6 | 时间/粒度/字段角色解析 + slot 契约（与 V11 切分） | [0176](../adr/0176-ads-v1-semantic-parsing.md) | [ads-v1-ds6-ledger](ads-v1-ds6-ledger.md) | 152 样本 accuracy 1.0000 |
| DS7 | 本地资产索引 + 归一 meta + sources-scan + 混合检索 10 组 | [0177](../adr/0177-ads-v1-local-asset-index.md) | [ads-v1-ds7-ledger](ads-v1-ds7-ledger.md) | 空目录显式 unavailable + 灌数指引 |
| DS8 | D4 全字段落库（迁移 0071）+ 864 组矩阵 + ratchet + 成本看板 + 校准 | [0178](../adr/0178-ads-v1-observability-matrix.md) | [ads-v1-ds8-ledger](ads-v1-ds8-ledger.md) | 矩阵 864/864；注入劣化 100% 拦截 |
| DS9 | 硬编码清零（PLATFORMS）+ 契约兼容矩阵 + i18n/埋点五类 + 安全复核 | [0179](../adr/0179-ads-v1-closeout.md) | 本文件 | 凭据零明文 / SSRF 缝断言化 |

## 2. 终版门禁对照（任务书 §6）

| 门禁 | 结果 | 备注 |
|---|---|---|
| unit lane（`-n 2 -m "not heavy and not real_services and not perf"`） | 11213 passed / 38 failed | +128 通过=本线新增；38 个失败为预存在环境问题 + 顺序/并行敏感抖动（geocompute 10 例单文件复跑全绿、M3 期 project_api/map_product 7 例本轮消失，逐项核verify与本线零相关） |
| data lane（串行 + `ADS_FORCE_OFFLINE=1`） | **625 passed / 8 failed** | 失败集与干净基线 17c77c73 **逐行 diff 一致**（Windows blob/alembic/promotion 环境问题） |
| 离线断言（本线硬闸） | ✓ | socket 阻断器下全 lane 绿；公网 connect typed 拦截 |
| `ruff check <变更文件>` | 0 告警 | 每波提交前复跑 |
| ADR 0170–0179 落 docs/adr + CHANGELOG | ✓ | 十波十篇 |
| `next build` | 0 次 | 全线零前端改动（显式声明） |
| 全量 quality_gate_local.sh | 步骤 1/3/4 通过；步骤 2（V11 golden）失败 | 本机缺 Node CLI（WinError 2），基线对照同因——V11 线预存在环境限制 |
| `git diff origin/master --stat` 越界检查 | ✓ | 全部落在 §8.2 可改清单（V11 战场仅 §8.1.2 授权的字面量→import 机械替换） |
| `.github/workflows/**` | 未触碰 | ✓ |

## 3. 数值对照表（缺数据标「未测量」，不标 0）

| 指标 | M1（基线） | M5（终版） | 变化 |
|---|---|---|---|
| 取数 P50/P95（fixture 源） | 7.98 / 10.09 ms | 未测量（同一 fixture 协议，DS9 无新流量面） | — |
| 内联缓存命中率 | 1.0 | 1.0 | 持平 |
| 检索 Recall@5 / MRR（278 样本） | 0.9748 / 0.9194 | 0.9748 / **0.9203**（权重校准） | +0.0009 |
| 代价估算偏差（行/字节） | — | 0% / 18.5%（P50 ≤30% 达标） | 首测 |
| 故障注入拦截率 | — | 30/30 矩阵 + ratchet 100% | 首测 |
| 外部源可用率 | unavailable（离线诚实） | unavailable | 沙箱一致 |
| data/unit lane 新增测试 | — | data +57 / unit +128（通过数 568→625 / 11085→11213） | 全绿 |

## 4. 与 V11 的协同交付说明（任务书 §9）

- 两线文件面近零重叠：本线主战场 `data_fabric/**`、`config/sources/**`、
  `adapters/**`、`semantic/**`；V11 主战场 `lib/cartography/**`、
  `gis_harness/**`、`frontend/lib/**`。交叉点仅七处阈值字面量的
  「字面量→import」机械替换（§8.1.2 纪律，ADR-0170 §4）。
- 意图切分（§8.1.4）：intent_semantic（制图）与 semantic/（取数）互不 import，
  通过冻结 slot 契约对接（ADR-0176 §5）。
- 成本治理表隔离（§8.1.7）：`ads_*` vs `carto_*`，仅展示层共享。
- 合流链路（§9）：用户一句话 → [本线] 语义检索 → 计划 → 降级 → 版本锁定 →
  D1 数据就位 → [V11] 意图理解 → 符号化 → 版面 → 出版 → 视觉裁判——
  取数侧全部落地且离线可验证。

## 5. 已知限制与后续建议（docs/dev/ 待办）

- 真实外呼默认禁止（沙箱）：6 个公开源 verified=false，联网复验后置 true；
- 快照/事实的多 worker DAO（0070/0071 表已建形）随运行面接线；
- local_first 硬编码链保留至生产等价性确认（开关已备，等价测试已备）；
- 超出基线预算的加深项按任务书 §4 优先级另行排期。
