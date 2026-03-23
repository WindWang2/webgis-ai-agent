# WebGIS AI Agent

基于大语言模型的智能 WebGIS 数据分析与制图系统。

## 技术栈

- **前端框架**: Next.js 14 + React 18 + TypeScript
- **样式**: Tailwind CSS + shadcn/ui
- **地图引擎**: MapLibre GL JS + react-map-gl
- **容器化**: Docker + Docker Compose

## 项目结构

```
webgis-ai-agent/
├── app/                    # Next.js App Router
│   ├── layout.tsx         # 根布局
│   ├── page.tsx           # 主页（三栏布局）
│   └── globals.css        # 全局样式
├── components/            # React 组件
│   ├── chat/             # 对话面板组件
│   ├── map/              # 地图面板组件
│   └── panel/            # 结果面板组件
├── lib/                   # 工具函数
├── docs/                  # 项目文档
└── ...
```

## 开发环境

### 本地开发

```bash
# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 访问 http://localhost:3000
```

### Docker 开发

```bash
# 使用 Docker Compose 启动（支持热重载）
docker-compose up --build
```

### Docker 生产构建

```bash
# 构建镜像
docker build -t webgis-ai-agent:latest .

# 运行容器
docker run -p 3000:3000 webgis-ai-agent:latest
```

## 功能模块

### 1. AI 对话面板（左侧）
- 自然语言指令输入
- 文件上传支持
- Markdown 渲染
- 工具调用进度显示

### 2. 地图面板（中间）
- MapLibre GL JS 交互式地图
- 图层管理
- 空间量测工具
- 实时图层加载

### 3. 结果面板（右侧）
- 分析结果展示
- 报告预览
- 多格式导出（HTML/PDF/Word）

## 开发规范

### Git 工作流

1. 从 `develop` 分支创建功能分支
2. 提交使用约定式提交（Conventional Commits）
3. 创建 Pull Request 到 `develop` 分支

```bash
# 创建功能分支
git checkout develop
git pull
git checkout -b feature/your-feature-name

# 提交（使用 git -c 指定身份）
git -c user.name="你的名字" -c user.email="your@email.com" commit -m "feat: 描述"

# 推送
git push origin feature/your-feature-name
```

### 提交规范

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `style:` 代码格式
- `refactor:` 重构
- `test:` 测试
- `chore:` 构建/工具

## 任务看板

查看 [docs/task-board.md](docs/task-board.md) 了解当前任务状态。

## 技术文档

- [技术方案说明书](docs/技术方案说明书.md)

## 开发团队

- **Frontend**: frontend-dev
- **Backend**: backend-dev
- **Testing**: tester
- **Experiment**: experimenter

## License

MIT
