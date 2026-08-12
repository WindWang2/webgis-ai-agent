'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { dataFabricApi, type CatalogItem } from '@/lib/api/data-fabric';
import { useToastStore } from '@/components/ui/toast';

// A-F-08: search-as-you-type debounce window — rapid keystrokes collapse into a
// single catalog fetch once the user pauses typing. Re-exported from
// data-sources-tab so the existing test's `import { CATALOG_SEARCH_DEBOUNCE_MS }
// from './data-sources-tab'` keeps working.
export const CATALOG_SEARCH_DEBOUNCE_MS = 300;

/**
 * Spatial catalog lifecycle (search debounce + fetch + abort/sequence guard).
 *
 * Extracted verbatim from data-sources-tab — EXACT timing/race semantics are
 * preserved:
 *  - raw input state (`searchQuery`) updates immediately;
 *  - a 300ms quiet-period effect promotes it to `debouncedSearchQuery`;
 *  - each fetch aborts the previous in-flight controller and bumps a sequence
 *    number, so a slow/stale response can never clobber newer results;
 *  - the latest controller is aborted on unmount.
 */
export function useSpatialCatalog() {
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [catalogTotal, setCatalogTotal] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  // A-F-08: the value actually sent to the catalog endpoint; follows searchQuery
  // after a quiet period (debounce), so typing never fires a fetch per keystroke.
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [selectedSourceFilter, setSelectedSourceFilter] = useState('');

  const addToast = useToastStore((s) => s.addToast);

  // A-F-08: each catalog fetch aborts the previous in-flight one and carries a
  // sequence number, so a slow/stale response can never clobber newer results.
  const catalogReqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  const fetchCatalog = useCallback(async () => {
    const seq = ++catalogReqRef.current.seq;
    catalogReqRef.current.controller?.abort();
    const controller = new AbortController();
    catalogReqRef.current.controller = controller;
    setLoadingCatalog(true);
    try {
      const res = await dataFabricApi.listSpatialCatalog({
        q: debouncedSearchQuery,
        source_id: selectedSourceFilter || undefined,
        limit: 50,
        signal: controller.signal,
      });
      if (seq !== catalogReqRef.current.seq) return; // superseded by a newer query
      setCatalogItems(res.items || []);
      setCatalogTotal(res.total || 0);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return; // superseded / unmount
      addToast(e instanceof Error ? e.message : '获取空间目录失败', 'error');
    } finally {
      if (seq === catalogReqRef.current.seq) setLoadingCatalog(false);
    }
  }, [debouncedSearchQuery, selectedSourceFilter, addToast]);

  // A-F-08: debounce search-as-you-type (the raw input stays immediate).
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearchQuery(searchQuery), CATALOG_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchQuery]);

  // A-F-08: cancel any in-flight catalog fetch on unmount. The ref is a stable
  // mutable data ref (not a rendered node), so reading .current in cleanup is
  // intentional — it must abort the *latest* controller, not the mount-time one.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => catalogReqRef.current.controller?.abort();
  }, []);

  useEffect(() => {
    fetchCatalog();
  }, [fetchCatalog]);

  return {
    catalogItems,
    catalogTotal,
    loadingCatalog,
    searchQuery,
    setSearchQuery,
    selectedSourceFilter,
    setSelectedSourceFilter,
    refreshCatalog: () => fetchCatalog(),
  };
}
