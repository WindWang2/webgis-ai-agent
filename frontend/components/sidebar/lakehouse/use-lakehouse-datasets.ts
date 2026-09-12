'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  lakehouseApi,
  type CatalogOwnerType,
  type DatasetDetail,
  type DatasetVersion,
  type LakehouseDataset,
} from '@/lib/api/lakehouse';

/**
 * Dataset 版本层（V8）数据生命周期：清单 / 详情（refs+head+descriptor）/
 * 版本历史。竞态配方同 use-lakehouse-catalog（AbortController + 序号守卫）。
 */
export function useLakehouseDatasets(options: {
  ownerType: CatalogOwnerType;
  sessionId: string;
  ownerToken?: string | null;
}) {
  const { ownerType, sessionId, ownerToken } = options;
  const [datasets, setDatasets] = useState<LakehouseDataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  const refresh = useCallback(async () => {
    if (ownerType !== 'session' || !sessionId) {
      // dataset REST 面只有 session 域 —— project 域数据集暂不可列举。
      setDatasets([]);
      setError(null);
      return;
    }
    const seq = ++reqRef.current.seq;
    reqRef.current.controller?.abort();
    const controller = new AbortController();
    reqRef.current.controller = controller;
    setLoading(true);
    setError(null);
    try {
      const res = await lakehouseApi.listDatasets(sessionId, 200, {
        ownerToken,
        signal: controller.signal,
      });
      if (seq !== reqRef.current.seq) return;
      setDatasets(res.datasets ?? []);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      if (seq !== reqRef.current.seq) return;
      setError(e instanceof Error ? e.message : '获取数据集清单失败');
    } finally {
      if (seq === reqRef.current.seq) setLoading(false);
    }
  }, [ownerType, sessionId, ownerToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 卸载中止在途请求（ref 是稳定可变数据引用，读 .current 是刻意的）。
  useEffect(() => () => reqRef.current?.controller?.abort(), []);

  return { datasets, loading, error, refresh };
}

/** 单个 dataset 的详情 + 版本历史（选中行驱动；随 datasetId 切换重取）。 */
export function useLakehouseDatasetDetail(options: {
  datasetId: string | null;
  sessionId: string;
  ownerToken?: string | null;
}) {
  const { datasetId, sessionId, ownerToken } = options;
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  const refresh = useCallback(async () => {
    if (!datasetId || !sessionId) {
      setDetail(null);
      setVersions([]);
      return;
    }
    const seq = ++reqRef.current.seq;
    reqRef.current.controller?.abort();
    const controller = new AbortController();
    reqRef.current.controller = controller;
    setLoading(true);
    setError(null);
    try {
      const [d, v] = await Promise.all([
        lakehouseApi.getDataset(datasetId, sessionId, { ownerToken, signal: controller.signal }),
        lakehouseApi.listDatasetVersions(datasetId, sessionId, undefined, 200, {
          ownerToken,
          signal: controller.signal,
        }),
      ]);
      if (seq !== reqRef.current.seq) return;
      setDetail(d);
      setVersions(v.versions ?? []);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      if (seq !== reqRef.current.seq) return;
      setError(e instanceof Error ? e.message : '获取数据集详情失败');
    } finally {
      if (seq === reqRef.current.seq) setLoading(false);
    }
  }, [datasetId, sessionId, ownerToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 卸载中止在途请求（ref 是稳定可变数据引用，读 .current 是刻意的）。
  useEffect(() => () => reqRef.current?.controller?.abort(), []);

  return { detail, versions, loading, error, refresh };
}
