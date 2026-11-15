# REVIEW — G46 独立评审与修复记录

## Round 1（独立 reviewer：agent_bc20a275，未参与实现；实证复现式评审）

### P1（全部修复 + 回归锚）
| ID | 发现 | 修复 | 回归 |
|---|---|---|---|
| P1-1 | apply 的 CAS 链读不存在的 `res["revision"]` 键 → ≥2 图表视图带 expected_revision 必失败回滚；superseded 死分支 | service.py：`mutation_revision` 前进 CAS 链；superseded 判定改 `status=="superseded"` | test_apply_cas_chain_multi_chart_p1_1（2 图 CAS 全量成功；基线读真实 `_cartographic_mutation_revision`） |
| P1-2 | Pi `webgis_exploration_views` 不带 view_id（list 分支）双重损坏：参数必填校验拒绝 + list 对象调 .get 崩 | ViewIdArgs.view_id 可选（default=""）；list 分支 isinstance 防御 + 正常切片 | Pi journey 新增"列出视图" turn + pi_list_views_branch gate |
| P1-3 | 导出 `_layer_source_ref` 不经 sources 表反查 → 生产形态图层（source=key+sources[key]={ref}）静默未过滤/未水化导出 | 经 `doc["sources"]` 反查（G41 source_ref_of 协议）；有条件但零匹配层 → typed 拒绝 EXPORT_NO_TARGET_LAYER（防口径声明与图面矛盾） | test_export_production_layer_shape_p1_3（生产形态）+ test_export_no_target_layer_rejected |

### P2（全部修复）
| ID | 发现 | 修复 |
|---|---|---|
| P2-1 | PATCH 换 plan 绕过 save 的锚再冻核对 | update_view 在 plan 变更时 freeze+锚核对（与 POST 同面）；test_update_view_revalidates_anchor_p2_1 |
| P2-3 | 结果表 where JSON 2KB 帽使合法条件组合（16×64 值）表格断裂 | 表格优先 where-scope selection handle 分页（服务端权威完整集合）；无句柄才走 where 下推 |

### P3（选择性修复）
- P3-1 ✓ 导出锚核对补 fingerprint（与 save/open 三面一致）
- P3-3 ✓ STATS_SCAN_LIMIT 500k→250k（活守卫）；match_count_exact 按实际投影
- P3-5 ✓ open 时持久化有界 preview 到行（preview/preview_digest 列活）
- P3-6 ✓ component_renderers 支持矩阵登记（catalog rendererSupport/exporterSupport 修正）+ 重导出
- P3-7 ✓ 会话切换清 preview-store 缓存
- P3-2/P3-4/P3-8 部分：HTTP 面 trusted_dispatch 恒真（require_owned_session 已挡越权，记录为已知深度防御权衡）；apply 回滚 enabled=False 语义（新建组件场景正确，重放场景留 P3）；panel 测试保存路径未点按钮（服务端 save 面已有集成覆盖）

### Reviewer 各重点结论摘录
- 权限/所有权、科学语义（分母/typed 全链/facet 分标）、CAS、资源上限、UI 语义、Pi 契约、迁移、oracle 独立性——核对过未见问题
- 阻塞判定源于 3 项 P1（均已修复并加回归）

## Round 2（复审）
待原 reviewer 复审修复 diff（见 PR_HANDOFF）。
