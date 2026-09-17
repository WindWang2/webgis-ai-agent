/**
 * Agent Ops Cockpit — Oracle 级行为测试。
 *
 * 覆盖任务书完成清单的关键验收面：
 * - kill-switch 关停 → 诚实空态（不发任何 projection 请求）；
 * - mission 选择 → detail 投影渲染（状态/目标/blocked reason 来自服务端）；
 * - operator 动作：两步确认 + 409 无乐观写（视图保持服务端态）；
 * - 1000+ 合成 events 窗口化渲染（虚拟化 Oracle）；
 * - session 切换：旧 session 投影不残留。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { AgentOpsCockpit } from './agent-ops-cockpit';
import { ApiError } from '@/lib/api/transport';
import * as cockpitApi from '@/lib/api/cockpit';

/** 与 use-cluster-poll.test.ts 同款：act 内推进 fake timers，状态确定刷新。 */
async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

vi.mock('@/lib/api/cockpit', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/cockpit')>();
  return {
    ...actual,
    getCockpitHealth: vi.fn(),
    listCockpitMissions: vi.fn(),
    getCockpitMission: vi.fn(),
    getCockpitTimeline: vi.fn(),
    getCockpitSwarm: vi.fn(),
    getCockpitSessionSkill: vi.fn(),
    getCockpitSessionEvidence: vi.fn(),
    getCockpitSessionTrace: vi.fn(),
    suspendCockpitMission: vi.fn(),
    resumeCockpitMission: vi.fn(),
    cancelCockpitMission: vi.fn(),
  };
});

const mocked = vi.mocked(cockpitApi);

function envelope<T extends object>(extra: T) {
  return { schema: 'cockpit.v1', generated_at: 1000, ...extra };
}

const missionSummary = {
  mission_id: 'm-1', org_id: 'org-a', state: 'running', revision: 7,
  goal_revision: 2, root_goal: '成都小学可达性分析', project_id: null,
  created_at: 1, updated_at: 2, lease_owner: 'w1', blocked_reason: '',
  frontier_counts: { completed: 3, running: 1, pending: 4, failed: 1, blocked: 0 },
  resource: { quota: { tokens: 100 }, consumed: { tokens: 40 }, reserved: { tokens: 5 } },
};

const missionDetail = envelope({
  mission: {
    schema_version: 'mission.v1', mission_id: 'm-1', org_id: 'org-a',
    user_id: 'u1', project_id: null, root_goal: '成都小学可达性分析',
    goal_revision: 2, state: 'running', revision: 7,
    created_at: 1, updated_at: 2,
    refs: { active_session_ids: ['sess-1'], artifact_refs: ['artifact:map/1'] },
    frontier: { completed: ['t1'], running: ['t2'], pending: [], failed: ['t9'], blocked: [] },
    resource_budget: { quota: { tokens: 100 }, consumed: { tokens: 40 }, reserved: { tokens: 5 }, retry_cost: {} },
    failure: { error_code: '', detail: '', recovery_class: '', at: 0 },
    recovery: { attempt: 1, last_checkpoint_id: 'cp-1', last_checkpoint_at: 9, last_recovery_at: 0, blocked_reason: '', unresolved_ops: [] },
    lease_owner: 'w1', lease_epoch: 3, lease_expires_at: 99,
  },
  diagnostics: {
    mission_id: 'm-1', goal: '成都小学可达性分析', state: 'running', revision: 7,
    goal_revision: 2, current_frontier: {}, blocked_reason: '', artifact_count: 1,
    swarm_status: 'running', resource_use: { tokens: 40 }, last_checkpoint: 'cp-1',
    lease_owner: 'w1', lease_epoch: 3, recovery_count: 1,
  },
});

