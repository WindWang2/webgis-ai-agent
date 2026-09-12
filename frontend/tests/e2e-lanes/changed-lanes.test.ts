/**
 * Changed-lane mapping correctness tests (#1216-class regression guard).
 *
 * #1216: a mapping whose keys can never match their own directory
 * (`"/".join(parts[:3])` vs two-segment keys) silently disables the lane.
 * Every key in e2e/tools/changed-lanes.mjs is asserted here against the exact
 * files it exists to catch — a key that stops matching turns these red.
 */
import { describe, expect, it } from 'vitest';
import { mapChangedFiles, SMOKE_JOURNEYS } from '../../e2e/tools/changed-lanes.mjs';

const smokeDecision = (files: string[]) => {
  const d = mapChangedFiles(files);
  expect(d.run, `expected smoke for ${JSON.stringify(files)}: ${d.reason}`).toBe(true);
  expect(d.grep).toBe('@smoke');
  expect(d.journeys).toEqual(SMOKE_JOURNEYS);
  return d;
};

describe('changed-lane mapping (quality-e2e lane)', () => {
  it('maps a journey file change to the smoke set', () => {
    smokeDecision(['frontend/e2e/journeys/j5-theme-persistence.journey.ts']);
  });

  it('maps frontend/e2e helpers and fixtures to the smoke set', () => {
    // #1216 regression shape: the key is a directory prefix — a file two and
    // three levels under it must match (a join-truncation bug would not).
    smokeDecision(['frontend/e2e/helpers/bootstrap.ts']);
    smokeDecision(['frontend/e2e/fixtures/api-stubs.ts']);
    smokeDecision(['frontend/e2e/tools/changed-lanes.mjs']);
  });

  it('maps shell surfaces the smoke journeys drive', () => {
    smokeDecision(['frontend/components/sidebar/chat-tab.tsx']);
    smokeDecision(['frontend/app/layout.tsx']);
    smokeDecision(['frontend/lib/api/transport.ts']);
    smokeDecision(['frontend/components/map/map-panel.tsx']);
  });

  it('maps the playwright config itself', () => {
    smokeDecision(['frontend/playwright.config.ts']);
  });

  it('a mixed diff with one mapped file still runs', () => {
    smokeDecision(['README.md', 'frontend/lib/store/useHudStore.ts', 'docs/x.md']);
  });

  it('does not run for docs-only diffs', () => {
    const d = mapChangedFiles(['docs/dev/quality-e2e-recon.md', 'WAYFINDER_MAP.md']);
    expect(d.run).toBe(false);
  });

  it('does not run for backend-only diffs (other lanes cover them)', () => {
    const d = mapChangedFiles([
      'app/services/workflow_runtime/driver.py',
      'tests/unit/workflow_runtime/test_x.py',
      'scripts/quality_runner.py',
      'perf/budgets.json',
    ]);
    expect(d.run).toBe(false);
  });

  it('does not run for other workflows', () => {
    const d = mapChangedFiles(['.github/workflows/production.yml']);
    expect(d.run).toBe(false);
  });

  it('quality-e2e workflow changes DO run the smoke gate', () => {
    const d = mapChangedFiles(['.github/workflows/quality-e2e.yml']);
    // The workflow is the lane's own asset: keep it self-triggering so a
    // broken lane config is caught by the lane itself.
    expect(d.run).toBe(true);
  });

  it('empty diff never runs', () => {
    expect(mapChangedFiles([]).run).toBe(false);
  });

  it('registry smoke entries match the mapping output journeys', async () => {
    const { JOURNEYS } = await import('../../e2e/helpers/registry');
    const smokeIds = JOURNEYS.filter((j) => j.smoke).map((j) => j.file);
    // mapping journeys are file stems; registry entries are file paths —
    // every mapped journey must exist in the registry as a smoke journey.
    for (const stem of SMOKE_JOURNEYS) {
      expect(smokeIds.some((f) => f.includes(stem))).toBe(true);
    }
  });
});
