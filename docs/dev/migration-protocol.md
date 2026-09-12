# Alembic 多线并发迁移协议（V9 P6）

> 状态：生效中（ADR-0140 配套）。历史动机：0034 前缀 4 路并行、0035 前缀
> 3 路并行两轮撞号（见 `migrations/versions/c0d8322aa2cb_merge_0034_multi_epic_heads.py`
> 与 `c1e2f3a4b5c6_merge_0035_multi_epic_heads.py`；撞号现场 CI 记录
> #1229 / #1224）。

## 1. 为什么需要协议

Alembic 的 revision id 权威在每个文件内的 `revision =` 字段；文件名 `NNNN_`
前缀只是人类可读序号，**不蕴含执行顺序**。多 feature 分支并行开发时，两线
各自新增 `00NN_xxx.py` → revision id 不同、前缀相同 → 各分支本地单头、
合并后多头 → 部署门（`alembic upgrade head`）失败，只能事后 merge revision
收拾。协议把「选号」从写文件时提前到**开分支时**。

## 2. 段位登记（分支级）

每个长生命周期分支在 `migrations/.alloc.json` 的 `segments` 里登记自己的
号段（连续 10 个号起步，按需追加）：

```json
"segments": {
  "foundation/data-lifecycle-v9": ["0046", ..., "0055"],
  "foundation/security-tenancy-v9": ["0036", ..., "0045"]
}
```

- 段位由维护者分配，分支内不得越段用号；
- `legacy_merged` 记录历史撞号收敛块（0034/0035），**永久封号**，禁止新增
  同前缀文件；
- 段位制度（0036）之前的全部历史号豁免登记（`--check` 按最小段位号自动
  判定 pre-regime）。

## 3. 领号（迁移级）

写迁移**之前**，在分支上领号并预登记 down_revision：

```bash
python scripts/alloc_migration.py --alloc 0049 \
  --branch foundation/data-lifecycle-v9 \
  --revision 0049_my_change \
  --down 0048_template_versions
```

- 号已被领 / 不在段位 / 与既有文件冲突 → 非 0 退出；
- 登记写入 `.alloc.json` 的 `reservations`，随分支 PR 一起提交 ——
  **合并冲突 = 撞号预警**（两线领同一个号时 .alloc.json 必然冲突，
  把治理从部署门前移到 merge 时）。

## 4. 写迁移的仓库惯例（不变）

- 新 model 模块必须显式 `import` 进 `migrations/env.py`（漏 import →
  autogenerate 对已迁移库生成 drop_table）；
- additive 新表用 create_all-coexistence guard 的可重入 DDL（0022+ 同款）；
- `downgrade()` 反序回滚；
- **merge revision 用单行元组**（#1224 教训：多行元组曾骗过正则单头检测）：

```python
down_revision: Union[str, Sequence[str], None] = ('a', 'b', 'c')
```

## 5. 门禁（撞号在哪里被抓住）

| 层 | 检查 | 位置 |
|---|---|---|
| 提交前 | `python scripts/alloc_migration.py --check` | 本地 / db-migrations lane 步骤 |
| pytest | revision id 全局唯一断言 | `tests/test_alembic_metadata.py::test_revision_ids_globally_unique` |
| pytest | 单头强校验（ScriptDirectory，无需 DB） | `tests/test_alembic_metadata.py::test_single_head_via_script_directory` |
| pytest | 重复编号副本必须 fail（门禁自证） | `tests/test_alembic_metadata.py::test_alloc_check_fails_on_duplicate_number_copy` |
| 部署 | 真 PostGIS `alembic upgrade head` + drift | `production.yml` db-migrations job |

## 6. 撞号了怎么办（消解规则，与 0034_workflow_v5_runtime 自述协议一致）

1. **重编号**（优先，未合入时）：把后写一方的 revision id 与文件名改到
   本分支段位的下一个空号，并改 `down_revision` —— 无痕消解；
2. **merge revision**（已分叉合入时）：新增单行元组的 merge 迁移收敛多头，
   并把该前缀记入 `legacy_merged` 封号；
3. 两种方式都要求补跑 §5 全部门禁。
