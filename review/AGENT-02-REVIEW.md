# AGENT-02 代码审查报告：视觉驱动的 MapSpec 自愈微变异引擎

- 分支：`agent/02-visual-self-healing-mapspec`（基于 origin/master @ 3eb2cc6a，隔离 worktree）
- 日期：2026-09-14
- 审查对象：ADR-0186 交付面（visual_healer.py + lifecycle_engine.py 挂载 + 测试 + 文档）
- 结论：**通过（含 3 项已披露的诚实边界与 1 项预存问题说明）**

## 1. 交付清单

| 文件 | 变更 | 说明 |
|---|---|---|
| `app/services/mapspec/visual_healer.py` | 新增（~880 行） | 纯函数编译器：归一化桥 / 规划器 / 4 Resolver / COW 应用器 |
| `app/services/mapspec/lifecycle_engine.py` | 修改（8 处注册面 + 1 个事务入口） | `ApplyVisualHealPatchIntent` + `apply_visual_heal_patch` |
| `app/services/mapspec/__init__.py` | 修改 | 导出新意图/异常/值对象 |
| `tests/unit/test_visual_self_healing.py` | 新增（25 用例） | 15 组缺陷参数化 + 守卫/事务/兼容 |
| `docs/adr/0186-visual-self-healing-mapspec.md` | 新增 | 架构决策（D1-D7 + 备选 + 诚实边界） |
| `docs/dev/visual-self-healing-rules.md` | 新增 | 缺陷→变异映射矩阵 / 阶梯 / 披露码 |

## 2. 门禁结果

- `pytest tests/unit/test_visual_self_healing.py`：**25/25 通过**（连续两次运行稳定 —— MapSpecStore
  磁盘持久化下用随机 sid + 动态 expected_revision 保证封闭性）。
- `ruff check`（E4/E7/E9/F，--max-warnings 0 等价）：**新文件与改动文件 0 告警**。
- 邻域回归（lifecycle_engine/store/cow/coordinator/layout_selfheal/cartography/fail_closed 共 97 用例）：
  **96 通过**；唯一失败 `test_mapspec_store.py::test_validate_and_compile` 在**干净基线同样失败**
  （需 Node 端 mapspec-compiler CLI 环境，与本分支无关，已用 git stash 复核）。
- 全量 `pytest tests/unit`：见交付时附注（后台运行结果）。

## 3. 设计符合性核对（任务书 §2/§3 逐条）

| 要求 | 落点 | 核对 |
|---|---|---|
| FSM + ≤2 次迭代防震荡 | `MAX_VISUAL_HEAL_ITERATIONS=2`，账本 attempts / no_improvement / tried_signatures 三重防护，`SelfHealConvergenceExhausted`（max_iterations / no_improvement / repeated_patch） | ✅ |
| 4 个原子操作常量 | `MUTATION_HEAL_LABEL_COLLISION/_CONTRAST/_LAYER_ORDER/_OPACITY` | ✅ |
| VisualHealStrategyPlanner | severity（error>warning>info）+ 影响域权重（layer_order>contrast>label>opacity）+ 缺陷指纹字典序稳定化；同图层属性面冲突消解 | ✅ |
| LabelCollisionResolver | text-allow-overlap=False 强制、text-padding 翻倍（cap 8）、text-ignore-placement=True、attempt≥1 步进 text-size×0.85（floor 8） | ✅ |
| ContrastRemapper | 画布=background 层色（亮度<0.18 判暗）；COLOR_PALETTES 端点采样 n 色；排序（AA 门限, min-ΔE↓, 名字典序）；**ΔE 严格更优才动**；镜像 `_apply_palette_change` 长度匹配逐位替换（legend 色带 + step/interpolate/match），分类断点/键不动 | ✅ |
| LayerZOrderAdjuster | 目标态=靶层高于全部遮挡型层；取上方最高遮挡型层抬升其正上方；background 恒居首；显式 occluder 三态（可抬/已满足/缺失） | ✅ |
| opacity 调整 | OpacityAdjuster：抬靶层 +0.3（cap 1.0）；靶层已实心且有 occluder → 压垫底者 ×0.6（floor 0.25）；表达式目标 → unsafe_target | ✅ |
| lifecycle_engine 挂载 | `ApplyVisualHealPatchIntent` 进 Union/锁面/呈现意图/深拷贝白名单 + 锁内权威重规划分发分支；复用 CAS/幂等/checkpoint/blocking 校验/revision+1/失败回滚全套 | ✅ |
| 15 组缺陷用例精确命中 | 参数化 A1-A5/B6-B10/C11-C14/D15，逐一断言靶图层×属性命中 + 靶外零触碰 + 诚实空计划 | ✅ |
| 向后兼容 | healed spec 过 `coordinator.validate()`（无新增 blocking）+ `MapSpecDocument.model_validate`；四类自愈混合序列 revision 严格单调、指纹逐次变化、checkpoint 正常 | ✅ |

