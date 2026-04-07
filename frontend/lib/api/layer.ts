import type { Layer, SortOption } from "@/lib/types/layer";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

// 图层 API 接口
export const layerApi = {
  // 获取图层列表
  async list(params?: {
    limit?: number;
    offset?: number;
    layer_type?: string;
    is_public?: boolean;
    search?: string;
    sort?: SortOption;
  }) {
    const searchParams = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          searchParams.append(key, String(value));
        }
      });
    }

    const response = await fetch(`${API_BASE}/layers?${searchParams.toString()}`, {
      credentials: "include",
    });

    if (!response.ok) {
      throw new Error(`获取图层列表失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data as {
      total: number;
      limit: number;
      offset: number;
      layers: Layer[];
    };
  },

  // 获取单个图层详情
  async get(layerId: string | number) {
    const response = await fetch(`${API_BASE}/layers/${layerId}`, {
      credentials: "include",
    });

    if (!response.ok) {
      if (response.status === 404) {
        throw new Error("图层不存在");
      }
      if (response.status === 403) {
        throw new Error("无权限访问该图层");
      }
      throw new Error(`获取图层详情失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data as Layer;
  },

  // 创建图层
  async create(layerData: Partial<Layer>) {
    const response = await fetch(`${API_BASE}/layers`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify(layerData),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(error?.message || `创建图层失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data as Layer;
  },

  // 更新图层
  async update(layerId: string | number, layerData: Partial<Layer>) {
    const response = await fetch(`${API_BASE}/layers/${layerId}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify(layerData),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(error?.message || `更新图层失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data as Layer;
  },

  // 删除图层
  async delete(layerId: string | number) {
    const response = await fetch(`${API_BASE}/layers/${layerId}`, {
      method: "DELETE",
      credentials: "include",
    });

    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(error?.message || `删除图层失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data as { success: boolean; message: string };
  },

  // 获取图层元数据
  async getMetadata(layerId: string | number) {
    const response = await fetch(`${API_BASE}/layers/${layerId}/metadata`, {
      credentials: "include",
    });

    if (!response.ok) {
      throw new Error(`获取图层元数据失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data;
  },

  // 获取支持的图层类型
  async getLayerTypes() {
    const response = await fetch(`${API_BASE}/layer-types`, {
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

  // 创建空间分析任务
  async createAnalysisTask(layerId: string | number, taskData: {
    task_type: string;
    parameters: Record<string, any>;
  }) {
    const response = await fetch(`${API_BASE}/layers/${layerId}/tasks`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify(taskData),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(error?.message || `创建分析任务失败: ${response.statusText}`);
    }

    const data = await response.json();
    return data;
  },
};
