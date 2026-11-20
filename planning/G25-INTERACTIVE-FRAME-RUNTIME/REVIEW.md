# G25 REVIEW — 独立审查记录

## R1（独立 reviewer，全量 diff BASE b8f2a6ecc→f53fb6b4a + 修复轮）

审查者：未参与实现的独立 subagent（10 重点面 + 独立复跑：前端 57/57、
后端 55/55、J18 mock 真实浏览器 16.3s、BASE 既有失败双侧复现）。

### 结论：修复后可提交（0 BLOCKER / 6 MAJOR / 6 MINOR / 多 INFO）

| 级别 | Finding | 修复 |
|---|---|---|
| MAJOR M1 | 错误/loading 帧捕获按 desired 版本入库冒充当前快照 | ✅ f-<next>：仅「帧在场+无错误+落定==desired」才入库；其余不存（宁缺毋假） |
| MAJOR M2 | latestRows 关闭/切会话不清空 → 观察 POST 永久回放旧声明 | ✅ clearFrameObservations() 接入 handleClose + 会话切换 |
| MAJOR M3 | context loss 重建后 hub 绑定旧实例（同步静默失效） | ✅ 轮询校验 hub.entryMap 身份不一致即重 attach |
| MAJOR M4 | 预算被占时打开网格，释放后永不自动挂载（banner 消失误导） | ✅ 调度 effect deps 加入 registryTick |
| MAJOR M5 | 守卫/路由死代码 + A20 证据过期（A03/A08/A17/A20 虚标） | ✅ lockedExtent/cameraWriteGuard/checkProjectionCompat/edit-scope(opacity 面板) 全部接线；A20 降 △ 如实披露平台缺口 |
| MAJOR M6 | 前后端帧解析三处分叉（iso_string/宽容 vs 严格/palette_colors+independent） | ✅ time_window 缺字段宽容对齐后端；independent 跳过；palette_colors 不匹配丢弃+披露；「逐位一致」声明降级为范围声明+边界披露块；补 2 对齐用例 |
| MINOR m1 | HiDPI 快照尺寸口径不一致 → 全部误判 stale | ✅ 捕获尺寸除以 pixelRatio（CSS 像素口径） |
| MINOR m3 | 键盘 deps 用数量，同数换 id 闭包滞后 | ✅ deps 改帧 id 串 |
| MINOR m5 | real 档观察断言只查 HTTP 200（指纹门拒绝也 200） | ✅ 断言 accepted=true |
| MINOR m6 | 删除帧快照可在同 id 复活 | ✅ M1 修复的「帧在场」前置天然覆盖 |
| MINOR m2/m4 | 32MiB 口径=base64 字符数；viewportSize last-wins | 接受现状（注释/单瓦片网格下无症状；登记不改） |

### reviewer 确认面（无需修复）
- applying 门/防回声/registry 计数/CAS 回灌/资源清理对称/seed fail-closed/
  J18 mock 非自证 stub/观察词表与 revision 域 —— 全部核实成立。
- comparison-view/map-panel/render-observation 补丁最小加性；BASE 4 失败
  与本方向无关（双侧复现）。

## 复审（R1 修复后）

修复轮完成后全量复跑：前端 8 文件 + hud 面板 + 后端观察接线全部通过，
J18 mock 真实浏览器复跑通过；lint/typecheck 0。详见 TEST_EVIDENCE。


## R2（同一 reviewer 复审修复提交 1bfd523f5 + 053445665）

**最终结论：可提交。** 六项 MAJOR 与四项 MINOR 均实质修复并经独立验证
（reviewer 自建不入仓仿真证伪 warmup 活锁假设；J18 mock 在修复后 HEAD
独立复跑 11.3s 通过）。

### R2 后续修复（本轮）
- r1 锁定 extent 帧的初值相机放置过门（lockedExtentOnly 跳过 initialCamera
  jumpTo）✅
- r3 time_window 缺 start/end 对齐后端跳过 ✅
- r2 面板路由分支的 UI 级测试：尝试 3 种定位/事件路径后按时间盒撤回
  （jsdom 受控滑杆事件时序与 draft 状态闭包不稳定）；路由逻辑本体由
  frame-edit-scope 4/4 + 类型/链接检查覆盖，UI 级断言登记 follow-up ✅（诚实降级）
- N1（warmup 相位 registry tick 抖动，收敛已实证）：接受现状登记 follow-up

### 残余 follow-up（不阻断）
1. r2：打开网格态的 panel 路由 UI 测试（真实浏览器 J18 扩展可承载）。
2. N1：调度 effect 改为「live 槽位空或预算刚释放」门，消除 warmup 相位抖动。
3. m2/m4：32MiB 口径注释、多尺寸瓦片 viewportSize 抖动（等宽网格无症状）。
4. iso_string×time_format_hint 同构渲染（两端一致性边界已披露）。
