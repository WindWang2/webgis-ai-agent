# G27 RECON — 实证基线（2026-11-11）

## 0. 仓库、基线与工作区

| 项 | 实证值 |
|---|---|
| 仓库 | `WindWang2/webgis-ai-agent`（`git remote -v` 核对） |
| 基线 BASE_SHA | `8a9a79f76875eb95d31bd448baad971edb2e61d3` |
| fetch | `GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/true git fetch origin master` → `FETCH_OK`；`origin/HEAD` 同值；末提交 `Merge PR #2083` |
| 起始工作区 | `/home/kevin/projects/webgis/webgis-ai-agent`，分支 `master` @ `816705218a…`，落后 `origin/master` **167** 提交（`rev-list --left-right --count`） |
| 起始工作区状态 | **只读，未 checkout/reset/改动**。`git status -sb` 38 行：`M vendor/pi`（gitlink，G22 owner，禁止触碰）；未跟踪 `.claude/skills/*`（gstack 系技能 symlink，实体 `.agents/skills/`）；`_tmp_47_f754af1b0a09e630d4cf829599d2868a` |
| 本方向 worktree | `/home/kevin/projects/webgis/webgis-wt-g27-scale-aware-20261111T144000Z`（路径预检 `PATH_FREE`，`git worktree add -b … origin/master`，HEAD 干净 0 改动） |
| 本方向分支 | `zcode/g27-scale-aware-portrayal-20261111T144000Z` |
| 其他方向 worktree | 6 个（`.bare` / all-issues-review / G21 OD-flow / G09 batches / G3DV2-B / ui-polish），全部未触碰 |
| 规划目录 | `planning/G27-SCALE-AWARE-PORTRAYAL/` 原不存在，本方向新建 |

任务快照（master `8a9a79f…`、开放 PR #2084/2085/2086）经实取**与当前远端一致**；不作为"未核实快照"使用。

## 1. PR / review / issue 全景（`gh` 实取，已认证 WindWang2）

- **开放 PR 恰 3 个**：`#2084` 旅程编号/registry、`#2085` MapToolId 联合类型、`#2086` Tailwind/PostCSS 兼容 —— 按指令**本轮避开**，其修复不作为本方向工作量。
- **近 30 天合并 PR**（`gh pr list --state merged --search "merged:>=2026-10-12" --limit 100`，返回 <100，无分页遗漏）：关键的已合并能力 `#1880`（GOAL04 多尺度综合 / ScaleRepresentationProfile.v1 / MapSpec v1.9）、`#2073` G13、`#2074` G19、`#2075` G14、`#2076` G15、`#2077` G16 标注、`#2078` G20 栅格、`#2079` G17、`#2080` G18、`#2021` G11、`#2022` G10、`#2033`/`#2034`/`#2035`/`#2036`、`#2081` Pi 原生发现（修 #2061）。
- **Review 记录区分**：`gh pr view 1880 / 2077 --json reviews,comments` → 两者 **GitHub 正式 reviews = 0、comments = 0**；#1880 有 18 commits，其 PR body 是**作者自述**（"复用不重写 representation.py"），不能当独立审查。仓库内独立审查记录在 `planning/g01..g07/REVIEW.md`、`planning/h02-cartographic-conformance/REVIEW.md`、`planning/review-fix-rereview-20261108/LOOP_LEDGER.md`。本方向自有独立 reviewer 的复跑结论见 `REVIEW.md`。
- **开放 issues**：`#2037`–`#2072` 全为 R1 审查项 P1/P2/P3 汇总 + `#1999`–`#2007` 旧审查项。按指令**不接管修复全部 issues**；与本方向相邻的仅登记依赖/回归/边界（如 `#2002` 组件族锁、`#2003` 指北针旋转、`#2060` 账本无界增长对"解释量有界"的参考）。`#2061` 已由 #2081 修复（按最新代码判断，非按 issue 状态）。
- **远端分支**：120 个；含 `origin/zcode/goal-multiscale-cartography-v1`（#1880）、`origin/zcode/goal-g15/g16/g20…`。无 `g27` 前缀分支/规划目录（命名无冲突）。

## 2. 生产调用图（只读调查员，P0 结论；证据路径见 DUPLICATION_MATRIX）

**ADR-0218 状态 Proposed / "MapSpec v1.5" / "不解决 scene_lod 生产接线" 是历史快照**：当前 `mapspec_schema.py` `LATEST_VERSION = "2.1"`（KNOWN_VERSIONS 1.0–2.1；1.9=GOAL04、2.0=frame_product、2.1=G16）。**不得倒退为 1.5**。

已真实接线（只回归，不重建）：

