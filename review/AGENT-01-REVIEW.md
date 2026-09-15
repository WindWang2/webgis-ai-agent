# AGENT-01 VLM Visual Critic Runtime — 代码审查纪要

- 日期：2026-09-14
- 分支：`agent/01-vlm-visual-critic-runtime`（基线 origin/master @ 3eb2cc6a）
- Worktree：`C:\Users\wangj.KEVIN\projects\webgis-wt-agent-01`
- ADR：docs/adr/0185-vlm-visual-critic-runtime.md（Proposed）
- 规格书：docs/dev/vlm-visual-critic-spec.md

## 1. 交付物清单

| 文件 | 性质 | 行数 |
|---|---|---|
| `app/lib/harness/visual_judge/contracts.py` | Pydantic 严格契约（5 维白名单 / bbox / 置信度 / fail-closed 报告） | 310 |
| `app/lib/harness/visual_judge/snapshot_extractor.py` | 解码/魔数/尺寸/哈希/灰度直方图初筛 | 213 |
| `app/lib/harness/visual_judge/vlm_provider.py` | OpenAI 兼容 + Gemini 双通道、单一 JSON-schema 事实源 | 346 |
| `app/lib/harness/visual_judge/critic_engine.py` | fail-closed 编排 / LRU 记忆化 / 确定性评分推导 | 326 |
| `app/lib/harness/visual_judge/fake_vlm.py` | FakeVLMClient + 10 黄金样本 canned 响应 | 207 |
| `app/lib/harness/visual_judge/golden_images.py` | 10 确定性缺陷渲染器（无随机源，字节级可复现） | 185 |
| `app/lib/harness/visual_judge/__init__.py` | 公开面（含单向依赖声明：evaluator → judge，无环） | 98 |
| `app/lib/harness/visual_evaluator.py` | **纯增量 +122 行**：opt-in 挂接 + `_apply_critic_report` | diff |
| `tests/unit/test_visual_critic_runtime.py` | 契约测试套件 | 935 |
| `docs/adr/0185-…`、`docs/dev/vlm-visual-critic-spec.md` | 设计文档先行 | — |

## 2. 架构对齐情况（对照 ADR-0185 决策）

| 决策 | 落地证据 | 判定 |
|---|---|---|
| D1 独立包、legacy 逐字节保持 | `git diff` 仅新增函数与 docstring 段；存量测试 0 改动全绿 | ✓ |
| D2 五维白名单 + bbox [ymin,xmin,ymax,xmax] 百分比 + 置信度 | `contracts.VisualDimension`；`VisualBBox` 值域/次序校验；非法 bbox 置空不判废（测试 `test_invalid_bbox_blanked_not_verdict_killing`） | ✓ |
| D3 fail-closed 矩阵 | 11 个负路径测试逐一断言 reason 码（image_empty/corrupt/oversized/too_small、provider_timeout/provider_error、invalid_output、no_api_key、not_configured）；零 `evaluated` 泄漏 | ✓ |
| D4 快照管线 + 初筛 hints 不越权 | PNG/JPEG/WebP 魔数；`pre_screen` 只进诊断披露（测试锁定 hints 语义） | ✓ |
| D5 双 provider + 单一 schema | `CRITIC_OUTPUT_SCHEMA is` 同一对象被两个方言引用（测试锁定）；MockTransport 覆盖 httpx 路径（超时/HTTP500/空内容映射） | ✓ |
| D6 评分引擎推导，不采信 VLM 自报分 | `_derive_dimension_scores`：error=4/warning=1.5/info=0.5 罚分；未观察维度分 10/置信 0；overall 置信度加权（测试断言 6.0 等具体值） | ✓ |
| D7 opt-in 接线 + 优先级 + record-only | 开关默认关；注入 judge > v2 > legacy（`test_injected_judge_takes_priority_over_runtime`）；v2 结论不改写三态 verdict（`test_runtime_on_evaluates_via_engine` 断言 status 不动） | ✓ |
| D8 离线确定性 Mock | 10 样本注册表 + 渲染器字节级复现（sha 断言）+ 故障注入 | ✓ |
| 记忆化 `(session, fingerprint, sha256)` 单轮单呼 | 引擎级（LRU 64，含 not_evaluated 缓存回放）与接线级（两次 attach 一次外呼）双重测试 | ✓ |

**安全审查**：api_key 仅存在于 client 私有字段与请求头，不进 `VLMRequest`、
报告、摘要或日志；改图意图字段（mutation/intent/spec/patch/layers…）在
契约层 `extra="forbid"` + 引擎 `_FORBIDDEN_KEYS` 双闸判废（ADR-0158 同词表）；
base_url/key/model 复用 legacy 配置家族并回落 settings（settings 自带
SSRF 校验），未新增 `.env.example` 键（env 卫生锁不受扰动）。

