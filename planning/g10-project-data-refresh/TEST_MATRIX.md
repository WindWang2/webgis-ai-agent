# G10-PROJECT-DATA-REFRESH · TEST_MATRIX（验收矩阵）

测试模式标记：`PU`=pure unit / `DI`=real domain integration / `PI`=real Pi RPC + stub LLM + 真实 dispatch / `BR`=browser + real backend（真实认证入口） / `LE`=live external service。

> 规则：修改前先写可失败测试；正例必须给明确预期数值/状态；`BR` 不能用 fake opener/伪 ACK/截图占位；没有真实浏览器结果就标「未验证」，不把组件测试提升为浏览器证据。

| Oracle | 测试 ID | 模式 | 预期（明确数值/状态） | 文件 | 状态 |
|---|---|---|---|---|---|
| O01 逻辑身份/精确版本 + 跨 owner/撤权拒绝 | T-O01a | PU | 不同 `ref` 同 `logical_id` 判为同一来源；`version_fingerprint` 变化时 `verdict=changed` | `tests/data/test_project_data_refresh_inventory.py` | ⏳ 待写 |
| O01 | T-O01b | DI | 跨 owner 请求 → 404（`Project not found`），响应体不含任何来源差异摘要 | 同上 | ⏳ |
| O02 feature ID 精确差异 | T-O02a | PU | fixture：新增 2 / 删除 1 / 属性改 3 / 几何改 1 → 输出计数精确相等 | `tests/data/test_project_data_refresh_diff.py` | ⏳ |
| O02 无 ID / 预算不足 | T-O02b | PU | 无稳定 ID → `alignment=unavailable`、`exact_counts=None`、`verdict=unknown`；禁止伪造精确数字 | 同上 | ⏳ |
| O02 抽样 | T-O02c | PU | 抽样命中 → `evidence=sample`、不得得出 `unchanged`；预算耗尽 → `conservative_full_recompute` | 同上 | ⏳ |
| O03 单位/CRS/范围/分母 | T-O03a | PU | 单位变 → `ChangeClass.SCHEMA` + 影响统计节点；CRS 变 → `CRS` + 全链重算；bbox/时间窗变 → 覆盖节点重算 | `tests/data/test_project_data_refresh_impact.py` | ⏳ |
| O03 | T-O03b | DI | 分母（行政区人口）变 → 归一化统计节点进入 `recompute`，图层样式节点不进入 | 同上 | ⏳ |
| O04 style-only / 保守重算 | T-O04a | PU | 仅改颜色/标题 → `recompute=[]`、`style_only=True`；全局分类断点/核密度/网络拓扑 → 整项重算 | 同上 | ⏳ |
| O04 结果等价 | T-O04b | DI | 增量结果与固定版本全量重算在声明容差内逐字段相等 | `tests/data/test_project_data_refresh_parity.py` | ⏳ |
| O05 预览无写入 + 绑定版本 | T-O05a | DI | `preview` 前后磁盘/DB 状态哈希相同；执行时 `source_version_set` 不匹配 → 409 `proposal_drift` | `tests/data/test_project_data_refresh_service.py` | ⏳ |
| O05 revision/权限漂移 | T-O05b | DI | MapSpec revision 漂移 / 权限撤销 / 预览后人工编辑 → 409 + 强制重预览 | 同上 | ⏳ |
| O06 用户编辑保留 | T-O06a | PU | baseline/current/candidate 三方合并后颜色、透明度、可见性、层序、组件族锁、文本全部按 entity ID 保留 | `tests/data/test_project_data_refresh_merge.py` | ⏳ |
| O06 显式冲突 | T-O06b | PU | 被删要素的标注 / 被移除字段的标签 → `conflict` 列表显式列出，不迁移到别的对象；硬语义错误不因 user-wins 变合法 | 同上 | ⏳ |
| O07 失败/取消/崩溃/幂等 | T-O07a | DI | 候选失败/取消后当前 MapSpec fingerprint 不变；重复 `execute`/`adopt` 返回同一 receipt 且 revision 只 +1 | `tests/data/test_project_data_refresh_execution.py` | ⏳ |
| O08 原子采纳/回退 | T-O08a | DI | 采纳走既有 `apply_mutation`/`map_product_service`，`revision` 单调增；回退走 `resume`/revert 式新 commit；旧快照字节不变 | 同上 | ⏳ |
| O09 GC pin | T-O09a | DI | 候选/当前/交付引用存在时 GC 不回收；`discard` 后引用与配额可回收 | `tests/data/test_project_data_refresh_gc.py` | ⏳ |
| O10 全链 | T-O10a | BR | 真实登录 → 检查更新 → 预览 → 执行 → 比较 → 采纳/回退，每步可见状态 | `frontend/e2e/journeys/j13-project-data-refresh.journey.ts` | ⏳ |
| O10 Pi 同链 | T-O10b | PI | 真实 Pi RPC + stub LLM 调 `check_project_source_updates` → 真实 dispatch → 领域计算 → 类型化回执 | `tests/integration/test_pi_project_data_refresh.py` | ⏳ |
| O11 状态分离 | T-O11a | PI | 回执分别给 `tool_execution` / `map_observation` / `version_adoption` 三态；未观察地图时 `map_observation=unverified` | 同上 | ⏳ |
| O12 三场景 | T-O12a | DI | ①学校点 +2/-1 保留热力/图例人工调整 ②人口表年度更换边界不变仅重算归一化统计 ③nodata/路网边变化保守重算正确范围 | `tests/data/test_project_data_refresh_scenarios.py` | ⏳ |
| O12 性能 | T-O12b | DI | 先证结果等价，再报工具执行次数 / IO 字节 / wall time / max RSS，含相同输入与环境前后对照 | `tests/data/test_project_data_refresh_perf.py` | ⏳ |

## 反例 / 边界（必须与正例同批）

- ❌ 同名文件 ≠ 同一来源；❌ 不同 ref ≠ 一定变化。
- ❌ 行位置当 feature 身份 → 必须拒绝。
- ❌ ETag/更新时间 ≠ 精确内容版本 → 证据级别须降级披露。
- ❌ 「改一个像元只影响同一块」→ 有 stencil/kernel/全局统计/重采样时扩大依赖域。
- ❌ 客户端 `verified=true` / `approved=true` / `owner_id` 不作权威。
- ❌ 大 FeatureCollection 进模型上下文 → 用有界 ref + `truncated` 标志。
- ❌ 抽样差异证明「没有变化」。
- ❌ 静默忽略版本/权限/revision 漂移。
- ❌ 崩溃后留下半新半旧 MapSpec。

## 验证命令（实际执行时逐条回填退出码）

```
pytest tests/data/test_project_data_refresh_*.py --no-cov -q -x          # PU + DI
PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_project_data_refresh.py --no-cov -q -m heavy   # PI
E2E_MODE=real pnpm exec playwright test --grep @g10                      # BR
pnpm exec vitest run test/... components/sidebar/project/...               # 前端单元 + i18n 闸
```
