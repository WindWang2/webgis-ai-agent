import { API_BASE } from './config';

/**
 * Layer API。
 *
 * 审计契约断裂（D-4）：getMetadata / createAnalysisTask 调用的路由在后端
 * 已被显式移除（layer.py 头部注释："图层 CRUD 已移除 — Agent 通过工具链
 * 自动创建和管理图层"）。前端这两个方法返回 404 但前端无任何 caller，
 * 属纯死代码 — 删除以避免误导未来开发者。
 *
 * 保留 getLayerTypes（GET /layer-types 仍存在）。
 */
export const layerApi = {
  async getLayerTypes() {
    const response = await fetch(`${API_BASE}/api/v1/layer-types`, {
      credentials: "include",
    });

    if (!response.ok) {
      throw new Error(`获取图层类型失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data as {
      layer_types: Array<{ type: string; description: string; formats: string[] }>;
      analysis_types: Array<{ type: string; description: string }>;
    };
  },
};
