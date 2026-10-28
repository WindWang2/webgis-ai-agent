# G3DV2-B BASELINE — 实时侦查基线（2026-10-28）

## 执行环境事实
- 侦查命令：`git status --short` / `git remote -v` / `git fetch origin master --prune` / `git rev-parse origin/master` / `gh pr list` / `gh issue list`（gh 已授权，账号 WindWang2）。
- **origin/master = `70dab2b92917fe4f85375de959dad360288a60ad`**（与任务书基线一致；本地 checkout master `1115d683a` 落后 72 提交，为陈旧快照，不作为基线）。
- 本 track worktree：`/home/kevin/projects/webgis/webgis-wt-g3dv2-b-scene`，分支 `zcode/g3dv2-b-scene-runtime`，自 origin/master 创建。
- 前端依赖：worktree 内独立 `pnpm install`（pnpm 10.34.9 / node v24.21.0；three 0.186.1 + @types 0.186.0 / next 16.4.3 / vitest 4.1.11）。主仓 node_modules 为陈旧依赖集（package.json 差 12 处，缺 three），不共用。
- Python：主仓 `.venv`（3.13.17），`requirements.lock` 与 worktree 逐字节一致，可安全共享跑定向 pytest。
- 构建互斥：`flock /tmp/webgis-build.lock`；NODE_OPTIONS=--max-old-space-size=4096；vitest --maxWorkers=2；pytest --no-cov。

## 五个已合并三维 PR（不得重做，逐条核对 mergedAt）
| PR | 标题 | mergedAt | 内容 |
| --- | --- | --- | --- |
| #1771 | 开源底图/DEM 资源治理 | 2026-10-27 14:22 | typed 画像/OpenFreeMap/PMTiles→XYZ/合成 DEM/地形署名 |
| #1834 | 三维场景渲染内核（g3d02） | 2026-10-27 14:28 | MapSpec v1.7 scene.assets/model3d + 3D Tiles 1.1/glTF 受控子集 + three custom-layer 桥 + 3D 拾取 IR |
| #1773 | 三维量测契约（g3d03） | 2026-10-27 14:22 | 剖面/LOS/地表距离面积 + 垂直基准 ADR-0274 |
| #1829 | Agent 三维编排（g3d04） | 2026-10-27 14:43 | Requirement IR SceneSpec + user-wins + 4 工具 + G05 F20-F26 |
| #1830 | 三维制图产品（g3d05） | 2026-10-27 14:37 | 3D 组合模板/scene-controller/导出诚实/StoryMap v1.1（ADR-0275） |

## 开放 PR / 活跃分支
- **#1835 OPEN**（zcode/g3dv2-a-geo-data-supply，领先 origin/master 8 commits）：A 方向 DEM 数据供给生产化。**禁止触碰** data-fabric provider/DEM 瓦片生产。
- 其余 worktree（g3d01–g3d05、g3dv2-a）均为已合并 PR 的遗留分支或活跃 A 方向，无与本 track 的文件冲突。

## #1834 EVIDENCE.md 声明的真实残留（本 track 优先级来源）
1. 纯 3D spec（零图层）不触发 cartographic-observation POST —— 指纹认证链依赖 HUD 图层"代指纹"。
2. 3D Tiles 聚合（跨资产累计）预算未实现：现防线仅 并发4 + LRU96 + 每 parse 5M三角/64MB/256MB。
3. WebGL context lost 浏览器事件面：无 `webglcontextrestored` 监听；恢复=用户手动 mapKey bump；资产无 stale 标记。
4. 无 GPU 真机矩阵：WebGL 验证基于 SwiftShader 软渲染；terrain 深度遮挡为近似。
5. **byteStride/sparse accessor 不支持且当前静默交错错读**（review P2-1）。
6. 生产 bundle 逐 chunk 对比未完成。
7. j1–j8 未本地重跑。

## 侦查报告核实的代码级基线（file:line 见 GAP_MATRIX.md）
- 内核：`frontend/lib/map-kit/scene3d/{asset-manager,custom-layer,geo,resource,status}.ts + gltf/{gltf-parser,build}.ts + tiles3d/{tileset,scheduler}.ts`；测试 4 文件 65 例全绿（本机 vitest 实跑 14.2s）。
- 观察链：`MapSpec(scene.assets) → map-panel reconcile → syncSceneAssets → collectRenderObservation(render-observation.ts:301, 附 scene3d_assets) → use-cartographic-observation.ts:137-382 POST → chat.py:1769 校验/落账 → gis_trace MAP_OBSERVATION`。
- 后端：envelope `render_work_envelope.py:243-413` 不投影 scene.assets；ACK `render_apply_ack.py` 无 scene 字段；DTO `chat_schema.py:248-312` 无 scene3d_assets（前端字段被 pydantic 静默丢弃）。
- 契约三重锁：`tests/cartography/render_work_corpus/{contract_manifest.json,render_work_golden.json}` + `frontend/lib/render-protocol/contract-manifest.ts`，只能经 `generate.py` 再生成。

## 已知开放 issues 避让
- #1797 terrain 水文 DoS、#1805 CGCS2000、#1809–#1811 MapSpec filter、#1821/#1824/#1826/#1827 审查汇总：属他人 backlog，本 track 仅在正交位置（scene3d）活动，不修改其目标文件的行为语义。
