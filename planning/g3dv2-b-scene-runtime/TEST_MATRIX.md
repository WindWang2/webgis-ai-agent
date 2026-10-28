# G3DV2-B TEST_MATRIX

## 新增测试（本分支，共 53 例）
| 模块 | 文件 | 覆盖 |
| --- | --- | --- |
| 严格解析 | scene3d-gltf-parser.test.ts（+9） | 交错正确读/值等价、sparse 覆写、normalized 5120/5122、sparse 越界 index 拒、indices 步进拒、byteStride < 元素拒、整型 POSITION 拒、未归一化整型属性拒、sparse-only accessor、sparse 步进拒 |
| Tiles 严格化 | scene3d-tiles.test.ts（+2） | refine 非法值 typed 拒（不再静默 coerce）、子未声明继承父 |
| 累计预算 | scene3d-budget.test.ts（+9） | 账本 unit（acquire/release/wouldExceed/limits 局部覆盖/amountsOfStats）、双资产超限 degraded+首资产存活、卸载归还后第二资产可挂、dispose 清账、清扫归属释放 spy ×3（含 setStyle 循环不净增） |
| 生命周期 | scene3d-lifecycle.test.ts（+6） | cancelled（在途打断）vs unmounted（落定移除）、context_lost（abort/释放/记账清零/sync 守卫/幂等）、restored 有限重建（状态链+重挂+重取）、无 lost 的 restored no-op、本地表象覆盖（显隐叠加 spec 优先/重同步存活/未知资产 false） |
| 纯 3D ACK | tests/unit/gis_harness/test_scene3d_observation_evidence.py（+8） | DTO 归一（词表/截断/extra 键丢弃/非 list/全非法→None）、envelope scene_work 投影（纯 3D/悬空源 unknown/进 work 指纹/无 scene 空数组） |
| corpus | corpus.golden.test.ts（+2/改 2） | s13 场景（scene_work + model3d sources 对账）、manifest max_scene_work 三重锁、计数 12→13 |
| 指纹门 | use-cartographic-observation.test.ts（+3） | 信封指纹 POST（body 断言）/revision 滞后跳过/无信封无 HUD 无游标跳过 |
| 工作台 | scene-controller.test.tsx（+3/改 1） | per-asset 控件驱动渲染缝、光照预设、视角保存/应用（easeTo 参数）、双 radiogroup a11y |
| j9 | j9-scene3d-assets.journey.ts（扩展） | 纯 3D observation POST（指纹/scene3d_assets/work echo）、toggle aria-pressed 门控、i18n 定位器 |

## 修改测试（行为锁定更新）
- corpus 场景计数 12→13（generate.py 再生）+ Python 侧同步。
- maplibre-mock-surface / 既有 scene-controller radiogroup 断言按名定位（新光照组共存）。

## 负例/故障注入覆盖清单（对照工作单）
非法 GLB（交错步进非法/sparse 越界/整型 POSITION）typed 拒 ✓；预算超限（单资产 degraded/tile 延后）✓；context lost/restored ✓；setStyle 清扫泄漏 ✓；旧 revision 晚回（信封滞后跳过）✓；取消在途 ✓；404（既有）✓；越权 URL（resource 门既有 + resolveUriSafe 清扫路径显式归属释放）✓。

## 全量回归
- 前端 vitest 492 文件：4659 绿 + 1 expected-fail + 1 既有 master 红（catalogue，见 EVIDENCE 取证）。
- 后端 cartography 2912 绿 + 1 既有 master 红（mutation parity set_scene_invalid_mode，同上取证）。
- j1–j9 journeys：11 passed / 7 skipped（非 mock 变体；并行负载抖动两例均单跑复验通过）。
