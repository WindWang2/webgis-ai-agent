/**
 * Shared vi.hoisted-compatible mock builders for the project assets tests.
 *
 * The repo mocks the API at module boundary (`vi.mock('@/lib/api/…', () => …)`).
 * Because the hooks import many named exports, each test file needs a mock
 * object covering every function symbol — these builders keep that in sync in
 * one place. `vi.hoisted` bodies cannot import, so tests inline-spread these
 * via a local factory mirroring the same key list (kept tiny on purpose).
 */

import { vi } from 'vitest';

/** Every function `lib/hooks/use-project-assets.ts` + panels import from
 * `@/lib/api/project-assets`. */
export const ASSETS_API_KEYS = [
  'attachDataset',
  'auditSpatialQuality',
  'cloneArtifact',
  'deleteWorkspaceSnapshot',
  'detachDataset',
  'executeDataGc',
  'fetchCatalogItemPreview',
  'fetchDataUsage',
  'fetchProjectArtifacts',
  'fetchProjectDatasetPage',
  'inspectWorkspaceSnapshot',
  'listWorkspaceSnapshots',
  'pinArtifact',
  'planDataGc',
  'repairQuality',
  'restoreWorkspaceSnapshot',
  'saveWorkspaceSnapshot',
  'unpinArtifact',
] as const;

/** Keys the hooks import from `@/lib/api/project` (+ the project-tab set so
 * the same factory also works if a test renders ProjectTab). */
export const PROJECT_API_KEYS = [
  'fetchArtifactLineage',
  'fetchProjectDatasets',
  'fetchProjects',
  'fetchProjectWorkflows',
  'fetchWorkflowRuns',
  'fetchWorkflowRun',
  'fetchWorkflowRevisions',
  'fetchRunComparison',
  'compareRuns',
  'runWorkflow',
  'replayWorkflowRun',
  'resumeWorkflowRun',
  'createProject',
  'auditQuality',
  'invalidateProjectRunCaches',
] as const;

export function makeFnRecord(keys: readonly string[]): Record<string, ReturnType<typeof vi.fn>> {
  return Object.fromEntries(keys.map((k) => [k, vi.fn()]));
}
