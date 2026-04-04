# 创建 Pull Request

## 前置条件
✅ 代码已推送到远程分支 `feature/T005-report-v2`

## 创建步骤

### 方法 1: 通过 GitHub Web 界面（推荐）

1. 访问项目 GitHub 页面：
   ```
   https://github.com/WindWang2/webgis-ai-agent
   ```

2. 点击 "Compare & pull request" 按钮（应该会看到黄色提示条）

3. 或者手动创建：
   - 点击 "Pull requests" tab
   - 点击 "New pull request"
   - 选择 base: `develop` ← compare: `feature/T005-report-v2`
   - 点击 "Create pull request"

4. 填写 PR 信息：
   - **标题**: `feat(T005): 报告生成与预览功能 - 前端实现完成`
   - **描述**: 复制 `.github/PULL_REQUEST_TEMPLATE.md` 的内容
   - **Labels**: `enhancement`, `frontend`
   - **Reviewers**: @WindWang2
   - **Projects**: WebGIS AI Agent

5. 点击 "Create pull request"

### 方法 2: 使用 GitHub CLI（如果已安装 gh）

```bash
cd /home/kevin/projects/webgis-ai-agent

gh pr create \
  --base develop \
  --head feature/T005-report-v2 \
  --title "feat(T005): 报告生成与预览功能 - 前端实现完成" \
  --body-file .github/PULL_REQUEST_TEMPLATE.md \
  --label "enhancement,frontend" \
  --reviewer WindWang2
```

## PR 信息模板

**标题**:
```
feat(T005): 报告生成与预览功能 - 前端实现完成
```

**简短描述**:
```
完成 T005 任务的前端部分，实现报告生成、预览、下载和分享功能的完整用户界面。

主要变更：
- 新增报告 API 客户端和类型定义
- 新增报告生成和预览 React 组件
- 集成到结果面板
- 修复 Next.js 配置问题
- 添加单元测试

后端功能已在之前的提交中完成。
```

## 合并前检查

- [ ] 代码审查通过
- [ ] CI/CD 测试通过
- [ ] 冲突已解决
- [ ] 所有讨论已解决
- [ ] 至少 1 个 approval

## 合并后操作

1. 删除 feature 分支（可选）
2. 更新任务看板状态
3. 通知相关人员
4. 部署到测试环境验证

## 联系方式

如有问题，请联系：
- 开发者: AI Coder (subagent)
- 项目负责人: @WindWang2
