# Task Plan: WebGIS AI Agent — 项目状态审计与后续开发路线

## Goal
全面审计 WebGIS AI Agent 项目的实现状态，明确已完成、部分完成和未实现的功能，为后续开发提供可靠的决策依据。

## Current Phase
Phase 1 (审计完成)

## 项目概况
- **版本**: 0.9.0+ (CHANGELOG Unreleased 有新增功能)
- **后端**: FastAPI + PostgreSQL/PostGIS + Redis + 多LLM (OpenAI/Gemini/Ollama)
- **前端**: Next.js + MapLibre GL + Zustand + TailwindCSS
- **后端文件数**: ~142 Python 源文件
- **前端文件数**: ~150 TS/TSX 源文件 (不含 node_modules)
- **后端测试**: ~105 测试文件
- **前端测试**: ~25 测试文件 (含 .test.tsx/.test.ts)

---

## Phases

### Phase 1: 项目审计 — 实现状态盘点
- [x] 后端架构分析
- [x] 前端架构分析
- [x] 测试覆盖分析
- [x] TODOS.md / CHANGELOG.md / MEMORY.md 交叉验证
- [x] 生成 task_plan.md / findings.md / progress.md
- **Status:** complete

### Phase 2: 安全与稳定性（P1 — 上线前必须）
- [x] P1-1: 前端回归测试 (base-layer dual-write) ✅ CLOSED
- [x] P1-3: WS 未认证放大 → Nominatim DoS (Phase-1) ✅ CLOSED
- [x] P1-4: 环境感知 prompt injection 防御 (Phase-1) ✅ CLOSED
- [x] P1-5: SVG 上传内容清洗 ✅ CLOSED
- [x] P1-6: layer-ref 前缀匹配擦除攻击 (后端) ✅ CLOSED
- [x] P1-2: MapActionHandler 新命令分支前端测试 (REORDER_LAYER, REMOVE_LAYER, zoom_to_bbox, set_map_view, add_marker, draw_measurement, clear_annotations) ✅ CLOSED
- [x] P1-4 Phase-2: 完整 XML-fence 隔离 (环境感知 block) ✅ CLOSED
- [x] P1-6 前端补充: map-action-handler.tsx 中 startsWith 改为精确 ID 匹配 ✅ CLOSED
- [x] P1-3 Phase-2: WS 认证收紧 (要求 verify_token) ✅ CLOSED
- **Status:** complete

### Phase 3: 性能优化（P2 — Chat 延迟热点）
- [x] P2-1: 缓存 GeoJSON bbox per ref_id ✅ CLOSED
- [x] P2-3: 复用 aiohttp.ClientSession for Nominatim ✅ CLOSED
- [x] P2-5: _aliases 私有属性耦合 → resolve_alias ✅ CLOSED
- [x] P2-7: task_cancelled SSE 事件前端无处理器 ✅ CLOSED
- [x] P2-2: Redis 管道合并 (env summary round-trips) ✅ CLOSED
- [x] P2-4: 地图点击视觉选择反馈 ✅ CLOSED
- [x] P2-6: 文档漂移 (11 新 LLM 工具、6 新地图命令、层生命周期模型) ✅ CLOSED
- [x] P2-8: 对话历史持久化 (数据库) (由 Phase 5 晋升，CEO review 建议加速执行) ✅ CLOSED
- **Status:** complete

### Phase 4: 可维护性清理（P3 — 随改随修）
- [x] P3-1: 拆分 context_builder.py (已完全拆分为 context 模块包，并重构为入口编排器) ✅ CLOSED
- [x] P3-2: DRY: bbox walkers + feature-property summarizer (已提炼至 app/utils/geojson.py) ✅ CLOSED
- [x] P3-3: _PENDING_STATUSES 全状态测试覆盖 ✅ CLOSED
- [x] P3-4: Redis variant get_started_at 测试 ✅ CLOSED
- [x] P3-5: Annotation state 从 module-level mutable array 迁移至 Zustand ✅ CLOSED
- [x] P3-6: error_msg[:200] 可能泄露密钥 ✅ CLOSED
- [x] P3-7: 测试中 asyncio.sleep(0.05) 不可靠时序 (已通过 Task Tracking 追踪机制解决) ✅ CLOSED
- **Status:** complete

### Phase 5: 功能增强（中期目标）
- [ ] 用户认证/授权系统
- [ ] 多用户 workspace 隔离
- [ ] Shapefile/GeoPackage/KML 上传支持
- [ ] CRS 转换服务
- [ ] 图层样式 API 增强
- [ ] 测量工具前端 UI
- [ ] 高级空间分析前端 UI (union, convex hull, distance)
- [ ] 地图导出 (图片/PDF)
- **Status:** pending

### Phase 6: 远期规划
- [ ] 栅格数据支持
- [ ] 3D 可视化
- [ ] 离线地图瓦片
- [ ] 插件系统 (自定义工具)
- [ ] ML 模型集成 (空间预测)
- [ ] 协作编辑
- [ ] 矢量瓦片服务
- [ ] 空间索引优化
- **Status:** pending

---

## Key Questions
1. Phase 2 剩余安全项是否阻塞新功能开发？→ 是，应优先完成 XML-fence 隔离和 WebSocket 认证收紧。
2. 对话历史持久化 (P2-8) 是否需要立即启动？→ 是，CEO 评审建议将其作为 10 星产品的体验基石，由 Phase 5 晋升至 Phase 3 优先执行。
3. 单元测试中 asyncio.sleep(0.05) 带来的 CI 随机失败如何解决？→ Eng 评审建议在 `viewport_naming.py` 内部引入 Task 追踪集，使测试可直接 await，彻底根治 CI 抖动。

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 先审计再规划 | 避免在不了解现状的情况下规划方向 |
| P1 安全项优先于功能开发 | TODOS.md 标记为 "before broader rollout"，不安全的功能不应上线 |
| 保留已关闭项的记录 | ✅ CLOSED 标记提供历史追溯，防止重复工作 |
| 拆分 context_builder 优先 | 工程复杂度高（870+ LOC），在加入 XML-fence 隔离前完成模块化拆分，能极大降低引入漏洞的风险 |
| 采用会话签名 WS 验证 | 在不重写前端登录态的前提下，通过 REST 握手生成 Session-Signature HMAC 校验 WS，极低成本换取极高安全性 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 研究子Agent报告的前端结构与实际不符 | 1 | 实际为 Next.js + MapLibre (非 Vite + OpenLayers)，已通过 find 命令验证真实结构 |

## Notes
- 后端架构远比初始报告描述的复杂：包含 tools/ (30+ 工具文件)、services/chat/、services/data_fetcher/、lib/geo_analysis/、lib/geo_processor/ 等
- 前端使用 Zustand 状态管理（4 个 slice: layers, settings, task, ui），MapLibre GL 地图渲染，有 glass-panel 等共享组件
- 测试覆盖较好：后端 ~105 个测试文件，前端 ~25 个测试文件
- TODOS.md 中大量 P1 项已 ✅ CLOSED，说明安全工作进展良好
