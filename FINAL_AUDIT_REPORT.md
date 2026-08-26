# FINAL_AUDIT_REPORT — WebGIS AI Agent 全自动审计与 Issue 闭环（2026-08-26）

> 执行基线: master @ `e349b18`（审计起点） → 收官: master @ 本报告提交
> 方式: 全自动、只读分析 → GitHub Issues（#978–#1011）→ 6 个修复批次（worktree + 资源受限本地测试 + commit + merge）
> 验证环境: 16 CPU / 62GB RAM 本地；测试全程 `OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2`、分模块运行、未使用线上 CI

---

## 1. 执行概览

| 阶段 | 内容 | 产出 |
|---|---|---|
| 一 | 5 个并行专项审查（Harness / GIS 算法 / Tool 系统 / 模型库 / 前端），全部结论 file:line 实证 | `audit-report.md`（commit 3426ca6） |
| 三 | Harness 重设计分析（七层目标架构映射、三条断链、GIS 知识库/模板库/算法库现状与补齐、Model Library 三步走） | 并入 audit-report.md §7 |
| 四 | 分类建 Issue（labels: architecture/performance/algorithm/harness/tools/models/frontend + P0–P3） | #978–#1010 共 33 个 + 收尾 #1011 |
| 五 | 6 批次修复（P0→P1→P2→P3），每批独立 worktree/分支，本地测试后 merge | 见 §2/§3 |
| 八 | 全仓回归 + 本报告 | tests/unit 2940 通过；cartography 门禁 41/41；根契约层 21/21 |

**Issue 闭环状态**: 34 个 Issue 中 **32 个已修复闭环**（#978–#1008、#1010），**2 个留作后续**（#1009 巨型组件拆分属多 PR 长期重构；#1011 预存失败甄别，均为本轮发现但超出单批安全修复范围）。

## 2. 修复列表（按批次）

### Batch 1 — P0/P1 Harness+Tools 核心（merge 61c0603）
| Issue | 修复 |
|---|---|
| #978 (P0) | `finalize_display` 孤儿域 `cartography`→`mapspec`；新增 tier-2 关键词可达性守护测试（一断言堵死整族缺陷） |
| #979 | `webgis_map_intent/product` 增加有界 `guidance` 投影（capability→resolved_tool、fallback/completeness 证据）并进 `_PRESERVED_META_KEYS`——harness 计划裁决首次真正到达 LLM |
| #980 | `fold_intra_turn_tool_results`：当前回合只保留最近 8 条 tool 结果原文（配对保持的占位折叠），组装期应用；内存 append 与 DB 同钳 100K——token 二次方增长止血 |
| #981 | tier-2 schema 增量硬预算 24KB（env 可调，fresh 域/webgis_* 前门优先）+ 粘性域上限 4（按最近命中） |
| #983 | `reproject_coordinates` 补域；高德路网族 4 工具 +chinese；`list_available_tools` enum + unknown-domain 返回 available_domains 纠错 |
| #984 | 错误归一第四族 `{success:False, message}`（17 站点）纳入失败折叠（与 metrics 口径对齐）；repeated 文案改诚实语义（未重新执行/可能过期/微调参数） |

### Batch 2 — P1 模型库（merge 23d82e4）
| Issue | 修复 |
|---|---|
| #985 | `stream_options.include_usage` + usage 帧在 choices 检查前捕获 → done 事件透传；非流式响应 usage 记账；`TurnEvidence.add_llm_usage` + `llm_usage` 汇总块 |
| #986 | 连接相位（Connect/PoolTimeout）+ 建流前 429/5xx 有界重试（3 次指数退避）；已产出 token 的中途失败绝不重试 |
| #987 | Pi spawn env 映射 `LLM_API_KEY→OPENAI_API_KEY`（占位符不映射）；可选 `PI_PROVIDER+PI_MODEL` → `set_model` RPC |
| #997 | 新 `app/services/chat/model_config.py`：`resolve_llm_config(role)` 单一解析点（execution/planner/title/spatial）+ 运行时覆盖传播；Settings 增 `LLM_TIMEOUT_S/LLM_MAX_TOKENS/LLM_TEMPERATURE/LLM_TITLE_MODEL`；spatial_reasoning 不再直读 settings |
| #1005 | 标题调用 TITLE 角色（max_tokens 64→512、只取 content，杜绝推理前缀当标题）；/llm/test 60s 去抖；删除 X-Prompt-Cache/deseek 非标头；移除 openai 死依赖 |

### Batch 3 — GIS 算法（merge e82a210，委托子代理）
| Issue | 修复 |
|---|---|
| #991 | 等时圈设施→边投影 STRtree 建树（数值等价验证，50–100× 提速）；修正失实注释 |
| #1002 | 变化检测网格参数对齐判定（res 容差+xy 相位）；Moran KNN 权重对称化（w∪wᵀ 后行标准化）；`nearest_target_id` 优先业务列否则改名 `nearest_target_index`；DEM 哨兵前置掩膜（未声明 nodata 走 NaN-aware 平均降采样，仅 DEM 路径启用） |
| #1003 | MVT 反子午线标志建索引时预计算（消除每瓦片每候选 O(顶点) 重扫，+8B/feature） |
| #1010 | numpy 约束收紧 `<2.5`（numba ABI 兼容；pyproject 镜像同步）——修复全新安装 LISA 全族 ImportError |

