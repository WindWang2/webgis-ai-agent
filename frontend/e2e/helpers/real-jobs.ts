/** Runner-local fixture, not a mocked API or a public seeding endpoint. */
import { execFile } from 'node:child_process';
import path from 'node:path';
import { promisify } from 'node:util';

const runFile = promisify(execFile);
const repoRoot = path.resolve(__dirname, '../../..');

export async function runRetryFixture(
  action: 'prepare' | 'verify',
  sessionId: string,
  userId: string,
  jobId?: string,
): Promise<Record<string, unknown>> {
  const { stdout } = await runFile(
    process.env.E2E_PYTHON ?? 'python',
    [
      '-m', 'scripts.seed_journey_retry', action,
      '--session-id', sessionId, '--user-id', userId,
      ...(jobId ? ['--job-id', jobId] : []),
    ],
    { cwd: repoRoot, timeout: 45_000 },
  );
  // Imported GIS libraries may print startup diagnostics; the CLI's final
  // line is its explicit JSON result. A missing/invalid result is a hard fail.
  return JSON.parse(stdout.trim().split('\n').at(-1) ?? '') as Record<string, unknown>;
}
