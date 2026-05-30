# Findings & Decisions

## 项目技术栈（已验证）

### 后端
- **框架**: FastAPI (async) + Uvicorn
- **数据库**: PostgreSQL + PostGIS (GeoAlchemy2) + Alembic 迁移
- **缓存**: Redis (已配置，部分使用：tool_cache 已实现，env summary 未优化)
- **LLM**: 多提供商 — OpenAI, Google Gemini, Ollama，支持 tool/function calling
- **地理**: geopy (Nominatim), shapely, pyproj, aiohttp
- **认证**: 基础 auth 模块存在 (app/core/auth.py)

### 前端
- **框架**: Next.js (app router) + React + TypeScript
- **地图**: MapLibre GL JS (非 OpenLayers — 初始报告有误)
- **状态管理**: Zustand (4 slices: layers, settings, task, ui)
- **样式**: TailwindCSS + CSS modules
- **通信**: SSE (Server-Sent Events) + WebSocket + REST API
- **图表**: 内置 chart-renderer 组件

---

## 已完成功能清单 ✅

### 后端核心
| 功能 | 文件/模块 | 说明 |
|------|----------|------|
| AI Chat 引擎 | `services/chat_engine.py` | 多轮对话、tool calling、max 60 rounds |
| 上下文构建器 | `services/chat/context_builder.py` | 环境感知、层 schema、历史压缩 |
| 工具注册表 | `tools/registry.py` | 统一工具分发、计时、metrics |
| 30+ GIS 工具 | `tools/*.py` | 地理编码、缓冲区、空间推理、OSM、制图、注释、测量等 |
| 图层管理器 | `tools/layer_manager.py` | 显示/隐藏/重排/删除图层 |
| 地图视图控制 | `tools/map_view.py` | fly_to, zoom_to_bbox, set_map_view |
| WebSocket 实时通信 | `services/ws_service.py` + `api/routes/ws.py` | viewport 变化、实时推送 |
| SSE 流式响应 | `utils/sse.py` | token 流、工具调用、任务状态 |
| 视口命名 | `services/viewport_naming.py` | Nominatim 反向地理编码 + 限流 |
| 会话数据管理 | `services/session_data*.py` | 内存 + Redis 双后端、别名解析 |
| 空间分析库 | `lib/geo_analysis/` | 聚合、插值、网络分析、光栅操作、统计 |
| 几何处理库 | `lib/geo_processor/` | core、geometry、overlay |
| 工具结果缓存 | `lib/tool_cache.py` | @cached_tool 装饰器、Redis-keyed |
| 文件上传 | `tools/upload_tools.py` + `api/routes/upload.py` | 文件处理 |
| SVG 安全清洗 | `api/routes/map.py` | defusedxml 解析、危险元素/属性剥离 |
| 坐标转换 | `tools/coord_transform.py` + `utils/coord_transform.py` | CRS 工具 |
| 中国地图集成 | `tools/chinese_maps/` | 高德、百度、天地图适配器 |
| Web 爬虫 | `tools/web_crawler.py` | 网页数据抓取 |
| 制图工具 | `tools/cartography.py` | 专题地图制作 |
| 图表工具 | `tools/chart.py` | 数据可视化 |
| 报告生成 | `tools/report.py` + `services/report_service.py` | 分析报告 |
| 探索器 | `tools/explorer_tools.py` + `services/explorer/` | 数据探索 |
| What-if 模拟 | `tools/what_if_simulate.py` + `tools/what_if_rules.py` | 场景分析 |
| 变化检测 | `tools/change_detection.py` | 时序变化分析 |
| 遥感服务 | `tools/remote_sensing.py` + `services/rs_service.py` | 遥感数据 |
| 地形分析 | `tools/terrain_analysis.py` | 地形相关分析 |
| 空间统计 | `tools/spatial_stats.py` | 统计工具 |
| 高级空间分析 | `tools/advanced_spatial.py` | 高级分析工具 |
| 自然资源工具 | `tools/nature_resources.py` | 自然资源查询 |
| 行政区划 | `tools/local_admin.py` | 本地行政区划 |
| Plan Mode | `tools/plan_mode.py` + `services/plan_mode.py` | 规划模式 |
| 子Agent | `tools/subagent.py` + `services/subagent.py` | 子Agent 编排 |
| Skills 系统 | `tools/skills.py` + `api/routes/knowledge.py` | 技能注册/管理 |
| RAG 服务 | `services/rag_service.py` + `adapters/rag/` | 检索增强生成 |
| 数据获取服务 | `services/data_fetcher/` | 多源数据适配器 (PostGIS, OSS, 文件, 第三方API) |
| Provider 健康 | `services/provider_health.py` | LLM 提供商健康监测 |
| 速率限制 | `core/rate_limiter.py` | 全局限流 |
| 日志配置 | `core/logging_config.py` | 结构化日志 |
| 签名验证 | `core/signing.py` | 请求签名 |

