# G19-TYPED-FIELD-COMPUTE — LOOP_LEDGER

恢复入口：**先读本文件 + `git diff --stat` + `TEST_MATRIX.md`**，不要从零重做。
本任务按「读调用链 → 可独立验收增量 → 实现 → 真实验证 → 记账」推进；实现集中在 5 个阶段（非机械 15 轮），每阶段均有真实命令证据。

| 轮 | WP | 改动 | 真实验证 | 结论 | 下一步 |
|---|---|---|---|---|---|
| 0 | WP01 | 首轮侦察：remote/status/worktree、fetch、12 开放 PR / 45 issues / 6 个相关 PR 的 reviews+inline+conversation、18 个排重 PR 状态；创建独立 worktree（BASE_SHA `8167052`）；建立 8 个 planning 文件 | `pytest tests/data/test_preparation_plan_v1.py` → **24 passed / 9 failed (78.48s)**，9 失败根因 `dsd-v1:<sha>.json` 冒号 WinError 123（干净基线可复现） | ✅ 侦察完成；⚠️ 既有基线失败如实记录 | WP02 |
| 1 | WP02 | 新域 `field_compute/`：闭合 IR（6 类节点）、Pydantic 契约、预算与版本常量、28 项函数词表、安全 tokenizer/递归下降解析器、`validate_expr` fail-closed、canonical 指纹、多列拓扑/前向引用/循环依赖闸门 | 交互 smoke：`{人口} / {面积 平方千米}` 编译、`__import__`/`open`/未知字段/单位不兼容全部拒绝、可交换重排同指纹 | ✅ | WP03–05 |
| 2 | WP03–05 | `interpreter.py` 确定执行（严格类型、三值逻辑、惰性分支、行级错误、批大小不变、大整数文本化）；`units.py`（同量纲换算，RATIO 标尺方向显式区分）；`geometry.py`（`classify_crs` 门控 + `pyproj.Geod`/shapely，洞/多部件/dateline 披露） | 交互 smoke：密度/变化率/前导零/闰日跨 UTC/单位换算等价/大整数/未选中行/批大小不变全部符合已知答案 | ✅ | WP06–08 |
| 3 | WP06–08 | `scope.py`（all_rows/predicate/可靠 selection）、`snapshot.py`（冻结快照+内容/schema 双锚+发布前二次核对）、`receipt.py`（原子 JSONL ring）、`service.py`（describe/compile/preview/execute/result/project manifest，CPU offload、取消、幂等锁、additive 登记） | 端到端 smoke（真实 MemorySessionStore）：describe→plan→preview→execute→get_result→幂等→项目清单→stale 拒绝；`descriptor_minted=False` 降级如实披露 | ✅ | WP09–10 |
| 4 | WP09–10 | API 路由（9 端点）+ schema + 4 个标准 `@tool`（catalog/preview/apply/result，同一 service）；后端共享接线 `app/main.py`/`app/tools/__init__.py` 各加行；前端独立面板/API client/i18n/组件测试（subagent）+ 前端共享接线 4 处 | `ruff check` 全过；`ToolRegistry.dispatch` 6 passed；契约门禁首轮 3 failed → 刷新生成物后 23 passed；i18n 137/137；`tsc --noEmit` 新增文件 0 错误 | ✅（前端 Vitest 组件测试 worker 超时 → blocked，见下） | WP11 |
| 5 | WP11 | 50 个后端回归用例（IR/解释器/服务层 45 + 工具 6，另有 `-k scope` 单跑）；用户契约文档 `docs/field-compute-contract.md` 由 `app/services/data_preparation/field_compute/gen_help.py` 从真实词表生成；TEST_MATRIX 21 场景回填 | **44 passed + 6 passed + 23 passed**；`git diff --check` 通过；规模基准只报告真实耗时/输入量，不虚构万行能力 | ✅（3 项 partial / 2 项 blocked 如实标注） | WP12 review |
| 6 | WP12 | 独立 reviewer → 修复 → 复审 → fetch 最新 master → push → PR | 待填（`REVIEW.md` / `PR_HANDOFF.md`） | 进行中 | PR |

## 关键决定（跨轮）

1. 不占 ADR 数字编号（已有 0286/0287 重号，且与并行任务冲突）；契约写 `INTERFACES.md` + 模块 docstring。
2. 核心成功路径不依赖 `dataset_semantics.store.put()`（Windows 基线缺陷），登记一律 additive + `descriptor_minted`/`degraded` 如实披露。
3. 表达式是闭合 IR，不是脚本；文本公式与 UI 构造器 IR 汇入同一个 `validate_expr`；预览与全量共用 `evaluate_rows`。
4. 单位默认 `undeclared`，只有 user-declared 才给科学资格；`convert_unit` 显式区分物理倍率与 RATIO 数值标尺（修复了首版 percent↔fraction 方向错误）。
5. 输入一次性冻结快照 + 发布前二次版本核对；selection 必须完整/稳定 ID/revision，截断或过期一律拒。
6. 共享文件只加行，不格式化/不重命名（见 `INTERFACES.md §2`）；生成物（OpenAPI 快照 / api-docs / 端点矩阵）按本分支当前版本独立完成，不留给用户/合并者。
7. 线上 CI 不等待；既有失败（preparation 9、前端 scene3d 8）在干净基线复现并隔离，不掩盖。

## 资源纪律实际执行

- 所有 pytest 单进程、`OMP/OPENBLAS/MKL=1`、`--no-cov -p no:cacheprovider`；未启用 xdist；未跑 3 万级全量。
- 前端只跑一次 `tsc --noEmit` + 一次 Vitest 尝试（worker 超时即停，不循环重试）；`node_modules` 为指向主工作区的 symlink，未重复安装。
- 与其他 G 任务共享同一台机器；未杀进程、未清共享 Redis、未占用公共端口。
