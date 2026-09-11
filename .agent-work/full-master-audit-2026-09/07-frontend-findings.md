# 07 — Frontend Findings（索引）

详证：agents/C/findings.md（P1×3 P2×1 P3×5）+ agents/C/layer-lifecycle-notes.md
（lifecycle 矩阵 / session-switch 污染面 / agent+manual 三路径结论）。

P1：C-1 agent remove_layer durability 永不发出（ST-P1-1 修复的 agent 路径回归，
主 agent 已复核确认守卫 undefined 折叠）、C-2 durability 响应缺会话复核（主
agent 已复核：postPresentationOnce 成功/superseded 两分支写游标前无复核，姊妹
路径均有）、C-3 agent reorder 只改 HUD store（主 agent 已复核：用户路径走
reorderLayersAndCommit，agent 路径裸 reorderLayers）。

P2/P3：C-4 vector-pdf 死接口、C-5 hydrate id() TypeError、C-6 MapSpecResult
重复字段、C-7 锁守卫双实现、C-8 组件工厂表缺 4 类、C-9 同步 reconcile ref 跳过
缺失。前轮遗留仍开放：ST-P3-5（presentation 不同步 runtime registry）、
ST-P2-3（components 整表替换无 CAS）。
