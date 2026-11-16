# G44 INTERFACES — 对外契约

## REST（prefix `/api/v1/chat/sessions/{sid}`，require_owned_session；actor 服务端裁决）

- `GET  /field-contracts?ref_id=` → `{contracts:[contractView]}`
- `POST /field-contracts` `{ref_id, document}` → contractView（400 invalid_document /
  draft_exists；404 dataset_not_found 面不泄跨会话存在性）
- `GET/PUT /field-contracts/{cid}`（PUT `{expected_revision, document}` CAS；409 revision_conflict）
- `POST /field-contracts/{cid}/assess` → `{assessment, job_id, poll}`（幂等键
  `g44:assess:{sid}:{cid}:{rev}:{fp16}`；复用收敛到既有 job 的收据）
- `POST /field-contracts/{cid}/activate`（完整零违反收据 + 编辑锁内指纹复核 +
  DB CAS；400 no_activatable_assessment | assessment_stale；409 activation_race）
- `POST /field-contracts/{cid}/retire`
- `GET  /field-contracts-assessments/{aid}`；`GET .../findings?cursor&limit&code`
- `GET  /field-contracts/datasets/{ref}/observe`；`GET .../binding`
  （binding.status ∈ ok|stale|missing；stale=ref 被替换，不自动重绑）

contractView：`{contract_id, contract_key, status draft|active|retired|superseded,
revision, rules{version:1,title,fields[],cross_rules[]}, normalized_digest,
binding{activation{actor,activated_at,assessment_id,data_digest,
content_revision,rules_digest,violations,completeness}}, …}`。

规则词表（封闭）：字段类型 string|number|integer|boolean|date|datetime；
required/nullable/empty_string(reject|allow)/enum(≤256)/min/max/exclusive_*/
min_length/max_length/label/description/unit/default/form_control；
跨字段 conditional_required(when{eq|ne|in|not_in|gt|gte|lt|lte})/compare(同类型
lt|lte|gt|gte)/unique(1–4 字段，null_policy ignore|participate)。
违规码封闭集见 `app/services/field_contracts/contracts.py::VIOLATION_CODES`。

## DB（migration `g44_field_contracts`，down=`g40_cartographic_symbol_assets`）

- `field_contracts`：版本行不可变（rules/digest/revision 创建后不改）；修订=同
  contract_key 新行；active 部分唯一索引 `uq_field_contracts_active_per_binding
  (session_id, ref_id) WHERE status='active'`；历史裁剪 ≤32 行/绑定。
- `field_contract_assessments`：收据（verified{data_digest,content_revision,
  features_total,rules_digest} + counts + findings 文件指针 DATA_DIR/
  field-contracts/<sid>/<aid>.json 原子写）。

## feature_edit 接缝（共享文件最小接线）

- `plan.build_plan(field_contract=EnforcedContract)`：声明字段跳过观测枚举 +
  候选态统一校验 + plan.expected_contract{id,revision,digest}。
- `executor.commit_plan`：锁内冷读复核 digest → 不一致 `contract_changed` 409
  （F12）；写前候选态门禁 `contract_violation` 阻断。
- `apply_undo/apply_redo`：恢复态违反当前合同 → typed 阻断（F23）。
- `feature_proposals.bridge.merge_proposal(field_contract=…)`：G32 同链（F22）。
- 新 finding codes：`contract_violation`/`contract_state_unavailable`/
  `contract_rename_blocked`（并入封闭集）。

## Pi 工具（tier2；session_id 注入即授权）

`webgis_field_contract_inspect`（只读：active/draft+观测画像+binding）；
`webgis_field_contract_draft`（草稿 upsert，激活/退役**无工具面**，F28）；
`webgis_field_contract_assess`（durable job）；`webgis_field_contract_result`
（收据+违规分页）。要素编辑走既有 `vector_feature_edit_preview/commit`（同一
central validator）。

## Durable job

`JobKind.field_contract_assess`；runner=`data_operations/runner.py`（取消/心跳/
fencing 复用 G23）；取消/崩溃 → 收据 incomplete，永不激活（F31）。

## 前端

`lib/api/field-contracts.ts`（typed client）；`components/map/structured-feature-form`
（合同驱动表单）；`components/sidebar/field-contracts/`（工作台 + 批量填写走
G24 句柄/G14 预览-批准链）；i18n `fieldContracts` namespace（zh/en 同步）。
E2E：`frontend/e2e/journeys/j24-g44-field-contracts.journey.ts`（registry 已登记）。

## 明确不做（typed unsupported）

跨数据集外键、任意表达式/SQL/JS/regex 求值、量纲换算、隐藏 trim/casefold、
企业表单平台、外部 writer 约束声明、派生数据自动继承合同（F33 显式不继承）。

## 运维注记（评审 R1-P2-4）

激活后的全部要素编辑（preview/commit/undo/merge）都会冷读合同表
（`resolve_active_contract`）。合同 DB 不可读时按 F36 fail-closed（typed
`contract_state_unavailable`），包括无合同数据集 —— 迁移 `g44_field_contracts`
必须随本代码同批发布（同一 PR 原子交付，已验证空库 upgrade / 上一稳定库
upgrade 均通过）。
