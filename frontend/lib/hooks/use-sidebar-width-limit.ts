'use client';

import { useSyncExternalStore } from 'react';
import { SIDEBAR_MAX_WIDTH_FLOOR, SIDEBAR_MIN_WIDTH, sidebarWidthLimit } from '@/lib/store/slices/uiSlice';

function subscribe(onChange: () => void) {
  window.addEventListener('resize', onChange);
  return () => window.removeEventListener('resize', onChange);
}

const getMax = () => sidebarWidthLimit().max;
const getServerMax = () => SIDEBAR_MAX_WIDTH_FLOOR;

/** Both width controls use the same initial range, then track the actual viewport. */
export function useSidebarWidthLimit() {
  const max = useSyncExternalStore(subscribe, getMax, getServerMax);
  return { min: SIDEBAR_MIN_WIDTH, max };
}
