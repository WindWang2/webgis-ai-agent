# WebGIS AI Agent 前端 (V2 重新设计)

基于 **WebGIS AI Agent v2.html** 设计规范重新构建的新一代具身空间智能引擎前端。

## 🎨 V2 新特性

### 全新视觉体系 - 玻璃拟态设计
- **Light / Dark 双主题** - 完整的暗色/亮色主题切换
- **玻璃拟态 (Glassmorphism)** - 半透明背景 + 毛玻璃模糊效果
- **Agentic 配色** - 绿色为主色调的科技感配色方案
- **动态光效** - 思考/执行状态的扫描线动画

### 重构的组件架构
```
frontend/components/
├── layout/
│   ├── top-bar.tsx          # 顶部导航栏（主题切换、AI 状态）
│   └── status-bar.tsx       # 底部状态栏（坐标、缩放、底图、图层数）
├── sidebar/
│   ├── left-sidebar.tsx     # 左侧边栏容器
│   ├── chat-tab.tsx         # 聊天标签页
│   ├── layers-tab.tsx       # 图层管理标签页
│   ├── ops-log-tab.tsx      # 操作日志标签页
│   └── exports-tab.tsx      # 导出文件标签页
├── map/
│   ├── map-toolbar.tsx      # 地图工具栏
│   ├── map-canvas.tsx       # 演示用地图画布
│   ├── baselayer-switcher.tsx
│   └── floating-legend.tsx
├── hud/
│   └── agent-env-hud.tsx    # Agent 环境感知 HUD
├── panel/
│   └── rag-independent-panel.tsx
├── overlays/
│   └── perception-rings.tsx  # 感知环动画
└── tweaks-panel.tsx         # UI 调整面板
```

### 核心状态管理
- **Zustand Store** (`lib/store/useHudStore.ts`) - 统一状态管理
- **Theme System** (`lib/theme.ts`) - 主题颜色管理
- **Type Definitions** (`lib/store/hud-types.ts`) - 完整的 TypeScript 类型

## 🚀 快速开始

```bash
# 安装依赖
cd frontend
npm install

# 开发模式
npm run dev

# 构建生产版本
npm run build
```

访问 http://localhost:3000 即可查看效果。

## 🎯 功能特性

### 1. 多标签侧边栏
- **聊天** - 与 GeoAgent 对话
- **图层** - 管理地图图层（可见性、排序、不透明度）
- **操作日志** - 查看操作历史
- **导出** - 管理导出的文件

### 2. 地图工具栏
- 🔍 缩放控制 (+/-)
- 🏠 回到首页
- 📍 定位当前位置
- 🎚️ 2D/3D 切换
- 📊 HUD 显示切换
- 💾 导出

### 3. Agent 状态系统
- `idle` - 就绪
- `thinking` - 思考中（扫描线动画）
- `acting` - 执行中
- `done` - 完成
- `error` - 错误

### 4. 演示模式
点击左下角 "Try Demo" 按钮即可体验完整的模拟运行流程，无需后端支持。

## 📝 开发指南

### 新增组件
所有新组件都应遵循：
1. 支持 `isDark` 主题切换
2. 使用 CSS 变量而非硬编码颜色
3. 提供 TypeScript 类型定义
4. 保持小而精，单一职责

### 状态更新
在 `lib/store/useHudStore.ts` 中添加新的状态字段和方法。

### 主题扩展
在 `lib/theme.ts` 中添加新的颜色变量，并在 `app/globals.css` 中同步。

## 🎨 设计规范

- **字体**: DM Sans (正文) + JetBrains Mono (代码/数值)
- **圆角**: 12px (大组件), 8px (小组件), 6px (按钮)
- **阴影**: 两层阴影（主阴影 + 细节阴影）
- **过渡**: 0.2s cubic-bezier(0.4, 0, 0.2, 1)

## 📚 相关文档

- [技术方案说明书](../docs/技术方案说明书.md)
- [架构文档](../docs/architecture.md)
- [API 文档](../docs/api-docs.md)
