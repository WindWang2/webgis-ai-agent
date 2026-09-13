# ADR-0179: ads-v1 横向收口（硬编码清零 / 契约兼容矩阵 / i18n / 安全复核）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS9（自适应数据供给与接入 · 并行线 P1 · 收口）
- 关联: ADR-0170~0178（本线全链）、任务书 §3-DS9/§6/§9

## 1. 决策一：死代码清理（按「确认零引用后删除」规则执行）

- **gov PLATFORMS 硬编码 → 清零**：DS1 迁移后唯一残留引用是其自身的过渡
  兜底；DS9 移除兜底（注册表失败 → 空清单 + WARNING，fail-loud 宁缺毋假）
  并将 `PLATFORMS = {}` 置空为兼容面，真实平台 URL 字面量零残留
  （可执行断言：`test_gov_platforms_hardcode_is_gone` grep 闸）。
- **local_first 硬编码链 → 保留**：任务规则「未确认前保留」——生产等价性
  未由运维确认，`registry_local_chain()`（ADS_LOCAL_FIRST_REGISTRY_DRIVEN）
  与硬编码缺省并存，等价性测试已备（DS4）。
- 明文 URL/清单残留复核：grep 闸纳入 closeout 测试。

## 2. 决策二：契约兼容矩阵（D1–D4 全覆盖）

`tests/unit/test_ads9_closeout.py::test_contract_upgrade_matrix`：四契约逐个
验证 v1 → v1.1 **加字段式演进**（未知可选字段可解析、可保留、可序列化），
与 ADR-0170 的冻结纪律构成完整闭环（改型须 ADR + 全消费方同步）。

## 3. 决策三：i18n 与埋点五类

- `user_messages.py`：降级换源（「数据来自 X（备用源）」）、不可比
  （「结果不可比…」）、版本 latest/pinned、本地资产未灌数、漂移分级、
  澄清、成本超限——八组双语模板为**唯一文案源**（代码不内联句子），
  zh 兜底、绝不空串；完整性有闸测试；
- 埋点五类（源选择/降级/版本/漂移/成本）→ D4 fact 字段映射断言
  （source_id / fallback+degraded+outcome / version / drift /
  rows+bytes+latency_ms + 成本告警文案）。

## 4. 决策四：安全复核（可执行断言化）

- **凭据零明文**：config/sources 全量 grep 闸（key 形似凭据而值非 `${ENV}`，
  即红）；注册表加载层同样拒绝；
- **SSRF**：stats_api 等 HTTP adapter 一律 `make_safe_session`（断言无裸
  requests 拨号）；本地路径一律 `resolve_safe_local_path`（三 local adapter
  断言）；
- **配额绕过**：配额声明进预算（DS8 budgets）+ lint 缺配额警告；
- **临时产物**：离线门禁产物限 docs/tests 目录；golden 校验快照与趋势 CSV
  不入库（.gitignore 既有规则沿用）。

## 5. 决策五：终版门禁

`docs/dev/ads-v1-final-ledger.md` 承载：全波次台账索引、门禁原文对照、
数值对照表、与 V11 协同交付说明（§9）。已知环境限制（本机 Node 缺失 →
V11 golden 步骤失败；8 个 data-lane / 35 个 unit-lane 既有失败经基线对照
确认与本线零差异）如实披露。

## 6. 后果

- 本线十波（DS0–DS9）交付完毕：任务书 §9 的两线合流链路
  （语义检索 → 计划 → 降级 → 版本锁定 → 数据就位 → V11 制图）在
  取数侧全部落地且离线可验证；
- PR #1272 为唯一交付车（M1–M5 同分支累积），里程碑 tag
  ads-v1-ds0…ads-v1-ds8 为回滚点。
