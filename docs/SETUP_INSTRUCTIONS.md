# 仓库初始化说明

## 当前状态

前端项目结构已完成，本地 Git 仓库已初始化：

- **当前分支**: `feature/frontend-scaffold`
- **目标分支**: `develop`
- **远程仓库**: 待创建

## 初始化步骤（需管理员操作）

### 1. 创建 GitHub 仓库

在 https://github.com/new 创建仓库：
- 仓库名：`webgis-ai-agent`
- 组织：`webgis-ai-team`
- 可见性：私有/公开（根据团队需求）
- **不要** 初始化 README、.gitignore 或 license

### 2. 配置远程并推送

```bash
cd /home/kevin/.openclaw/agents/frontend-dev/workspace/projects/webgis-ai-agent

# 添加远程仓库（替换为你的实际仓库 URL）
git remote set-url origin https://github.com/webgis-ai-team/webgis-ai-agent.git

# 创建并推送 develop 分支
git checkout develop
git push -u origin develop

# 推送 feature 分支
git checkout feature/frontend-scaffold
git push -u origin feature/frontend-scaffold
```

### 3. 创建 Pull Request

在 GitHub 上创建 PR：
- 源分支：`feature/frontend-scaffold`
- 目标分支：`develop`
- 标题：`feat: 初始化前端项目结构`

## 本地验证

### 开发环境测试

```bash
# 安装依赖（如果还没安装）
npm install

# 启动开发服务器
npm run dev

# 访问 http://localhost:3000
```

### Docker 测试

```bash
# 开发模式（热重载）
docker-compose up --build

# 生产构建
docker build -t webgis-ai-agent:latest .
docker run -p 3000:3000 webgis-ai-agent:latest
```

## 后续任务

完成仓库初始化后，继续以下任务：

- [ ] T003: 实现自然语言对话界面
- [ ] T004: 集成 MapLibre 地图功能
- [ ] T005: 实现报告生成与预览

## 联系人

如有问题，请联系：frontend@webgis.ai