## 4. 无副作用 / 无非法突变核查

- **呈现面边界**：四个 Resolver 的写面仅 layer.layout（text-*）、paint 呈现键（*-color 方法包装的
  输出色、*-opacity）、legend_spec 色带/类别色、layers 数组次序。无任何路径触碰 sources、
  数据绑定、filter、legend_spec.min/max/breaks/categories[].key（测试 B6/B8 显式断言断点不动）。
- **无伪造成功**：空计划（所有缺陷被诚实跳过）→ `HEAL_PLAN_EMPTY`，不提交不推 revision；
  对比度无达标候选 → `no_palette_meets_target` 而非降门限凑数；表达式目标 → `unsafe_target`。
- **收敛防护不泄漏账面**：账本仅在 `is_error=False and not duplicate` 后推进；失败回滚无残留。
- **锁面**：heal 的全部靶图层 + occluder 注册进 `intent_lock_targets`，用户锁定图层整笔拒绝
  （error_code=layer_locked，测试覆盖）；user origin 不受自有锁约束（既有语义不变）。
- **写放大**：COW 仅深拷贝被触碰图层；重排不复制图层本体；每次 heal 恰好一次事务提交。

## 5. 已披露的诚实边界（非缺陷，写入 ADR D7）

1. **收敛账本为进程内状态**：多 pod 部署下各 pod 独立计数；quality_loop/runtime repair 的
   既有预算（各 2 次）独立兜底，不产生正确性问题，仅可能多花一轮自愈。
2. **入口预检读无锁快照**：`apply_visual_heal_patch` 的账本/签名预检在锁外，锁内分发分支用
   权威 spec 重规划保证应用正确；跨进程竞态窗口内可能出现"预检与实际应用相差一次提交"，
   后果是修复阶梯 attempt 偏移一档，无结构性风险。
3. **归一化定位依赖证据文本提及图层 id**：未提及即诚实 `unlocalized_defect` 跳过；
   Direction 01 未来在 critique 上增配结构化 layer_ids 字段后桥直接透传（已预留）。

## 6. 测试封闭性说明

MapSpecStore 为磁盘持久化单例（`BASE_STORAGE_DIR` 会话目录跨 pytest 进程留存）。本套件
以 `uuid` 后缀会话 id + 相对 revision 断言保证跨运行封闭（对齐既有 mapspec 测试的隐含约定）；
会话目录随测试累积与既有套件行为一致，不做额外清理（清除入口 `clear_session_files` 属
session 存储层职责）。

## 7. 遗留与建议（不阻塞交付）

- `requirements-dev.txt` 含 Pillow 但本机 venv 初装曾缺失 —— `parse_css_color` 依赖 Pillow
  兜底 hex 解析，缺装时 `min_adjacent_delta_e` 全量 None（fail-closed，行为正确但选色门限
  全不可达）。建议部署清单核对 Pillow。
- `pip install -e .` 在 flat-layout 下报 setuptools 多顶层包错误（预存，非本线引入）；
  单测依赖 pytest.ini `pythonpath = .` 不受影响。
- 后续可把 `normalize_visual_report` 的关键词规则表外置为配置（当前为代码内常量，映射矩阵
  文档与实现有一致性维护成本，已在规则文档标注"实现与测试以本文为准"）。
