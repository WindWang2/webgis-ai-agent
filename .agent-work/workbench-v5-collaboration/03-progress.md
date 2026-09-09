# 03 — Progress（全部完成）

| Wave | 状态 | 提交 |
|---|---|---|
| W1 WorkbenchDoc V5 嵌套分组树模型 + V4 迁移 | ✅ | `feat(workbench): W1 ...` |
| W2 Agent lock 强制（visibility/remove + typed conflict） | ✅ | `feat(workbench): W2 ...` |
| W3 patch_workbench_state intent + 前端持久化/恢复 | ✅ | `feat(workbench): W3 ...` |
| W4 Undo/redo 命令模型 + op journal 接活 | ✅ | `feat(workbench): W4 ...` |
| W5 多 tab 协同基座（BroadcastChannel） | ✅ | `feat(workbench): W5 ...` |
| W6 真双面板对比 + 副视图 parity | ✅ | `feat(workbench): W6 ...` |
| W7 视口 thinning + 陈旧视口取消 | ✅ | `perf(workbench): W7 ...` |
| W8 10k 图层树虚拟化 | ✅ | `feat(workbench): W8 ...` |
| W9/W10/W12 编辑 UX + artifact linkage + a11y | ✅ | `feat(workbench): W9/W10/W12 ...` |
| W11 刷新恢复/会话锚 + pagehide 冲刷 | ✅ | `feat(workbench): W11 ...` |
| 全量门修复（mock 形状/防御守卫） | ✅ | `fix(test): ...` |
| 两轮 review 修复（C×2/M×6/m×9 + ADR-0105） | ✅ | `fix(workbench): 两轮 review 修复 ...` |

origin/master 未前进（基 445ad30e == 最新 origin/master），无需 rebase；全部门禁在最新基线上验证。

## 验收指标对照

- ✅ group tree 重启后保持（patch_workbench_state 落盘 + 恢复水合 + 会话锚自动恢复）
- ✅ layer lock 同时约束人机/agent path（visibility 事务 + remove_layer typed `layer_locked`）
- ✅ undo/redo 对 async/agent mutation 不破坏状态（反向重放模型 + 共享 import promise 修复 + R1-M1 污染修复）
- ✅ two-view 可验证 parity（is3D/图例过滤/选择过滤/副图族图例 + 双面板契约测试）
- ✅ 10k layer tree 操作不卡死（窗口虚拟化 + Set 化投影 + 10k 压测）
- ✅ refresh/reconnect 不产生 ghost/duplicate layer（既有 pendingRemoved/allowedIds 防御 + 会话锚走同一恢复管线）
- ✅ 不创建与后端 project truth 竞争的隐藏 state store（doc 走 MapSpec 同一 CAS 链；锚只存指针）
