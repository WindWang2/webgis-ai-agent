import { layerCommands } from './layerCommands';
import { viewCommands } from './viewCommands';
import { heatmapCommands } from './heatmapCommands';
import { annotationCommands } from './annotationCommands';
import { exportCommands } from './exportCommands';
import { queryCommands } from './queryCommands';
import { chartCommands } from './chartCommands';
import { workbenchCommands } from './workbenchCommands';
import { styleCommands } from './styleCommands';
import { sceneCommands } from './sceneCommands';
import { mapOperationsCommands } from './mapOperationsCommands';
import { temporalCommands } from './temporalCommands';

export type { MapCommandContext, CommandValidator, CommandEntry } from './types';

/**
 * The executable map command vocabulary.
 *
 * Key set is locked to the backend-exported manifest
 * (`manifest.generated.ts`, generated from `app/contracts/map_commands.py`):
 * `catalogue-contract.test.ts` asserts exact set equality in both directions
 * (domain slices are `Record<string, CommandEntry>`, so the lock is a
 * runtime contract test rather than a `satisfies` clause).
 * The backend dispatcher only forwards manifest names, so a command that is
 * not registered here can never reach the browser.
 *
 * Merges every domain slice into one record. Keys are lowercase command names;
 * dispatch (`map-action-handler.tsx`) lowercases `action.command` before lookup, so
 * UPPERCASE emissions from the backend are tolerated at runtime without
 * rewriting the `MapActionPayload.command` union.
 */
export const COMMAND_CATALOGUE = {
  ...viewCommands,
  ...layerCommands,
  ...heatmapCommands,
  ...annotationCommands,
  ...exportCommands,
  ...queryCommands,
  ...chartCommands,
  ...workbenchCommands,
  ...styleCommands,
  ...sceneCommands,
  ...mapOperationsCommands,
  ...temporalCommands,
} as const;

export type MapCommandName = keyof typeof COMMAND_CATALOGUE;
