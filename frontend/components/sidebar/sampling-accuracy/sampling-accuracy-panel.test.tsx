/**
 * SamplingAccuracyPanel 组件测试（GOAL-20261127-03 / WP5）。
 *
 * 覆盖（mock API 层，照 raster-sampling-panel.test.tsx 方式）：
 * ① 建框成功显示层表（strata + digest + excluded 披露）；
 * ② 设计预览分配表（层权 W / 建议 n / warnings / rationale）；
 * ③ 创建调查 → 样点列表 → 记录标注 → progress 更新（CAS revision 前进）；
 *    附：样点上图走 addLayer（check_status 属性着色）；
 * ④ 估计结果卡与状态徽章（supported）+ OA 卡 + 误差矩阵。
 *
 * useT 用键直回的确定性 translator（i18n 键完整性由 key-completeness 测试锁）。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api/transport';
import { SamplingAccuracyPanel } from './sampling-accuracy-panel';

const mockBuildFrame = vi.fn();
const mockListFrames = vi.fn();
const mockPreviewDesign = vi.fn();
const mockCreateSurvey = vi.fn();
const mockFetchSurvey = vi.fn();
const mockFetchProgress = vi.fn();
const mockPostRecord = vi.fn();
const mockRunEstimate = vi.fn();
const mockListEstimates = vi.fn();
const mockAddToast = vi.fn();
const mockAddLayer = vi.fn();
const mockRemoveLayer = vi.fn();
const mockSetViewport = vi.fn();

vi.mock('@/lib/api/sampling-accuracy', () => ({
  buildFrame: (...a: unknown[]) => mockBuildFrame(...a),
  listFrames: (...a: unknown[]) => mockListFrames(...a),
  previewDesign: (...a: unknown[]) => mockPreviewDesign(...a),
  createSurvey: (...a: unknown[]) => mockCreateSurvey(...a),
  fetchSurvey: (...a: unknown[]) => mockFetchSurvey(...a),
  fetchProgress: (...a: unknown[]) => mockFetchProgress(...a),
  postRecord: (...a: unknown[]) => mockPostRecord(...a),
  postAdjudicate: vi.fn(),
  runEstimate: (...a: unknown[]) => mockRunEstimate(...a),
  listEstimates: (...a: unknown[]) => mockListEstimates(...a),
  listSurveys: vi.fn(),
  // 纯归一化 helper（无 I/O）——mock 内联同实现，避免 importOriginal 拉入 transport。
  normalizeFrameStrata: (payload: { strata: unknown }) => {
    const raw = payload?.strata;
    const row = (id: string, info: { N_units?: number; area?: number; class_value?: number | null } | undefined) => ({
      stratum_id: String(id),
      N_units: Number(info?.N_units ?? 0),
      area: Number(info?.area ?? 0),
      class_value: info?.class_value ?? null,
    });
    if (Array.isArray(raw)) return raw.map(([id, info]) => row(String(id), info));
    return Object.entries(raw ?? {}).map(([id, info]) => row(id, info));
  },
  // 纯错误信封提取（无 I/O）——与实现同口径：ApiError.body.detail 对象信封。
  surveyErrorOf: (err: unknown) => {
    const e = err as { body?: { detail?: unknown }; message?: string } | null | undefined;
    const d = e?.body?.detail;
    if (d && typeof d === 'object') {
      const rec = d as Record<string, unknown>;
      return {
        code: String(rec.code ?? 'SURVEY_FAILED'),
        detail: String(rec.detail ?? e?.message ?? 'request failed'),
        ...(typeof rec.correction_hint === 'string' && rec.correction_hint
          ? { correction_hint: rec.correction_hint }
          : {}),
      };
    }
    if (typeof d === 'string' && d) return { code: 'SURVEY_FAILED', detail: d };
    return { code: 'SURVEY_FAILED', detail: e?.message ?? String(err) };
  },
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (sel: (s: unknown) => unknown) =>
    sel({ addLayer: mockAddLayer, updateLayer: vi.fn(),
          removeLayer: mockRemoveLayer, setViewport: mockSetViewport }),
}));

vi.mock('@/components/ui/toast', () => ({
  useToastStore: (sel: (s: unknown) => unknown) => sel({ addToast: mockAddToast }),
}));

vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string) => key,
}));

// ── fixtures ────────────────────────────────────────────────────────

function makeFrame() {
  return {
    success: true,
    payload: {
      frame_digest: 'frm_abc123def4567890',
      total_units: 1200,
      total_area: 12000,
      pixel_area_spread: 0.002,
      pixel_area_tolerance: 0.01,
      excluded: { nodata: 10, outside_dictionary: 5 },
      // 后端序列化：[stratum_id, info] 对数组（frame.py sorted(per_stratum.items())）
      strata: [
        ['Farmland', { N_units: 700, area: 7000, class_value: 1 }],
        ['Water', { N_units: 500, area: 5000, class_value: 2 }],
      ],
    },
  };
}

function makePreview() {
  return {
    success: true,
    preview: {
      allocation: { Farmland: 60, Water: 40 },
      n_total: 100,
      weights_W: { Farmland: 0.5833, Water: 0.4167 },
      warnings: ['n_h=40 is below the suggestion for Water'],
      rationale: { strategy: 'equal', min_n_per_stratum: 2 },
    },
  };
}

function makeCreate() {
  return {
    success: true,
    survey_id: 'svy_20261128',
    design_digest: 'des_digest_0001',
    frame_digest: 'frm_abc123def4567890',
    allocation: { Farmland: 60, Water: 40 },
    backup_per_stratum: 2,
    sample_count: 4,
    main_count: 3,
  };
}

function makeSurvey() {
  const sample = (i: number, backup: boolean) => ({
    sample_id: `svy_a_${String(i).padStart(6, '0')}`,
    lon: 10.01 + i * 0.01,
    lat: 20.02 - i * 0.01,
    x: 501000 + i, y: 4420000 - i,
    inclusion_prob: 0.1, weight: 10,
    draw_rank: i, is_backup: backup,
  });
  return {
    success: true,
    survey: {
      survey_id: 'svy_20261128',
      design_digest: 'des_digest_0001',
      allocation: { Farmland: 2, Water: 1 },
      strata: [
        { stratum_id: 'Farmland', N_units: 700, area: 7000 },
        { stratum_id: 'Water', N_units: 500, area: 5000 },
      ],
      weights_W: { Farmland: 0.5833, Water: 0.4167 },
      samples: [sample(0, false), sample(1, false), sample(2, true)],
      sample_count: 3,
      revision: 1,
      estimates: [],
      blind_labelling: true,
    },
  };
}

function makeProgress(labelledFarmland: number) {
  return {
    success: true,
    progress: {
      survey_id: 'svy_20261128',
      design_digest: 'des_digest_0001',
      per_stratum: {
        Farmland: { planned: 2, main: 2, backup: 1, labelled: labelledFarmland,
                    nonusable: 0, unvisited: 2 - labelledFarmland,
                    response_rate: labelledFarmland / 2 },
        Water: { planned: 1, main: 1, backup: 1, labelled: 0,
                 nonusable: 0, unvisited: 1, response_rate: 0 },
      },
      total_planned: 3,
      total_labelled: labelledFarmland,
      overall_response_rate: labelledFarmland / 3,
      can_estimate: false,
    },
  };
}

function makeRecord() {
  return {
    success: true,
    result: { recorded: true, sample_id: 'svy_a_000000', version: 1, revision: 2, blind: true },
  };
}

function makeEstimate() {
  const mv = (e: number, se: number, lo: number, hi: number) => ({
    estimate: e, se, ci_low: lo, ci_high: hi, evaluable: true,
  });
  return {
    success: true,
    estimate: {
      status: 'supported',
      limitations: [],
      strata: ['Farmland', 'Water'],
      ref_classes: ['Farmland', 'Water'],
      matrix: [[0.55, 0.05], [0.1, 0.3]],
      sample_counts: [[33, 3], [6, 18]],
      n_responded: [36, 24],
      n_planned: [60, 40],
      overall_accuracy: mv(0.85, 0.03, 0.79, 0.91),
      user_accuracy: { Farmland: mv(0.9167, 0.04, 0.82, 0.98), Water: mv(0.75, 0.06, 0.6, 0.9) },
      producer_accuracy: { Farmland: mv(0.8462, 0.05, 0.72, 0.95), Water: mv(0.8571, 0.06, 0.7, 0.98) },
      area_fraction: { Farmland: mv(0.5833, 0.03, 0.52, 0.65), Water: mv(0.4167, 0.03, 0.35, 0.48) },
      area_estimate: { Farmland: mv(7000, 360, 6200, 7800), Water: mv(5000, 360, 4200, 5800) },
      excluded_total: 0,
      excluded_reasons: {},
      estimator_version: 'survey-estimation/1.0.0',
      estimate_id: 'est_000001',
    },
  };
}

function setup() {
  return render(<SamplingAccuracyPanel sessionId="sess-1" ownerToken={null} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockBuildFrame.mockResolvedValue(makeFrame());
  mockPreviewDesign.mockResolvedValue(makePreview());
  mockCreateSurvey.mockResolvedValue(makeCreate());
  mockFetchSurvey.mockResolvedValue(makeSurvey());
  mockFetchProgress.mockResolvedValue(makeProgress(0));
  mockPostRecord.mockResolvedValue(makeRecord());
  mockRunEstimate.mockResolvedValue(makeEstimate());
  mockListEstimates.mockResolvedValue({ success: true, estimates: [] });
});

/** 走完 建框→预览→创建 三个前置步骤，停在 step 4（核查）。 */
async function setupAtVerifyStep() {
  setup();
  fireEvent.change(screen.getByPlaceholderText('ref:data-…'),
    { target: { value: 'ref:raster-lulc' } });
  fireEvent.change(screen.getByLabelText('className 1'), { target: { value: 'Farmland' } });
  fireEvent.change(screen.getByLabelText('className 2'), { target: { value: 'Water' } });
  fireEvent.click(screen.getByText('buildFrame'));
  await waitFor(() => expect(mockBuildFrame).toHaveBeenCalled());
  // 建框成功自动进入 step 2
  fireEvent.click(await screen.findByText('runPreview'));
  await waitFor(() => expect(mockPreviewDesign).toHaveBeenCalled());
  // 预览成功自动进入 step 3
  fireEvent.change(await screen.findByPlaceholderText('objectivePlaceholder'),
    { target: { value: 'LULC accuracy verification' } });
  fireEvent.click(screen.getByText('createSurvey'));
  await waitFor(() => expect(mockCreateSurvey).toHaveBeenCalled());
  // 创建成功自动进入 step 4 并拉取详情/进度
  await screen.findByTestId('sample-list');
}

