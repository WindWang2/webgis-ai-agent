import { devOnly } from '@/lib/utils/logger';

/**
 * Helper to parse filters that might come in as JSON strings from AI commands.
 *
 * Extracted verbatim from map-action-handler.tsx (the APPLY_LAYER_FILTER case).
 * Used by `apply_layer_filter` in layerCommands.ts.
 */
export function parseFilter(filter: any): any[] | null {
  if (!filter) return null;
  if (typeof filter === 'string') {
    try {
      return JSON.parse(filter);
    } catch {
      devOnly.warn('[MapActionHandler] Failed to parse filter string:', filter);
      return null;
    }
  }
  return filter;
}