## 3. 测试覆盖率指标

命令：`pytest tests/unit/test_visual_critic_runtime.py --cov=app/lib/harness/visual_judge`

| 模块 | Stmts | Miss | Cover |
|---|---|---|---|
| `__init__.py` | 7 | 0 | 100% |
| `contracts.py` | 137 | 2 | 99% |
| `critic_engine.py` | 148 | 7 | 95% |
| `fake_vlm.py` | 42 | 0 | 100% |
| `golden_images.py` | 105 | 2 | 98% |
| `snapshot_extractor.py` | 133 | 4 | 97% |
| `vlm_provider.py` | 139 | 10 | 93% |
| **合计** | **711** | **25** | **96.5%**（门禁 ≥90% ✓） |

未覆盖行均为防御性分支（旧 Pillow 字体回退、settings 加载失败回退、
`_percentile` 理论兜底），无主路径缺口。

## 4. 门禁结果（阶段四验收证据）

| 门禁 | 结果 |
|---|---|
| `pytest tests/unit/test_visual_critic_runtime.py -v` | **77 passed**（先红后绿：首跑 63/65，修复 2 处后全绿） |
| 存量回归 `tests/cartography/`（release-blocking，含 visual_judge_selfheal 48 项） | **1477 passed, 2 skipped** |
| `tests/unit/gis_harness/ + tests/harness_replay/` | **1709 passed, 5 skipped** |
| `tests/quality/` | 410 passed；`test_ledger_is_current` 1 失败为 **master 存量**（见 §6） |
| `ruff check`（select E4/E7/E9/F，--max-warnings 0 语义） | **零告警**（2 个 F401 已修） |
| L5 消费面（`derive_goal_satisfaction` / replay / GisTraceChain） | 零改动、复用测试全绿（v2 摘要 `source="visual_judge"` 兼容键） |

## 5. 资源与并发检查证明

- **Subagents 峰值：0**（全程主 agent 直接执行；上限 3 未触达）。
- 后台任务共 2 个（venv 创建 + requirements 安装），均**正常退出（exit 0）**，
  无存续进程、无未释放句柄；测试套件超时护栏 60s/项（pytest.ini）生效。
- pytest 全程单进程（未开 xdist 并行）——遵守「禁止资源耗尽」约束。
- 无网络外呼进入生产代码路径：provider httpx 路径全部经 `MockTransport`
  注入测试；venv 安装走 pip 官方源。
- Worktree 隔离开发，主仓工作区零改动（仅复用其 pip 缓存）。

## 6. 偏差与存量问题记录（诚实披露）

1. **`pip install -e .` 不可行**：任务书 prescribed 命令因 setuptools 平面
   布局多包发现（app/perf/agent/specs/... 多顶层目录）失败——仓库实际研发
   方式是 `pip install -r requirements*.txt` + pytest `pythonpath = .`
   （CI 同款）。本 PR 依赖安装改用 requirements-only，行为等价。
2. **`tests/quality/test_generated_artifact_graph.py::test_ledger_is_current`
   在 master 上即失败**（生成物台账过期）：对 pristine master 与本 worktree
   分别运行 `scripts/check_generated_staleness.py`，过期集合**逐行一致**
   （diff 为空），证非本次改动引入；修复需按其提示跑 `--update` 刷新，
   属独立卫生问题，未混入本 PR。
3. **仓库误提交的 `.coverage.root.pid*` 临时文件**（31 个）会被任何一次
   coverage 运行连带删除；本次已全部 `git checkout` 还原，未把无关删除
   带进提交。建议后续独立 PR 清理并补 `.gitignore`。
4. ADR-0185 状态为 **Proposed**（随本分支评审），与 ADR-0184 出场形态一致；
   合并时由评审者翻 Accepted。
5. `$goal` / `$loop` 内置命令在当前执行环境不可用（未注册技能），收敛推进
   以 planning-with-files 式持久台账（`.agent-work/agent-01/PROGRESS.md`）
   + 人工阶段门替代。

## 7. 已知限制与后续接口点

- 阻断模式（block）仍按 ADR-0158 预留未开启——v2 结论同样 record-only。
- `spatial_alignment` 的确定性互证（底图/矢量像素级配准）不在本范围。
- 维度级自愈动作仅沿用既有 `rotate_palette`（color_discriminability+error）；
  新动作注册是后续方向。
- Gemini `responseSchema` 各版本对 `additionalProperties` 支持不一——契约层
  `extra="forbid"` 兜底保证消毒语义不依赖 provider 行为。
