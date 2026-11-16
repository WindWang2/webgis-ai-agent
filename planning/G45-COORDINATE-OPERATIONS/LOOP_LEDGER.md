# G45 LOOP_LEDGER — 执行账本

## 基线
- base：655f769ca17f17c5797da13b4c299b8fa600e370（master，含 G40 #2124）
- 分支：zcode/g45-coordinate-operations-20261115T154805Z（独立 worktree）
- ID 保留：`.bare/webgis-id-reservation.lock`（migration g45_coordinate_operations、
  journey e2e/j25、pytest 前缀 g45、JobKind coord_operations、DataObject kind
  coordinate_operation）

## 轮次记录（浓缩；每轮=确认闭环→追链→增量→验证→修正→记账）

1. **阶段 B 侦察**：fetch master/PR/review（#2117–#2124 空评审、#2120 一条复审
   comment、#2123 基线债 issue、#2125 G47 open）；迁移单 head g40；Explore agent
   侦察 G38/G39 模式 + 前端结构 + 生成物流程；pyproj 3.8.0/PROJ 9.8.1 离线
   （0 格网）能力实测（TransformerGroup/from_pipeline bit-exact 重建/轴序/单位）。
2. **后端域包**：contracts/crs_contract/candidates/freeze/execute/outputs/service +
   model/迁移/schema/routes/tools + main.py/JobKind/DataObject/capability/algorithm
   接线。冒烟抓出并修复：GC 空几何误判（真实缺陷）、`_dimension_support` 顺序
   （dynamic 先于 3D 分支）、非有限检查 all/any、C37/C03 用 (55,25) 合法对。
3. **域测试**：52 项通过（oracle 26 + 执行流 21 + G18 交付 3）。修复：failure
   路径传播（pytest.raises+事后 inspect）、显示副本隔离层（C22 真实产品修复）、
   run_in_executor 参数（functools.partial）、线程内 progress 协程警告
   （_report_progress）。
4. **Pi 旅程**：vendor 构建（v1.5.2 pin）+ 真实子进程全链。三轮修复：
   failure_modes 元组→列表（GIS_MANIFEST_STRICT 抓出）、每个工具 turn 前置
   tool_search、plan_id 注入时机。21 gates 全绿（C32）。
5. **前端**：api client/store/panel/i18n/入口接线 + 面板测试。修复：ICU 插值
   单括号、toast API、icon import、断言歧义。typecheck 绿（剩 2 项未触及
   文件基线债，干净 base 可复现）。
6. **浏览器旅程 j25**（迭代最多的包，环境工程为主）：
   - CORS（默认只放行 :3000 → 显式 CORS_ORIGINS）
   - NEXT_PUBLIC_API_URL 冷缓存内联（.env.local + 清 .next）
   - API client BASE 缺 /api/v1（真实缺陷修复）
   - 播种链三换：upload ref 是 {geojson:…} 包装不可消费 → data-quality
     autofix/apply 铸真 FC ref（声明 CRS + 所有权守卫 + schema_info.crs 保真）
   - 会话 id：SSE 内部载荷不可直接用 → owner_token 归属 + 列表兜底
   - 产品修复①：list_runs 剥离 receipt → 面板恢复改 inspectRun（真实缺陷）
   - 产品修复②：execute 轮询重渲染使 click actionability 永不满足 → 旅程用
     Escape（useDialogFocus 契约）
   - 幂等重跑：alreadyLocked 条件跳过（恢复态）
   - 终态：1 passed（真实登录→chat→播种 1201→候选/锁定/预览→执行 completed
     1201/1201→重开恢复→G18 GPKG 下载 SQLite magic 回读）
7. **生成物**：OpenAPI snapshot 刷新（11 passed）、api-docs 重生成（drift 3
   passed）、staleness 账本 --update。

## 验证汇总（本 worktree 实测）

- 域测试 52 passed；G18 交付集成 3 passed（C23/C24/C21）
- Pi 旅程 21 gates passed（C32）
- 浏览器旅程 j25 1 passed（C33）
- 迁移空库 upgrade/downgrade/re-upgrade 真实执行通过
- 前端 tsc 绿 + 面板测试 13 passed（含 i18n 完整性）
- 生成物三闸绿（snapshot/drift/staleness）
- 详见 TEST_MATRIX.md（C01–C40 逐项）

## 未完成/边界（如实）

- C35：本环境 0 受信格网，格网正向转换路径无法真实执行（NOT RUN）
- C12 执行期域外 fail 政策与非有限失败共享代码路径，未单列集成测试
- #2123 基线债不在本任务范围（未触及文件的失败在干净 base 可复现）
- j25 耗时长（冷编译 8–10 分钟）—— nightly 档可接受；热路径 15.6s