### 前端核心
| 功能 | 文件/模块 | 说明 |
|------|----------|------|
| 地图面板 | `components/map/map-panel.tsx` | MapLibre 地图容器 |
| 地图画布 | `components/map/map-canvas.tsx` | 地图渲染核心 |
| 底图切换 | `components/map/baselayer-switcher.tsx` | 多底图切换 |
| 地图装饰 | `components/map/map-decorations.tsx` | 比例尺等装饰 |
| AI 命令处理 | `components/map/map-action-handler.tsx` | AI→地图命令桥接 |
| 专题图例 | `components/map/thematic-legend.tsx` + `legends/` | 分级/分类/连续/双向图例 |
| 浮动图例 | `components/map/floating-legend.tsx` | 浮动图例面板 |
| 空间十字线 | `components/map/spatial-crosshair.tsx` | 空间定位辅助 |
| 导出遮罩 | `components/map/export-mask.tsx` | 地图导出区域选择 |
| 可拖拽图层列表 | `components/map/draggable-layer-list.tsx` | 图层排序 |
| Chat 面板 | `components/chat/chat-panel.tsx` | AI 对话界面 |
| 工具调用卡片 | `components/chat/tool-call-card.tsx` | 工具调用展示 |
| 折叠思考 | `components/chat/collapsible-think.tsx` | 推理过程折叠 |
| 规划卡片 | `components/chat/plan-card.tsx` + `plan-proposal-card.tsx` | 规划展示 |
| 制图结果卡片 | `components/chat/cartography-result-card.tsx` | 制图结果展示 |
| Mini Markdown | `components/chat/mini-md.tsx` | 轻量 Markdown 渲染 |
| 建议提示词 | `components/chat/suggested-prompts.tsx` | 预设提示词 |
| 任务进度 | `components/chat/task-progress.tsx` | 任务进度条 |
| 地图动作渲染 | `components/chat/map-action-renderer.tsx` | 地图命令可视化 |
| 图表渲染 | `components/chat/chart-renderer.tsx` + `panel/chart-renderer.tsx` | 数据图表 |
| 左侧面板 | `components/sidebar/left-sidebar.tsx` | 侧边栏容器 |
| 图层标签页 | `components/sidebar/layers-tab.tsx` | 图层管理 |
| 资源标签页 | `components/sidebar/assets-tab.tsx` | 资源管理 |
| 聊天标签页 | `components/sidebar/chat-tab.tsx` | 聊天入口 |
| 导出标签页 | `components/sidebar/exports-tab.tsx` + `export-layout-tab.tsx` | 导出功能 |
| 地图工作室 | `components/sidebar/map-studio-tab.tsx` | 地图编辑 |
| RAG 标签页 | `components/sidebar/rag-tab.tsx` | RAG 检索 |
| 操作日志 | `components/sidebar/ops-log-tab.tsx` | 操作记录 |
| 设置面板 | `components/settings/settings-panel.tsx` | 系统设置 |
| LLM 配置 | `components/settings/llm-config.tsx` | LLM 切换/配置 |
| 地图配置 | `components/settings/map-config.tsx` | 地图参数配置 |
| RAG 配置 | `components/settings/rag-config.tsx` | RAG 设置 |
| 图层管理 | `components/settings/layer-management.tsx` | 图层高级管理 |
| 技能中心 | `components/settings/skills-hub.tsx` | 技能注册/启用 |
| 系统设置 | `components/settings/system-settings.tsx` | 系统级设置 |
| 上传区域 | `components/upload/upload-zone.tsx` + `upload-progress.tsx` | 文件上传 |
| 报告生成 | `components/report/report-generator.tsx` + `report-preview.tsx` | 报告生成/预览 |
| 感知环 | `components/overlays/perception-rings.tsx` | 空间感知覆盖 |
| 图层卡片 | `components/layer-card.tsx` + `sort-controls.tsx` | 图层信息卡 |
| 资产卡片 | `components/panel/asset-card.tsx` | 资产项展示 |
| RAG 独立面板 | `components/panel/rag-independent-panel.tsx` | RAG 独立视图 |
| 顶栏 | `components/layout/top-bar.tsx` | 页面顶栏 |
| 调整面板 | `components/tweaks-panel.tsx` | 参数微调 |
| Toast 通知 | `components/ui/toast.tsx` | 消息通知 |
| 共享组件 | `components/shared/glass-panel.tsx`, `toggle-switch.tsx`, `section-title.tsx` | UI 基础组件 |
| 地图桥接 Hook | `lib/hooks/useMapBridge.ts` | SSE + AI 状态管理 |
| WebSocket Hook | `lib/hooks/use-websocket.ts` | WS 连接管理 |
| 键盘快捷键 | `lib/hooks/use-keyboard-shortcut.ts` | 快捷键支持 |
| 地理定位 | `lib/hooks/use-geolocation.ts` | 浏览器定位 |
| 地图渲染器 | `lib/map-kit/renderer.ts` | 图层渲染引擎 |
| 地图导航 | `lib/map-kit/navigation.ts` | 导航控制 |
| 地图导出 | `lib/map-kit/exporter.ts` | 地图导出 |
| 地图状态 | `lib/map-kit/state.ts` | 地图状态管理 |
| Zustand Store | `lib/store/useHudStore.ts` + `slices/` | 全局状态 (4 slices) |
| API 客户端 | `lib/api/` (chat, config, explorer, layer, report, skills, task, upload) | 完整 API 层 |
| 地理工具 | `lib/utils/geo.ts` | 地理计算工具 |
| 主题系统 | `lib/theme.ts` | 亮/暗主题 |

