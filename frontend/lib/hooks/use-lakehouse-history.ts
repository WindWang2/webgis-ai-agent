'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * Lakehouse 查询历史 + 收藏（本地持久化）。
 *
 * localStorage 直存（非 zustand persist）：查询历史是「工作痕迹」而非
 * 布局态 —— 不进 PERSIST_KEY 的 partialize 集合（该集合演进受持久化
 * 兼容性约束），独立 key 便于整段清理与容量控制（有界 50 条）。
 */
export const LAKEHOUSE_HISTORY_KEY = 'geoagent-lakehouse-history';
export const LAKEHOUSE_FAVORITES_KEY = 'geoagent-lakehouse-favorites';
const MAX_HISTORY = 50;
const MAX_FAVORITES = 30;

export interface LakehouseQueryRecord {
  id: string;
  /** 'window' | 'labeled' 窗口读；'scan' 矢量扫描；'rs'/'revise' 构建动作。 */
  kind: 'window' | 'labeled' | 'scan' | 'rs' | 'revise';
  /** 请求摘要（人类可读，列表展示用）。 */
  label: string;
  /** 请求体（重放用 —— 与 lib/api/lakehouse.ts 请求类型对齐的 JSON）。 */
  request: Record<string, unknown>;
  createdAt: number;
  ref: string;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    if (typeof window === 'undefined') return fallback;
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as T;
    return Array.isArray(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 5MB 配额耗尽 / 隐私模式：历史缓存可丢弃，不打扰用户。
  }
}

function loadHistory(): LakehouseQueryRecord[] {
  return readJson<LakehouseQueryRecord[]>(LAKEHOUSE_HISTORY_KEY, []);
}
function loadFavorites(): LakehouseQueryRecord[] {
  return readJson<LakehouseQueryRecord[]>(LAKEHOUSE_FAVORITES_KEY, []);
}

export function useLakehouseHistory() {
  // 挂载后读 localStorage（SSR 安全：首次渲染与服务端一致，随后水合）。
  const [history, setHistory] = useState<LakehouseQueryRecord[]>([]);
  const [favorites, setFavorites] = useState<LakehouseQueryRecord[]>([]);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setHistory(loadHistory());
    setFavorites(loadFavorites());
    setHydrated(true);
  }, []);

  const record = useCallback((entry: Omit<LakehouseQueryRecord, 'id' | 'createdAt'>) => {
    setHistory((prev) => {
      const next = [
        { ...entry, id: `q_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`, createdAt: Date.now() },
        ...prev.filter((r) => !(r.kind === entry.kind && r.ref === entry.ref && r.label === entry.label)),
      ].slice(0, MAX_HISTORY);
      writeJson(LAKEHOUSE_HISTORY_KEY, next);
      return next;
    });
  }, []);

  const clearHistory = useCallback(() => {
    setHistory([]);
    writeJson(LAKEHOUSE_HISTORY_KEY, []);
  }, []);

  const toggleFavorite = useCallback((rec: LakehouseQueryRecord) => {
    setFavorites((prev) => {
      const exists = prev.some((f) => f.id === rec.id);
      const next = exists
        ? prev.filter((f) => f.id !== rec.id)
        : [...prev, rec].slice(0, MAX_FAVORITES);
      writeJson(LAKEHOUSE_FAVORITES_KEY, next);
      return next;
    });
  }, []);

  const removeFavorite = useCallback((id: string) => {
    setFavorites((prev) => {
      const next = prev.filter((f) => f.id !== id);
      writeJson(LAKEHOUSE_FAVORITES_KEY, next);
      return next;
    });
  }, []);

  /** 收藏判定（历史列表星标渲染用）。 */
  const isFavorite = useCallback(
    (rec: LakehouseQueryRecord) => favorites.some((f) => f.id === rec.id),
    [favorites],
  );

  return { history, favorites, hydrated, record, clearHistory, toggleFavorite, removeFavorite, isFavorite };
}
