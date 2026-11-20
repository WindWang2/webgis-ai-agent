# G44 PLAN

目标：owner 声明的业务字段合同（类型/必填/取值域/唯一性/条件规则）+ 同合同驱动的结构化
要素录入；全部写入口统一执行；完整评估→激活→审计闭环；Pi 同链；双旅程真实验收。

## 架构决策

1. **单一中央 validator**：`app/services/field_contracts/validator.py` 纯函数内核
   （预览 / 提交 / 完整评估 / undo-redo 四处复用同一实现）。
2. **执行接线（WP06）**：合同视图由 `enforcement.resolve_active_contract(session_id, ref_id)`
   解析（DB 冷读 + 绑定有效性）；传入 `feature_edit/plan.py::build_plan(field_contract=...)`
   → 候选态校验 + plan 绑定 `expected_contract`；`executor.commit_plan` 锁内冷读复核
   （不匹配 → `contract_changed` 409）+ 重求值含合同；undo/redo 恢复态校验（typed 阻断）；
   `feature_proposals/bridge.py` 同样传合同（F22）。声明字段跳过观测枚举（attr_ops 增
   `contract_fields` 参数），未声明字段行为不变。
3. **持久化**：新 DB 表 `field_contracts`（版本行不可变：contract_key 组 + revision CAS +
   draft/active/retired/superseded + 部分唯一 active 索引）与 `field_contract_assessments`
   （评估收据：verified 数据指纹/规则 digest/counts/findings 文件指针）。迁移
   `g44_field_contracts`（down=g40_cartographic_symbol_assets）。SQLite/Postgres 双兼容。
   合同激活记录：verified data revision/fingerprint、rules digest、actor、时间、评估收据 id。
4. **并发边界（WP05）**：激活/退役在 `feature_edit.executor._edit_lock(session_id)` 内
   复核「评估 verified 指纹 == 当前载荷指纹」后 DB CAS → 与数据写共享同一持久竞争边界
   （F11/F13/F14）。
5. **规则语义（WP03）**：严格标量（string/number/integer/boolean/date/datetime）；
   presence/nullable/empty-string 三态显式；枚举 ≤256；数值闭/开区间；字符串长度（码点）；
   ISO 日期（闰年严格）；datetime 强制显式 offset、≤6 位小数（超出拒绝）、UTC 瞬时规范
   （比较+唯一性，F37）；布尔≠整数；NaN/Inf 拒绝；无隐藏 trim/casefold/转换。跨字段封闭集：
   conditional_required（unknown 不默认满足）+ compare（同类型有序）+ unique（1-4 字段，
   null/missing 政策显式 ignore|participate）。错误码封闭、确定性。
6. **评估（WP04/WP11）**：完整冻结快照逐行 O(N×R) + 唯一性受控哈希分组；JobKind
   `field_contract_assess` durable job（G23 runner）；findings 上限物化 + 溢出计数披露；
   分页从结果文件（原子写）读取；取消/崩溃 → incomplete，永不产生可激活证明。
7. **工具（WP10）**：`webgis_field_contract_inspect / _draft / _assess / _result`（tier2，
   有界回执）；激活/退役仅 owner REST —— Pi 无此工具（F28）。预览/提交复用既有
   feature_edit 工具（同链）。
8. **前端（WP08/WP09）**：`structured-feature-form`（typed 控件/必填/单位/逐字段错误/
   键盘焦点；不默认首项、不把空串转零）挂 feature-layer-editor 创建+属性面；raw JSON 保留
   为高级入口；`field-contracts` 侧栏工作台（观测/声明分列、草稿、评估进度、违规分页、
   激活/退役、历史、批量填写走 G24 handle+G14 preview≤200 显式目标）。i18n zh/en。
9. **旅程（WP12）**：`tests/integration/test_pi_g44_field_contracts_journey.py`（真实 Pi 子
   进程，模板 g41）+ `frontend/e2e/j24-g44-field-contracts.journey.ts`（mock 模式基线 +
   real 可跑）。

## 工作包序

WP01 盘点(本文+DEDUP) → WP02 模型/迁移 → WP03/WP04 内核 → WP05 service/激活 → WP06 执行
接线 → WP07 undo/G32/派生语义 → WP08/WP09 前端 → WP10 工具 → WP11 job/预算 → WP12 oracle+
旅程 → review → PR。

## Done 判据

十二 WP 落地；F01–F37 逐项记录 PASS/FAIL/NOT RUN；核心正向旅程真实通过（Pi+浏览器）；
迁移空库+升级验证；独立 review 无 P0/P1 阻塞；push+PR。
