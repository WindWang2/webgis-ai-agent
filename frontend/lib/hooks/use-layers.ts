"use client";
import { useState, useEffect, useCallback } from "react";
import { layerApi } from "@/lib/api/layer";
import type { Layer } from "@/lib/types/layer";

export function useLayers() {
  const [layers, setLayers] = useState<Layer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 加载图层列表
  const loadLayers = useCallback(async (params?: Parameters<typeof layerApi.list>[0]) => {
    try {
      setLoading(true);
      setError(null);
      const response = await layerApi.list(params);
      setLayers(response.layers);
      return response;
    } catch (err) {
      const message = err instanceof Error ? err.message : "加载图层失败";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  // 添加图层
  const addLayer = useCallback(async (layerData: Partial<Layer>) => {
    try {
      setLoading(true);
      setError(null);
      const newLayer = await layerApi.create(layerData);
      setLayers(prev => [newLayer, ...prev]);
      return newLayer;
    } catch (err) {
      const message = err instanceof Error ? err.message : "创建图层失败";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  // 更新图层
  const updateLayer = useCallback(async (layerId: string | number, layerData: Partial<Layer>) => {
    try {
      setLoading(true);
      setError(null);
      const updatedLayer = await layerApi.update(layerId, layerData);
      setLayers(prev => prev.map(layer => 
        layer.id === String(layerId) ? { ...layer, ...updatedLayer } : layer
      ));
      return updatedLayer;
    } catch (err) {
      const message = err instanceof Error ? err.message : "更新图层失败";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  // 删除图层
  const deleteLayer = useCallback(async (layerId: string | number) => {
    try {
      setLoading(true);
      setError(null);
      await layerApi.delete(layerId);
      setLayers(prev => prev.filter(layer => layer.id !== String(layerId)));
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : "删除图层失败";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  // 切换图层显隐
  const toggleLayerVisibility = useCallback((layerId: string) => {
    setLayers(prev => prev.map(layer => 
      layer.id === layerId ? { ...layer, visible: !layer.visible } : layer
    ));
  }, []);

  // 调整图层透明度
  const setLayerOpacity = useCallback((layerId: string, opacity: number) => {
    setLayers(prev => prev.map(layer => 
      layer.id === layerId ? { ...layer, opacity: Math.max(0, Math.min(1, opacity)) } : layer
    ));
  }, []);

  // 移动图层顺序
  const moveLayer = useCallback((fromIndex: number, toIndex: number) => {
    setLayers(prev => {
      const newLayers = [...prev];
      const [movedLayer] = newLayers.splice(fromIndex, 1);
      newLayers.splice(toIndex, 0, movedLayer);
      return newLayers;
    });
  }, []);

  // 重置错误
  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
    // 状态
    layers,
    loading,
    error,
    
    // 方法
    loadLayers,
    addLayer,
    updateLayer,
    deleteLayer,
    toggleLayerVisibility,
    setLayerOpacity,
    moveLayer,
    clearError,
  };
}