### Batch 4 — Harness 语义（merge 51c10d1，委托子代理）
| Issue | 修复 |
|---|---|
| #982 | Pi `stream_prompt` 整轮总预算（复用 PI_TURN_TOTAL_TIMEOUT，滴答流也会被切断；timeout_reason 区分 budget/stall）；预算注释漂移修正 |
| #992 | 图层清单 20 行上限 + 汇总行；截断发生在 schema 推断**之前**（隐藏 ref 不再全量取数） |
| #993 | `webgis_map_intent` tier 2→1（tier-1 满员 40，降级最低频 `webgis_project_init` 至 tier-2+mapspec 域，实测数据决策）；Pi 迟到回调按 verifiedTurnId 归属，错配丢弃+warning |
| #994 | `PlanStep.tool_binding`（合成计划从 resolved_tool/capability 候选集填充，canonical 往返不丢失）；`advance_step` 绑定步骤精确匹配，无关工具不再推进 |

### Batch 5 — 前端 UX（merge 2d6b7c7，委托子代理）
| Issue | 修复 |
|---|---|
| #988 | `useMapBridge.cancel()` 暴露 + isBusy 时发送键切换"停止"形态（合成 task_cancelled 终态，消息流 UI 直接承接） |
| #989 | `emitSyntheticError` 改走 `describeApiError`（后端中文 detail 不再丢弃） |
| #998 | 热力图例 min/max/unit 量化刻度行（formatLegendValue 同源）；色条弃 toFixed(1) 换统一格式化器 + unit |
| #999 | 最小版小屏保护：`mapInsetLeft` 视口 50% 上限 + <768px 默认收起面板（完整响应式重构留路线图） |
| #1000 | 失败链默认展开（尊重用户后续手动开合）；错误 InlineNotice 增"重试上一条" |
| #1001 | HUD 空闲态 rAF 冻结（仅 isThinking 保留 JS 相位），消灭空闲 60fps setState 循环 |
| #1007 | 标注默认 1px 白色 halo（编译器无主题上下文，采用 GIS 惯例 halo 方案；SVG 导出同源受益）；glyphs URL 进 `NEXT_PUBLIC_MAP_GLYPHS_URL` |
| #1008 | 5 处裸 console → devOnly；eslint scoped `no-console: error` 防回归 |

### Batch 6 — Tool 元数据（merge 8a49a94，委托子代理）
| Issue | 修复 |
|---|---|
| #990 | `heatmap_data` native 分支顶层浅拷贝后写元数据——get_shared 只读契约恢复（双 palette 连续调用 store 本体逐字节不变已验证） |
| #995 | 6 站点 schema 约束：render_type/palette/mode/provider/profile/index_type→Literal（取值抄自体内合法值）、lat/lon→范围、日期→pattern；运行时校验不动，schema 前置拦截 |
| #996 | @tool 注册契约增 `cost: light/medium/heavy`（非法值注册期拒绝）；3 个内部投 Celery 的工具标 heavy+timeout；webgis_map_intent/product bump contract_version=cv2 |
| #1004 | `json_schema_extra={"ref_cursor": True}` 声明式游标通道（registry 统一识别，旧硬编码名单兼容保留） |

## 3. Commit 列表

```
3426ca6 docs(audit): add 2026-08-26 round-4 full-scope audit report
686568a fix(harness,tools): … (closes #978,#979,#980,#981,#983,#984)
61c0603 merge: audit4 batch 1
becea2d fix(gis,deps): STRtree isochrone projection, algorithm edge batch… (closes #991,#1002,#1003,#1010)
e82a210 merge: audit4 batch 3
65966d7 fix(models): usage accounting, connect-phase retry, role-based config… (closes #985,#986,#987,#997; #1005)
23d82e4 merge: audit4 batch 2
ccfbb9d fix(harness): Pi stream total budget, layer inventory cap… (closes #982,#992,#993,#994)
881fa7c test(pi): align post-success dedup wording assertions with #984
51c10d1 merge: audit4 batch 4
e0cd32a fix(frontend): stop button, readable stream errors… (closes #988,#989,#998–#1001,#1007,#1008)
2d6b7c7 merge: audit4 batch 5
4912c1f fix(tools): shared-ref immutability, schema enum constraints… (closes #990,#995,#996,#1004)
8a49a94 merge: audit4 batch 6
```

新增守护/回归测试 **~150 个**（batch1 15 + gis 17 + models 12 + harness 10 + frontend 87 + tools-meta 17，含对既有断言的契约更新），全部通过。

## 4. 架构改进总结

