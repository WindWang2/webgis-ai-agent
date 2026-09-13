# qc-loop FINAL — 审查与优化收敛循环总结（2026-09-13）

- 分支：`quality/review-optimize-loop`（base = origin/master `17c77c73`）
- 退出条件：**E1**（缺陷池 P0=0 且 open P1=4 ≤ 5）。E2/E3 未触发（末三轮 Δ = 4.0/3.0/3.0，循环仍在有效收敛，E1 先到者为准）。
- 回滚点：tag `qc-loop-r1` … `qc-loop-r5`（每轮门禁绿后打 tag）。

## 一、轮次台账

| 轮 | R 扫描 | P 池变化 | F 修复 | 指标（open P0/P1/P2） | Δ | 门禁 |
|---|---|---|---|---|---|---|
| 基线 | — | — | 环境修复 + 覆盖率回归认定 | 门禁红（lane 覆盖 48.55%<50） | — | ❌（E4 处置） |
| r1 | cartography 63 文件 / 24,059 行（4 代理全文精读） | +65（0/7/58） | 3 P1 + design_system 契约测试 6 条 | 0/4/58 | 基线 | ✅ SKIP_BROWSER（覆盖率 **51.14%**） |
| r2 | gis_harness 114 文件 / 43,714 行（8 代理全文精读） | +97（0/13/84） | 3 P1 | 0/14/142 | −35.2（发现爆发轮） | ✅ SKIP_BROWSER |
| r3 | —（池已封闭） | −1 误报剔除 | 3 P1 | 0/10/142 | 4.0 | ✅ SKIP_BROWSER |
| r4 | — | — | 3 P1 | 0/7/142 | 3.0 | ✅ SKIP_BROWSER |
| r5 | — | — | 3 P1 | **0/4/142** | 3.0 | ✅ SKIP_BROWSER（另附 golden 环境诊断，见 deferred） |

勘误：r3/r4/r5 台账初版的 open P1 绝对水平每次多报 3（漏减当轮修复数），已在本文件与 state.json 更正；各轮 Δ 数值不受影响。

## 二、修复清单（15 条 P1，每条独立 commit + 回归测试）

**Round 1（cartography）**
1. `0fe188e8` semantic_checks.py:518 — divergent 图例 center 校验对非数值域防 TypeError（Q001）
2. `8c4faf00` thematic_spec.py:183 — RFC 7946 `properties:null` 特征不再炸 build_graduated_spec（Q052）
3. `81e303f4` symbology.py:246 — skew=None（median≤0）理由串以 n/a 呈现，模板偏好分支防 TypeError（Q022）
4. `e9b87796` + design_system.py 契约测试 6 条 —— **恢复 lane 覆盖率闸**（48.55%→51.14%，master 因 3 个 adaptive-cartography PR 合入而真实回归）

**Round 2（gis_harness）**
5. `dd82527f` runtime_state_machine.py:295 — legacy 块缺 verdict 键防 AttributeError（此前整个 V7 状态机对 legacy 会话静默冻结）
6. `0953174f` tool_surface.py:149 — done 集合补 `available`（分析域工具恢复激活）
7. `fc7f5382` workflow_instance.py:615 — gate 指纹两侧同形，「不变即跳过」幂等恢复

**Round 3**
8. `0468cc29` planner.py:698 — 显式 recipe_id 路径证据链恢复发射（CANDIDATE_WORKFLOWS/SELECTED_WORKFLOW）
9. `c88ffe92` component_registry.py:1063 — 校验器内部异常 fail-closed 出 issue
10. `b5689596` map_critique.py:79 — canonical list 形状观察层双键索引，5 项 V7 检查恢复生效

**Round 4**
11. `30f873eb` completion/pipeline.py:415 — final_gate 幂等门识别 dict 形状裁决，READY 会话零开销跳过
12. `a77400a9` recipes.py:233 — 资格门读取 producer 真实 `null_ratio` 键（此前恒 unknown 放行）
13. `66506193` completion/pipeline.py:121 — 终验状态用全量 findings 判定（截断只在披露层）

**Round 5**
14. `edff4712` render_scene.py:210 — 连续色带取色对齐前端 Math.round（.5 边界不再与前端分歧；golden 共用语料 parity 全绿）
15. `7f1b4349` components.py:1234 — rebind 换绑清掉旧 inline chart（渲染端 inline 优先不再被残留压过）
16. `2c7f13c1` tools.py:806/:839 — converter 降级警告接入诚实披露通道

另：1 条误报拦截（Q002 pdf_renderer transform —— Figure.add_artist 自动补 transFigure，实证裁定），子代理断言不实，未进入修复。

## 三、指标曲线（open 缺陷池）

```
P1: 7 ──(r1 修3)──> 4 ──(+13 发现, 修3)──> 14 ──(修3+FP1)──> 10 ──(修3)──> 7 ──(修3)──> 4
P0: 0 ────────────────────────────────────────────────────────────────────────────> 0（假数据红线全程零命中）
P2: 0 ──(+58)──> 58 ──(+84)──> 142 ──────────────────────────────────────────────> 142
lane 覆盖率: 48.55%(红) ──> 51.14%(绿，ratchet 只升不降)
```

## 四、遗留（open P1 ×4，E1 允许 ≤5）

| id | 位置 | 问题 |
|---|---|---|
| Q053 | style_tokens.py:356 | 迁移映射按子集内序数而非源色带索引占比（与 docstring 相悖；test-only） |
| R2Q004 | tools.py:608 | overlay_refs 文档化输入无消费者（**转 deferred**：需产品语义决策） |
| R2Q006 | trace_store.py:327 | 逐出顺序违背文档承诺（最旧段全保护时提前丢保护记录） |
| R2Q009 | plan_candidates.py:505 | 排序后 top-1 语义错位，rerouted 在 blocked-top-1 场景失效 |

P2 ×142 详见 `round-1.pool.csv` / `round-2.pool.csv`（status=open），按 score 列就绪；删除类（死代码/重复）修复须逐条核对契约测试关联。任务书既有声明裁定：ts_projection 孤儿**否定**（代码生成入口）、select_composition_alternatives 孤儿**属实**（test-only）、label_engine/label_collision 8 组同名**属实**（7 同 1 漂移）。

## 五、Deferred

见 `docs/dev/qc-loop-deferred.md`：R2Q004（需产品语义决策）+ golden 环境项。

## 六、收尾验证状态

- [x] 各轮门禁（SKIP_BROWSER=1）绿：round-{1..5}.gate.log，lane 806→811 passed，ratchet 无劣化，覆盖率 51.14%
- [~] 全量单元测试：`pytest tests/unit -q -n 2 -m "not heavy and not real_services and not perf"` → full-unit-run.log：11184 passed / 67 failed。裁定全部为预存环境失败（file_adapters/geoparquet/geocompute/data_fabric 等从未触及文件域 + 缺 trio/pyarrow 类重型依赖；唯一 scope 相关失败 `test_component_lifecycle[trio]` 经 git stash 对照——基线同样挂，缺 trio 后端所致）
- [x] 改动域回归：`pytest tests/unit/gis_harness tests/cartography` → scope-regression-run.log：**2635 passed**，唯一失败即上述预存 trio 用例
- [ ] **完整门禁（含 golden 头照）须在主检出环境复核**：本 worktree 缺 golden 运行时基建（tsx/playwright 解析，诊断见 round-5.md 与 deferred）；本分支未触前端，golden 失配风险低，但合并前应跑一次
- [x] PR：含轮次台账、每轮门禁摘要、指标对照、遗留与 deferred、回滚点（tag qc-loop-r{1..5}）；**不自行合并**
