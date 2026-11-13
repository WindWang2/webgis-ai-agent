/**
 * G30 SkillWorkbench：目录分型 → 参数表单 → 预览 → 启动 → 运行回看 → 预设。
 *
 * 钉住的行为契约：
 * - 目录三类资产分型呈现（可执行技能/方法指南/扩展 candidate）；
 *   guidance_only 技能不提供启动入口（无伪调用面）。
 * - prepare typed 拒绝（rate 缺分母）按 code 定位展示，不静默。
 * - 预览面板展示步骤/证据覆盖与未覆盖义务；启动后轮询运行到终态。
 * - 刷新回看：运行列表来自 durable 行（runs API）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { sessionState } = vi.hoisted(() => ({
  sessionState: { sessionId: 'sess-wb-1' },
}));

vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: () => sessionState,
  setSessionIdentity: vi.fn(),
  subscribeSessionIdentity: () => () => undefined,
}));

import { SkillWorkbench } from './skill-workbench';

const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(JSON.stringify(body)),
});

const jsonErr = (status: number, body: unknown) => ({
  ok: false,
  status,
  statusText: 'Bad Request',
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(JSON.stringify(body)),
});

const CATALOG = {
  skills: [
    {
      asset_type: 'gis_skill', id: 'validate_spatial_data', name: '空间数据验证',
      description: '数据入场质检', version: '1.1.0', digest: 'abc123',
      runnability: 'runnable', result_kind: 'report',
      parameters: [
        { name: 'crs', label: 'CRS', type: 'string', required: false, max_length: 64 },
      ],
      role_bindings: [
        { role: 'subject', required: true, geometry_kinds: ['point'], min_features: 0 },
      ],
    },
    {
      asset_type: 'gis_skill', id: 'density_map_design', name: '密度图设计',
      description: '指导', version: '1.0.0',
      runnability: 'guidance_only',
      runnability_reasons: ['无执行映射（方法知识资产）'],
      parameters: [], role_bindings: [],
    },
  ],
  md_guides: [
    { asset_type: 'md_guide', id: 'heatmap', name: 'heatmap',
      description: '热力图指南', runnability: 'guidance_only' },
  ],
  extension_candidates: [],
  counts: { gis_skills: 2, runnable: 1, md_guides: 1, extension_candidates: 0 },
};

const PREPARED = {
  prepared_id: 'skp-abc123',
  skill: { id: 'validate_spatial_data', version: '1.1.0', digest: 'abc123' },
  idempotency_key: 'skj-key1',
  preview: {
    skill: { id: 'validate_spatial_data', name: '空间数据验证',
             version: '1.1.0', digest: 'abc123', domain: 'data_preparation' },
    result_kind: 'report',
    steps: [
      { step_id: 'profile_dataset', tool: 'audit_spatial_quality',
        capability: 'dataset_profiling_quality', side_effect: 'deterministic_compute',
        reads_roles: ['subject'],
        produces_evidence: ['dataset_profile_report'], required: true },
    ],
    bindings: [
      { role: 'subject', ref: 'ref:geojson-x', inline: false, geometry_kind: 'point',
        feature_count: 12, fields: ['name'], content_digest: 'd1' },
    ],
    params: [{ name: 'crs', source: 'skill_default' }],
    obligations: ['verdict_not_silent'],
    evidence_covered: ['dataset_profile_report'],
    evidence_not_covered: ['quality_verdict'],
  },
};

const fetchMock = vi.fn();

function routeFetch(opts?: { prepareStatus?: number; prepareBody?: unknown }) {
  fetchMock.mockImplementation((url: string | URL, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method || 'GET').toUpperCase();
    if (u.includes('/api/v1/skill-jobs/catalog')) {
      return Promise.resolve(jsonOk(CATALOG));
    }
    if (u.includes('/api/v1/skill-jobs/presets') && !u.includes('/prepare')) {
      return Promise.resolve(jsonOk({ presets: [] }));
    }
    if (u.includes('/api/v1/skill-jobs/prepare') && method === 'POST') {
      return Promise.resolve(
        opts?.prepareStatus && opts.prepareStatus >= 400
          ? jsonErr(opts.prepareStatus, { detail: {
              code: 'PARAM_CONFLICT',
              message: "参数 measure_semantics='rate' 时必须提供 denominator_field",
            } })
          : jsonOk(PREPARED));
    }
    if (u.includes('/api/v1/skill-jobs/start')) {
      return Promise.resolve(jsonOk({
        job_id: '9', status: 'completed', result_ref: 'ref:skillreport-z',
        evidence_covered: ['dataset_profile_report'],
        evidence_not_covered: [], artifact_refs: {}, steps: [],
      }));
    }
    if (u.includes('/api/v1/skill-jobs/runs?')) {
      return Promise.resolve(jsonOk({ runs: [
        { job_id: '9', status: 'completed', skill_id: 'validate_spatial_data',
          skill_version: '1.1.0', result_ref: 'ref:skillreport-z' },
      ] }));
    }
    return Promise.resolve(jsonOk({}));
  });
}

describe('SkillWorkbench', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    routeFetch();
  });

  it('目录分型：可执行技能与指南分列，guidance_only 无启动', async () => {
    render(<SkillWorkbench />);
    expect(await screen.findByTestId('skill-validate_spatial_data')).toBeTruthy();
    expect(screen.getByTestId('skill-density_map_design')).toBeTruthy();
    // 切到方法指南 tab：md 指南分型呈现
    await userEvent.click(screen.getByTestId('tab-guides'));
    expect(screen.getAllByText('heatmap').length).toBeGreaterThanOrEqual(1);
    // 回到可执行技能并选择 guidance_only 技能：不出现启动入口
    await userEvent.click(screen.getByTestId('tab-skills'));
    await userEvent.click(screen.getByTestId('skill-density_map_design'));
    expect(screen.getAllByText(/仅指导|guidance_only/i).length).toBeGreaterThanOrEqual(1);
    const prepareBtn = screen.queryByTestId('prepare-btn');
    expect(prepareBtn).toBeTruthy();
    expect((prepareBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it('选择可执行技能 → 绑定 ref → 预览（步骤/证据/未覆盖义务）', async () => {
    render(<SkillWorkbench />);
    await userEvent.click(await screen.findByTestId('skill-validate_spatial_data'));
    const refInput = await screen.findByTestId('binding-ref-subject');
    await userEvent.type(refInput, 'ref:geojson-x');
    await userEvent.click(screen.getByTestId('prepare-btn'));
    // 预览面板：步骤 + 证据覆盖 + 未覆盖义务如实展示
    expect(await screen.findByTestId('preview-panel')).toBeTruthy();
    expect(await screen.findByText(/audit_spatial_quality/)).toBeTruthy();
    expect(await screen.findByText(/quality_verdict/)).toBeTruthy();
  });

  it('prepare typed 拒绝按 code 定位展示', async () => {
    routeFetch({ prepareStatus: 400 });
    render(<SkillWorkbench />);
    await userEvent.click(await screen.findByTestId('skill-validate_spatial_data'));
    await userEvent.type(
      await screen.findByTestId('binding-ref-subject'), 'ref:geojson-x');
    await userEvent.click(screen.getByTestId('prepare-btn'));
    const alert = await screen.findByRole('alert');
    expect(alert.getAttribute('data-error-code')).toBe('PARAM_CONFLICT');
    expect(alert.textContent).toContain('denominator_field');
  });

  it('启动 → 运行完成 → 刷新回看运行列表', async () => {
    render(<SkillWorkbench />);
    await userEvent.click(await screen.findByTestId('skill-validate_spatial_data'));
    await userEvent.type(
      await screen.findByTestId('binding-ref-subject'), 'ref:geojson-x');
    await userEvent.click(screen.getByTestId('prepare-btn'));
    await screen.findByTestId('preview-panel');
    await userEvent.click(screen.getByTestId('start-btn'));
    // 运行面板出现 completed + result ref
    await waitFor(() => {
      expect(screen.getAllByText(/ref:skillreport-z/).length).toBeGreaterThanOrEqual(1);
    });
    // 运行记录列表（durable 行）
    await waitFor(() => {
      expect(screen.getByText('本会话运行记录', { exact: false })).toBeTruthy();
    });
  });
});
