# 05-review-findings — 两轮独立 review 与修复

Reviewers 与主要实现思路独立（实现由主 agent + 2 个实现 subagent 完成；
review 由 2 个全新上下文的 reviewer 对 `origin/master...HEAD` 全量 diff
执行，Round 1 架构/正确性/回归，Round 2 安全/性能/可维护性）。

## Round 1（架构/正确性/回归）

| # | severity | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| R1-1 | **MAJOR** | tests/quality/test_delete_ownership_matrix.py:31-56 | SEC-KG-01 AST 扫描可被 `from app.services import artifact_registry` + 裸名调用绕过（reviewer AST 复现 offenders=[]） | ✅ 修复：ImportFrom alias 名检查 + Name 裸名匹配 + 动态 import 字符串探针；自验 5 种旁路形态全抓（含 `import ... as x` 别名） |
| R1-2 | MINOR | algorithm_registry.py:498-515 | tool_to_algorithms 未随 tool_to_capability 过滤 planned → 半截派生态 | ✅ 修复：反查索引同 filter（_is_analysis_capability） |
| R1-3 | MINOR | runtime_manifest.py:376-387 | 自建反查图未过滤 planned，与 AlgorithmRegistry 过滤版分歧 | ✅ 显式注释"描述性视图，禁止用于复用/回填判定"（无消费方，行为零改动） |
| R1-4 | MINOR | algorithms/platform.py:66-73 | 绑定算法硬编码 deterministic=True，与 geocoding/scenario_simulation/meta_tool_surface 的 capability deterministic=False 自相矛盾 | ✅ 修复：deterministic 随 capability 传入（3 个绑定改 False；seed policy "none"——词表无 "seeded"） |
| R1-5 | MINOR | api_compat.py:587-676 | realtime 分类器漏类：resume 锚丢失/envelope 与 wire 形状变化/primitives 新增 | ✅ 修复：sse_resume_anchor_dropped(breaking)、*_envelope_changed(breaking)、sse_primitive_added(additive)；快照再生成 |
| R1-6 | MINOR | api_compat.py:531-540 | auth 锚是子串探针（锁注释不锁代码） | ✅ 修复：verify_token/token_version/rate-limit 改 AST 调用与符号探针 |
| R1-7 | MINOR | manifest.py:463-477 | 畸形 baseline/waivers JSON → traceback 而非闸红 | ✅ 修复：捕获 JSONDecodeError → ValueError 带文件名与可操作信息 |
| R1-8 | MINOR | behavioral.py:33-53 | "只可能漏报"宣称过强（不校验接收者类型） | ✅ 文档修正：明确不精确处与保守下限语义 |
| R1-n | NIT×6 | api_compat 死缓存、realtime 测试恒假分支、delete matrix 死代码、conftest 非法 seed 静默、typing×2、artifact_graph 静默截断、staleness 时间戳、perf_budget 重复采样 | | ✅ 全部修复（截断改 raise；账本去时间戳；assert_within_budget 复用 measure_median） |

逐项结论（duplicate truth/抽象/race/persistence/取消泄漏/API compat/typing/mock-only/真实路径）：
9 项中 7 项 PASS，2 项带 MINOR 已修；无 BLOCKER/CRITICAL。

## Round 2（安全/性能/可维护性）

| # | severity | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| R2-1 | **MAJOR** | 同 R1-1 | 交叉确认同一旁路 | ✅ 同 R1-1 修复 |
| R2-2 | MINOR | test_delete_ownership_matrix.py:325 | knowledge delete 绑定 versioned 依赖无钉扎（换 unversioned 不红） | ✅ 新增 test_knowledge_delete_binds_versioned_auth_dependency（源级断言绑定 get_current_user_with_version） |
| R2-3 | MINOR | test_delete_ownership_matrix.py:86,133 | admin role 经 JWT 伪造路径不在矩阵（由 test_auth_bypass 承担） | 披露性，记录于本文件（不扩大战线） |
| R2-4 | MINOR | manifest.py render | MD 棘轮段不渲染 waived_current（豁免项在 MD 面不可见） | ✅ 修复：棘轮行追加被豁免计数与可见性说明 |
| R2-5 | MINOR | findings-baseline.json:16 | note 引用不存在的 ADR-0112 | ✅ 改 ADR-0118 |
| R2-6 | MINOR | ADR-0118 D2 | "92 个词表外工具"与交付 93 不一致 | ✅ 改 93 |
| R2-7 | MINOR | 03-progress.md | 停留在中间态 | ✅ 更新终态 |
| R2-n | NIT×2 | artifact_graph 静默截断、staleness 时间戳 | | ✅ 已修（见上） |

安全结论：11 案 owner 矩阵核心 IDOR 面钉住；chaos monkeypatch 全部
contextmanager 闭合、生产零引用扫描通过；fuzz/property 全部固定 seed 可重放。
性能结论：gen --check 实测 5.9s、staleness 0.075s、迁移面 43 测试 1.54s——
quick lane 增量可接受；perf_budget iterations ≤5 有界。UX：N/A（无前端改动）。
向后兼容：manifest v1→v2 纯扩展，消费方全兼容（有测试钉扎）。

## 总判定

两轮均为 FAIL → 修复全部 MAJOR/MINOR/NIT 后转 **PASS**。
修复后验证：tests/quality 全量 332 passed（含 7 skip / 4 xfail 既有）；
tests/unit/gis 279 passed；migrated perf 面 33 passed；ruff app/+tests/ 全绿。
