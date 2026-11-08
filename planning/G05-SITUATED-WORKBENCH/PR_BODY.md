# feat(g05): 情境工作台范围披露与诚实终验闭环 —— #2004 选择截断族清偿 / undo 扩展 / 终验收口 / 金标回放

## 问题（G05 审计：用户看到的联动 ≠ 模型收到的事实）

1. **#2004（P0）**：框选 23 要素时 UI 如实报 23（本地 store 50 ids + matched_count），但 IR 上行被前后端**双侧静默截到 8**（`bus.ts .slice(0,8)` + `contracts.py value[:8]`），且 `matched_count` 不在白名单——"这些学校"的分析范围被静默缩小；无稳定 id 图层框选**零上行**（模型失明）；chart 类别截 8（与 store ≤20 失配）；框选 bbox 谓词不进 IR（"刚才框选的区域"不可指代）；selection_history 截断标志是死代码。
2. **SW-04（P1）**：map-op `invertible` 只认显隐/透明度——Agent 改符号尺寸后用户无法 Ctrl+Z（before/after 事实已在回执里）。
3. **SW-09（P1）**：终验四层结构可区分，但 display-confirmation auto 模式下 UI 完全静默——"渲染已发生"被无声当作闭环。
4. **SW-05/SW-08（P2）**：可解释面板手动刷新；图表类别选择无键盘路径。

## 实现（ADR-0287；全部 additive，manifest 双闸同步）

- **SW-02 范围披露契约**：`feature_selected`/`table_selection_changed` 白名单 + `matched_count`/`ids_truncated`（**服务端权威派生**，双判据：原始 id 数>50 或 matched>携带数——前端预截样本不误判）；`selected_ids` 专属上限 50 **三方对齐**（selection-store=IR=analysis_scope）；选择 kind 载荷预算 4096B（512B 会把截断恶化成整事件拒绝）；`bbox` 谓词进白名单；chart categories 20 对齐；无 id 图层 count-only 上行。投影→声称面→验证面→选择史四层贯通（`truncated_sample` 披露进 `checks.features`；史代披露与 previous 句柄同权）。
- **SW-04**：`invertible` 扩展 paint/symbol（before 全数值在场；缺值诚实不可逆）；前端 `styleCommand`（undo/redo 经 `patch_layer_style` 用户通道反向重放）。
- **SW-09**：`FinalAcceptanceLine` 被动披露行——auto 模式明确"自动确认（未请求用户）"，required ack 后"用户已确认"，无裁决"暂无裁决"；四层终态各归其位不聚合第二真相。
- **SW-05**：AgentRunPanel 订阅 `projection-invalidations`（500ms 防抖 + 会话复核），事件驱动非轮询。
- **SW-08**：图表类别原生 `<select>`（Tab/方向键/Enter）与点击同链（publishSelection + IR 上行；空值=清除）。
- **SW-10**：18 条回放金标（含 5 条新语义：20 保真/80 披露/前端预截样本/类别 20/count-only），生产摄入同路径双跑确定性。

## 兼容性

- 全部 additive：旧事件（无新键）按缺席语义兼容；词表/上限由 `contract_manifest.json` 双闸（pytest ≡ JSON ≡ vitest）锁漂移。
- 不改 display-confirmation 默认 auto（部署级决策，本 PR 只保证 auto 下诚实披露）；不建第二真相/第二目录/第二渲染器。

## 证据

- 后端全域（situation+IR+harness+scope 工具+E5 多轮旅程+SSE chaos）：复审清偿后 **3423 passed, 4 skipped**（清偿前 3488 基线亦全绿）
- 回放金标：**31 passed**（18 场景 × 双跑 + present；2 条 32 字符 id 真实路径）
- 前端全量 vitest：**5011 passed**（1 expected fail 为基线）+ tsc 0 + eslint 0 + i18n 双语
- 新增测试：后端 24+（含复审钉扎）+ 语料 5；前端 13
- 契约文档：`docs/SITUATED_WORKBENCH_CONTRACT.md`、`docs/INTERACTION_REPLAY_GOLDENS.md`、`docs/UX_JOURNEY_EVIDENCE.md`（J1–J8 → 测试证据映射）；ADR-0287
- 本地复现命令见 `planning/G05-SITUATED-WORKBENCH/TEST_MATRIX.md`

## 测试命令

```bash
.venv/bin/python -m pytest tests/test_gis_situation_*.py tests/unit/gis_harness/ \
  tests/unit/test_interaction_scope_tool.py tests/acceptance/gis_agent/test_e5_multiturn_journeys.py -q
cd frontend && pnpm vitest run && pnpm typecheck && pnpm lint
```

## 风险与未验证项

- e2e 真浏览器档（playwright real）未跑（需夜间账号；mock 档回归绿）——诚实披露于 UX_JOURNEY_EVIDENCE。
- `test_quality_manifest_gate`/`test_generated_artifact_graph` 在干净 master 同样失败（Issue #2007 基线已坏，实测归因，非本 PR 引入，未抢修）。
- 冲突面：与在途 #2011（execution compiler）/#2012（Pi native runtime）零文件交叠（BASELINE 冲突矩阵）。
- 独立复审：R1（BLOCK P0×1/P1×3，含实测复现的记录预算脱节 accepted-but-dropped）→ 全清偿 → **R2 SHIP（P0=0/P1=0）**；R2 遗留 3 项非阻塞亦当场清偿（诚实拒绝裁决/64 字符钉扎/bbox 非 list 丢键）。详见 `planning/G05-SITUATED-WORKBENCH/REVIEW.md`。
