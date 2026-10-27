# G3D05 测试矩阵（验收条款 → 测试定位）

| 验收条款（工作单 §D） | 测试 |
| --- | --- |
| ≥3 类可编辑 3D/2.5D 组合产品 + UI + 组件 + 图例/来源/交互证据 | `tests/cartography/test_scene_3d_pack.py`（pack 注册/校验/必填披露/模型解析）；`components/map/map-components/scene-disclosure.test.tsx`（13）+ `elevation-profile-panel.test.tsx`（9）；`components/map/scene-controller.test.tsx`（8） |
| 交互/静态无静默语义冲突；透视比例尺与矢量限制显式披露 | `tests/cartography/test_scene_export_honesty.py`（阈值互锁/孪生收据/manifest 溯源）；`lib/map-kit/scene-export-disclosure.test.ts`（11）；`export-chrome.test.ts` 披露注记 2 项；`vector-svg-export` 收据（既有套件内 8 过）；语义语料 round-trip（`test_scene_3d_pack.py::test_semantic_corpus_roundtrip`） |
| 场景/统计剖面/相机 human+Agent 共控、保存/恢复、用户优先 | `test_user_scene_mutation_api.py`（6：同管线/幂等/409）；`scene-mutation.test.ts`（5）；`scene-controller.test.tsx`（is3D 联动/2d 回落）；`test_storymap_scene_v11.py`（资产 round-trip/损坏/HTTP）+ `storymap-scene.test.ts`（4） |
| 无"分析成功但图上无结果/导出缺披露"标完成；负例阻塞出图并解释 | `test_scene_quality_gate.py`（透视比例尺 warning/info 降级、遮挡、矢量降级）；conformance `required_slot_missing`；SVG 孪生 `profile_ref_unavailable` 结构化省略 |
| 2D 存量无回归 | cartography gate 2024 全绿（golden corpus 零漂移）；前端全量 4533 绿（map-panel 40、exporter 族 100+、story 32） |
