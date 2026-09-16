# TEST_MATRIX — 覆盖矩阵（TDD 先行）

标记：`[M*]` 里程碑；`[O*]` Oracle 条款。全部新文件；既有套件作回归。

## 后端 pytest（tests/unit/、tests/benchmarks/）

| 文件 | 覆盖 | 场景 |
|---|---|---|
| `test_scene_planning.py` [M1][O2] | SceneIntent→SceneDecision 决策表 | happy 2d/2.5d/3d；无 elevation 证据→绝不 3d 挤出；medium=print→2d；reduced_motion→相机 duration=0；unknown 字段→保守档+披露；确定性 replay（同输入同输出） |
| `test_mapspec_schema_v14_scene.py` [M2][O3] | v1.4 additive | 旧 1.0–1.3 spec parse/canonicalize byte-stable；1.4 字段校验（exaggeration clamp、mode 词表、terrain.source 必填字符串）；unknown 键保留；TS 投影再生成幂等（运行生成器比对） |
| `test_scene_lifecycle_mutations.py` [M2][M6][O1][O3] | SetSceneIntent | 提交后 layers/sources/legend_spec/thresholds 深比较不变（不漂移）；revision+1；expected_revision CAS→superseded；mutation_id 重放→duplicate 幂等；异常回滚；旧 spec 无 scene 字段行为不变；锁定分区不受 SetScene 影响 |
| `test_scene_elevation_failclosed.py` [M3][O2] | DEM ref/terrarium | 已知 DEM ref→terrarium 编码字节正确（合成小网格已知答案：高程→RGB 公式）；非 DEM ref→结构化错误（不伪造 0）；nodata→透明像素；windowed 有界（大网格超时预算）；缺 ref→404 语义 |
| `test_scene_camera_planner.py` [M5] | 相机 | overview/detail/compare；antimeridian 跨 ±180 bbox→center 最短弧；zoom clamp 3–18；pitch 分档且 ≤60（产品）；reduced_motion→duration 0；安全高度代理；确定性 |
| `test_scene_lod.py` [M5] | LOD | zoom 分档边界；label topRatio 单调；terrain maxzoom 单调不升；抽稀预算有界；空/单要素 |
| `test_scene_degradation.py` [M6] | 退化链 | 3d→2.5d→2d 每跳有披露码；触发条件矩阵；不可逆信息（挤出高度）在降级披露中声明 |
| `test_scene_quality_gate.py` [M7][O1][O4] | 质量门 | legend drift（同源配对层 digest 不一致→error）；exaggeration 越界→blocking；pitch 极端→warning；无证据挤出→warning+披露；terrain 源悬空→blocking；vertical unit unknown→warning；synthetic 典型场景全绿 |
| `test_scene_selfheal.py` [M7] | 自愈 | scene 缺陷→仅既有 intents（断言产物类型 ∈ {SetView,SetScene,PatchLayerPresentation}）；收敛上限；空计划诚实拒绝 |
| `test_scene_tools.py` [M8] | agent 工具 | plan（只读、无副作用）；set（经 facade 事务、错误码透传）；租户/会话隔离（session_id 作用域） |
| `tests/benchmarks/test_scene_perf.py` [M8][O6] (marker perf) | 性能 | 合成 10k 多边形高度场决策/LOD/相机/质量门有界耗时；DEM 1000×1000 合成网格 terrarium tile 编码有界；内存不随 N 无界增长（抽样断言） |

## 前端 vitest（frontend/lib/…）

| 文件 | 覆盖 |
|---|---|
| `map-kit/scene-terrain.test.ts` [M8][O5] | enable3DTerrain 选项（自定义 url/exaggeration/sourceId）；默认参数不变（AWS 兜底）；disable 幂等 |
| `mapspec-runtime/scene-evidence-gate.test.ts` [M8][O2] | 自动挤出证据门控：有 extrusion 契约→挤出；无契约无 height→不挤出 + degradation 码；旧 spec（无 scene）行为不变 |
| `map-kit/render-scene.parity.test.ts`（扩展）[M8][O1] | scene 披露码进 RenderSceneSnapshot；双端序列化一致 |
| compiler scene 测试 [M2] | scene.terrain → style.terrain 投影；无 scene → style 无 terrain 键（旧样式字节不变） |

## 回归面（既有套件，不改断言）

- `pytest -m cartography`（lifecycle/semantic/闭环门）
- `tests/unit/test_3d_extrusion_cartography.py`（挤出既有面）
- `tests/unit/test_mapspec_schema.py`（golden corpus）
- 前端 `vitest run lib/map-kit lib/mapspec-compiler lib/mapspec-runtime`（targeted）
- `ruff check app tests`；`git diff --check`
