# DECISIONS — GOAL 03 temporal map products

D1 MapSpec v1.8 新一等字段 `temporal`（MapSpecTemporalConfig, temporal_view.v1），不复活/不删除幽灵 `time` 分支（intents.py SetTimeIntent + REST SetTimeBody 保留原样，无消费者，向后兼容零风险）。理由：time 分支字段松散（type 自由串/current: Any/无时区/无缺失策略），无法承载科学契约；v1.5 data_binding/1.7 scene 的 additive 一等字段是既有样板。
D2 时间过滤载体 = 既有 where AST（feature_pages/aggregate 通道）+ 前端 MapLibre filter 表达式，两侧由同一 time_filter 编译器生成、同 fingerprint。零端点协议改动。iso/date 字符串字典序在「同一格式」前提下恒正确 → 契约固化 time_representation，bind 时探测并归一。
D3 MVT 无服务端时间索引 → render_path=client_filter（tiles 按既有预算流式、帧切换为客户端过滤，不重复下载）；统计/图表/表格走服务端 where AST pushdown（同 fingerprint）。诚实披露在 contract.disclosures。
D4 不向 map_operation.v1 EFFECT_KINDS 添加时间效果（任务书禁未审查 effect）；新增 temporal_view_* 薄工具族，经 ToolRegistry（Pi/legacy 同面）+ MapSpecLifecycleEngine CAS（origin=agent）。
D5 时间游标单一真相 = MapSpec.temporal（committed spec）。前端控制器无独立持久化状态；会话恢复读 committed spec。用户 seek/pause → origin=user mutation + user_pinned=true；agent control 在 user_pinned 时拒改 cursor（user-wins，镜像 ADR-0058 语义）。
D6 raster cube 路径：lakehouse window 端点全范围（x0=y0=0）单读 + 客户端切片，规避 #1837（transform 不随窗口平移，OPEN audit issue 不认领）；超 CUBE_WINDOW_MAX_CELLS → typed 降级诚实拒绝。新代码独立计算 bbox，不复刻缺陷。
D7 帧序列仅来自真实数据 distinct 时间桶（profiler 探测）；缺年不内插帧（missing_policy 只影响播放提示与相邻帧策略披露）；单期数据 typed fallback `single_period_unsupported`，绝不假动画。
D8 atlas 时序导出 = AtlasPolicy.driver 增加 "time"（additive enum）+ AtlasPageBinding time 窗口字段 + manifest 时间证据；复用 #1871 job/PDF/QA 引擎，不重复实现。
D9 区间语义统一 [start, end)（左闭右开），与 where AST 的 gte+lt 编译一致；年帧 = [当年1月1日, 次年1月1日)（timezone 按数据锚定的契约时区）。
D10 相对时间（「上一年」「最近3个月」）以数据 extent 为参考系（extent.max 所属期），不取 wall-clock now（避免时区伪造与不可复现）；relative_window 既有近似语义保留在其工具内不动。