---

## 已关闭的安全/性能修复

| ID | 项目 | 关闭日期 | 说明 |
|----|------|---------|------|
| P1-1 | base-layer dual-write 回归测试 | 2026-05-21 | AI dispatch + user-click 双写测试 |
| P1-3 P1 | WS Nominatim DoS 防护 | 2026-05-21 | Token bucket 30 calls/min + 共享 aiohttp session |
| P1-4 P1 | 环境感知 prompt injection | 2026-05-21 | _untrusted() 转义 + 500 char 限制 + 12 回归测试 |
| P1-5 | SVG 上传清洗 | 2026-05-21 | defusedxml + 危险元素剥离 + 13 测试 |
| P1-6 BE | layer-ref 前缀擦除攻击 | 2026-05-21 | 后端 ref_id 存在性检查 + 7 测试 |
| P2-1 | GeoJSON bbox 缓存 | 2026-05-21 | 不可变 ref → schema 缓存, 上限 1024 |
| P2-3 | aiohttp Session 复用 | 2026-05-21 | 模块级共享 session |
| P2-5 | _aliases 解耦 | 2026-05-21 | resolve_alias() 公共 API |
| P2-7 | task_cancelled 前端处理 | 2026-05-21 | aiStatus → 'idle' |

---

## 未完成项（按优先级）

### P1 — 安全 (剩余)
| ID | 项目 | 文件 | 工作量 |
|----|------|------|--------|
| P1-2 | MapActionHandler 新命令分支测试 | `map-action-handler.test.tsx` | ~30 min CC |
| P1-4 P2 | XML-fence 完整隔离 | `context_builder.py` | 需与 Ambient v1 协调 |
| P1-6 FE | startsWith → 精确 ID 匹配 | `map-action-handler.tsx:339` | ~15 min CC |
| P1-3 P2 | WS auth 收紧 | `ws_service.py` | ~1h human |

### P2 — 性能/UX (剩余)
| ID | 项目 | 文件 | 工作量 |
|----|------|------|--------|
| P2-2 | Redis 管道合并 | `context_builder.py` | ~30 min CC |
| P2-4 | 地图点击视觉反馈 | `map-panel.tsx` | ~30 min CC |
| P2-6 | 文档漂移 | `docs/api-docs.md` | ~15 min CC |

### P3 — 可维护性 (剩余)
| ID | 项目 | 文件 |
|----|------|------|
| P3-1 | context_builder.py 拆分 | 520+ LOC, 7 concerns |
| P3-2 | DRY: bbox + feature-property | 重复代码合并 |
| P3-3 | _PENDING_STATUSES 全测试 | 参数化 5 状态 |
| P3-4 | Redis get_started_at 测试 | fakeredis |
| P3-5 | Annotation state → Zustand | 模块级 mutable array |
| P3-6 | error_msg 密钥泄露 | 允许列表/模式剥离 |
| P3-7 | 测试 asyncio.sleep 不可靠 | Task 显式追踪 |

---

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 使用 MapLibre GL (非 OpenLayers) | 矢量瓦片原生支持，样式灵活性高 |
| Zustand 状态管理 (非 Redux) | 轻量、无 boilerplate、slice 模式 |
| SSE + WebSocket 双通道 | SSE 用于 AI 流式响应，WS 用于 viewport 实时同步 |
| 多 LLM 提供商 | 灵活切换 + fallback 容错 |
| 内存 + Redis 双后端 session | 开发简便 + 生产可扩展 |
| @cached_tool 装饰器 | 可选 Redis 缓存，优雅降级 |
| display_layer 隐式→显式 | 中间层保持隐藏，仅最终结果显示 |

## Issues Encountered
| Issue | Resolution |
|-------|-----------|
| 初始研究子Agent报告前端为 Vite+OpenLayers | 实际为 Next.js+MapLibre，通过文件系统验证确认 |
| TODOS.md 与 CHANGELOG.md 信息密度差异大 | 以 TODOS.md 为准（更详细、含修复状态），CHANGELOG 主要记录新功能 |

## Resources
- 项目根目录: `/home/kevin/projects/webgis-ai-agent`
- TODOS.md: 最权威的任务状态记录
- MEMORY.md: 长期记忆与开发规则
- CHANGELOG.md: 版本发布记录
- docs/api-docs.md: API 文档 (有漂移)
- docs/: 其他架构文档

## Visual/Browser Findings
- 未进行 (本次为代码审计，非 UI 审计)

---
*Update this file after every 2 view/browser/search operations*
*This prevents visual information from being lost*
