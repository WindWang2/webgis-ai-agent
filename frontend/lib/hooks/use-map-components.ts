'use client';
/**
 * useMapComponents — subscribe to the committed MapSpec's component instances
 * via the shared resolution layer (Workspace V2 / Goal C4).
 *
 * Subscribes to the MapSpec live generation (session-cursor, single map
 * truth) and the component override store (optimistic placements); resolves
 * with `resolveMapComponents` — the same projection chrome and export use.
 */
import { useMemo, useSyncExternalStore } from 'react';
import {
  getCommittedMapSpec,
  getMapSpecLiveGeneration,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import {
  getComponentOverridesGeneration,
  subscribeComponentOverrides,
} from '@/lib/mapspec/component-mutation';
import { resolveMapComponents } from '@/lib/map-components/resolve-components';

export function useMapComponents() {
  useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);
  useSyncExternalStore(subscribeComponentOverrides, getComponentOverridesGeneration);

  return useMemo(() => resolveMapComponents(getCommittedMapSpec()), []);
}
