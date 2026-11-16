# G44 REVIEW — 独立评审记录

Reviewer：未参与实现的独立 subagent；对 `655f769ca..HEAD` 全量 diff 只读评审
（含两个一次性复现脚本验证假设，未触碰工作树）。

## 第 1 轮（R1）—— 13 项发现（P0×1 / P1×1 / P2×3 / P3×8），BLOCKING: yes

| # | 级 | 发现 | 修复 |
|---|---|---|---|
| 1 | P0 | 激活竞态：锁外选零违反收据 → 修订落盘 → 迁移只 CAS status，未审计 digest 可上膛（已动态复现） | `activate_contract` 锁内冷读复核 revision+digest；`transition_status` 增 `expected_digest` CAS；`revise_contract` 全程持 `_edit_lock`（Pi 草稿修订与激活不再交错）。回归：test_review_fixes::test_revision_inside_activation_window_rejected + test_transition_status_digest_cas |
| 2 | P1 | compare 规则左值类型守卫缺失 → 字符串声明+数字右值 TypeError（500 而非 typed finding，已复现） | 两侧 JSON 类型都须匹配声明类型才比较；比较移入受保护路径。回归：TestR1P1CompareTypeSafety ×2 |
| 3 | P2 | REST schema `min/max: float` 把 2^53+1 强转 float，bigint 边界失真（已验证） | `Union[int, float]`。回归：TestR1P2RestBigint（int 直达内核） |
| 4 | P2 | 全部要素编辑耦合合同 DB 可用性（fail-closed 面） | 保持 F36 语义；INTERFACES.md 增运维注记（迁移与代码同 PR 原子交付，空库/升级库已验证） |
| 5 | P2 | 表单客户端秒位可选（与服务端不一致）；datetime-local 原生值无 offset 永不过校验 | 秒位必填（正则对齐服务端 + 测试）；移除 datetime→datetime-local 控件映射（一律文本） |
| 6 | P3 | conditional_required eq/ne 在 datetime 上用原字符串比较 | 按 UTC 瞬时归一比较（与 compare/unique 同语义） |
| 7 | P3 | binding 路由/inspect 工具遇 DB 错误裸 500 | `contract_binding_status` typed 包裹（status=unavailable + error） |
| 8 | P3 | 幂等复用撞终态 job 且收据行缺失 → 误报 "crashed" | AlreadyFinished 收敛为 completed+说明（failed 不可激活不变式不变） |
| 9 | P3 | 批量填值 invalid_value/scope_unavailable 落 default 文案 | switch 补两 case + i18n 键（zh/en） |
| 10 | P3 | `validate_candidate_state` 死参数 | 删除 |
| 11 | P3 | 违规 value 数值投影无截断 | bigint 文本化 64 字符截断 |
| 12 | P3 | 模型注释「行不可变」与 draft 原行修订不符 | 注释改为「非 draft 行不可变；draft 锁内修订」 |
| 13 | P3 | datasets.py/geometry_ops.py 无关 lint 改动 | 已 revert 至 base |

修复后全量复跑：后端 219 passed（G44 51 + G14/G32 168）+ 前端 34 passed +
i18n key-completeness ✓ + Pi 旅程 **13/13**（commit turn 补 tool_search 确定性）+
j24 **1 passed**。

## 第 2 轮（R2）—— 复审通过（BLOCKING: no）

同一 reviewer 独立复跑：G44 单测 51/51、G14/G32 168/168、触及前端 vitest
24/24、i18n 149=149 无孤儿键，并对修复后代码做了新一轮对抗性竞态探测
（真实 `_edit_lock` 下 revise 于激活窗口内排队 → 激活以已审计 digest 完成 →
revise 被诚实拒绝；`UNAUDITED RULES ACTIVE: False`）。

结论：R1 全部 13 项闭环（12 项代码修复 + 1 项按约定以文档落地）；R1-P0 竞态
在真实锁序下被判定关闭。R2 新增 3 项 P3 尾巴（激活调用未接 expected_digest
纵深防御、模型 docstring 残句、FE binding 状态 union 缺 'unavailable'）——
三项均已在提交前顺手修复并复跑（51 passed / lint clean / tsc clean / 13 passed）。

最终判定：**P0=0 / P1=0 / P2=0 / P3=0（余量已清）**，不阻塞合并。
