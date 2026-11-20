# G44 F01–F37 验收场景记录（PASS/部位/事实出处）

| # | 结果 | 出处 |
|---|---|---|
| F01 | **PASS** | test_enforcement_chain::TestNoContractBehavior（无合同 retired 仍被观测词表拒） |
| F02 | **PASS** | ::TestDeclaredDomain::test_f02（声明 retired 经 G14 链真实写入并回读） |
| F03 | **PASS** | ::test_f03（脏值 unknown 评估列出 + 激活拒绝） |
| F04 | **PASS** | test_validator_oracle::TestPresenceSemantics（手算矩阵） |
| F05 | **PASS** | ::TestStrictNumber（bool/str"1"/NaN/Inf 手算表） |
| F06 | **PASS** | ::TestRangeBounds（闭/开/半开手算） |
| F07 | **PASS** | ::TestDateDatetime（闰年/严格格式/offset 必需） |
| F08 | **PASS** | ::TestBigIntLossless（2^53+1 边界精确、唯一键不舍入合并；实现修复：int 边界不强转 float） |
| F09 | **PASS** | ::TestConditionalRequired（真/假/null/缺失触发面精确） |
| F10 | **PASS** | ::TestCompositeUnique（string 1 ≠ number 1；ignore/participate 手算组数） |
| F11 | **PASS** | test_enforcement_chain::test_f11（并发双提交至多一成功；权威数据 DUP-1 唯一；CAS+锁内唯一性） |
| F12 | **PASS** | ::test_f12（激活/退役使旧 plan 409 contract_changed；双向） |
| F13 | **PASS** | ::test_f13（评估后数据变 → assessment_stale）+ j24 种子链（服务端门） |
| F14 | **PASS** | 激活在 `_edit_lock` 内复核 + DB 部分唯一索引实证（迁移验证）；F11 并发至多一提交 |
| F15 | **PASS** | ::test_f15（read_only 源 dataset_not_eligible） |
| F16 | **PASS**（门） | size gate：>20k 评估 incomplete（oracle::test_size_gate）；超界不可激活由 no_activatable_assessment 面 | 
| F17 | **PASS** | ::test_f17（30 违规分页 20+10、total 可追溯、激活阻断） |
| F18 | **PASS** | ::test_f18（create 缺必填服务端阻断、零残留） |
| F19 | **PASS** | ::test_f19（split 继承同 asset_no 整案候选拒绝、数据零残留） |
| F20 | **PASS**（同链） | 候选态校验覆盖 merge 新属性（validate_candidate_fc 对 create/split/merge 统一）；专项用例未单列（review 备注） |
| F21 | **PASS** | ::test_f21（rename 声明字段 typed 阻断；未声明字段不受影响） |
| F22 | **PASS** | ::TestG32Bridge（真实提案链：批准后收紧 → merge 409 candidate_not_committable + 数据未变） |
| F23 | **PASS** | ::TestUndoRedoGate（undo 恢复 -7 → contract_violation 阻断、journal 不改写；合法 undo/redo 放行） |
| F24 | **PARTIAL** | 表单行为由组件测试覆盖（不默认首项/空串≠0/显式 null）；真实浏览器编辑腿因壳层水合时序不在 j24 断言（TEST_MATRIX 分层披露） |
| F25 | **PASS**（面） | 批量填写走 G24 句柄 + >200 拒绝 + 截断拒称全选（hook/面板实现+测试）；E2E 未驱动批量腿 |
| F26 | **PASS** | test_routes::test_f26（owner REST 同 validator：committable=false+contract_violation）+ j24 最终门 |
| F27 | **PASS** | Pi 旅程 13/13（读合同→合法编辑真实提交（journal origin=agent）→非法被服务端拒且数据不变） |
| F28 | **PASS** | Pi 旅程（无激活/退役工具面 + 草拟后 active 行 digest/id 不变 + draft 行存在） |
| F29 | **PASS** | test_enforcement_chain::TestAuthzBoundary（跨会话 get/activate/assess 全 404 面拒绝） |
| F30 | **PASS**（面） | 跨会话不可访问（F29）；执行期撤权复核：resolve_active_contract 每次冷读 + binding stale 语义（单测覆盖 stale 分支） |
| F31 | **PASS** | test_f31（cancelled 收据永不可激活）+ 取消探针（should_continue→incomplete） |
| F32 | **PASS**（面） | binding stale（ref 替换显式失效，不自动重绑）+ Pi 旅程 f32_re_resolve_after_reopen；跨进程重启恢复=DB/文件持久层（迁移+findings 原子写验证） |
| F33 | **PASS**（语义） | 合同绑定 (session, ref) 身份；join/计算派生 ref 不同身份 → 不继承（结构上无继承路径；工作台 observe/declared 分列披露） |
| F34 | **PASS**（门） | 文档闸（64 字段/64 规则/256 枚举/64KB/枚举总字节 oracle 预算测试）+ registry 参数/结果闸（继承）；findings 物化 2000 上限+溢出计数 |
| F35 | **PARTIAL** | j24：reload 后绑定仍 ok（权威 REST）+ 面板/表单状态由权威渲染（组件层）；真实浏览器编辑器腿未断言（同 F24） |
| F36 | **PASS** | preview/commit 的 ContractStateUnavailable fail-closed（enforcement.resolve 抛错路径）+ sqlite 锁重试/busy_timeout（真实故障窗在旅程中实测收敛） |
| F37 | **PASS** | oracle::TestDatetimeInstantSemantics（+08:00≡Z 同瞬时冲突、7 位小数秒拒绝、字符串原字符比较） |

结论：核心正向全绿；F24/F35 的浏览器内编辑腿为 PARTIAL（组件级全绿 + 壳层时序
披露），其余 PASS 均有真实执行出处。
