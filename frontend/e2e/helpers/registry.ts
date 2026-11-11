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
    smoke: true,
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
    title: '数据源准入→probe→查询→上图',
    smoke: false,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j7',
    file: 'journeys/j7-chat-stream-resume.journey.ts',
    title: 'Chat/SSE 断线重连续读 + 流式发送保护',
    smoke: false,
    modes: ['mock', 'real'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j8',
    file: 'journeys/j8-mobile-workflow.journey.ts',
    title: '移动档（≤768px）chat→分析→出图',
    // mock-only：mobile 档布局契约由 mock 档完整覆盖（fixtures 即传输层），
    // real 档无额外断言面 —— modes 是档面声明，不是覆盖率自评。
    smoke: true,
    modes: ['mock'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j9',
    file: 'journeys/j9-scene3d-assets.journey.ts',
    title: '三维场景资产（MapSpec v1.7 → custom layer → 拾取 IR）',
    smoke: true,
    modes: ['mock'],
    owner: 'quality-e2e-v9',
  },
  {
    id: 'j10',
    file: 'journeys/j10-situation-restore.journey.ts',
    title: '情境状态面：服务端投影恢复（刷新/重连）',
    smoke: true,
    modes: ['mock'],
    owner: 'situated-agent-workbench-vnext',
  },
  {
    id: 'j11',
    file: 'journeys/j11-situation-six-states.journey.ts',
    title: '情境状态面六态（范围/选中/结果/执行/验证/可重试）',
    smoke: true,
    modes: ['mock'],
    owner: 'situated-agent-workbench-vnext',
  },
  {
    id: 'j12',
    file: 'journeys/j12-situation-degradation.journey.ts',
    title: '情境面诚实降级（不可用/中性不编造）',
    smoke: false,
    modes: ['mock'],
    owner: 'situated-agent-workbench-vnext',
  },
  {
    id: 'j13',
    file: 'journeys/j13-brief-delivery.journey.ts',
    title: '证据化地图简报：草稿→编辑/证据→预检→冻结→私有交付→分享',
    smoke: true,
    modes: ['mock', 'real'],
    owner: 'goal-g11-evidence-map-briefs',
  },
];

export const smokeJourneys = (): JourneyEntry[] => JOURNEYS.filter((j) => j.smoke);
