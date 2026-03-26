# WebGIS AI Agent - Frontend

基于 Next.js + MapLibre GL JS 的地理信息系统前端。

## 功能特性

- 🗺️ 交互式地图（缩放、平移）
- 📂 图层管理（上传、列表、加载、属性查询）- 开发中
- 📐 空间分析（任务提交、进度展示、结果加载）- 开发中

## 快速开始

### 环境要求

- Node.js 18+
- npm 9+

### 安装依赖

```bash
cd frontend
npm install
```

### 启动开发服务器

```bash
npm run dev
```

访问 http://localhost:3000

### 构建生产版本

```bash
npm run build
npm start
```

## 项目结构

```
frontend/
├── app/
│   ├── components/     # 组件（Sidebar, Header, MapView）
│   ├── layout.tsx      # 全局布局
│   └── page.tsx        # 首页
├── package.json
└── README.md
```

## 技术栈

- Next.js 16 (App Router)
- MapLibre GL JS
- TypeScript