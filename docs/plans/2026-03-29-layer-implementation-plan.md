# 图层管理模块实施计划 (T003)
---
**创建时间**: 2026-03-29 09:55
**状态**: 进行中
**优先级**: 高
---

## 实施步骤

### Step 1: 修复现有测试 (预计 5min)
- [ ] 1.1 修复 LayerList.mock 问题（Mock 放错位置）
- [ ] 1.2 运行测试验证

### Step 2: 补齐缺失组件 (预计 40min)
- [ ] 2.1 LayerList 组件（含列表、筛选、排序联动）
- [ ] 2.2 LayerForm 组件（新建/编辑表单）
- [ ] 2.3 UploadModal 组件（上传弹窗）
- [ ] 2.4 LayerPreview 组件（图层预览）

### Step 3: 创建页面 (预计 10min)
- [ ] 3.1 /layer 管理页面
- [ ] 3.2 首页添加入口链接

### Step 4: 检查验证 (预计 5min)
- [ ] 4.1 TypeScript 编译检查
- [ ] 4.2 ESLint 检查
- [ ] 4.3 所有测试通过

### Step 5: 提交代码 (预计 5min)
- [ ] 5.1 创建 feature/frontend-layer-management 分支
- [ ] 5.2 提交代码并推送

## 相关文件
- 类型定义: src/types/layer.ts
- API 封装: src/lib/api.ts
- 设计文档: docs/plans/2026-03-29-layer-management-design.md

## 后端接口 (http://localhost:8000)
- GET /api/v1/layers - 图层列表
- GET /api/v1/layers/:id - 图层详情  
- PUT /api/v1/layers/:id - 更新图层
- DELETE /api/v1/layers/:id - 删除图层
- POST /api/v1/layer/upload - 上传图层 (注意：不是 layers 是 layer)