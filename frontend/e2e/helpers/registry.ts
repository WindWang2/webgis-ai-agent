/**
 * Journey registry (ADR-0146 journey registration pattern).
 *
 * The canonical index of user journeys this suite protects. Parallel lines
 * (D–J) extend the suite by:
 *   1. adding `frontend/e2e/journeys/jN-<name>.journey.ts` (dual-mode where a
 *      backend can represent the surface, mock-only otherwise),
 *   2. registering it here (id, file, smoke flag, modes, owner line),
 *   3. if the journey should join the PR smoke gate, tagging it `@smoke`.
 *
 * CI consumes the smoke subset via `--grep @smoke`; `tools/changed-lanes.mjs`
 * narrows it further by diff. Keeping the table here (not in CI YAML) means
 * the gate surface is code-reviewed together with the journeys.
 */
export interface JourneyEntry {
  id: string;
  file: string;
  title: string;
  /** Joins the PR smoke gate (mock mode, ≤8 min budget shared with CI lane). */
  smoke: boolean;
  modes: Array<'mock' | 'real'>;
  /** Owning parallel line (this line = quality-e2e-v9). */
  owner: string;
}

export const JOURNEYS: JourneyEntry[] = [
  {
    id: 'j1',
    file: 'journeys/j1-upload-analyze-export.journey.ts',
    title: '上传→分析→出图→导出',
    smoke: true,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j2',
    file: 'journeys/j2-task-cancel-retry.journey.ts',
    title: '任务取消/重试（真取消语义）',
    smoke: false,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j3',
    file: 'journeys/j3-template-apply.journey.ts',
    title: '模板库浏览→应用→画布变化',
    smoke: false,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j4',
    file: 'journeys/j4-storymap-share.journey.ts',
    title: 'StoryMap 播放→分享链接→回放',
    smoke: false,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j5',
    file: 'journeys/j5-theme-persistence.journey.ts',
    title: '暗色主题+accent 切换→刷新不闪白',
    smoke: true,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j6',
    file: 'journeys/j6-fabric-source-query.journey.ts',
    title: '数据源注册→probe→查询→上图（MVT）',
    smoke: false,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
];

export const smokeJourneys = (): JourneyEntry[] => JOURNEYS.filter((j) => j.smoke);