- `visibility.ts` precedence（`layout.visibility` 用户 durable 终裁 > authored `visible:false` > visible；zoom 门正交）被 5 个生产消费者使用：runtime.ts、runtime-evidence.ts、compiler.ts、mapspec-to-svg.ts（2 处）。
- `representation.py::representation_at_scale()` 是 `lod_for_zoom` 的**唯一生产调用**（`:452`），落笔面 `analysis_cartography_converter.py:883–931`；前端 `carto-ir/representation.ts` + compiler/runtime 消费窗口。
- PR #1880 全链：profile.py / scale_profile service（CAS+receipt）/ scale_profile_tools.py / 前端窗口源覆盖 / export_adjudication.py。
- G04 live↔export parity（`docs/cartography/RENDER_EXPORT_PARITY.md` 四链版面模型）；导出 zoom 恒取 `spec.view.zoom`，DPI 只做 `dpi_scale` 几何/字号缩放与瓦片采样 boost，**不进入 visibility/representation 门**。
- zoom 手势为 observed camera，不写 MapSpec（ADR-0054/0057/0058）。

真实未接线 / 缺口（有调用图证据，非"文档说没接"）：

1. `build_label_zoom_bands()` **零生产 import/调用**（仅 `tests/unit/test_scene_lod.py` 与 ADR 文档声称"投影进 zoomBands"）。
2. `terrain_maxzoom_for_zoom()` **零调用，连测试也无**（`scene_lod.py:96`）。
3. `LOD_BANDS.decimate_budget` / `symbol_scale` 仅 advisory 快照（`representation.py:135-153,467-468`），无渲染消费者；`thresholds.maxFeatures` 既有 spec 字段未被该决策填充。
4. 四档 `LOD_BANDS`（3/6/10/14/19）与 `SCALE_TIERS`/`DEFAULT_ZOOM_BANDS`（0/8/11/14/24）**有意并存**，契约测试 `test_scale_reconciliation_v1.py:32-37` 显式锁定差异 —— 不是 bug，不删表。
5. 组件 registry/composer/模板 **无按档位变体**，布局不随档位切换。
6. `publication_ir.py` **无 scale/tier/DPI/CRS/viewport 输入面**（关键字计数全 0），只有 frame.extent / view.zoom / pageSize。
7. 无「有效策略读取 + 有界解释 + 按尺度表达 UI + Agent 局部覆盖」入口；`resolve_scale_tier_stable` hysteresis 无运行时消费者。

**四类尺度优先级（实证）**：用户隐藏优先 > 人工 pin/锁 > 结构 zoom 门（与显隐正交）> 语义带/LOD 自动策略 > 输出 DPI/物理比例尺（独立通道，只影响采样与尺寸）> data-plane 传输 LodBand（display-only，不改数据）。

## 3. 技能、预算与资源

- **原生 `/goal` / goal-loop skill / 自定义命令**：`~/.workbuddy/commands` 与 `<repo>/.claude/commands` **均不存在**，`available_skills` 无 goal-loop 条目 → 本机无已安装可调用的真实入口。按指令"缺失不阻断"，以本会话原生执行推进，不自行安装未知脚本，不虚构 `--tokens`/`--budget`/`/goal-loop` 参数。
- **本机已有技能**：`.claude/skills/*`（34 个未跟踪 symlink）→ 实体 `.agents/skills/`（gstack 系：autoplan / browse / careful / cso / design-review / guard / investigate / office-hours …）。这些是 Claude Code 技能，非本会话原生 Skill 工具入口；需要时先读其 SKILL.md 再决定是否采用。
- **预算**：指令目标 3.2 亿 tokens 是"可用预算目标"，**无法通过 prompt 创建账户额度**；本机未发现已授权的真实 Goal 配额读取/配置入口，故如实记录：有效预算 = 本会话上下文窗口，按"有价值工作"推进，不以空转/重复扫描耗尽额度。
- **硬件**：`Linux cachyos-3080 7.3.1` / 16 CPU / 62 GiB RAM（可用 39 GiB）/ 938G 磁盘（可用 543G）。编译默认 `-j2`，重型任务前取宿主锁。
- **宿主级重任务锁**：`/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock` 已存在（其他方向共用），`flock` 可用。重型任务（Pi vendor build / 完整 Next build / 大范围 pytest / 浏览器 E2E / 重基准 / PDF-native 测试）同一时刻只跑一个，用 `flock -w` 真实 OS 锁；锁失败转静态分析，不忙轮询。
- **测试入口**：`pytest.ini` 在场；`frontend/package.json` 真实 scripts：`test=vitest run`、`typecheck=tsc --noEmit && tsc -p tsconfig.test.json --noEmit`、`lint=eslint . --max-warnings 0`、`build=next build --webpack`。Vitest worker 参数须查本版本真实帮助后再限。
- **隔离**：worktree 独立；不共享可写 node_modules/.next/测试 DB/生产数据。起始工作区 `vendor/pi` gitlink 与 `.claude/skills` 未跟踪项只读保留。