function stubPoll<T>(fn: ReturnType<typeof vi.fn>, value: T) {
  fn.mockImplementation(() => Promise.resolve(value));
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
  stubPoll(mocked.getCockpitHealth, Promise.resolve({ enabled: true, schema: 'cockpit.v1' }));
  stubPoll(mocked.listCockpitMissions, Promise.resolve(envelope({
    missions: [missionSummary], has_active: true, poll_after_ms: 3000,
  })));
  stubPoll(mocked.getCockpitMission, Promise.resolve(missionDetail));
  stubPoll(mocked.getCockpitTimeline, Promise.resolve(envelope({
    mission_id: 'm-1', state: 'running', revision: 7, goal_revision: 2,
    root_goal: 'g', updated_at: 2, blocked_reason: '',
    refs: { active_session_ids: ['sess-1'] },
    checkpoints: [
      { checkpoint_id: 'cp-1', mission_revision: 7, goal_revision: 2, state: 'running', created_at: 9 },
    ],
  })));
  stubPoll(mocked.getCockpitSwarm, Promise.resolve(envelope({
    mission_id: 'm-1',
    runs: [{
      swarm_run_id: 'sr-1', mission_id: 'm-1', goal_slice: 'slice-1', state: 'running',
      created_at: 1, updated_at: 2,
      tasks: {
        't1': { task_id: 't1', assignment_id: 'a1', state: 'SUCCEEDED', operation_class: 'pure', produced_refs: ['artifact:map/1'], summary: '完成', error_code: '', attempt: 1, idempotency_key: '', settled_at: 2 },
        't2': { task_id: 't2', assignment_id: 'a2', state: 'RUNNING', operation_class: 'idempotent', produced_refs: [], summary: '', error_code: '', attempt: 1, idempotency_key: '', settled_at: 0 },
        't9': { task_id: 't9', assignment_id: 'a9', state: 'FAILED', operation_class: 'compensatable', produced_refs: [], summary: '超时', error_code: 'TIMEOUT', attempt: 2, idempotency_key: '', settled_at: 3 },
      },
    }],
  })));
  stubPoll(mocked.getCockpitSessionSkill, Promise.resolve(envelope({
    session_id: 'sess-1', present: true, mission_id: 'm-1',
    guidance: {
      decision: { selected_skill: 'density-analysis', confidence: 0.9 },
      projection: { guides_planning: true }, shadow: null, guides_planning: true,
      pi_context: { skill: 'density-analysis' },
    },
  })));
  stubPoll(mocked.getCockpitSessionEvidence, Promise.resolve(envelope({
    session_id: 'sess-1', present: true, truncated: false,
    claims: [{
      claim_id: 'c1', claim_type: 'density', subject: '成都市', predicate: '', value: 12.5,
      value_text: '', unit: '个/km²', comparator: '', method: '',
      supporting_evidence_refs: ['e1'], contradicting_evidence_refs: [],
      confidence: 0.9, status: 'supported', narrative: '',
    }],
    edges: [{ edge_id: 'ed1', relation: 'supports', src: 'c1', dst: 'e1' }],
    stats: { claims: 1, edges: 1 },
  })));
  stubPoll(mocked.getCockpitSessionTrace, Promise.resolve(envelope({
    session_id: 'sess-1',
    events: [{ ts: 1.5, stage: 'observation', detail: { layer: 'roads' } }],
    summary: { by_stage: { observation: 1 } },
    counters: { observation_rejects: 0 },
  })));
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('AgentOpsCockpit — Oracle 行为', () => {
  it('kill-switch 关停 → 诚实空态，且不发任何 projection 请求', async () => {
    mocked.getCockpitHealth.mockResolvedValue({ enabled: false, schema: 'cockpit.v1' });
    render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    expect(screen.getByTestId('cockpit-disabled')).toBeInTheDocument();
    expect(mocked.listCockpitMissions).not.toHaveBeenCalled();
    expect(mocked.getCockpitSessionSkill).not.toHaveBeenCalled();
  });

  it('mission 列表 → 选择 → detail 投影渲染（状态来自服务端）', async () => {
    render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    expect(screen.getByText('成都小学可达性分析')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('cockpit-mission-m-1'));
    await flush(0);
    // detail 投影字段直接可见：状态、revision、frontier 计数、租约
    expect(screen.getByTestId('cockpit-mission-state')).toHaveTextContent('running');
    expect(screen.getByTestId('cockpit-mission-revision')).toHaveTextContent('7');
    expect(screen.getByText(/w1/)).toBeInTheDocument();
  });

  it('suspend 两步确认 → POST；409 冲突 → 显示冲突且视图不变（无乐观写）', async () => {
    render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    fireEvent.click(screen.getByTestId('cockpit-mission-m-1'));
    await flush(0);

    const suspendBtn = screen.getByTestId('cockpit-action-suspend');
    fireEvent.click(suspendBtn); // 第一步：武装确认
    expect(screen.getByTestId('cockpit-action-suspend-confirm')).toBeInTheDocument();
    // 服务端 409（真实 ApiError，走 runOp 的实例分支而非鸭子类型）
    mocked.suspendCockpitMission.mockRejectedValueOnce(
      new ApiError(409, 'Conflict', 'FencingError: LEASE_ACQUIRE_FAILED'),
    );
    fireEvent.click(screen.getByTestId('cockpit-action-suspend-confirm'));
    await flush(0);
    expect(mocked.suspendCockpitMission).toHaveBeenCalledWith('m-1', expect.objectContaining({ ownerToken: 'tok' }));
    // 无乐观写：mission state 仍显示服务端最后一次投影的 running
    expect(screen.getByTestId('cockpit-mission-state')).toHaveTextContent('running');
    expect(screen.getByTestId('cockpit-action-error')).toHaveTextContent(/409|租约|lease/i);
  });

  it('resume 软拒绝（200 + ok:false, LEASE_HELD）→ 显示冲突，绝不显示成功', async () => {
    render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    fireEvent.click(screen.getByTestId('cockpit-mission-m-1'));
    await flush(0);

    // running 状态镜像允许 resume；服务端恢复协调器软拒绝（不抛异常）
    mocked.resumeCockpitMission.mockResolvedValueOnce({ ok: false, reason: 'LEASE_HELD' });
    fireEvent.click(screen.getByTestId('cockpit-action-resume'));
    fireEvent.click(screen.getByTestId('cockpit-action-resume-confirm'));
    await flush(0);
    expect(mocked.resumeCockpitMission).toHaveBeenCalledWith('m-1', expect.anything());
    expect(screen.getByTestId('cockpit-action-error')).toHaveTextContent(/409|租约|lease/i);
    // 视图保持服务端最后投影的 running，无乐观写
    expect(screen.getByTestId('cockpit-mission-state')).toHaveTextContent('running');
  });

  it('resume 软拒绝（非租约原因）→ 显示原始 reason', async () => {
    render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    fireEvent.click(screen.getByTestId('cockpit-mission-m-1'));
    await flush(0);

    mocked.resumeCockpitMission.mockResolvedValueOnce({ ok: false, reason: 'NO_CHECKPOINT' });
    fireEvent.click(screen.getByTestId('cockpit-action-resume'));
    fireEvent.click(screen.getByTestId('cockpit-action-resume-confirm'));
    await flush(0);
    expect(screen.getByTestId('cockpit-action-error')).toHaveTextContent('NO_CHECKPOINT');
  });

  it('session 视图（skill/evidence/trace）挂在活跃 session 上', async () => {
    render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    fireEvent.click(screen.getByTestId('cockpit-view-skill'));
    await flush(0);
    expect(mocked.getCockpitSessionSkill).toHaveBeenCalledWith('sess-1', expect.anything());
    expect(screen.getByText('density-analysis')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('cockpit-view-evidence'));
    await flush(0);
    expect(screen.getByText('成都市')).toBeInTheDocument();
    expect(screen.getByText(/个\/km²/)).toBeInTheDocument();
  });

  it('session 切换 → 旧 session 投影不残留（skill 视图重新拉取）', async () => {
    const { rerender } = render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    fireEvent.click(screen.getByTestId('cockpit-view-skill'));
    await flush(0);
    expect(mocked.getCockpitSessionSkill).toHaveBeenCalledWith('sess-1', expect.anything());
    expect(screen.getByText('density-analysis')).toBeInTheDocument();

    mocked.getCockpitSessionSkill.mockResolvedValue(envelope({
      session_id: 'sess-2', present: false, mission_id: '', guidance: null,
    }));
    rerender(<AgentOpsCockpit sessionId="sess-2" ownerToken="tok" />);
    await flush(0);
    expect(mocked.getCockpitSessionSkill).toHaveBeenCalledWith('sess-2', expect.anything());
    // 旧 session 的技能选择不再显示
    expect(screen.queryByText('density-analysis')).not.toBeInTheDocument();
  });

  it('1000+ 合成 events → 窗口化渲染（挂载行数有界）', async () => {
    const manyEvents = Array.from({ length: 1200 }, (_, i) => ({
      ts: i, stage: i % 2 ? 'observation' : 'action_intent',
      detail: { seq: String(i) },
    }));
    stubPoll(mocked.getCockpitSessionTrace, Promise.resolve(envelope({
      session_id: 'sess-1', events: manyEvents, summary: {}, counters: {},
    })));
    render(<AgentOpsCockpit sessionId="sess-1" ownerToken="tok" />);
    await flush(0);
    fireEvent.click(screen.getByTestId('cockpit-view-trace'));
    await flush(0);
    const virtual = screen.getByTestId('cockpit-trace-virtual');
    // 窗口化：实际挂载的行数远小于总量（视口 + overscan）
    const mounted = within(virtual).queryAllByTestId('cockpit-trace-row');
    expect(mounted.length).toBeGreaterThan(0);
    expect(mounted.length).toBeLessThan(200);
    expect(screen.getByText(/1200/)).toBeInTheDocument(); // 总量可见（诚实计数）
  });
});
