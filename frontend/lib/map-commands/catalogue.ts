import { layerCommands } from './layerCommands';
import { viewCommands } from './viewCommands';
import { heatmapCommands } from './heatmapCommands';
import { annotationCommands } from './annotationCommands';
import { exportCommands } from './exportCommands';
import { queryCommands } from './queryCommands';
import { chartCommands } from './chartCommands';
import { workbenchCommands } from './workbenchCommands';
import { styleCommands } from './styleCommands';
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
 *
 * H8/#2144：scene 编排四命令（get_3d_capabilities/apply_scene_representation/
 * fly_camera/run_3d_analysis）只在 viewCommands 登记（带参数校验）。此前的
 * sceneCommands 切片以 acceptAnyParams 展开在其后，把校验定义整体覆盖 —
 * — 畸形参数照 ack succeeded，校验器成死代码（#535 幽灵命令不变量仍成立：
 * 四命令的 catalogue 覆盖来自 viewCommands）。
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
  ...mapOperationsCommands,
  ...temporalCommands,
} as const;

export type MapCommandName = keyof typeof COMMAND_CATALOGUE;
