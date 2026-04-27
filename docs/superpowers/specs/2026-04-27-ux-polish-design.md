# UX Polish: Toast + Suggested Prompts + Lazy Loading

## Context

功能层已完善（20+ 空间分析工具、中国地图 API、图层样式编辑器、Skills 系统全部实现），但用户体验有三个显著短板：操作反馈静默、新用户引导缺失、首屏加载慢。本次以最小投入横跨三个领域各一项高 ROI 改进。

## 1. 全局 Toast 通知系统

### 架构

新建 `components/ui/toast.tsx`，基于 Zustand store 管理通知队列。

```
useToastStore (Zustand)
  ├── toasts: Toast[]
  ├── addToast(message, type, duration?)
  └── removeToast(id)

ToastContainer (挂载在 layout.tsx > ClientProviders 内)
  └── 渲染 toasts 队列，底部右侧弹出
```

### Toast 类型

| type | 颜色 | 图标 | 用途 |
|------|------|------|------|
| success | `hud-green` | Check | 上传成功、图层加载 |
| error | `hud-red` | X | API 失败、网络错误 |
| info | `hud-cyan` | Info | 图层删除、状态变更 |
| warning | `hud-orange` | Alert | 依赖缺失、非致命问题 |

### 行为

- 默认 3 秒自动消失，可指定 duration
- 支持手动关闭（X 按钮）
- 同类消息不重复堆叠（2s 内相同 message 去重）
- HUD 主题：玻璃面板背景、`motion` 从右侧滑入、stagger 延迟

### 接入点

| 操作 | 触发位置 | 类型 |
|------|----------|------|
| 文件上传成功 | `upload-zone.tsx` onUploadSuccess | success |
| 图层加载到地图 | `results-panel.tsx` onLoad | success |
| 图层删除 | `results-panel.tsx` onDelete | info |
| 资产删除 | `results-panel.tsx` onDelete | info |
| API 错误 | `lib/api/*.ts` catch | error |

## 2. 建议提示词（Suggested Prompts）

### 位置

聊天面板底部输入框上方，仅当 `messages.length === 0 && !isLoading` 时显示。

### 布局

水平排列的可滚动卡片列表，3-4 张卡片：

| 图标 | 文案 | 覆盖能力 |
|------|------|----------|
| MapPin | 分析北京市学校分布 | 空间分析 |
| Satellite | 计算 NDVI 植被指数 | 遥感影像 |
| BarChart3 | 生成人口密度热力图 | 数据可视化 |
| Search | 搜索成都市天府广场 | 地理编码 |

### 交互

- 点击 → 调用 `onSend(text)` 回调（从 `app/page.tsx` 的 `handleSend` 传入） → 清空建议
- hover：HUD 青色边框发光
- 入场：framer-motion stagger（每张延迟 50ms）

### 数据流

```
app/page.tsx (handleSend)
  └── ChatHud (新增 onSend prop)
        └── SuggestedPrompts (空闲时渲染，点击调用 onSend)
```

注意：当前 `ChatHud` 是只读展示组件，消息输入在 `DynamicIsland`。需要给 `ChatHud` 新增 `onSend` prop。

### 数据源

静态配置（`SUGGESTIONS` 常量数组），不依赖 API。后续可改为从 skills 列表动态生成。

## 3. 组件懒加载

### 改动

`app/page.tsx` 中 3 个重组件改用 `next/dynamic`：

| 组件 | 当前 | 改为 | 原因 |
|------|------|------|------|
| MapPanel | 静态 import | `dynamic(() => ..., { ssr: false })` | maplibre-gl ~200KB，依赖浏览器 |
| ChartRenderer | 静态 import | `dynamic(() => ..., { ssr: false })` | recharts ~150KB，不需要 SSR |
| DynamicIsland | 静态 import | `dynamic(() => ...)` | 非首屏关键，可延迟 |

### Loading Fallback

每个懒加载组件配一个轻量占位：
- MapPanel: 全屏脉冲动画（现有 HUD 脉冲风格）
- ChartRenderer: 灰色骨架条
- DynamicIsland: 无需 fallback（顶部浮层，用户不会注意到延迟）

### 预期效果

首屏 JS 减少 ~350KB（maplibre-gl + recharts 延迟加载），FCP 改善。

### 不做

- Route-based code splitting（只有 2 个路由，收益小）
- Service worker 离线缓存（scope 太大）
- Image/font optimization（已有 next/font + 外部 CDN）

## Files

| File | Change |
|------|--------|
| `components/ui/toast.tsx` | 新建 — Toast 组件 + useToastStore |
| `components/providers/client-providers.tsx` | 挂载 `<ToastContainer />` |
| `components/chat/suggested-prompts.tsx` | 新建 — 建议提示词卡片 |
| `components/chat/chat-panel.tsx` | 空闲状态渲染 SuggestedPrompts |
| `app/page.tsx` | MapPanel/ChartRenderer/DynamicIsland 改 dynamic import |
| `components/upload/upload-zone.tsx` | 上传成功触发 toast |
| `components/panel/results-panel.tsx` | 图层操作触发 toast |

## Verification

1. `npx next lint` — 0 warnings
2. `npm run build` — 通过，检查 chunk 拆分效果
3. `npx vitest run` — 测试通过
4. 手动验证：上传文件 → toast 弹出；清空聊天 → 建议卡片出现；点击建议 → 消息发送；刷新页面 → map 延迟加载有骨架屏
