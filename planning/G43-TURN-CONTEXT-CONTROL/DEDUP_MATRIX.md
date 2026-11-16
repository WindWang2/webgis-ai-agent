# G43 DEDUP MATRIX

| 能力/要求 | 状态 | 依据（生产调用链） |
|---|---|---|
| typed assembly + receipt 对象 | present | context_assembly/{contract,receipt,assembly}.py；receipt 含 decision lines/digest/governor |
| receipt 持久化 + 按 turn 读取 | missing | 全仓无持久消费者；bridge 丢弃 `_receipt` |
| 提交状态（prepared/not_sent/submitted/handled/queued/failed） | missing | bridge 只在 SSE 里对 handled 特判，无回执状态面 |
| 来源粒度（pk 条目 / cm 事实） | missing | knowledge:card / carto:memory 均单块 item |
| 用户可选资料策略 + CAS | missing | 无 policy 入口；无表 |
| 预览（无副作用） | missing | UI 不能触发（无端点）；完整装配有副作用（plan slot/demotion/situation advance） |
| 记忆/知识管理面 | present（复用） | project.py /carto-memory CRUD、knowledge 面板 —— 不重做 |
| G36 图像回执 | present（复用） | visual_context store + attachment_receipt SSE —— 只链接不重管 |
| G37 模型/用量 | present（复用） | model_runtime + model_status SSE —— 只在 receipt 中记录 not_sent 原因 |
| 预拼块抑制派生 provider | present | assembly `_collect_wave(skip_cartography)` + scope gate caller 豁免 |
| 显式策略下预拼块绕过 | missing（需堵） | typed 路径 carto:legacy caller 项整块注入；无策略校验 |
| @tool 只读回执查询 | missing | 无 webgis_turn_context_* 工具 |
| 前端资料面板 | missing | 无组件/事件/API client |
| fence/secret scrub | present（复用） | fence.py —— 所选数据仍不可信 |
| G22 恢复目录 / G28 完成证据 / G24 选择句柄 | present（边界，不改） | 本任务不触碰 |

owned-by-other：G42 goal repair（无 PR，不占用）；G40 资产（已合并，仅作消费方视角）。
G09/G21：历史分配未检索到 PR，不属于本域，不占用。
