# G28 DUPLICATION MATRIX

| 能力 | 现状证据 | owner/PR | 本任务剩余增量 | 明确不做 |
|---|---|---|---|---|
| goal_satisfaction 评估 | ADR-0183 已有（5 文件 ~1700 行）+ 语料 | 既有 | receipt 证据面 + 语义闸 + frame 闸（加性） | 不造第二 evaluator |
| frame 验收 | G15 #2076 verify_frame_product + frame_evidence_rows | G15 | finalizer 持久化 frame_product 块（缺口闭合） | 不重写验证逻辑 |
| 矢量编辑回执 | G14 #2075 EditReceipt/journal | G14 | 只读 adapter 消费 | 不改 executor |
| 字段计算回执 | G19 #2074 ComputeReceipt（诚实分母） | G19 | 只读 adapter + 单位/分母闸 | 不改 interpreter |
| 导入回执 | G17 #2079 CommitReceipt | G17 | 只读 adapter | 不改 commit |
| 交付回执 | G18 #2080 DeliveryReceipt+manifest.verify | G18 | 只读 adapter + EXPORT 需求绑定 | 不改交付服务 |
| 栅格回执 | G20 #2078 commit receipt/MapSpec | G20 | 只读对账（data_revision vs checked_revision） | 不改 render |
| 数据刷新 | G10 #2022 Adoption/CheckReceipt | G10 | 消费 map_observation=unverified 阻断语义 | 不做 session 桥（无则 unknown） |
| 标注 | G16 #2077 overrides+sourceRevision | G16 | S2 约束未被破坏的验收消费 | 不改 mutation |
| 持久 job | G23 未合入 | G23 | S5 按现有 queued/running 语义诚实 unknown | 不伪造持久 job 证据 |
| selection snapshot | G24 未合入；interaction_ir 已有 claimed scope | G24 | S3 用 claimed scope/matched_count/truncated | 不伪造数据集回执 |
| mixed 输出 | G26 未合入 | G26 | S4 → unsupported/blocked 诚实披露 | 不写占位 PDF 回执 |
| scale 事实 | G27 未合入 | G27 | 不消费 | 不造 scale receipt |
| Pi 生命周期 | G22 未合入；#2081 已合 v1.5.1 | G22 | 现有 pin 上跑闭环 | 不动 vendor |
