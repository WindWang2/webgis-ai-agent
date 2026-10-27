# G3DV2-B INDEPENDENT_REVIEW — 独立评审（两轮）

评审者：未参与任何实现的独立 subagent（general-purpose 上下文）；方法 = 全 diff 通读 + 调用路径实证（含 maplibre-gl 6.10.0 `_contextLost`/`Style._remove` 内核源码与 three 0.186.1 `Raycaster.intersect` 行为追踪）+ 测试实跑 + `gh pr view 1835` 交叉核对。评审者被明确要求"不得复述作者结论"。

## Round 1（commit c9373388e）
**判定：0 P0 / 0 P1 / 3 P2 / 7 P3 —— 核心工程面（glTF 严格语义/账本算术/生命周期状态机/ACK 链）被对抗验证为正确，缺陷集中在 M5 交互面。**

| # | 级别 | 发现 | 位置 |
| --- | --- | --- | --- |
| 1 | P2 | 本地隐藏资产仍可拾取（three intersect 不看 group.visible） | asset-manager.ts pick() |
| 2 | P2 | 受控 checkbox 直读 spec visible → 重渲染弹回勾选形成死角开关 | scene-controller.tsx |
| 3 | P2 | tile 预算推迟路径（budgetDeferredAt/退避/跨压力逐出）零测试覆盖 | asset-manager.ts loadTile/evictForBudget |
| 4 | P3 | acquire→buildModel 抛错窗口记账幻影（30s 退避重试叠加） | loadTile |
| 5 | P3 | decodeIndices 缺 count/byteOffset typed 校验；sparse-only 索引保守拒绝无注释 | gltf-parser.ts |
| 6 | P3 | evictForBudget 可在 200ms 节流窗口逐出刚可见 tile（自愈瞬态） | evictForBudget |
| 7 | P3 | canvas context-lost 监听 + preventDefault 改变所有会话（含纯 2D）的恢复可达性 | map-panel.tsx |
| 8 | P3 | 清扫释放启发式耦合 MapLibre 精确行为，两处未防护窗口（addLayer 先抛/onRemove 语义变更） | unmountAsset |
| 9 | P3 | OpenAPI 快照吸收无关 master 漂移（storymap 1.1/set_scene 等）；openapi.json 与开放 PR #1835 单文件冲突 | snapshots/openapi.json |
| 10 | P3 | 杂项：pendingLighting 模块级跨 map 渗漏、loadInFlight 死记账、budgetLimits"只能收紧"注释未强制、两个继承自 master 的 no-op 测试 | 多处 |

**Verified-OK 清单**（评审者对抗后确认）：预算 acquire/release 全路径平衡（六条路径逐一追踪）；泄漏修复在 6.10.0 无双重释放（removeLayer→onRemove 恰一次；Style.destroy 后清扫 no-op）；glTF 交错/sparse 数学（含 count=0 边界/负满幅 clamp）；指纹回退不可能被错误接受（服务端重算指纹硬门 + revision 对齐）；handleContextRestored 状态/账本一致；2D 回归、corpus 语义、i18n 21 键 zh/en 精确平行；EVIDENCE 诚实性（commit 链与计数核对）。

## Round 1 修复（commit 199485ebe）
P2-1 pick 增表象门；P2-2 checkbox 改本地覆盖 map（effectiveVisible = spec ∧ override，spec 隐藏禁用）；P2-3 新增 tile 推迟/逐出/归还集成测试；P3-4 build 抛错归还；P3-5 indices typed 校验；P3-7 监听按 3D 资产在场门控（2D 零行为差）；P3-10 WeakMap 作用域 + 死记账移除 + 注释纠偏；P3-6/8/9 披露更新（RISKS R6 逐项漂移明细 + #1835 快照冲突 + 重叠矩阵修正）。

## Round 2 复核（commit 199485ebe）
**判定：PR-ready。** 实跑 8 文件 114 测试绿 + tsc 0 错；逐项核对：P2-1/2-3/P3-4/5/7/9/10 全部 FIXED（P2-3 测试被确证非空洞断言——推迟语义/逐出释放重记/归还清零三段各有判别性断言）；P2-2 FIXED 带 1 个新 P3 残差（revision 换代重挂后控制器覆盖 map 未随管理器复位——标签滞后、点击自愈、非状态分叉，已记 RISKS R3）；P3-8 维持披露（当前 6.10.0 实证平衡）；两处文档 nit（sparse-only 索引拒绝注释、5s 退避行无覆盖）已在最终 commit 补注释/记录。

## 最终复验（本轮）
- vitest 全量：**4660 passed + 1 expected-fail + 1 既有 master 红**（catalogue，干净 origin/master 检出同红取证）。
- tsc 双配置 0 错；eslint 0 警告；ruff 通过；j9 真浏览器通过（含纯 3D ACK 断言）。

**结论：无未清偿 P0/P1；P2 全修；P3 全部修复或带证据披露。具备 PR 条件。**
