/**
 * Goal Repair 类型解析测试 —— 与后端 goalrepair.preview/v1、
 * goalrepair.attempt/v1 契约对齐；非法形状 → null（零漂移）。
 */
import {
  parseGoalRepairAttempt,
  parseGoalRepairPreview,
} from './goal-repair';

const PREVIEW = {
  schema_version: 'goalrepair.preview/v1',
  session_id: 's1',
  goal_id: 'g1',
  verdict: 'partial',
  signal: 'continue',
  plan_version: 'abc123',
  goal_fingerprint: 'fp',
  mapspec_revision: 7,
  observation_seq: 3,
  evidence_signature: 'sig',
  actions: [
    {
      action_id: 'a1',
      action: 'export_product',
      requirement_id: 'export:pdf',
      rule: 'export_receipt_absent',
      side_effect: 'write',
      budget_class: 'repair',
      expected_new_evidence: 'export_receipt@revision==current',
      disposition: 'executable',
      reason: 'r',
      entry: 'webgis_map_export',
      params: { format: 'pdf', expected_revision: 7 },
    },
    {
      action_id: 'a2',
      action: 'run_delivery',
      requirement_id: 'export:gpkg',
      rule: 'delivery_receipt_absent',
      side_effect: 'write',
      budget_class: 'repair',
      expected_new_evidence: 'e',
      disposition: 'needs_acknowledgment',
      reason: 'r',
      entry: 'submit_data_delivery',
      params: { format: 'gpkg', source_ref: 'ref:geojson-x' },
      ack_codes: ['NESTED_FLATTEN'],
    },
  ],
  blockers: [],
  progress_rule: 'new_evidence_required_per_round',
  active_attempt: null,
  attempts: [],
};

describe('parseGoalRepairPreview', () => {
  it('parses a well-formed preview', () => {
    const view = parseGoalRepairPreview(PREVIEW);
    expect(view).not.toBeNull();
    expect(view!.goalId).toBe('g1');
    expect(view!.planVersion).toBe('abc123');
    expect(view!.actions).toHaveLength(2);
    expect(view!.actions[0].disposition).toBe('executable');
    expect(view!.actions[0].sideEffect).toBe('write');
    expect(view!.actions[1].ackCodes).toEqual(['NESTED_FLATTEN']);
  });

  it('returns null for missing identity keys', () => {
    expect(parseGoalRepairPreview(null)).toBeNull();
    expect(parseGoalRepairPreview({})).toBeNull();
    expect(parseGoalRepairPreview({ goal_id: 'g1' })).toBeNull();
  });

  it('drops malformed action rows and clamps unknown dispositions', () => {
    const view = parseGoalRepairPreview({
      ...PREVIEW,
      actions: [
        { action_id: '', action: 'x' },
        'not-an-object',
        { action_id: 'a9', action: 'reobserve', disposition: 'weird' },
      ],
    });
    expect(view!.actions).toHaveLength(1);
    expect(view!.actions[0].disposition).toBe('unsupported');
  });

  it('parses active attempt for refresh recovery', () => {
    const view = parseGoalRepairPreview({
      ...PREVIEW,
      active_attempt: {
        attempt_id: '42',
        status: 'running',
        active: true,
        actions: [
          {
            action_id: 'a1', action: 'export_product',
            requirement_id: 'export:pdf', status: 'running',
            detail: '', evidence_ref: '', download_url: '',
            job_id: '', error_code: '',
          },
        ],
      },
    });
    expect(view!.activeAttempt).not.toBeNull();
    expect(view!.activeAttempt!.attemptId).toBe('42');
    expect(view!.activeAttempt!.actions[0].status).toBe('running');
  });
});

describe('parseGoalRepairAttempt', () => {
  it('parses receipts and final verdict projection', () => {
    const view = parseGoalRepairAttempt({
      attempt_id: '7',
      status: 'completed',
      active: false,
      cancellable: false,
      session_id: 's1',
      goal_id: 'g1',
      plan_version: 'abc123',
      outcome: 'completed',
      final_verdict_before: 'partial',
      final_verdict_after: 'satisfied',
      final_changed: true,
      actions: [
        {
          action_id: 'a1', action: 'export_product',
          requirement_id: 'export:pdf', status: 'succeeded',
          detail: 'map export pdf revision=7 bytes=100',
          evidence_ref: 'mixed_map_x.pdf',
          download_url: '/api/v1/export/download/mixed_map_x.pdf?exp=1&sig=2',
          job_id: '', error_code: '',
        },
      ],
    });
    expect(view!.finalVerdictAfter).toBe('satisfied');
    expect(view!.finalChanged).toBe(true);
    expect(view!.actions[0].download_url).toContain('/api/v1/export/download/');
  });

  it('returns null without attempt identity', () => {
    expect(parseGoalRepairAttempt({ status: 'running' })).toBeNull();
    expect(parseGoalRepairAttempt(null)).toBeNull();
  });
});