1. **意图→裁决→执行三断链打通**: guidance 投影使 harness 计划可见（#979）+ PlanStep.tool_binding 使执行与计划对账（#994）+ webgis_map_intent tier=1 恒可达（#993）——"GIS Harness 只有建议权"的核心机制缺陷解除，向 `Intent → Planner → Execution` 接管执行演进铺路。
2. **上下文经济学**: 轮内折叠（#980）+ schema 硬预算与粘性上限（#981）+ 图层清单上限（#992）——prompt 从"无界增长"变为"三层有界"（历史 6000 token / 轮内 8 条原文 / 工具面 24KB 增量）。
3. **诚实性契约**: 错误归一第四族 + repeated 诚实文案 + 失败链默认展开——失败不再被伪装成成功。
4. **用户控制回路补全**: 停止按钮 + 重试上一条——Feedback Loop 双向闭合。

## 5. Harness 优化方案（已落地 / 路线）

已落地: 上述三断链 + 三层上下文预算 + Pi 流式总预算（跨会话队头阻塞的失控面封死）+ 双引擎漂移三处对齐。
路线（按 ROI）: ① 高置信意图确定性短路（免规划 LLM 调用）；② tier-1 收缩至 ≤15 高频工具（需产品逐工具论证）；③ Pi 按会话分桶锁或并发 prompt；④ 两引擎能力矩阵契约测试固化为常驻护栏；⑤ GIS Ontology 单一词汇表（intent 城市表与 DOMAIN_KEYWORDS 合流）。

## 6. Tool 体系优化方案（已落地 / 路线）

已落地: 发现性守护（tier-2 域可达性不变量）、错误契约第四族归一、cost 三档注册契约、contract_version 首次真实 bump、声明式 ref-cursor 通道、schema 枚举前置拦截、共享 ref 只读契约恢复。
对照目标九类工具: 八类覆盖已确认，唯一能力缺口为矢量格式落盘导出（SHP/GPKG 下载）——留作后续 issue。
路线: cost 分档驱动 wave 信号量与 catalog 提示、存量错误形态渐进迁移 std_error_response、`{"error": str}` 139 站点收敛、version bump 纪律进 code review checklist。

## 7. Model 体系优化方案（已落地 / 路线）

已落地（Registry 最小前体）: `resolve_llm_config(role)` 单一解析点 + 运行时覆盖、usage 全链路记账（TurnEvidence.llm_usage）、连接相位重试、temperature/timeout/max_tokens Settings 化、Pi 凭证映射与 set_model 通道、标题辅助调用修复。
路线: ① ModelRole→ProviderConfig 注册表（角色级温度/预算）；② usage 出每会话成本报表（CostManager 雏形）；③ Provider 接口拆分（OpenAI 兼容为第一实现，Anthropic/Ollama 按需）；④ ModelSelector 按任务路由（简单查询→小模型）。

## 8. 验证与测试

| 门禁 | 结果 |
|---|---|
| tests/unit 全量（资源受限） | **2940 通过 / 12 失败**——12 项与审计前基线完全一致（#1011 甄别：3 项本机 sys.executable→AppImage 环境假象、1 项网络依赖、2 项真实预存契约漂移、6 项待甄别），**零新增回归** |
| cartography 门禁（release-blocking 确定性子集） | 41/41 通过 |
| 根契约层（tool_meta / ci_local / perf_coverage） | 21/21 通过 |
| ruff（app/ 全仓 + 新测试） | 全部通过 |
| 前端 | 新增 87 测试 + 相关回归全绿；eslint 改动文件 0 warning；typecheck 零新增错误（环境预存的 plugin-react d.ts/TS 版本失配见 #1011 类） |
| numpy 兼容 | 2.4.6 下 statistics 全族 12/12（修复前 3 失败） |

## 9. 遗留事项

- **#1009**（map-panel.tsx 1223 行拆分 + MapLibre 边界 317 处 any）: 留 open，多 PR 渐进重构路线已写入 Issue。
- **#1011**: 12 项预存失败甄别清单（含 2 项真实项的修复建议）。
- **#982 中期项**: Pi 会话分桶锁/并发 prompt 未做（Issue 内已注明另立）。
- **#999 完整响应式**: 本轮仅最小保护，抽屉式小屏交互留路线图。
- 本机环境事实记录: 本地 .venv 实际解析到 uv 基础解释器 + user-site 包（numpy 已降至 2.4.6、esda/libpysal 已补装）——与 CI 全新 pip 安装路径不完全同构，CI 为最终权威。

## 10. 结论

本轮将仓库从上一审计闭环点（#977）推进到 **#1011**：新增 40 项实证发现全部转为 Issue，其中 32 项已完成代码修复、测试守护与合并；平台在 Agent 循环效率（三层上下文预算）、诚实性契约、成本可观测（usage 记账）、GIS 算法正确性尾巴、制图读数保真（量化图例/色条/标注光晕）与用户控制回路（停止/重试）六个方向获得实质升级。"LLM 调用 GIS 工具"向"面向空间智能任务的 GIS Agent Harness 平台"演进的三条关键断链（意图裁决到达 LLM、计划-执行绑定、用户中断回路）已打通，Model/Tool Registry 的最小前体（角色化解析 + cost/版本契约）已落地并有守护测试锁定。
