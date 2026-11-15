# G38 REVIEW — 独立评审记录

## Round 1（reviewer：独立 subagent 槽 C，未参与实现；重跑全部测试 + 探针验证）

- 结论：**NO-GO** —— 1×P1 + 6×P2 + 8×P3；后端核心（键/空间语义、CAS、冻结执行、
  原子发布、取消收敛、受信回退鉴权）与测试体系判扎实（reviewer 亲跑 61 单测 +
  Pi 旅程 34.6s 全绿 + 前端 4 测过）。
- Reviewer 复跑证据：tests/unit/data_join_authoring 61 passed；Pi 旅程
  PI_JOURNEY_E2E=1 passed；tests/jobs + test_audit4_tools_meta 269 passed；
  迁移闸 28 passed / 1 既有；capability parity 基线对照（/tmp 干净 clone）
  确认 1 新增违规。探针在 /tmp/g38-review/。

### Findings 与修复

| ID | 级 | 问题 | 修复 |
|---|---|---|---|
| F1 | P1 | 空间模式下预览/执行按钮永久禁用（键字段门控不适用于空间模式；旗舰 B UI 不可达） | 按模式分派就绪判据（attribute=双键；spatial=双源 + pick_by_field 另需择一字段）；新增空间模式 vitest 回归 |
| F2 | P2 | 输出预算闸在匹配分支后 —— null_key/unmatched 透传行不计入（cap=10 时 301 行照样发布） | `_check_runtime_budget` 每Left行处理后强制（两路径）；回归 test_f2 |
| F3 | P2 | `data_join` 不在 DataObject kind 白名单 —— 每次 publish 必 failed（承诺的 CAS 阶段是死代码，收据恒 state=failed） | data_object.py 白名单加 `data_join`（data_delivery 先例）；回归断言 state=published + data_object_id |
| F4 | P2 | capability 无 algorithm —— 跨注册表校验新增致命违规 | algorithms/data_access.py 登记 `data.join.authoring`（conformance 指向本域三测试文件）；parity 仅剩 base 既有 platform.evidence_map_brief |
| F5 | P2 | 收据短路只匹配 0 世代键 —— 失效后每次重执行都新 run、终态目录无限累积（幂等永失） | `_find_completed_run_by_key` 按 base 前缀取最新 completed + verify 后复用；回归 test_f5（g1 收敛第三跑同 run） |
| F6 | P2 | 空间路径 `unmatched_right_rows` 恒 0（假零） | 跟踪被命中右序集合；`有效右行 - 命中`；回归 test_f6（孤立面 → 1） |
| F10 | P2 | recover_runs 无启动钩子 —— 崩溃后 run 行永久 running，活跃收敛把死 run 当活跃楔死该计划+源版本组合 | lifespan 启动收敛（sweep_staging + recover_runs，to_thread best-effort）；`_find_active_run_by_key` 复核 job 存活（终态 job 的残留 run 不算活跃） |
| F7 | P3 | 聚合计划行 schema 残缺（未匹配/空键行缺 match_count/聚合列） | 非 agg_source 行补 `match_count=0` + 空聚合列 + valid_count=0；回归 test_f7 |
| F8 | P3 | count(right_field) 数 null（COUNT(*) 语义）；valid_count 只对 sum/mean 披露 | count 显式字段=COUNT(col) 非空计数（无字段维持 COUNT(*)）；valid_count 全聚合披露；回归 test_f8 |
| F9 | P3 | KeyFieldSpec docstring 声称 transform 遇非字符串 typed 拒绝，实际跳过 | docstring 如实（键族语义优先于文本清洗） |
| F11 | P3 | (a)>10k 不预禁用上图；(b)聚合无 UI 编辑器；(c)joinFromHere 键未用；(d)预置 ref 残留旧检查卡 | (a) total_rows>10000 预禁用 + renderOverBudgetNote 披露；(c) 删未用键；(d) 预置时清 describe；(b) 后端语义完整（工具/REST 可达），UI 聚合编辑器登记为后续增强（诊断卡如实披露非 UI 可达项） |
| F12 | P3 | 台账登记失败时 output_ref 伪造 `ref:data-join/<id>`（不可解析） | 登记失败时不落 output_ref（文件本体 + manifest/receipt 仍可核验） |
| F13 | P3 | 受信回退 table_rows 锚不校验 rows 形态（FC 可静默当几何源） | expect_rows 强校验（G18 `_as_rows_payload` 同构）；回归 test_f13 |
| F14 | P3 | CAS 终检在 job 创建后 —— 计划窗口被改时留孤儿 job | run 行先落（CAS 终检在前），job 后建并回填 job_id；同世代并发竞态收敛时清理本 run 行 |

### 测试质量发现（同轮处置）

- 前端从未测空间模式（正是 P1 藏身处）→ 新增空间模式用例（禁用→启用→pick 门控）。
- toast mock 缺 `getState` → add-to-map 成功路径静默抛错 → hoist-safe 完整 store 形态
  mock（vitest 4 无 unhandled error）。
- 无 DataObject published 断言 → test_f3；幂等只测 0 世代 → test_f5；
  空间 unmatched_right 恒 0 未锚定 → test_f6。

### 修复后验证（全部重跑）

- 后端：tests/unit/data_join_authoring **68 passed**（61 + 7 评审回归）；
  capability parity 仅剩 base 既有项。
- Pi 旅程（PI_JOURNEY_E2E=1）：**1 passed**（31.1s）。
- 前端：vitest **5 passed**（+空间模式）无 unhandled；tsc 主配置干净；eslint 0 警告。
- 工件闸：openapi/realtime/manifest/report/drift/catalog/账本 全部再生成后
  api_compatibility + quality_manifest + generated_graph + realtime 68 passed /
  1 既有（percent-encoded-url，base 复现在案）。

## Round 2（复审：同 reviewer 携 Round-1 报告复核）

（待复审结论回填）
