# 05 — Review Findings（两轮独立 review，与实现思路隔离的只读 reviewer）

## Round 1 — 架构/正确性/回归

| 级别 | 发现 | 处置 |
|---|---|---|
| CRITICAL R1-C1 | persistence 把 409 superseded 当提交成功：被拒 doc 被封为基线并广播其它 tab，组织态静默分叉 | ✅ 修复：superseded 分支回灌服务端 workbench 真相 + revision 收敛 + 不写基线/不广播；附回归测试 |
| MAJOR R1-M1 | reorder undo/redo 重放经 reorderLayersAndCommit 再次 recordCommand → redo 栈清空、undo 栈污染（redo 一次需 undo 两次） | ✅ 修复：重放改裸提交（store reorder + commitMapSpecMutation），附往返测试 |
| MAJOR R1-M2 | applyDocSlices 直写 mode 绕过 setWorkbenchMode 的 tab 协调，undo 静默回退用户模式 | ✅ 修复：组织态命令不含 mode（比较/回放均剥离） |
| MAJOR R1-M3 | 多目标（一 ref 多层）agent 可见性 undo 只覆盖首层（半撤销） | ✅ 修复：多目标只入 journal（诚实不可逆），单目标保留 undo |
| MINOR | 死代码 captureDocSnapshot/recordDocChange；respectLock 注释口径；noteAgentDisplayed 先于锁门标记；finalize lock 冲突误报 target_not_found；revision=-1 必弃广播；hello 应答泄漏未提交编辑；恢复失败后持久化断供；renderer idle apply 无守卫；disarm/清 store 顺序；预估口径差；订阅全量 stringify；虚拟模式展开行裁剪；undo 覆盖不一致；后端 intent 未入包导出 | ✅ 修复 9 项（m3/m4/m6/m7/m8/m13/m15/m9 注释/死代码删除）；其余记入 06 已知限制/follow-up |

## Round 2 — 性能/安全/UX/可维护性

| 级别 | 发现 | 处置 |
|---|---|---|
| CRITICAL R2-C1 | 64KB doc 闸与 10k 目标场景正面冲突（membership ~250-300KB），超限后持久化静默停摆且无用户反馈 | ✅ 修复：上限提至 256KB（前后端同值）+ 超限一次性 toast + 容量依据写入 ADR-0105 |
| MAJOR R2-M1 | 后端闸用 estimate_json_bytes（自述 metrics-only、码点近似）作正确性闸，与前端 UTF-8 实字节口径不一致 | ✅ 修复：改 `json.dumps(ensure_ascii=False).encode('utf-8')` 真实字节精确口径 |
| MAJOR R2-M2 | 409「回灌收敛」声明与实现不符（applyCommittedMapSpec 只回灌 layers 不回灌 workbench） | ✅ 修复：superseded 路径显式回灌 workbench 分支（与 R1-C1 同一修复），注释对齐实现 |
| MAJOR R2-M3 | useVirtualRows 的 ResizeObserver 空依赖 effect：容器迟到挂载（异步图层跨阈值）后永附着失败，窗口恒 640 回退值 | ✅ 修复：callback ref（挂载即观察、卸载即断开） |
| MAJOR R2-M4 | persistence 订阅对每次 store 变更 2-3 次全量 stringify（10k membership jank） | ✅ 修复：四切片引用门控（无关变更零序列化）+ json 预算传参消重 |
| MAJOR R2-M5 | 投影 per-layer `Array.includes`（O(n×(L+S))） | ✅ 修复：入口 Set 化（兼容 Set/数组入参） |
| MAJOR R2-M6 | undo 栈 50×2 份全量 doc 快照 + 折叠高频操作同样入栈（数十 MB 最坏） | ✅ 修复：折叠/展开降为 journal-only（不入栈）；其余容量在 256KB doc 下有界，membership 倒排列为 follow-up |
| MINOR | normalize 无数量上限；provenance 徽标不选中产物；undo/redo 无 UI 入口；会话锚无 TTL；注释与实现不一致 3 处 | ✅ 修复（组数/键数/名称上限、徽标 setSelectedArtifactId、面板头按钮、7 天 TTL、注释对齐） |

## 文档补记（R2 item 7）

- 新增 `docs/adr/0105-workbench-v5-collaboration.md`（决策/理由/已知限制/未来工作）；
- ADR-0104「不持久化」与 workbench-v4.md「side-by-side 下线」两处过时声明加 V5 修订指针。

## 记录为已知限制（不修，PR 披露）

- 虚拟化窗口固定行高下展开「更多操作」次行可见性受限（>200 行）；
- undo 不覆盖 remove_layer；多目标 agent 显隐只入 journal；
- presentation（显隐/样式）不做跨 tab 实时广播（collab 仅组织态 doc）；
- moveLayerGroup 拖拽 reparent UI 未接（API/守卫/测试已就绪）。
