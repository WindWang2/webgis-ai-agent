# DECISIONS — G05-SITUATED-WORKBENCH

| # | 决策 | 理由 | 备选与否决原因 |
|---|---|---|---|
| D1 | selected_ids 上限三方对齐 50（而非 8 或无限） | 前端 selection-store 已按 50+谓词运行；analysis_scope 上限 50；50×32 字符 id ≈1.9KB 在 4KB 有界预算内 | 无限：违背有界契约；8：即 #2004 本身 |
| D2 | 选择 kind 载荷预算 4096B（per-kind），通用 512B 不变 | 只放 ids 不放预算会把"截断"恶化为"整事件拒绝"（更隐蔽） | 全局提预算：放松 19/20 kind 的纪律 |
| D3 | ids_truncated 服务端派生，覆写客户端申报 | 前端上报不是可信事实（红线）；防御性冗余仅在客户端先行披露 | 信客户端：一个谎报标记破坏整个披露面 |
| D4 | 无 id 图层框选发 count-only feature_selected | 零上行=模型失明比"空集"更不诚实 | 新 kind：词表纪律要求真实钩子语义，count-only 复用现有 kind 足够 |
| D5 | bbox 进 feature_selected 白名单（4 元数值） | "刚才框选的"作为空间范围可指代；几何本体仍走 affordance 环 | 完整几何进 IR：预算/隐私面失控 |
| D6 | chart categories 8→20（对齐 selection-store） | 图表 12+ 类别联动对 Agent 只见 8 是同族断裂 | 保持 8：与前端 store 契约失配 |
| D7 | 史代披露不用 id_count < matched_count 推断 | 部分取消是用户显式决策，非静默截断——两者必须可区分 | 计数推断：把用户决策误报为截断 |
| D8 | invertible 扩展 paint/symbol 且要求 before 全数值 | before/after 已在回执；"原本缺失"无法经 patch 恢复是事实约束 | 全 kind 可逆：视图/删除不可逆是语义事实 |
| D9 | SW-09 用被动披露行而非翻转 display 默认 required | 默认翻转是部署级行为变化（历史行为零变化纪律）；诚实披露是 UI 层 additive | 翻转默认：影响所有旅程/测试基线，非本方向所有权 |
| D10 | SW-08 原生 select 而非改 recharts 内核 | Tab/方向键/Enter 免费获得；同链（publishSelection+IR）；不重构视觉系统 | recharts 键盘化：大改渲染器，违避让红线 |
| D11 | SW-05 订阅 invalidations 500ms 防抖 | 同源广播（interaction ack 已驱动 situation 投影），非轮询 | 轮询：违反"事件有顺序与隔离"纪律 |
| D12 | ADR-0287（下一个空号；#2012 用 0286 未合并） | 编号冲突以 master 实测为准 | — |
| D13 | 不修 #1999-#2003/#2005-#2007 | 非本方向集成链（导出/指北针/族锁/单位/summary）；任务书明示不抢修 | — |
| D14 | 地图终验四层不聚合为单一"超级卡" | 四层各有权威与生命周期；聚合=第二真相 | 单卡：生命周期错配（receipt 即时/裁决延迟） |
| D15 | 矛盾披露（matched_count < ids 数）不交叉校验、原样透传 | 可能是跨子层去重后的合法状态；误报截断比保留原值更有害（复审 T7/P3-8 确认为有意设计，测试钉扎） | 强制 matched ≥ ids：误伤去重场景 |
| D16 | id 裁剪统一 64 字符（emit 层） | 与 brush/selection-store 解析 64 对齐，防 33~64 字符真实 id 在 IR 与数据面身份漂移→存活核对 all_missing（复审 P3-4） | 保持 32：身份漂移 |
