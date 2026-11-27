/**
 * The PR smoke selector is also runnable locally/manually, where the GitHub
 * Actions-provided GITHUB_OUTPUT file is absent. It must still write its
 * decision and exit cleanly instead of crashing on an undefined path.
 */
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const TOOL = join(import.meta.dirname, '../../e2e/tools/select-journey-smoke.mjs');

describe('select-journey-smoke CLI outside GitHub Actions', () => {
  it('writes lanes.json and exits cleanly when GITHUB_OUTPUT is absent', () => {
    const cwd = mkdtempSync(join(tmpdir(), 'select-journey-smoke-'));
    try {
      const env: NodeJS.ProcessEnv = { ...process.env, GITHUB_EVENT_NAME: 'workflow_dispatch' };
      delete env.GITHUB_OUTPUT;
      const result = spawnSync(process.execPath, [TOOL], { cwd, env, encoding: 'utf8' });
      expect(result.status, result.stderr).toBe(0);
      expect(JSON.parse(readFileSync(join(cwd, 'lanes.json'), 'utf8'))).toMatchObject({
        run: true,
        reason: 'non-PR event',
      });
    } finally {
      rmSync(cwd, { recursive: true, force: true });
    }
  });
});
