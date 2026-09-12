/**
 * Changed-lane mapping for the journey E2E lane (quality-e2e-v9, ADR-0146).
 *
 * The PR smoke gate runs only the smoke subset, and only when the diff can
 * plausibly affect it. Mapping keys are PREFIX keys matched with
 * `f === key || f.startsWith(key + '/')` — the exact contract that #1216
 * showed must never silently regress to non-matching keys: every key here is
 * covered by frontend/tests/e2e-lanes/changed-lanes.test.ts asserting the
 * intended files actually match (#1216-class bug = key exists but never
 * matches its own directory).
 *
 * CLI (used by .github/workflows/quality-e2e.yml):
 *   git diff --name-only <base>...HEAD | node e2e/tools/changed-lanes.mjs
 * Prints one JSON line: { run, reason, projects, grep, journeys }.
 */

export const SMOKE_JOURNEYS = ['j1-upload-analyze-export', 'j5-theme-persistence'];

/** Ordered by specificity: first match wins (most specific prefixes first). */
const PREFIX_MAP = [
  // The lane's own assets: run the full smoke set (helpers/fixtures changes
  // can affect every journey).
  { key: 'frontend/e2e/', effect: 'smoke' },
  // Shell surfaces the smoke journeys drive.
  { key: 'frontend/components/', effect: 'smoke' },
  { key: 'frontend/app/', effect: 'smoke' },
  // Transport/store/auth contract changes can break journeys via fixtures.
  { key: 'frontend/lib/', effect: 'smoke' },
  // Playwright config itself.
  { key: 'frontend/playwright.config.ts', effect: 'smoke' },
  // The lane's own workflow stays self-triggering (a broken lane config is
  // caught by the lane itself on the next PR).
  { key: '.github/workflows/quality-e2e.yml', effect: 'smoke' },
];

/** Files that must NEVER trigger the lane (pure docs/CI on other lanes). */
const NEUTRAL = [/^docs\//, /^\.github\/workflows\/production\.yml$/, /^\.$/];

/**
 * @param {string[]} files changed file paths (repo-relative, '/' separators)
 * @returns {{run: boolean, reason: string, grep: string, journeys: string[]}}
 */
export function mapChangedFiles(files) {
  const list = (files ?? []).filter(Boolean);
  if (list.length === 0) {
    return { run: false, reason: 'no changed files', grep: '', journeys: [] };
  }
  const neutralOnly = list.every(
    (f) => NEUTRAL.some((re) => { re.lastIndex = 0; return re.test(f); }),
  );
  if (neutralOnly) {
    return { run: false, reason: 'diff touches only neutral paths (docs / other-lane CI)', grep: '', journeys: [] };
  }
  for (const { key, effect } of PREFIX_MAP) {
    const hit = list.some((f) => f === key || f.startsWith(key));
    if (hit) {
      if (effect === 'smoke') {
        return {
          run: true,
          reason: `matched prefix key "${key}"`,
          grep: '@smoke',
          journeys: [...SMOKE_JOURNEYS],
        };
      }
    }
  }
  const backendOnly = list.every((f) => /^(app|migrations|tests|scripts|perf)\//.test(f));
  if (backendOnly) {
    return { run: false, reason: 'backend-only diff (covered by production.yml lanes)', grep: '', journeys: [] };
  }
  return { run: false, reason: 'no mapping key matched', grep: '', journeys: [] };
}

/** CLI: read newline-separated paths on stdin, print decision JSON. */
export async function runCli(inputStream) {
  const chunks = [];
  for await (const chunk of inputStream) chunks.push(chunk);
  const files = Buffer.concat(chunks).toString('utf8').split(/\r?\n/);
  process.stdout.write(`${JSON.stringify(mapChangedFiles(files))}\n`);
}

const isDirectRun = process.argv[1] && import.meta.url === new URL(`file://${process.argv[1].replace(/\\/g, '/')}`).href;
if (isDirectRun) {
  runCli(process.stdin).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
