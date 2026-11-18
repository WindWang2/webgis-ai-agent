import { expect, it } from 'vitest';
import { scanMapCommandEmissions } from './helpers/scan-map-command-emissions';
it('recognizes real dictionary emissions and indexed writes, including unknown map commands', () => {
  expect(scanMapCommandEmissions(`{"command": "FLY_TO"}
result['command'] = 'future_map_command'`))
    .toEqual(['fly_to', 'future_map_command']);
});
it('does not treat a Pi control local variable or a different key as a map command', () => {
  expect(scanMapCommandEmissions(`command = "steer" if adjust else "follow_up"
some_command = "other"
{"rpc_command": "steer"}`)).toEqual([]);
});
