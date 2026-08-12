'use client';

import { useCallback, useEffect, useState } from 'react';
import { dataFabricApi, type DataSource } from '@/lib/api/data-fabric';
import { useToastStore } from '@/components/ui/toast';

/**
 * Data sources list lifecycle (mount fetch + refresh).
 *
 * Extracted from data-sources-tab: owns the registered sources list and its
 * loading state. All API calls / error-toast shapes are preserved verbatim.
 */
export function useDataSources() {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [loadingSources, setLoadingSources] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const fetchSources = useCallback(async () => {
    setLoadingSources(true);
    try {
      const res = await dataFabricApi.listDataSources();
      setSources(res.sources || []);
    } catch (e) {
      addToast(e instanceof Error ? e.message : '获取数据源列表失败', 'error');
    } finally {
      setLoadingSources(false);
    }
  }, [addToast]);

  useEffect(() => {
    fetchSources();
  }, [fetchSources]);

  return { sources, loadingSources, refreshSources: fetchSources };
}
