# G06 — 执行计划与阶段门禁

审计 ID：`G06-CAPABILITY-ECOSYSTEM`｜目标 branch：`zcode/goal-gis-capability-ecosystem-20261107`
worktree：`/home/kevin/projects/webgis/webgis-wt-gis-capability-ecosystem-20261107`

## 执行宿主事实（不捏造技能）

原 prompt 指定 `ZCode /goal-loop`。实测 `.claude/skills/` 与 `.agents/skills/`
**没有** `goal-loop` / `planning-with-files`；仓库有 `loop-me` / `autoplan` /
`code-review` / `qa` / `investigate` 等技能但不冒充已装 `goal-loop`。
按契约回退到**原生 /goal + 自管理 Oracle 驱动循环**：
`audit → characterize → design → implement → test → review → fix → re-test →
second independent review → PR`，账本落在本目录。

子代理使用（上限 3，实际 3）：
| 角色 | 名称 | 职责 | 文件所有权 |
|---|---|---|---|
| A | `audit-a` | M0 只读能力清单审计（8 组、精确行号） | 只读，不写文件 |
| C | `test-c` | M1 变更前失败测试（23 例，2 个文件） | 仅 `tests/unit/{gis,tools}/test_capability_ecosystem_*.py` |
| B | `reviewer-b` | M8 独立冷读 `git diff origin/master...HEAD` | 只读，不写文件 |

主 Agent 持有架构与共享接口决策，负责全部 `app/` / `docs/` / `examples/` /
`scripts/` / `planning/` 写入与最终集成。

## 阶段门禁与实绩

| 轮次 | 主交付 | 门禁 | 实绩 |
|---|---|---|---|
| M0 | 实时审计 + 冲突矩阵 + 能力真值清单 | 必须留 SHA / 文件路径 / 缺口 | ✅ `BASELINE.md` + `RECON.md`；`585812ee4…`；7 个真实缺口 |
| M1 | 目标用例与变更前失败测试 | 每个拟修问题可复现 | ✅ 23 failed / 0 passed（子代理 C 原始输出），见 `LOOP_LEDGER.md` |
| M2 | 单一能力契约扩展 + 兼容 ADR | 旧目录/接口零破坏 | ✅ ADR-0287；algorithm count 仍 253；`get_schemas()` 未改 |
| M3 | 可用性 / 资格矩阵 / 科学前置门 | 真实描述符/权限/类型对拍 | ✅ 213/186/18 分层；CAP-O02/O04/O06/O07 全绿 |
| M4 | 工具发现 / 动态刷新 / 缺依赖负例 | Pi 面不出现不可执行能力 | ✅ 重排硬过滤 + 黄金语料 10 例 + `pi_availability_adapter` |
| M5 | 至少两项真实计算原语 | 真实 dispatch 产生可消费产物 | ✅ 2 个原语；numpy/networkx 真实计算；已知答案 + metamorphic |
| M6 | SDK / 扩展包认证与生命周期示范 | 认证+激活+卸载可复现 | ✅ `check_pack.py` + 2 组离线 demo；CAP-O10 全链真实通过 |
| M7 | 压测 / 长期回归 / 失效注入 | 数量级指标与预算证据 | ✅ 见 `TEST_MATRIX.md`；慢 provider 有界 / 200 候选截断 / 长结果压缩 |
| M8 | 独立冷审 → Fix → 二次 review | P0/P1 清零 | 见 `REVIEW.md` |
| M9 | 同步 master / 完整本地验收 / Push PR | PR 链接及证据表 | 见 `PR_HANDOFF.md` |

## 关键工程纪律

1. **唯一事实源**：不建第二 registry / catalog / 认证 / 指纹 / Planner / 渲染器。
2. **真实适配**：所有接口先 probe 真实签名再写，不臆测。
3. **不改断言让错误消失**：唯一一次改测试是修 fixture 的
   `manifest.name`（真实 schema 约束），断言本身未动，见 `DECISIONS.md` D6。
4. **生成物用既有生成器**：`execution_catalog_docs` / `gen_science_catalog`；
   不手工编辑生成物、不新写生成器。
5. **资源红线**：pytest 分批、无 `-n`、编译并行 ≤4；后端/前端不三路满负荷。
6. **不等待线上 CI**：Issue #2007 的生成质量文档历史债务如实记录，
   不修改 Actions、不宣称 CI 通过。
7. **危险操作**：未 stash/reset/clean/force-push 任何他人工作树；
   未自行扩大权限；未引入外部秘钥/生产数据。
