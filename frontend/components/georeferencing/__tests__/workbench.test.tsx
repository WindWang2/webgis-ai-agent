/**
 * G48 Frontend Workbench component tests (`GeoreferencingWorkbenchPanel`).
 * Covers: no-session guard, upload + EXIF retry + embedded geo warning, pixel canvas + map-click
 * GCP picking + undo, CAS Save & Solve diagnostics + CAS error handling, warped preview,
 * execute + polling, partial_success MapSpec bind retry -> commit, and GeoTIFF download trigger.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ApiError } from '@/lib/api/transport';
import { GeoreferencingWorkbenchPanel } from '../GeoreferencingWorkbenchPanel';

const mockAddToast = vi.fn();
const mockSetActiveMapTool = vi.fn();
let currentActiveMapTool: string | null = null;
let mapClickListener: ((lngLat: [number, number]) => void) | null = null;

const mockSetMapSpecRevision = vi.fn();
const mockCommitMapSpecDocument = vi.fn();
const mockGetMapSpecSessionCursor = vi.fn(() => ({ revision: 0 }));

const mockListGeorefSources = vi.fn();
const mockListGeorefPlans = vi.fn();
const mockUploadGeorefSource = vi.fn();
const mockCreateGeorefPlan = vi.fn();
const mockSaveGeorefDraft = vi.fn();
const mockImportGeorefGcps = vi.fn();
const mockPreviewGeorefPlan = vi.fn();
const mockExecuteGeorefPlan = vi.fn();
const mockPollGeorefStatus = vi.fn();
const mockCancelGeorefPlan = vi.fn();
const mockBindGeorefLayer = vi.fn();
const mockDownloadGeorefArtifact = vi.fn();
const mockDeleteGeorefSource = vi.fn();
const mockFetchProtectedImageUrl = vi.fn();

vi.mock('@/components/ui/toast', () => ({
  useToastStore: (sel: (s: unknown) => unknown) => sel({ addToast: mockAddToast }),
}));

const translators = new Map<string, (k: string, p?: Record<string, unknown>) => string>();
function stableUseT(ns?: string) {
  const key = ns || '__root__';
  let fn = translators.get(key);
  if (!fn) {
    fn = (k: string, params?: Record<string, unknown>) =>
      params ? `${ns ? ns + '.' : ''}${k}|${JSON.stringify(params)}` : `${ns ? ns + '.' : ''}${k}`;
    translators.set(key, fn);
  }
  return fn;
}

vi.mock('@/lib/i18n/useT', () => ({
  useT: (ns?: string) => stableUseT(ns),
}));

vi.mock('@/lib/mapspec/session-cursor', () => ({
  commitMapSpecDocument: (...a: unknown[]) => mockCommitMapSpecDocument(...a),
  getMapSpecSessionCursor: () => mockGetMapSpecSessionCursor(),
  setMapSpecRevision: (...a: unknown[]) => mockSetMapSpecRevision(...a),
}));

vi.mock('@/lib/raster-authoring/map-click', () => ({
  subscribeMapClick: (cb: (lngLat: [number, number]) => void) => {
    mapClickListener = cb;
    return () => {
      if (mapClickListener === cb) mapClickListener = null;
    };
  },
}));

vi.mock('@/lib/store/useHudStore', () => {
  const hook = (sel: (s: unknown) => unknown) =>
    sel({
      activeMapTool: currentActiveMapTool,
      setActiveMapTool: (tool: string | null) => {
        currentActiveMapTool = tool;
        mockSetActiveMapTool(tool);
      },
    });
  hook.getState = () => ({
    activeMapTool: currentActiveMapTool,
    setActiveMapTool: (tool: string | null) => {
      currentActiveMapTool = tool;
      mockSetActiveMapTool(tool);
    },
  });
  return { useHudStore: hook };
});

vi.mock('@/lib/georeferencing/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/georeferencing/api')>(
    '@/lib/georeferencing/api',
  );
  return {
    ...actual,
    listGeorefSources: (...a: unknown[]) => mockListGeorefSources(...a),
    listGeorefPlans: (...a: unknown[]) => mockListGeorefPlans(...a),
    uploadGeorefSource: (...a: unknown[]) => mockUploadGeorefSource(...a),
    createGeorefPlan: (...a: unknown[]) => mockCreateGeorefPlan(...a),
    saveGeorefDraft: (...a: unknown[]) => mockSaveGeorefDraft(...a),
    importGeorefGcps: (...a: unknown[]) => mockImportGeorefGcps(...a),
    previewGeorefPlan: (...a: unknown[]) => mockPreviewGeorefPlan(...a),
    executeGeorefPlan: (...a: unknown[]) => mockExecuteGeorefPlan(...a),
    pollGeorefStatus: (...a: unknown[]) => mockPollGeorefStatus(...a),
    cancelGeorefPlan: (...a: unknown[]) => mockCancelGeorefPlan(...a),
    bindGeorefLayer: (...a: unknown[]) => mockBindGeorefLayer(...a),
    downloadGeorefArtifact: (...a: unknown[]) => mockDownloadGeorefArtifact(...a),
    deleteGeorefSource: (...a: unknown[]) => mockDeleteGeorefSource(...a),
    fetchProtectedImageUrl: (...a: unknown[]) => mockFetchProtectedImageUrl(...a),
  };
});

function makeSource(overrides: Record<string, unknown> = {}) {
  return {
    source_id: 'gsrc_1',
    session_id: 'sess-g48-ui',
    filename: 'scan.png',
    detected_format: 'PNG',
    width: 340,
    height: 240,
    bands: 4,
    dtype: 'uint8',
    has_alpha: true,
    exif_orientation: 1,
    exif_overridden: false,
    original_sha256: 'a'.repeat(64),
    normalized_sha256: 'b'.repeat(64),
    normalized_path: '/tmp/scan_norm.png',
    byte_size: 2048,
    existing_georef: { has_crs: false, has_transform: false, gcp_count: 0, warnings: [] },
    created_at: '2026-11-15T00:00:00Z',
    ...overrides,
  };
}

function makePlan(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 'gplan_1',
    session_id: 'sess-g48-ui',
    source_id: 'gsrc_1',
    name: 'scan.png',
    revision: 1,
    transform_model: 'affine_2d' as const,
    target_crs: 'EPSG:3857',
    resampling: 'bilinear' as const,
    target_resolution_m: null,
    generate_cog: true,
    ignore_exif: false,
    gcps: [],
    fit_metrics: null,
    preview_digest: null,
    preview_stale: true,
    preview_footprint_wgs84: null,
    preview_bounds_wgs84: null,
    preview_path: null,
    active_job_id: null,
    active_execution_id: null,
    latest_artifact: null,
    mapspec_bind_status: null,
    created_at: '2026-11-15T00:00:00Z',
    updated_at: '2026-11-15T00:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  currentActiveMapTool = null;
  mapClickListener = null;
  mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [] });
  mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [] });
  mockFetchProtectedImageUrl.mockResolvedValue('blob:mock-image');
  mockGetMapSpecSessionCursor.mockReturnValue({ revision: 0 });
  if (!globalThis.URL.revokeObjectURL) {
    globalThis.URL.revokeObjectURL = vi.fn();
  }
});

describe('GeoreferencingWorkbenchPanel (G48)', () => {
  it('shows noSession notice when sessionId is missing', () => {
    render(<GeoreferencingWorkbenchPanel sessionId={null} />);
    expect(screen.getByTestId('georeferencing-workbench')).toHaveTextContent('georeferencing.noSession');
    expect(screen.queryByTestId('georef-upload-input')).not.toBeInTheDocument();
  });

  it('handles EXIF retry, embedded geo warning, canvas/map GCP picking + undo, Save & Solve + CAS error', async () => {
    const srcWarn = makeSource({
      existing_georef: {
        has_crs: true,
        has_transform: true,
        gcp_count: 0,
        warnings: ['Existing GeoTIFF transform detected'],
      },
    });
    mockUploadGeorefSource
      .mockRejectedValueOnce(
        new ApiError(422, 'Unprocessable Entity', {
          data: { code: 'SOURCE_EXIF_FAILED', message: 'Corrupt EXIF header' },
        }),
      )
      .mockResolvedValueOnce({ source: srcWarn, image_url: '/api/v1/georeferencing/sources/gsrc_1/image' });
    mockCreateGeorefPlan.mockResolvedValue({ plan: makePlan({ revision: 1 }) });

    render(<GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />);
    await waitFor(() => expect(mockListGeorefSources).toHaveBeenCalled());

    const input = screen.getByTestId('georef-upload-input') as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3, 4])], 'scan.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });

    // First upload hits SOURCE_EXIF_FAILED -> retry button appears
    await waitFor(() => expect(screen.getByTestId('georef-retry-ignore-exif-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('georef-retry-ignore-exif-btn'));

    // Second upload succeeds with ignoreExif=true & displays embedded geo warning
    await waitFor(() => expect(mockUploadGeorefSource).toHaveBeenCalledTimes(2));
    expect(mockUploadGeorefSource.mock.calls[1][2]).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId('georef-embedded-geo-warning')).toHaveTextContent(
        'Existing GeoTIFF transform detected',
      ),
    );

    // Image pixel (85, 60) is viewport (93.5, 66) after the real
    // 12px-padded fit: scale .9, pan (17, 12). Never rely on an identity
    // transform rendered before a passive source-change effect.
    const canvas = screen.getByTestId('georef-pixel-canvas');
    Object.defineProperty(canvas, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 340, height: 240, right: 340, bottom: 240 }),
    });
    fireEvent.mouseUp(canvas, { button: 0, clientX: 93.5, clientY: 66 });
    await waitFor(() => expect(screen.getByTestId('georef-gcp-row-0')).toBeInTheDocument());

    const pxInput = screen.getByLabelText('px-0') as HTMLInputElement;
    expect(Number(pxInput.value)).toBeCloseTo(85, 0);
    expect(Number((screen.getByLabelText('py-0') as HTMLInputElement).value)).toBeCloseTo(60, 0);

    // Edit label then undo
    fireEvent.change(screen.getByLabelText('label-0'), { target: { value: 'TMP' } });
    expect((screen.getByLabelText('label-0') as HTMLInputElement).value).toBe('TMP');
    fireEvent.click(screen.getByTestId('georef-undo-btn'));
    expect((screen.getByLabelText('label-0') as HTMLInputElement).value).toBe('P1');

    // Toggle map pick tool & click MapLibre mock
    fireEvent.click(screen.getByTestId('georef-pick-map-btn'));
    expect(mockSetActiveMapTool).toHaveBeenCalledWith('georef_pick_map');
  });

  it('runs Save & Solve -> Preview -> Execute -> Partial Bind Retry -> Download', async () => {
    const src = makeSource();
    const initialGcps = [
      { gcp_id: 'g1', label: 'P1', px: 0, py: 0, map_x: 100, map_y: 200, wgs84_lon: 120.1, wgs84_lat: 30.2, role: 'fit', enabled: true, note: '' },
      { gcp_id: 'g2', label: 'P2', px: 340, py: 0, map_x: 440, map_y: 200, wgs84_lon: 120.2, wgs84_lat: 30.2, role: 'fit', enabled: true, note: '' },
      { gcp_id: 'g3', label: 'P3', px: 340, py: 240, map_x: 440, map_y: -40, wgs84_lon: 120.2, wgs84_lat: 30.1, role: 'fit', enabled: true, note: '' },
      { gcp_id: 'g4', label: 'P4', px: 0, py: 240, map_x: 100, map_y: -40, wgs84_lon: 120.1, wgs84_lat: 30.1, role: 'check', enabled: true, note: '' },
    ];
    const planRev1 = makePlan({ revision: 1, gcps: initialGcps });
    const planRev2 = makePlan({
      revision: 2,
      gcps: initialGcps,
      fit_metrics: {
        status: 'valid',
        fit_count: 3,
        check_count: 1,
        disabled_count: 0,
        fit_rmse: 0.42,
        fit_max_error: 0.5,
        check_rmse: 0.88,
        check_max_error: 0.88,
        zero_residual_unverified: false,
        condition_number: 1.5,
        rank: 6,
        hull_coverage_ratio: 0.72,
        residuals: [
          { gcp_id: 'g1', label: 'P1', role: 'fit', enabled: true, px: 0, py: 0, map_x: 100, map_y: 200, pred_x: 100, pred_y: 200, dx: 0.2, dy: -0.1, radial_error: 0.22 },
        ],
        outlier_gcp_ids: [],
        warnings: [],
        blockers: [],
        coefficients: {
          a0: 100, a1: 1, a2: 0, b0: 200, b1: 0, b2: -1,
          px_scale_x: 1, px_scale_y: 1, rotation_deg: 0, shear_deg: 0,
          determinant: -1, is_reflection: false,
        },
      },
    });
    const planPrev = {
      ...planRev2,
      preview_stale: false,
      preview_digest: 'prev123',
      preview_path: '/tmp/prev.png',
    };
    const artifact = {
      artifact_id: 'gart_99',
      plan_id: 'gplan_1',
      execution_id: 'gexec_99',
      job_id: '42',
      session_id: 'sess-g48-ui',
      format: 'GeoTIFF',
      cog_requested: true,
      cog_verified: true,
      cog_checks: {},
      target_crs: 'EPSG:3857',
      width: 340,
      height: 240,
      bands: 4,
      dtype: 'uint8',
      has_alpha: true,
      nodata_mode: 'band4_alpha',
      sha256: 'c'.repeat(64),
      byte_size: 4096,
      blob_key: 'georefbin-c',
      file_path: '/tmp/out.tif',
      raster_ref: 'ref:raster_georef-123',
      bounds_target_crs: [0, 0, 100, 100],
      bounds_wgs84: [120.1, 30.1, 120.2, 30.2],
      created_at: '2026-11-15T00:01:00Z',
    };
    const planExecuted = { ...planPrev, latest_artifact: artifact };

    mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [src] });
    mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [planRev1] });
    mockSaveGeorefDraft
      .mockRejectedValueOnce(
        new ApiError(409, 'Conflict', {
          data: { code: 'CAS_CONFLICT', message: 'Stale rev' },
        }),
      )
      .mockResolvedValueOnce({ plan: planRev2 });
    mockPreviewGeorefPlan.mockResolvedValue({
      plan: planPrev,
      preview_available: true,
      preview_image_url: '/api/v1/georeferencing/plans/gplan_1/preview/image',
      out_width: 256,
      out_height: 180,
      warnings: [],
    });
    mockExecuteGeorefPlan.mockResolvedValue({
      plan_id: 'gplan_1',
      job_id: '42',
      execution_id: 'gexec_99',
      status: 'running',
      background: true,
      idempotent_reuse: false,
      artifact: null,
      poll_url: '/poll',
    });
    mockPollGeorefStatus.mockResolvedValue({
      plan_id: 'gplan_1',
      job_id: '42',
      execution_id: 'gexec_99',
      status: 'completed',
      stage: 'completed',
      progress: 100,
      error_code: null,
      error_message: null,
      artifact,
      plan: planExecuted,
    });
    mockBindGeorefLayer
      .mockResolvedValueOnce({
        bound: false,
        partial_success: true,
        layer_id: null,
        mapspec_revision: null,
        current_mapspec_revision: 5,
        error_code: 'MAPSPEC_BIND_CONFLICT',
        message: 'Revision collided',
        artifact,
        mapspec: null,
      })
      .mockResolvedValueOnce({
        bound: true,
        partial_success: false,
        layer_id: 'georef_gsrc_1',
        mapspec_revision: 6,
        current_mapspec_revision: 6,
        error_code: null,
        message: 'Bound',
        artifact,
        mapspec: { version: '1.0', layers: [{ id: 'georef_gsrc_1' }] },
      });
    mockDownloadGeorefArtifact.mockResolvedValue(undefined);

    render(<GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.getByTestId('georef-save-solve-btn')).toBeInTheDocument());

    // First Save hits CAS_CONFLICT toast
    fireEvent.click(screen.getByTestId('georef-save-solve-btn'));
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith(expect.stringContaining('CAS_CONFLICT'), 'error'),
    );

    // Second Save succeeds & renders diagnostics (fit RMSE & check RMSE)
    fireEvent.click(screen.getByTestId('georef-save-solve-btn'));
    await waitFor(() => expect(screen.getByTestId('georef-diag-fit-rmse')).toHaveTextContent('0.420 m'));
    expect(screen.getByTestId('georef-diag-check-rmse')).toHaveTextContent('0.880 m');

    // Generate warped preview
    fireEvent.click(screen.getByTestId('georef-preview-btn'));
    await waitFor(() => expect(screen.getByTestId('georef-preview-img')).toBeInTheDocument());

    // Execute GeoTIFF job -> polls -> verifies completed status banner/toast + reveals bind & download buttons
    fireEvent.click(screen.getByTestId('georef-execute-btn'));
    await waitFor(() => expect(screen.getByTestId('georef-job-status')).toHaveTextContent('georeferencing.jobSucceeded'));
    expect(mockAddToast).toHaveBeenCalledWith('georeferencing.jobSucceeded', 'success');
    await waitFor(() => expect(screen.getByTestId('georef-bind-layer-btn')).toBeInTheDocument());

    // First Bind returns partial_success=true with current_mapspec_revision=5
    fireEvent.click(screen.getByTestId('georef-bind-layer-btn'));
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith(
        expect.stringContaining('partialBindWarning'),
        'warning',
      ),
    );

    // Retry Bind submits expected_mapspec_revision=5 -> binds revision 6 & commits MapSpec
    await act(async () => {
      fireEvent.click(screen.getByTestId('georef-bind-layer-btn'));
    });
    await waitFor(() => expect(mockBindGeorefLayer).toHaveBeenCalledTimes(2));
    expect(mockBindGeorefLayer.mock.calls[1][2]).toMatchObject({ expected_mapspec_revision: 5 });
    expect(mockSetMapSpecRevision).toHaveBeenCalledWith(6);
    expect(mockCommitMapSpecDocument).toHaveBeenCalled();

    // Trigger authorized GeoTIFF download
    fireEvent.click(screen.getByTestId('georef-download-tif-btn'));
    expect(mockDownloadGeorefArtifact).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'sess-g48-ui', ownerToken: 'tok-1' }),
      'gart_99',
    );
  });

  it('auto-detects projected vs WGS84 columns on CSV import and rotates idempotency key after failure', async () => {
    const src = makeSource();
    const gcps = [
      { gcp_id: 'g1', label: 'P1', px: 0, py: 0, map_x: 100, map_y: 200, wgs84_lon: null, wgs84_lat: null, role: 'fit', enabled: true, note: '' },
      { gcp_id: 'g2', label: 'P2', px: 340, py: 0, map_x: 440, map_y: 200, wgs84_lon: null, wgs84_lat: null, role: 'fit', enabled: true, note: '' },
      { gcp_id: 'g3', label: 'P3', px: 340, py: 240, map_x: 440, map_y: -40, wgs84_lon: null, wgs84_lat: null, role: 'fit', enabled: true, note: '' },
    ];
    const planRev1 = makePlan({
      revision: 1,
      gcps,
      fit_metrics: {
        status: 'valid',
        fit_count: 3,
        check_count: 0,
        disabled_count: 0,
        fit_rmse: 0.2,
        fit_max_error: 0.2,
        check_rmse: null,
        check_max_error: null,
        zero_residual_unverified: false,
        condition_number: 1.5,
        rank: 6,
        hull_coverage_ratio: 0.8,
        residuals: [],
        outlier_gcp_ids: [],
        warnings: [],
        blockers: [],
        coefficients: {
          a0: 100, a1: 1, a2: 0, b0: 200, b1: 0, b2: -1,
          px_scale_x: 1, px_scale_y: 1, rotation_deg: 0, shear_deg: 0,
          determinant: -1, is_reflection: false,
        },
      },
    });
    mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [src] });
    mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [planRev1] });
    mockImportGeorefGcps.mockResolvedValue({ plan: planRev1 });
    mockExecuteGeorefPlan.mockResolvedValue({
      plan_id: 'gplan_1',
      job_id: '42',
      execution_id: 'gexec_fail',
      status: 'running',
      background: true,
      idempotent_reuse: false,
      artifact: null,
      poll_url: '/poll',
    });
    mockPollGeorefStatus.mockResolvedValueOnce({
      plan_id: 'gplan_1',
      job_id: '42',
      execution_id: 'gexec_fail',
      status: 'failed',
      stage: 'failed',
      progress: 20,
      error_code: 'JOB_FAILED',
      error_message: 'Transient failure',
      artifact: null,
      plan: planRev1,
    });

    const { container } = render(<GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.getByTestId('georef-execute-btn')).toBeInTheDocument());

    const csvInput = container.querySelector('input[accept=".csv,.txt"]') as HTMLInputElement;
    const projFile = new File(['px,py,map_x,map_y\n0,0,100,200'], 'exported.csv', { type: 'text/csv' });
    Object.defineProperty(projFile, 'text', { value: async () => 'px,py,map_x,map_y\n0,0,100,200' });
    fireEvent.change(csvInput, { target: { files: [projFile] } });
    await waitFor(() => expect(mockImportGeorefGcps).toHaveBeenCalledTimes(1));
    expect(mockImportGeorefGcps.mock.calls[0][2]).toMatchObject({ coords_are_wgs84: false });

    const wgsFile = new File(['px,py,lon,lat\n0,0,120.1,30.2'], 'wgs.csv', { type: 'text/csv' });
    Object.defineProperty(wgsFile, 'text', { value: async () => 'px,py,lon,lat\n0,0,120.1,30.2' });
    fireEvent.change(csvInput, { target: { files: [wgsFile] } });
    await waitFor(() => expect(mockImportGeorefGcps).toHaveBeenCalledTimes(2));
    expect(mockImportGeorefGcps.mock.calls[1][2]).toMatchObject({ coords_are_wgs84: true });

    // First execute fails -> retry increments idempotency suffix cleanly
    fireEvent.click(screen.getByTestId('georef-execute-btn'));
    await waitFor(() => expect(screen.getByTestId('georef-job-status')).toHaveTextContent('Transient failure'));

    fireEvent.click(screen.getByTestId('georef-execute-btn'));
    await waitFor(() => expect(mockExecuteGeorefPlan).toHaveBeenCalledTimes(2));
    expect(mockExecuteGeorefPlan.mock.calls[0][2].idempotency_key).toBe('ui:gplan_1:r1');
    expect(mockExecuteGeorefPlan.mock.calls[1][2].idempotency_key).toBe('ui:gplan_1:r1:retry1');
  });

  it('keeps unsaved GCP edits while a running job is polled', async () => {
    const src = makeSource();
    const plan = makePlan({
      revision: 1,
      active_job_id: '42',
      gcps: [
        { gcp_id: 'g1', label: 'P1', px: 10, py: 20, map_x: 100, map_y: 200, wgs84_lon: null, wgs84_lat: null, role: 'fit', enabled: true, note: '' },
      ],
    });
    mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [src] });
    mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [plan] });
    mockPollGeorefStatus.mockResolvedValue({
      plan_id: 'gplan_1',
      job_id: '42',
      execution_id: 'gexec_42',
      status: 'running',
      stage: 'warping',
      progress: 30,
      error_code: null,
      error_message: null,
      artifact: null,
      plan,
    });

    render(<GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.getByTestId('georef-gcp-row-0')).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId('georef-job-status')).toHaveTextContent('georeferencing.jobRunning'),
    );

    fireEvent.change(screen.getByLabelText('label-0'), { target: { value: 'EDITED' } });
    expect(screen.getByText('georeferencing.dirtyBadge')).toBeInTheDocument();

    // Let at least one 650ms poll tick land after the edit.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 900));
    });

    expect(mockPollGeorefStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect((screen.getByLabelText('label-0') as HTMLInputElement).value).toBe('EDITED');
    expect(screen.getByTestId('georef-undo-btn')).toBeEnabled();
    expect(screen.getByText('georeferencing.dirtyBadge')).toBeInTheDocument();
  });

  it('clears stale running status when a status poll fails', async () => {
    const plan = makePlan({ revision: 1, active_job_id: '42', active_execution_id: 'gexec_42' });
    mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [makeSource()] });
    mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [plan] });
    mockPollGeorefStatus
      .mockResolvedValueOnce({
        plan_id: 'gplan_1',
        job_id: '42',
        execution_id: 'gexec_42',
        status: 'running',
        stage: 'warping',
        progress: 30,
        error_code: null,
        error_message: null,
        artifact: null,
        plan,
      })
      .mockRejectedValue(new Error('status endpoint down'));

    render(<GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />);
    await waitFor(() =>
      expect(screen.getByTestId('georef-job-status')).toHaveTextContent('georeferencing.jobRunning'),
    );
    expect(screen.getByTestId('georef-execute-btn')).toBeDisabled();

    await waitFor(() => expect(screen.getByTestId('georef-execute-btn')).not.toBeDisabled(), {
      timeout: 2500,
    });
    expect(screen.queryByTestId('georef-job-status')).not.toBeInTheDocument();
    expect(mockAddToast).toHaveBeenCalledWith('status endpoint down', 'error');
  });

  it('drops stale delete-source completion after switching sessions', async () => {
    const keepSource = makeSource({ source_id: 'gsrc_keep', filename: 'keep.png' });
    const delSource = makeSource({ source_id: 'gsrc_del', filename: 'del.png' });
    const planDel = makePlan({ source_id: 'gsrc_del', revision: 1, gcps: [] });
    let resolveDelete: (() => void) | undefined;
    mockDeleteGeorefSource.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveDelete = resolve;
        }),
    );
    mockListGeorefSources
      .mockResolvedValueOnce({ session_id: 'sess-g48-ui', sources: [keepSource, delSource] })
      .mockResolvedValueOnce({ session_id: 'sess-other', sources: [] })
      .mockResolvedValue({ session_id: 'sess-other', sources: [] });
    mockListGeorefPlans
      .mockResolvedValueOnce({ session_id: 'sess-g48-ui', plans: [planDel] })
      .mockResolvedValueOnce({ session_id: 'sess-other', plans: [] })
      .mockResolvedValue({ session_id: 'sess-other', plans: [] });

    const { rerender } = render(
      <GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />,
    );
    await waitFor(() => expect(screen.getByTestId('georef-delete-source-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('georef-delete-source-btn'));
    await waitFor(() => expect(mockDeleteGeorefSource).toHaveBeenCalledTimes(1));

    rerender(<GeoreferencingWorkbenchPanel sessionId="sess-other" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.queryByTestId('georef-source-select')).not.toBeInTheDocument());

    await act(async () => {
      resolveDelete?.();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByTestId('georef-source-select')).not.toBeInTheDocument();
    expect(screen.queryByTestId('georef-delete-source-btn')).not.toBeInTheDocument();
  });

  it('surfaces hard bind failures with error code and message', async () => {
    const artifact = {
      artifact_id: 'gart_hard_fail',
      plan_id: 'gplan_1',
      execution_id: 'gexec_hard_fail',
      job_id: '42',
      session_id: 'sess-g48-ui',
      format: 'GeoTIFF',
      cog_requested: true,
      cog_verified: true,
      cog_checks: {},
      target_crs: 'EPSG:3857',
      width: 340,
      height: 240,
      bands: 4,
      dtype: 'uint8',
      has_alpha: true,
      nodata_mode: 'band4_alpha',
      sha256: 'd'.repeat(64),
      byte_size: 4096,
      blob_key: 'georefbin-d',
      file_path: '/tmp/out-fail.tif',
      raster_ref: 'ref:raster_georef-fail',
      bounds_target_crs: [0, 0, 100, 100],
      bounds_wgs84: [120.1, 30.1, 120.2, 30.2],
      created_at: '2026-11-15T00:02:00Z',
    };
    const plan = makePlan({ revision: 1, latest_artifact: artifact });
    mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [makeSource()] });
    mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [plan] });
    mockBindGeorefLayer.mockResolvedValue({
      bound: false,
      partial_success: false,
      layer_id: null,
      mapspec_revision: null,
      current_mapspec_revision: null,
      error_code: 'BIND_FAILED',
      message: 'artifact missing',
      artifact,
      mapspec: null,
    });

    render(<GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.getByTestId('georef-bind-layer-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('georef-bind-layer-btn'));

    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith('BIND_FAILED: artifact missing', 'error'),
    );
    expect(screen.getByText('BIND_FAILED: artifact missing')).toBeInTheDocument();
  });

  it('writes n/a in the downloaded report when optional fit-metric fields are missing', async () => {
    const artifact = {
      artifact_id: 'gart_report',
      plan_id: 'gplan_1',
      execution_id: 'gexec_report',
      job_id: null,
      session_id: 'sess-g48-ui',
      format: 'GeoTIFF',
      cog_requested: true,
      cog_verified: true,
      cog_checks: {},
      target_crs: 'EPSG:3857',
      width: 340,
      height: 240,
      bands: 1,
      dtype: 'uint8',
      has_alpha: false,
      nodata_mode: 'none',
      sha256: 'e'.repeat(64),
      byte_size: 1024,
      blob_key: 'georefbin-e',
      file_path: '/tmp/out-report.tif',
      raster_ref: 'ref:raster_report',
      bounds_target_crs: [0, 0, 100, 100],
      bounds_wgs84: [120.1, 30.1, 120.2, 30.2],
      created_at: '2026-11-15T00:03:00Z',
    };
    const plan = makePlan({
      revision: 1,
      latest_artifact: artifact,
      fit_metrics: {
        status: 'valid',
        fit_count: 3,
        check_count: 0,
        disabled_count: 0,
        fit_rmse: 0.2,
        fit_max_error: 0.2,
        check_rmse: null,
        check_max_error: null,
        zero_residual_unverified: false,
        condition_number: 1.5,
        rank: 6,
        residuals: [],
        outlier_gcp_ids: [],
        warnings: [],
        blockers: [],
        // hull_coverage_ratio 与 coefficients.determinant 缺失（畸形/旧版回包）
        coefficients: {
          a0: 100, a1: 1, a2: 0, b0: 200, b1: 0, b2: -1,
          px_scale_x: 1, px_scale_y: 1, rotation_deg: 0, shear_deg: 0,
          is_reflection: false,
        },
      },
    });
    mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [makeSource()] });
    mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [plan] });
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report');
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      render(<GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />);
      await waitFor(() =>
        expect(screen.getByTestId('georef-download-report-btn')).toBeInTheDocument(),
      );

      fireEvent.click(screen.getByTestId('georef-download-report-btn'));

      expect(createObjectURL).toHaveBeenCalledTimes(1);
      const blob = createObjectURL.mock.calls[0][0] as Blob;
      const md = new TextDecoder().decode(await blob.arrayBuffer());
      expect(md).toContain('**det(J)**: n/a');
      expect(md).toContain('**Convex Hull Coverage**: n/a');
    } finally {
      anchorClick.mockRestore();
      createObjectURL.mockRestore();
    }
  });

  it('detects WGS84 JSON imports when the first GCP row has no coordinates', async () => {
    const plan = makePlan({ revision: 1, gcps: [] });
    mockListGeorefSources.mockResolvedValue({ session_id: 'sess-g48-ui', sources: [makeSource()] });
    mockListGeorefPlans.mockResolvedValue({ session_id: 'sess-g48-ui', plans: [plan] });
    mockImportGeorefGcps.mockResolvedValue({ plan });

    const { container } = render(
      <GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />,
    );
    await waitFor(() => expect(screen.getByTestId('georef-execute-btn')).toBeInTheDocument());

    const jsonInput = container.querySelector('input[accept=".json"]') as HTMLInputElement;
    const content = JSON.stringify({
      gcps: [
        { px: 0, py: 0, map_x: null, map_y: null, wgs84_lon: null, wgs84_lat: null },
        { px: 10, py: 10, wgs84_lon: 120.1, wgs84_lat: 30.2 },
      ],
    });
    const jsonFile = new File([content], 'gcps.json', { type: 'application/json' });
    Object.defineProperty(jsonFile, 'text', { value: async () => content });
    fireEvent.change(jsonInput, { target: { files: [jsonFile] } });

    await waitFor(() => expect(mockImportGeorefGcps).toHaveBeenCalledTimes(1));
    expect(mockImportGeorefGcps.mock.calls[0][2]).toMatchObject({ coords_are_wgs84: true });
  });

  it('deletes a just-uploaded source when the session changes before the upload resolves', async () => {
    let resolveUpload: ((value: { source: ReturnType<typeof makeSource>; image_url: string }) => void) | undefined;
    mockUploadGeorefSource.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );

    const { rerender } = render(
      <GeoreferencingWorkbenchPanel sessionId="sess-g48-ui" ownerToken="tok-1" />,
    );
    await waitFor(() => expect(mockListGeorefSources).toHaveBeenCalled());

    const input = screen.getByTestId('georef-upload-input') as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], 'scan.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(mockUploadGeorefSource).toHaveBeenCalledTimes(1));

    rerender(<GeoreferencingWorkbenchPanel sessionId="sess-other" ownerToken="tok-1" />);
    await waitFor(() => expect(mockListGeorefSources).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveUpload?.({ source: makeSource(), image_url: '/api/v1/georeferencing/sources/gsrc_1/image' });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    await waitFor(() =>
      expect(mockDeleteGeorefSource).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: 'sess-g48-ui' }),
        'gsrc_1',
      ),
    );
    expect(mockCreateGeorefPlan).not.toHaveBeenCalled();
  });
});