describe('SamplingAccuracyPanel', () => {
  it('builds a frame and renders the strata table with digest and exclusions', async () => {
    mockListFrames.mockResolvedValue({ success: true, frames: [] });
    setup();
    fireEvent.change(screen.getByPlaceholderText('ref:data-…'),
      { target: { value: 'ref:raster-lulc' } });
    fireEvent.change(screen.getByLabelText('className 1'), { target: { value: 'Farmland' } });
    fireEvent.change(screen.getByLabelText('className 2'), { target: { value: 'Water' } });
    fireEvent.click(screen.getByText('buildFrame'));
    await waitFor(() => expect(mockBuildFrame).toHaveBeenCalled());
    expect(mockBuildFrame.mock.calls[0][1]).toEqual({
      sourceRef: 'ref:raster-lulc',
      band: 1,
      classDictionary: { '1': 'Farmland', '2': 'Water' },
    });
    const table = await screen.findByTestId('frame-strata-table');
    expect(table.textContent).toContain('Farmland');
    expect(table.textContent).toContain('Water');
    expect(table.textContent).toContain('700');
    expect(table.textContent).toContain('5,000');
    // 占比：700/1200 = 58.33%
    expect(table.textContent).toContain('58.33%');
    const summary = screen.getByTestId('frame-summary');
    expect(summary.textContent).toContain('frm_abc123def4567890');
    expect(summary.textContent).toContain('excludedNodata');
    // 建框成功 → 自动进入设计步骤
    expect(screen.getByTestId('design-step')).toBeInTheDocument();
  });

  it('previews the allocation table with weights and warnings', async () => {
    setup();
    fireEvent.change(screen.getByPlaceholderText('ref:data-…'),
      { target: { value: 'ref:raster-lulc' } });
    fireEvent.change(screen.getByLabelText('className 1'), { target: { value: 'Farmland' } });
    fireEvent.change(screen.getByLabelText('className 2'), { target: { value: 'Water' } });
    fireEvent.click(screen.getByText('buildFrame'));
    fireEvent.click(await screen.findByText('runPreview'));
    await waitFor(() => expect(mockPreviewDesign).toHaveBeenCalled());
    expect(mockPreviewDesign.mock.calls[0][1]).toEqual({
      frameDigest: 'frm_abc123def4567890',
      strategy: 'equal',
      nTotal: 100,
    });
    const table = await screen.findByTestId('preview-allocation-table');
    expect(table.textContent).toContain('Farmland');
    expect(table.textContent).toContain('0.5833');
    expect(table.textContent).toContain('60');
    expect(table.textContent).toContain('40%');
    // warnings + rationale 如实披露
    const block = screen.getByTestId('preview-block');
    expect(block.textContent).toContain('warningsTitle');
    expect(block.textContent).toContain('below the suggestion');
    expect(block.textContent).toContain('rationaleTitle');
    // 预览成功 → 自动进入创建步骤
    expect(screen.getByTestId('create-step')).toBeInTheDocument();
  });

  it('records a labelling and refreshes progress (CAS revision advances)', async () => {
    await setupAtVerifyStep();
    // 创建后自动拉详情与进度（首次进度：0 标注）
    await waitFor(() => expect(mockFetchProgress).toHaveBeenCalledTimes(1));
    // 样点上图：addLayer 携带 check_status 属性的 FeatureCollection
    fireEvent.click(screen.getByText('showOnMap'));
    await waitFor(() => expect(mockAddLayer).toHaveBeenCalled());
    const layerArg = mockAddLayer.mock.calls[0][0];
    expect(layerArg.source.type).toBe('FeatureCollection');
    expect(layerArg.source.features[0].properties.check_status).toBe('not_visited');
    expect(layerArg.legend_spec.type).toBe('categorical');
    expect(layerArg.legend_spec.field).toBe('check_status');
    // 行点击定位
    fireEvent.click(screen.getByText('a_000000').closest('tr') as HTMLElement);
    await waitFor(() => expect(mockSetViewport).toHaveBeenCalled());
    expect(mockSetViewport.mock.calls[0][0]).toEqual([10.01, 20.02]);
    // 填标注表单：参考类 = Farmland（默认状态 labelled），提交
    const combos = screen.getAllByRole('combobox');
    fireEvent.change(combos[1], { target: { value: 'Farmland' } });
    mockFetchProgress.mockResolvedValue(makeProgress(1));
    fireEvent.click(screen.getByText('submitRecord'));
    await waitFor(() => expect(mockPostRecord).toHaveBeenCalled());
    expect(mockPostRecord.mock.calls[0][2]).toMatchObject({
      sampleId: 'svy_a_000000',
      status: 'labelled',
      referenceClass: 'Farmland',
      expectedRevision: 1, // CAS：详情 revision 1
    });
    // 记录成功 → revision 前进到 2，progress 刷新（第二次调用：1 标注）
    await waitFor(() => expect(mockFetchProgress).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('50%')).toBeInTheDocument();
    const progress = screen.getByTestId('progress-table');
    expect(progress.textContent).toContain('totalLabelled');
    expect(screen.getByTestId('survey-revision').textContent).toContain('2');
    // 样点行状态变 labelled（本地追踪）
    const list = screen.getByTestId('sample-list');
    expect(list.textContent).toContain('statusLabelled');
  });

  it('renders the estimate card with status badge, OA and error matrix', async () => {
    await setupAtVerifyStep();
    // 进入 step 5
    fireEvent.click(screen.getByText('stepEstimate'));
    fireEvent.click(await screen.findByText('runEstimate'));
    await waitFor(() => expect(mockRunEstimate).toHaveBeenCalled());
    const badge = await screen.findByTestId('estimate-status-badge');
    expect(badge.textContent).toBe('statusSupported');
    const card = screen.getByTestId('estimate-card');
    // OA 卡：估计 ± SE + 95% CI
    expect(card.textContent).toContain('0.85');
    expect(card.textContent).toContain('0.03');
    expect(card.textContent).toContain('0.79');
    expect(card.textContent).toContain('0.91');
    // UA / PA / 面积表
    expect(card.textContent).toContain('uaTitle');
    expect(card.textContent).toContain('paTitle');
    expect(card.textContent).toContain('areaTitle');
    expect(card.textContent).toContain('7,000');
    // 误差矩阵热力表默认比例模式
    const matrix = screen.getByTestId('matrix-table');
    expect(matrix.textContent).toContain('0.55');
    // 切换计数模式
    fireEvent.click(screen.getByText('showCounts'));
    expect(screen.getByTestId('matrix-table').textContent).toContain('33');
    // 历史列表刷新
    await waitFor(() => expect(mockListEstimates).toHaveBeenCalled());
  });

  it('surfaces the typed estimate refusal envelope (422/409)', async () => {
    await setupAtVerifyStep();
    fireEvent.click(screen.getByText('stepEstimate'));
    mockRunEstimate.mockRejectedValueOnce(new ApiError(
      422, 'Unprocessable Entity',
      { detail: { code: 'ESTIMATION_NOT_EVALUABLE', detail: 'stratum Water has zero responded samples', correction_hint: 'label more samples in Water' } },
    ));
    fireEvent.click(await screen.findByText('runEstimate'));
    const err = await screen.findByTestId('estimate-error');
    expect(err.textContent).toContain('ESTIMATION_NOT_EVALUABLE');
    expect(err.textContent).toContain('zero responded samples');
    expect(err.textContent).toContain('label more samples in Water');
    // 估计失败不渲染估计卡
    expect(screen.queryByTestId('estimate-card')).not.toBeInTheDocument();
  });
});
