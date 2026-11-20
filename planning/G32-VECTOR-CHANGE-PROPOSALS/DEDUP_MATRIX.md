# G32 DEDUP_MATRIX — 查重矩阵(每项追到生产调用链)

判定词表: present(已有,转兼容回归)/ partial(部分覆盖,补剩余)/ missing(本任务实现)/ owned-by-other(他任务所有权)/ blocked(外部阻塞)。

| 能力要求 | 判定 | 证据(生产调用链) | G32 行动 |
|---|---|---|---|
| 持久待审要素变更候选(跨页面/重启恢复) | missing | feature_edit plan 存 map_state TTL 600s(executor.py:602-616),重启/过期即失;review 提案是 MapSpec union(review_schema.py:168-195),不承载要素数据变更 | 新增 `app/services/feature_proposals/`(JSON 文件持久 + BlobStore base 快照) |
| 三方差异(base/current/proposed)按要素/字段/几何 | missing | refresh/merge.py 只做 MapSpec 表现层(PRESENTATION_LAYER_KEYS);无 feature/field/geometry 粒度 | 新增 conflicts.py 纯函数差异+冲突分类 |
| 字段级冲突(同字段不同值/同值已满足/null-vs-0 严格区分) | missing | 全仓无要素级三方比较 | conflicts.py typed 比较(区分 None/missing/False/0/"0") |
| 几何冲突(双改不平均/不猜测) | missing | geometry_ops.py 只有单方编辑算子 | conflicts.py 确定性几何比较(顶点级 exact) |
| delete/update、parent retired、ID 歧义、依赖变化 typed 冲突 | missing | — | conflicts.py 分类 |
| rebase 预览+裁决(保留current/采用proposed/新值经旧验证器) | missing | — | rebase.py + resolutions |
| 批准绑定候选内容 digest(旧批准不可提交新内容) | missing | review 只有 base_revision 绑定 | approval_digest + proposal revision CAS |
| 单 ref 受控提交(多要素异构一次原子提交) | partial | G14 plan 单 plan 原子(executor commit_plan),但 set_fields 是 plan 级同一值(attr_ops.py:211-231),多要素异构不可表达 | G14 最小适配: op 级 `wfids` + `plan_seed`(任务 §二授权);提交桥经原 executor |
| 崩溃恢复(已写数据/回执落库前) | partial | executor 有 journal 幂等(executor.py:222-224)但 journal 是 LRU/易失;review 有 merge lease 自愈(store.py:88-113) | proposal 持久 merge record + 租约;recovery_required 诚实态 |
| Agent 工具(起草/列表/差异/rebase 预览,不可批准) | partial | feature_edit_tools.py 有 preview/commit(policy 闸);无 proposal 工具 | 新增 feature_proposal_tools.py 4 工具 |
| 审阅 UI(队列/差异/裁决/提交) | missing | 前端只有即时编辑提案卡 + MapSpec review drawer | 新增 proposal review drawer + 保存待审入口 |
| 审计(谁对哪个版本做了何种决定) | partial | review/export.py 有 allowlist 投影先例 | proposal decisions 全history持久 + 导出投影 |
| 只读源保护 | present | service.py:62-79 dataset_read_only_source + datasets.py:339-389 editable-copy | 复用(创建 proposal 走同一资格判定) |
| 稳定 ID/物化 | present | identity.py(materialize_wfids/wfid_for_created) | 复用;op_seed 持久化 |
| 选择句柄完整性 | present | selection_handles.py(三重绑定,ids≤200) | 提案目标冻结;句柄过期拒绝 |
| undo/redo 一致 | present | executor.py:427-527 + 前端 featureEditCommand | merge 走同一 executor → journal/undo 自然一致 |
| MapSpec 审批/盖章 | present | mapspec_stamp.py + review/merge.py | 不改;merge 后 stamp 由 executor 内建 |

## 与并行任务的边界

- G31(拓扑质量工作台): 其修复走 G14 原路径;G32 不开发规则引擎。g31 worktree 改 data_quality/task_queue — 无共享冲突。
- G29(Pi RPC): 本任务不改 vendor/RPC/native_surface;工具经现有 ToolRegistry 注册。
- G30(#2094 open): 改 gis_skills/jobs 入口;共享文件仅 app/tools/__init__.py、app/main.py 注册行 — 加性。
- G33/G34/G35: 域无交集(专题表达/版式/时间媒体)。
