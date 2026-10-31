# PLAN — 里程碑与验收

角色：主 Agent（契约冻结/串并联调/合并）+ ≤3 subagents（勘察已完成；后续：实现支持、测试 oracle、独立 review）。

## M0/M1 ✅ 勘察与基线（已完成，见 BASELINE.md）
- 三路 subagent 勘察（后端链路 / 前端+工具链 / 测试基建）。
- 网络中断记录；PR/issue 二次去重挂起（恢复后、PR 创建前必须补做）。

## M2 ✅ 契约冻结（INTERFACE_CONTRACTS.md）
角色词表 / 提案与收据 schema / 计划步 closed union / API / 工具判别式 / 前端结构。

## M3 核心引擎：`app/services/data_preparation/`
- `vocabulary.py`（角色/原因码/建议码）、`field_proposal.py`、`spatial_receipt.py`
- 测试：`tests/data/test_field_proposal_v1.py`（双语语料+负例+置信度序）、`tests/data/test_spatial_receipt_v1.py`（20 场景矩阵中 #2/3/4/5/6/8/19 相关项）
- 验收：纯函数、无 IO；对 10 万行合成 CSV 的提案仅消费 profile 统计（时间有界）。

## M4 计划与执行：`plan.py` / `executor.py`
- closed-union 步校验（AxisSwapStep 需前置 finding）；dry-run ≤2000 行采样；幂等 plan_hash；输出新 ref + provenance + mint_output_descriptor；原件不可变断言。
- 测试：`tests/data/test_preparation_plan_v1.py`（dry-run/幂等/回滚/质量 delta/指纹对）

## M5 API 面：`app/api/routes/data_preparation.py`
- 7 端点 + auth/ownership/有界样本；`/bindings/fields` 写回 descriptor v2。
- 测试：`tests/data/test_preparation_api_v1.py`（含跨会话 404、伪造 owner、413 类负例）
- OpenAPI/contract 快照再生（用项目生成器）。

## M6 工具接线：`app/tools/data_preparation_tools.py`
- prepare_dataset 四态判别式；挂 _TOOL_MODULES；经 ToolDispatchService 真实分发测试（非直接调用）。
- 测试：`tests/unit/test_data_preparation_tools_v1.py`（dispatch 全链 + llm_payload 无行数据断言 + capability 目录一致性）

## M7 前端工作台：`frontend/components/sidebar/data-preparation/`
- prepare-flow 一屏（inspect→映射复核→收据→预览 delta→执行→入图）；lib/api/data-preparation.ts；i18n 双语。
- 验收：pnpm vitest 组件测试 + tsc + eslint；上传后展示 crs_source/quality/warnings（补上现有未展示项）。

## M8 E2E 合成城市场景
- 学校 CSV（中文字段、xy 对调子集）+ 行政区 polygon FC + 人口分母缺失变体：
  检查→提案→确认→准备→绑定→（模拟）统计各区小学数量数据就绪，preflight 输出 typed 结论。
- 跨会话恢复：绑定后重启上下文（新 store 读取）复用；数据替换后旧指纹拒复用。
- 测试：`tests/data/test_preparation_e2e_v1.py`（fixtures 扩展 gis_samples 工厂，不自造变体）

## M9 独立 review ×2 + 回归 + PR
- 独立 reviewer subagent（未参与实现）读 diff → 编号发现 → 修复 → 复审；无引入 P0/P1/P2。
- 全量相关面回归（pytest tests/data + tests/unit/dataset_semantics + tools 面 + frontend vitest 相关面 + ruff/mypy 白名单面 + 快照闸）。
- 网络恢复后：gh 二次去重（open PRs + issues #1842–#1866）→ push → gh pr create（不合并）。

## 提交切分（原子 commit 计划）
1. `feat(data-prep): M3 字段角色提案引擎 + 空间检查收据（双语语料/负例）`
2. `feat(data-prep): M4 准备计划/执行器（幂等/原件不可变/provenance）`
3. `feat(data-prep): M5 检查/计划/执行/绑定 API + 鉴权负例 + 快照再生`
4. `feat(data-prep): M6 prepare_dataset 工具四态接线（ToolDispatch 真实分发）`
5. `feat(data-prep): M7 前端准备工作台一屏流`
6. `feat(data-prep): M8 合成城市 E2E + 恢复/替换场景`
7. `docs(data-prep): planning 定稿 + PR 材料`

## 资源纪律
- pytest 默认单进程窄面（--no-cov）；宽面才 `-n auto --dist loadfile`；与 subagent 并行时错峰。
- 前端 pnpm install 在 worktree（pnpm store 缓存，离线优先）；vitest 单文件优先。
- 编译类 -j2（本任务无 native 编译预期）。
