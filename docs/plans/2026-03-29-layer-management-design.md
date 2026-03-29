# 图层管理模块设计方案 (T003)

## 概述
基于 Next.js 14 + TypeScript + Tailwind CSS 的图层管理前端模块，提供完整的图层 CRUD 功能。

## 技术栈
- Framework: Next.js 14 (App Router)
- Language: TypeScript
- Styling: Tailwind CSS v4
- Testing: Vitest + @testing-library/react
- HTTP Client: Axios

## 数据模型

### LayerItem
```typescript
interface LayerItem {
  id: string;
  name: string;
  type: 'vector' | 'raster' | 'tile';
  isPublic: boolean;
  opacity?: number;
  style?: {
    color?: string;
    borderColor?: string;
    fillOpacity?: number;
  };
  bounds?: [number, number, number, number]; // [west, south, east, north]
  createdAt: string;
  updatedAt: string;
}
```

### PageResponse<T>
```typescript
interface PageResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/layers | 图层列表，支持分页、筛选 |
| GET | /api/v1/layers/{id} | 图层详情 |
| PUT | /api/v1/layers/{id} | 更新图层 |
| DELETE | /api/v1/layers/{id} | 删除图层 |
| POST | /api/v1/layer/upload | 上传图层 |

### GET /api/v1/layers Query Parameters
- `page`: 页码，默认1
- `pageSize`: 每页数量默认20
- `type`: 筛选类型 vector|raster|tile
- `isPublic`: 筛选公开状态 true|false
- `sortBy`: 排序字段 name|created_at
- `sortOrder`: 排序方向 asc|desc

## 组件规格

### LayerCard
显示单个图层信息卡片，包含：
- 图层名称、类型Badge、公开状态Badge
- 基础样式预览（颜色/透明度）
- 编辑/删除/预览按钮

### LayerFilters
- 类型筛选下拉菜单(vector/raster/tile/All)
- 公开状态复选框

### SortControls
- 排序字段选择(name/created_at)
- 升序/降序切换按钮

### LayerList
综合列表组件，整合LayerCard/LayerFilters/SortControls

### LayerForm
新建/编辑表单包含：
- 名称输入
- 类型选择
- 公开状态开关
- 透明度滑块
- 样式配置(颜色、边框色、填充透明度)

### UploadModal
- 拖拽上传区域
- 文件类型提示
- 上传进度显示
- 完成回调

### LayerPreview
- 基本信息展示
- 简易地图渲染(边界框)

## 页面路由
- `/layers` - 图层管理主页