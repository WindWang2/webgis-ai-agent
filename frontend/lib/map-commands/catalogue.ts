import { layerCommands } from './layerCommands';
import { viewCommands } from './viewCommands';
import { heatmapCommands } from './heatmapCommands';
import { annotationCommands } from './annotationCommands';
import { exportCommands } from './exportCommands';

export type { MapCommandContext, CommandValidator, CommandEntry } from './types';

/**
 * The single source of truth for the map command vocabulary.
 *
 * Merges every domain slice into one record. Keys are lowercase command names;
 * dispatch (`map-action-handler.tsx`) and the renderer gate
 * (`map-action-renderer.tsx`) both lowercase `action.command` before lookup, so
 * UPPERCASE emissions from the backend are tolerated at runtime without
 * rewriting the `MapActionPayload.command` union.
 */
export const COMMAND_CATALOGUE = {
  ...viewCommands,
  ...layerCommands,
  ...heatmapCommands,
  ...annotationCommands,
  ...exportCommands,
} as const;

export type MapCommandName = keyof typeof COMMAND_CATALOGUE;
