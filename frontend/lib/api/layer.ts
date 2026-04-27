import { API_BASE } from './config';

export const layerApi = {
  async getMetadata(layerId: string | number) {
    const response = await fetch(`${API_BASE}/api/v1/layers/${layerId}/metadata`, {
      credentials: "include",
    });

    if (!response.ok) {
      throw new Error(`获取图层元数据失败: ${response.statusText}`);
    }

    return await response.json();
  },

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

  async createAnalysisTask(layerId: string | number, taskData: {
    task_type: string;
    parameters: Record<string, unknown>;
  }) {
    const response = await fetch(`${API_BASE}/api/v1/layers/${layerId}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(taskData),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(error?.message || `创建分析任务失败: ${response.statusText}`);
    }

    return await response.json();
  },
};
