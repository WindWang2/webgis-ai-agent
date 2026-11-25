'use client';

/**
 * G48 控制点影像配准与可交付地理栅格工作台。
 *
 * 与后端 `/api/v1/georeferencing/*` 与 Pi 工具共享唯一领域契约；
 * 双视图拾取：左侧源图 canvas/SVG 精确亚像元拾取 + 右侧 MapLibre 地图点选经纬度；
 * 受保护图像与 GeoTIFF 下载均经由 unified transport (`apiFetchBlob` / `downloadWithAuth`)。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type MouseEvent as ReactMouseEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react';
import { useToastStore } from '@/components/ui/toast';
import { useT } from '@/lib/i18n/useT';
import {
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { useSourceViewTransform } from '@/lib/georeferencing/use-source-view-transform';
import { subscribeMapClick } from '@/lib/raster-authoring/map-click';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  asGeorefError,
  bindGeorefLayer,
  cancelGeorefPlan,
  createGeorefPlan,
  deleteGeorefSource,
  downloadGeorefArtifact,
  downloadGeorefGcps,
  executeGeorefPlan,
  fetchProtectedImageUrl,
  importGeorefGcps,
  listGeorefPlans,
  listGeorefSources,
  pollGeorefStatus,
  previewGeorefPlan,
  previewImagePath,
  saveGeorefDraft,
  sourceImagePath,
  toEditableRows,
  uploadGeorefSource,
  type EditableGcpRow,
  type GcpInputItem,
  type GeorefCtx,
  type GeorefDraftPlan,
  type GeorefJobStatusResponse,
  type GeorefSourceRecord,
  type PointResidual,
} from '@/lib/georeferencing/api';
import {
  computeFitTransform,
  containerToImagePixel,
  effectiveScale,
  imagePixelToContainer,
  residualArrowEnd,
  zoomAroundPoint,
} from '@/lib/georeferencing/coords';

export interface GeoreferencingWorkbenchPanelProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

const VIEWER_WIDTH = 340;
const VIEWER_HEIGHT = 240;

const CRS_PRESETS = [
  'EPSG:3857',
  'EPSG:32648',
  'EPSG:32649',
  'EPSG:32650',
  'EPSG:32651',
];

function newEmptyGcp(index: number): EditableGcpRow {
  const num = index + 1;
  return {
    gcp_id: `gcp_${num}_${Math.random().toString(36).slice(2, 6)}`,
    label: `P${num}`,
    px: null,
    py: null,
    wgs84_lon: null,
    wgs84_lat: null,
    map_x: null,
    map_y: null,
    role: 'fit',
    enabled: true,
    note: '',
  };
}

/**
 * 目标分辨率输入解析（R2 #2186）：空串 = 未设置（null）；「0」/负数/非法值
 * 显式判 invalid —— 旧代码把 0 走 falsy 分支当 null 发送且 clear=false，
 * 服务端静默保留旧分辨率。
 */
function parseTargetResolution(
  raw: string,
): { ok: true; value: number | null } | { ok: false; value: null } {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: null };
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n <= 0) return { ok: false, value: null };
  return { ok: true, value: n };
}

function toInputItems(rows: EditableGcpRow[]): GcpInputItem[] {
  const out: GcpInputItem[] = [];
  for (const r of rows) {
    if (r.px == null || r.py == null) continue;
    const hasLonLat = r.wgs84_lon != null && r.wgs84_lat != null;
    const hasXY = r.map_x != null && r.map_y != null;
    if (!hasLonLat && !hasXY) continue;
    out.push({
      gcp_id: r.gcp_id,
      label: r.label,
      px: r.px,
      py: r.py,
      wgs84_lon: hasLonLat ? r.wgs84_lon : null,
      wgs84_lat: hasLonLat ? r.wgs84_lat : null,
      map_x: !hasLonLat && hasXY ? r.map_x : null,
      map_y: !hasLonLat && hasXY ? r.map_y : null,
      role: r.role,
      enabled: r.enabled,
      note: r.note || '',
    });
  }
  return out;
}

function buildMarkdownReport(
  source: GeorefSourceRecord | null,
  plan: GeorefDraftPlan,
): string {
  const d = plan.fit_metrics;
  const art = plan.latest_artifact;
  const lines: string[] = [
    `# Georeferencing Verification Report — ${plan.name || plan.plan_id}`,
    '',
    `- **Plan ID**: \`${plan.plan_id}\` (revision \`${plan.revision}\`)`,
    `- **Source**: \`${source?.filename ?? plan.source_id}\` (${source ? `${source.width}x${source.height}, ${source.bands}b, ${source.dtype}` : 'n/a'})`,
    `- **Target CRS**: \`${plan.target_crs}\` | **Resampling**: \`${plan.resampling}\` | **COG**: \`${plan.generate_cog}\``,
    '',
    '## Affine Diagnostics',
    `- **Status**: \`${d?.status ?? 'unsolved'}\``,
    `- **Fit GCPs**: ${d?.fit_count ?? 0} | **Check GCPs**: ${d?.check_count ?? 0}`,
    `- **Fit RMSE (m)**: ${d?.fit_rmse != null ? `${d.fit_rmse.toFixed(4)} m` : 'n/a'}`,
    `- **Check RMSE (m)**: ${d?.check_rmse != null ? `${d.check_rmse.toFixed(4)} m` : 'n/a'}`,
    `- **Condition Number**: ${d?.condition_number != null ? d.condition_number.toExponential(3) : 'n/a'} | **det(J)**: ${d?.coefficients && d.coefficients.determinant != null ? d.coefficients.determinant.toExponential(4) : 'n/a'}`,
    `- **Convex Hull Coverage**: ${d?.hull_coverage_ratio != null ? `${(d.hull_coverage_ratio * 100).toFixed(1)}%` : 'n/a'}`,
  ];
  if (d?.warnings?.length) {
    lines.push('', '## Warnings');
    for (const w of d.warnings) lines.push(`- ${w}`);
  }
  if (d?.blockers?.length) {
    lines.push('', '## Blockers');
    for (const b of d.blockers) lines.push(`- ${b}`);
  }
  if (d?.residuals?.length) {
    lines.push('', '## Control Point Residuals', '| Label | Role | Enabled | px | py | Residual (m) |', '| --- | --- | --- | ---: | ---: | ---: |');
    for (const r of d.residuals) {
      lines.push(
        `| ${r.label} | ${r.role} | ${r.enabled} | ${r.px.toFixed(2)} | ${r.py.toFixed(2)} | ${r.radial_error != null ? r.radial_error.toFixed(4) : 'n/a'} |`,
      );
    }
  }
  if (art) {
    lines.push(
      '',
      '## Deliverable Artifact',
      `- **Artifact ID**: \`${art.artifact_id}\``,
      `- **Raster Ref**: \`${art.raster_ref}\``,
      `- **Dimensions**: ${art.width}x${art.height} (${art.bands} bands, ${art.dtype})`,
      `- **SHA256**: \`${art.sha256}\``,
      `- **COG Verified**: \`${art.cog_verified}\` | **Has Alpha**: \`${art.has_alpha}\``,
      `- **WGS84 Bounds**: \`${JSON.stringify(art.bounds_wgs84)}\``,
    );
  }
  return `${lines.join('\n')}\n`;
}

function detectImportedCoordsAreWgs84(fmt: 'json' | 'csv', content: string): boolean {
  const trimmed = content.replace(/^\uFEFF/, '').trim();
  if (!trimmed) return false;
  if (fmt === 'json') {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      const rows = Array.isArray(parsed)
        ? parsed
        : parsed && typeof parsed === 'object' && Array.isArray((parsed as { gcps?: unknown }).gcps)
          ? (parsed as { gcps: unknown[] }).gcps
          : [];
      const hasProjCoords = (row: Record<string, unknown>) =>
        'map_x' in row && 'map_y' in row && row.map_x != null && row.map_y != null;
      const hasWgsCoords = (row: Record<string, unknown>) =>
        ('wgs84_lon' in row &&
          'wgs84_lat' in row &&
          row.wgs84_lon != null &&
          row.wgs84_lat != null) ||
        ('lon' in row && 'lat' in row && row.lon != null && row.lat != null);
      // 只看首行会误判：首行可能只有 px/py，坐标字段在后续行。
      const first = rows.find(
        (r): r is Record<string, unknown> =>
          Boolean(r && typeof r === 'object') &&
          (hasProjCoords(r as Record<string, unknown>) ||
            hasWgsCoords(r as Record<string, unknown>)),
      );
      if (!first) return false;
      if (hasProjCoords(first)) return false;
      return hasWgsCoords(first);
    } catch {
      return false;
    }
  }
  const headerLine = trimmed.split(/\r?\n/, 1)[0] ?? '';
  const cols = new Set(
    headerLine
      .split(',')
      .map((c) => c.trim().replace(/^"|"$/g, '').toLowerCase()),
  );
  const hasProj = cols.has('map_x') && cols.has('map_y');
  const hasWgs =
    (cols.has('wgs84_lon') && cols.has('wgs84_lat')) || (cols.has('lon') && cols.has('lat'));
  return hasWgs && !hasProj;
}

export function GeoreferencingWorkbenchPanel({
  sessionId,
  ownerToken = null,
}: GeoreferencingWorkbenchPanelProps) {
  const t = useT('georeferencing');
  const addToast = useToastStore((s) => s.addToast);
  const activeMapTool = useHudStore((s) => s.activeMapTool);
  const setActiveMapTool = useHudStore((s) => s.setActiveMapTool);

  const [sources, setSources] = useState<GeorefSourceRecord[]>([]);
  const [plans, setPlans] = useState<GeorefDraftPlan[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState<string>('');
  const [activePlan, setActivePlan] = useState<GeorefDraftPlan | null>(null);

  const [uploading, setUploading] = useState(false);
  const [ignoreExif, setIgnoreExif] = useState(false);
  const [pendingRetryFile, setPendingRetryFile] = useState<File | null>(null);
  const [showIgnoreExifRetry, setShowIgnoreExifRetry] = useState(false);

  // Editable draft state
  const [targetCrs, setTargetCrs] = useState('EPSG:3857');
  const [resampling, setResampling] = useState<'bilinear' | 'nearest'>('bilinear');
  const [targetResM, setTargetResM] = useState('');
  const [buildCog, setBuildCog] = useState(true);
  const [gcps, setGcps] = useState<EditableGcpRow[]>([]);
  const [undoStack, setUndoStack] = useState<EditableGcpRow[][]>([]);
  const [dirty, setDirty] = useState(false);
  const [selectedGcpId, setSelectedGcpId] = useState<string | null>(null);

  // Viewer & media ObjectURLs (fetched via apiFetchBlob with Bearer/X-Session-Token)
  const [sourceImgUrl, setSourceImgUrl] = useState<string | null>(null);
  const [previewImgUrl, setPreviewImgUrl] = useState<string | null>(null);
  const [previewDims, setPreviewDims] = useState<{ width: number; height: number } | null>(null);
  const [panMode, setPanMode] = useState(false);
  const dragRef = useRef<{
    active: boolean;
    moved: boolean;
    startX: number;
    startY: number;
    panX: number;
    panY: number;
  }>({ active: false, moved: false, startX: 0, startY: 0, panX: 0, panY: 0 });

  // Busy & status states
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [binding, setBinding] = useState(false);
  const [jobStatus, setJobStatus] = useState<GeorefJobStatusResponse | null>(null);
  const [bindMessage, setBindMessage] = useState<string | null>(null);
  const [partialBindRev, setPartialBindRev] = useState<number | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryCounterRef = useRef<Map<string, number>>(new Map());
  const importJsonRef = useRef<HTMLInputElement | null>(null);
  const importCsvRef = useRef<HTMLInputElement | null>(null);
  const mutationEpochRef = useRef(0);
  /**
   * 会话/源切换代次（R2 #2186）：resetSessionUiState 与切源时 +1；上传/
   * 保存/预览/执行/取消/导入/绑定/轮询的 await 之后若与本代次不符即丢弃，
   * 旧会话（源）的响应不得污染当前工作台状态或重启轮询。
   */
  const sessionUiEpochRef = useRef(0);
  const deletedSourceIdsRef = useRef<Set<string>>(new Set());
  /** 未保存编辑标记镜像：轮询回包不得覆盖用户进行中的 GCP/配置编辑。 */
  const dirtyRef = useRef(false);
  const tRef = useRef(t);
  tRef.current = t;
  const addToastRef = useRef(addToast);
  addToastRef.current = addToast;

  const ctx = useMemo<GeorefCtx | null>(
    () => (sessionId ? { sessionId, ownerToken } : null),
    [sessionId, ownerToken],
  );

  const selectedSource = useMemo(
    () => sources.find((s) => s.source_id === (activePlan?.source_id || selectedSourceId)) ?? null,
    [sources, activePlan, selectedSourceId],
  );
  const [transform, setTransform] = useSourceViewTransform(selectedSource, sessionId ?? null, {
    width: VIEWER_WIDTH, height: VIEWER_HEIGHT,
  });

  const applyPlanToEditor = useCallback((plan: GeorefDraftPlan, preserveLocalEdits = false) => {
    setActivePlan(plan);
    setSelectedSourceId(plan.source_id);
    // 作业轮询期间若用户有未保存编辑，只刷新 activePlan（诊断/产物），
    // 不回写编辑器，避免静默覆盖 GCP 行并清空撤销栈。
    if (preserveLocalEdits) return;
    setTargetCrs(plan.target_crs);
    setResampling(plan.resampling);
    setTargetResM(plan.target_resolution_m != null ? String(plan.target_resolution_m) : '');
    setBuildCog(plan.generate_cog);
    const rows = toEditableRows(plan.gcps);
    setGcps(rows);
    setUndoStack([]);
    setDirty(false);
    if (rows.length > 0) {
      setSelectedGcpId((prev) =>
        prev && rows.some((g) => g.gcp_id === prev) ? prev : rows[0].gcp_id,
      );
    } else {
      setSelectedGcpId(null);
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (planId: string) => {
      if (!ctx) return;
      stopPolling();
      const epoch = sessionUiEpochRef.current;
      const pollOnce = async () => {
        if (epoch !== sessionUiEpochRef.current) { stopPolling(); return; }
        try {
          const st = await pollGeorefStatus(ctx, planId);
          if (epoch !== sessionUiEpochRef.current) { stopPolling(); return; }
          setJobStatus(st);
          applyPlanToEditor(st.plan, dirtyRef.current);
          if (!['pending', 'queued', 'running', 'cancelling'].includes(st.status)) {
            stopPolling();
            setExecuting(false);
            if (st.status === 'completed') {
              addToastRef.current(tRef.current('jobSucceeded'), 'success');
            } else if (st.status === 'failed' || st.status === 'cancelled') {
              const rkey = `${st.plan.plan_id}:r${st.plan.revision}`;
              retryCounterRef.current.set(rkey, (retryCounterRef.current.get(rkey) ?? 0) + 1);
              addToastRef.current(
                st.error_message || st.status,
                st.status === 'cancelled' ? 'info' : 'error',
              );
            }
          }
        } catch (err) {
          if (epoch !== sessionUiEpochRef.current) return;
          stopPolling();
          setExecuting(false);
          // 轮询失败必须同时清掉旧 running 状态，否则 isJobRunning 恒真、
          // 面板永久卡在「运行中」且进度不再更新。
          setJobStatus(null);
          addToastRef.current(asGeorefError(err).message, 'error');
        }
      };
      void pollOnce();
      pollTimerRef.current = setInterval(() => {
        void pollOnce();
      }, 650);
    },
    [ctx, stopPolling, applyPlanToEditor],
  );
  const startPollingRef = useRef(startPolling);
  startPollingRef.current = startPolling;

  const resetSessionUiState = useCallback(() => {
    sessionUiEpochRef.current += 1;
    stopPolling();
    setSources([]);
    setPlans([]);
    setSelectedSourceId('');
    setActivePlan(null);
    setGcps([]);
    setUndoStack([]);
    setDirty(false);
    setSelectedGcpId(null);
    setJobStatus(null);
    setExecuting(false);
    setSourceImgUrl(null);
    setPreviewImgUrl(null);
    setPreviewDims(null);
    setBindMessage(null);
    setPartialBindRev(null);
    setPendingRetryFile(null);
    setShowIgnoreExifRetry(false);
    setIgnoreExif(false);
    setTargetCrs('EPSG:3857');
    setResampling('bilinear');
    setTargetResM('');
    setBuildCog(true);
    retryCounterRef.current.clear();
    deletedSourceIdsRef.current.clear();
    if (useHudStore.getState().activeMapTool === 'georef_pick_map') {
      useHudStore.getState().setActiveMapTool(null);
    }
  }, [stopPolling]);

  // Initial load & reload persistence recovery
  useEffect(() => {
    resetSessionUiState();
    if (!ctx) {
      return;
    }
    let cancelled = false;
    const loadEpoch = mutationEpochRef.current;
    (async () => {
      try {
        const [srcRes, planRes] = await Promise.all([
          listGeorefSources(ctx),
          listGeorefPlans(ctx),
        ]);
        if (cancelled) return;
        const deleted = deletedSourceIdsRef.current;
        if (mutationEpochRef.current !== loadEpoch) {
          // Merge server items created before the concurrent local mutation without clobbering user edits.
          setSources((prev) => {
            const seen = new Set(prev.map((s) => s.source_id));
            const older = srcRes.sources.filter(
              (s) => !deleted.has(s.source_id) && !seen.has(s.source_id),
            );
            return [...older, ...prev];
          });
          setPlans((prev) => {
            const seen = new Set(prev.map((p) => p.plan_id));
            const older = planRes.plans.filter(
              (p) => !deleted.has(p.source_id) && !seen.has(p.plan_id),
            );
            return [...older, ...prev];
          });
          return;
        }
        setSources(srcRes.sources);
        setPlans(planRes.plans);
        if (planRes.plans.length > 0) {
          const latest = planRes.plans[planRes.plans.length - 1];
          applyPlanToEditor(latest);
          if (latest.active_job_id) {
            setExecuting(true);
            startPollingRef.current(latest.plan_id);
          }
        } else if (srcRes.sources.length > 0) {
          setSelectedSourceId(srcRes.sources[srcRes.sources.length - 1].source_id);
        }
      } catch (err) {
        if (!cancelled) addToastRef.current(asGeorefError(err).message, 'error');
      }
    })();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [ctx, applyPlanToEditor, stopPolling, resetSessionUiState]);

  // End canvas panning cleanly if mouseup or blur occurs outside the canvas element
  useEffect(() => {
    const endDrag = () => {
      dragRef.current.active = false;
      dragRef.current.moved = false;
    };
    window.addEventListener('mouseup', endDrag);
    window.addEventListener('blur', endDrag);
    return () => {
      window.removeEventListener('mouseup', endDrag);
      window.removeEventListener('blur', endDrag);
    };
  }, []);

  // Turn off map pick tool cleanly on unmount
  useEffect(() => () => {
    if (useHudStore.getState().activeMapTool === 'georef_pick_map') {
      useHudStore.getState().setActiveMapTool(null);
    }
    stopPolling();
  }, [stopPolling]);

  const selectedSourceIdKey = selectedSource?.source_id ?? null;

  // Load protected normalized source image ObjectURL
  useEffect(() => {
    if (!ctx || !selectedSourceIdKey) {
      setSourceImgUrl(null);
      return;
    }
    const controller = new AbortController();
    let created: string | null = null;
    fetchProtectedImageUrl(
      sourceImagePath(ctx.sessionId, selectedSourceIdKey),
      ctx.ownerToken,
      controller.signal,
    )
      .then((url) => {
        if (controller.signal.aborted) {
          URL.revokeObjectURL(url);
          return;
        }
        created = url;
        setSourceImgUrl(url);
      })
      .catch(() => {
        if (!controller.signal.aborted) setSourceImgUrl(null);
      });
    return () => {
      controller.abort();
      if (created) URL.revokeObjectURL(created);
    };
  }, [ctx, selectedSourceIdKey]);

  const activePlanIdKey = activePlan?.plan_id ?? null;
  const activePreviewPath = activePlan?.preview_path ?? null;
  const activePreviewDigest = activePlan?.preview_digest ?? null;
  const activePreviewStale = Boolean(activePlan?.preview_stale);

  // Load protected warped preview image ObjectURL when preview is up to date
  useEffect(() => {
    if (!ctx || !activePlanIdKey || !activePreviewPath || activePreviewStale) {
      setPreviewImgUrl(null);
      return;
    }
    const controller = new AbortController();
    let created: string | null = null;
    fetchProtectedImageUrl(
      previewImagePath(ctx.sessionId, activePlanIdKey, activePreviewDigest),
      ctx.ownerToken,
      controller.signal,
    )
      .then((url) => {
        if (controller.signal.aborted) {
          URL.revokeObjectURL(url);
          return;
        }
        created = url;
        setPreviewImgUrl(url);
      })
      .catch(() => {
        if (!controller.signal.aborted) setPreviewImgUrl(null);
      });
    return () => {
      controller.abort();
      if (created) URL.revokeObjectURL(created);
    };
  }, [ctx, activePlanIdKey, activePreviewPath, activePreviewDigest, activePreviewStale]);

  /**
   * gcps 的 ref 镜像（R2 #2186）：mutateGcps 需要同步拿到 prev 以压撤销
   * 快照，且 updater 必须保持纯函数 —— React 18 StrictMode 会双调用
   * setState updater，旧的「在 setGcps updater 内 setUndoStack」每次编辑
   * 压两条快照。
   */
  const gcpsRef = useRef<EditableGcpRow[]>([]);
  useEffect(() => { gcpsRef.current = gcps; }, [gcps]);
  useEffect(() => { dirtyRef.current = dirty; }, [dirty]);

  const mutateGcps = useCallback((updater: (prev: EditableGcpRow[]) => EditableGcpRow[]) => {
    const prev = gcpsRef.current;
    const next = updater(prev);
    gcpsRef.current = next;
    setGcps(next);
    setUndoStack((hist) => [...hist.slice(-24), prev]);
    setDirty(true);
  }, []);

  // Subscribe to MapLibre clicks while activeMapTool === 'georef_pick_map'
  useEffect(() => {
    if (activeMapTool !== 'georef_pick_map') return;
    return subscribeMapClick(([lng, lat]) => {
      const lon = Number(lng.toFixed(6));
      const la = Number(lat.toFixed(6));
      const prev = gcpsRef.current;
      if (prev.length === 0) {
        // 选中副作用移出 updater（StrictMode 双调用下不得留在纯 updater 内）。
        const g = newEmptyGcp(0);
        g.wgs84_lon = lon;
        g.wgs84_lat = la;
        mutateGcps(() => [g]);
        setSelectedGcpId(g.gcp_id);
        return;
      }
      const targetId = selectedGcpId ?? prev[prev.length - 1].gcp_id;
      mutateGcps((rows) =>
        rows.map((g) =>
          g.gcp_id === targetId
            ? { ...g, wgs84_lon: lon, wgs84_lat: la, map_x: null, map_y: null }
            : g,
        ),
      );
    });
  }, [activeMapTool, selectedGcpId, mutateGcps]);

  const handleUpload = async (file: File, forceIgnoreExif: boolean) => {
    if (!ctx) {
      addToast(t('noSession'), 'error');
      return;
    }
    mutationEpochRef.current += 1;
    const epoch = sessionUiEpochRef.current;
    // R2 #2186：分辨率输入「0」/非法值不再静默当 null 处理（null 且
    // clear=false 会保留服务端旧分辨率）——显式拒绝并提示。
    const parsedTargetRes = parseTargetResolution(targetResM);
    if (!parsedTargetRes.ok) {
      addToast(t('invalidTargetResM'), 'error');
      return;
    }
    setUploading(true);
    setShowIgnoreExifRetry(false);
    try {
      const res = await uploadGeorefSource(ctx, file, forceIgnoreExif);
      if (epoch !== sessionUiEpochRef.current) {
        // 代次已变：清理刚上传的源，避免服务端残留孤立文件。
        try {
          await deleteGeorefSource(ctx, res.source.source_id);
        } catch {
          /* best-effort cleanup */
        }
        return;
      }
      let created: { plan: GeorefDraftPlan } | null = null;
      try {
        created = await createGeorefPlan(ctx, {
          session_id: ctx.sessionId,
          source_id: res.source.source_id,
          name: file.name,
          target_crs: targetCrs,
          resampling,
          target_resolution_m: parsedTargetRes.value,
          generate_cog: buildCog,
        });
      } finally {
        deletedSourceIdsRef.current.delete(res.source.source_id);
        setPendingRetryFile(null);
        setSources((prev) => [
          ...prev.filter((s) => s.source_id !== res.source.source_id),
          res.source,
        ]);
        setSelectedSourceId(res.source.source_id);
      }
      if (epoch !== sessionUiEpochRef.current) return;
      if (created) {
        const createdPlan = created.plan;
        setPlans((prev) => [
          ...prev.filter((p) => p.plan_id !== createdPlan.plan_id),
          createdPlan,
        ]);
        applyPlanToEditor(createdPlan);
      }
      setJobStatus(null);
      setBindMessage(null);
      setPartialBindRev(null);
    } catch (err) {
      if (epoch !== sessionUiEpochRef.current) return;
      const info = asGeorefError(err);
      if (info.code === 'SOURCE_EXIF_FAILED') {
        setPendingRetryFile(file);
        setShowIgnoreExifRetry(true);
      }
      addToast(`${info.code}: ${info.message}`, 'error');
    } finally {
      setUploading(false);
    }
  };

  const onFileInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (file) void handleUpload(file, ignoreExif);
  };

  const handleDeleteSource = async () => {
    if (!ctx || !selectedSource) return;
    const deletedSid = selectedSource.source_id;
    const epoch = sessionUiEpochRef.current;
    mutationEpochRef.current += 1;
    deletedSourceIdsRef.current.add(deletedSid);
    try {
      await deleteGeorefSource(ctx, deletedSid);
      if (epoch !== sessionUiEpochRef.current) return;
      const remSources = sources.filter((s) => s.source_id !== deletedSid);
      const remPlans = plans.filter((p) => p.source_id !== deletedSid);
      setSources(remSources);
      setPlans(remPlans);
      if (activePlan?.source_id === deletedSid || selectedSourceId === deletedSid) {
        stopPolling();
        setExecuting(false);
        setJobStatus(null);
        setPreviewDims(null);
        setBindMessage(null);
        setPartialBindRev(null);
        if (remPlans.length > 0) {
          applyPlanToEditor(remPlans[remPlans.length - 1]);
        } else {
          setActivePlan(null);
          setSelectedSourceId(remSources[0]?.source_id ?? '');
          setGcps([]);
          setUndoStack([]);
          setDirty(false);
          setSelectedGcpId(null);
          setSourceImgUrl(null);
          setPreviewImgUrl(null);
        }
      }
    } catch (err) {
      deletedSourceIdsRef.current.delete(deletedSid);
      if (epoch !== sessionUiEpochRef.current) return;
      addToast(asGeorefError(err).message, 'error');
    }
  };

  const handleSaveDraft = useCallback(async (): Promise<GeorefDraftPlan | null> => {
    if (!ctx || !selectedSource) return null;
    mutationEpochRef.current += 1;
    const epoch = sessionUiEpochRef.current;
    setSaving(true);
    try {
      // R2 #2186：「0」/非法分辨率显式拒绝（旧代码 parsedRes=0 走 falsy 分支
      // 发送 null + clear=false，静默保留服务端旧分辨率）。
      const parsed = parseTargetResolution(targetResM);
      if (!parsed.ok) {
        addToast(t('invalidTargetResM'), 'error');
        return null;
      }
      const parsedRes = parsed.value;
      let plan = activePlan;
      if (!plan) {
        const res = await createGeorefPlan(ctx, {
          session_id: ctx.sessionId,
          source_id: selectedSource.source_id,
          name: selectedSource.filename,
          target_crs: targetCrs.trim(),
          resampling,
          target_resolution_m: parsedRes,
          generate_cog: buildCog,
        });
        if (epoch !== sessionUiEpochRef.current) return null;
        plan = res.plan;
        setPlans((prev) => [...prev, plan!]);
      }
      const res = await saveGeorefDraft(ctx, plan.plan_id, {
        expected_revision: plan.revision,
        target_crs: targetCrs.trim(),
        resampling,
        target_resolution_m: parsedRes,
        clear_target_resolution: parsedRes == null,
        generate_cog: buildCog,
        gcps: toInputItems(gcps),
      });
      if (epoch !== sessionUiEpochRef.current) return null;
      setPlans((prev) => prev.map((p) => (p.plan_id === res.plan.plan_id ? res.plan : p)));
      applyPlanToEditor(res.plan);
      return res.plan;
    } catch (err) {
      if (epoch !== sessionUiEpochRef.current) return null;
      const info = asGeorefError(err);
      addToast(`${info.code}: ${info.message}`, 'error');
      return null;
    } finally {
      setSaving(false);
    }
  }, [
    ctx,
    selectedSource,
    activePlan,
    targetCrs,
    resampling,
    targetResM,
    buildCog,
    gcps,
    applyPlanToEditor,
    addToast,
    t,
  ]);

  const handleUndo = () => {
    if (undoStack.length === 0) return;
    const prev = undoStack[undoStack.length - 1];
    setUndoStack((hist) => hist.slice(0, -1));
    setGcps(prev);
    setDirty(true);
  };

  const handleCanvasWheel = (e: ReactWheelEvent<HTMLDivElement>) => {
    if (!selectedSource) return;
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const anchorX = e.clientX - rect.left;
    const anchorY = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.25 : 0.8;
    setTransform((prev) => zoomAroundPoint(prev, prev.zoom * factor, anchorX, anchorY));
  };

  const handleCanvasMouseDown = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (!selectedSource) return;
    if (panMode || e.button === 1 || e.shiftKey) {
      dragRef.current = {
        active: true,
        moved: false,
        startX: e.clientX,
        startY: e.clientY,
        panX: transform.panX,
        panY: transform.panY,
      };
    }
  };

  const handleCanvasMouseMove = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (!dragRef.current.active) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (Math.hypot(dx, dy) > 3) dragRef.current.moved = true;
    setTransform((prev) => ({
      ...prev,
      panX: dragRef.current.panX + dx,
      panY: dragRef.current.panY + dy,
    }));
  };

  const handleCanvasMouseUp = (e: ReactMouseEvent<HTMLDivElement>) => {
    const wasMoved = dragRef.current.moved;
    const wasDragging = dragRef.current.active;
    dragRef.current.active = false;
    dragRef.current.moved = false;
    if (wasMoved || (panMode && wasDragging) || e.button !== 0 || e.shiftKey || !selectedSource) {
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const picked = containerToImagePixel(cx, cy, transform, {
      width: selectedSource.width,
      height: selectedSource.height,
    });
    if (!picked.inside) return;

    const prev = gcpsRef.current;
    if (prev.length === 0) {
      const g = newEmptyGcp(0);
      g.px = picked.px;
      g.py = picked.py;
      mutateGcps(() => [g]);
      setSelectedGcpId(g.gcp_id);
      return;
    }
    const targetId = selectedGcpId ?? prev[prev.length - 1].gcp_id;
    mutateGcps((rows) =>
      rows.map((g) =>
        g.gcp_id === targetId ? { ...g, px: picked.px, py: picked.py } : g,
      ),
    );
  };

  const handleImportFile = async (fmt: 'json' | 'csv', e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !ctx || !activePlan) return;
    const epoch = sessionUiEpochRef.current;
    try {
      const content = await file.text();
      const coordsAreWgs84 = detectImportedCoordsAreWgs84(fmt, content);
      const res = await importGeorefGcps(ctx, activePlan.plan_id, {
        expected_revision: activePlan.revision,
        format: fmt,
        content,
        replace_existing: true,
        coords_are_wgs84: coordsAreWgs84,
      });
      if (epoch !== sessionUiEpochRef.current) return;
      applyPlanToEditor(res.plan);
      setPlans((prev) => prev.map((p) => (p.plan_id === res.plan.plan_id ? res.plan : p)));
    } catch (err) {
      addToast(asGeorefError(err).message, 'error');
    }
  };

  const handlePreview = async () => {
    if (!ctx) return;
    const epoch = sessionUiEpochRef.current;
    let plan = activePlan;
    if (!plan || dirty) {
      plan = await handleSaveDraft();
    }
    if (!plan || epoch !== sessionUiEpochRef.current) return;
    setPreviewing(true);
    try {
      const res = await previewGeorefPlan(ctx, plan.plan_id, plan.revision);
      if (epoch !== sessionUiEpochRef.current) return;
      applyPlanToEditor(res.plan);
      if (res.out_width && res.out_height) {
        setPreviewDims({ width: res.out_width, height: res.out_height });
      }
    } catch (err) {
      if (epoch !== sessionUiEpochRef.current) return;
      addToast(asGeorefError(err).message, 'error');
    } finally {
      setPreviewing(false);
    }
  };

  const handleExecute = async () => {
    if (!ctx) return;
    const epoch = sessionUiEpochRef.current;
    let plan = activePlan;
    if (!plan || dirty) {
      plan = await handleSaveDraft();
    }
    if (!plan || epoch !== sessionUiEpochRef.current) return;
    setExecuting(true);
    setBindMessage(null);
    setPartialBindRev(null);
    try {
      const rkey = `${plan.plan_id}:r${plan.revision}`;
      const retryN = retryCounterRef.current.get(rkey) ?? 0;
      const idempotencyKey =
        retryN > 0 ? `ui:${plan.plan_id}:r${plan.revision}:retry${retryN}` : `ui:${plan.plan_id}:r${plan.revision}`;
      const res = await executeGeorefPlan(ctx, plan.plan_id, {
        expected_revision: plan.revision,
        background: true,
        idempotency_key: idempotencyKey,
      });
      if (epoch !== sessionUiEpochRef.current) return;
      startPolling(plan.plan_id);
      if (!res.background && res.artifact) {
        const st = await pollGeorefStatus(ctx, plan.plan_id);
        if (epoch !== sessionUiEpochRef.current) return;
        setJobStatus(st);
        applyPlanToEditor(st.plan);
        setExecuting(false);
        stopPolling();
      }
    } catch (err) {
      if (epoch !== sessionUiEpochRef.current) return;
      setExecuting(false);
      addToast(asGeorefError(err).message, 'error');
    }
  };

  const handleCancel = async () => {
    if (!ctx || !activePlan) return;
    const epoch = sessionUiEpochRef.current;
    try {
      const st = await cancelGeorefPlan(ctx, activePlan.plan_id);
      if (epoch !== sessionUiEpochRef.current) return;
      setJobStatus(st);
      applyPlanToEditor(st.plan);
      if (st.status === 'completed' || st.status === 'failed' || st.status === 'cancelled') {
        setExecuting(false);
        stopPolling();
      } else {
        setExecuting(true);
        startPolling(activePlan.plan_id);
      }
    } catch (err) {
      if (epoch !== sessionUiEpochRef.current) return;
      addToast(asGeorefError(err).message, 'error');
    }
  };

  const handleBindLayer = async () => {
    if (!ctx || !activePlan?.latest_artifact) return;
    const epoch = sessionUiEpochRef.current;
    setBinding(true);
    setBindMessage(null);
    setPartialBindRev(null);
    try {
      const curRev = partialBindRev ?? getMapSpecSessionCursor().revision;
      const res = await bindGeorefLayer(ctx, activePlan.plan_id, {
        expected_mapspec_revision: curRev,
        layer_name: activePlan.name || selectedSource?.filename || activePlan.plan_id,
      });
      if (epoch !== sessionUiEpochRef.current) return;
      if (res.bound) {
        if (typeof res.mapspec_revision === 'number') {
          setMapSpecRevision(res.mapspec_revision);
        }
        if (res.mapspec) {
          commitMapSpecDocument(res.mapspec as never, res.mapspec_revision ?? undefined);
        }
        const msg = t('layerBound', {
          layerId: res.layer_id ?? '',
          rev: res.mapspec_revision ?? '',
        });
        setBindMessage(msg);
        addToast(msg, 'success');
      } else if (res.partial_success) {
        const nextRev = res.current_mapspec_revision ?? 0;
        setPartialBindRev(nextRev);
        const msg = t('partialBindWarning', { rev: nextRev });
        setBindMessage(msg);
        addToast(msg, 'warning');
      } else {
        // 既非 bound 也非 partial_success 的硬失败（带 error_code/message）：
        // 旧代码无 else 分支，服务端错误被静默吞掉。
        const msg =
          [res.error_code, res.message].filter(Boolean).join(': ') || t('jobFailed');
        setBindMessage(msg);
        addToast(msg, 'error');
      }
    } catch (err) {
      addToast(asGeorefError(err).message, 'error');
    } finally {
      setBinding(false);
    }
  };

  const handleDownloadReport = () => {
    if (!activePlan) return;
    const md = buildMarkdownReport(selectedSource, activePlan);
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `georef_report_${activePlan.plan_id}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const diag = activePlan?.fit_metrics ?? null;
  const outlierSet = useMemo(
    () => new Set(diag?.outlier_gcp_ids ?? []),
    [diag],
  );
  const residualById = useMemo(() => {
    const m = new Map<string, PointResidual>();
    for (const r of diag?.residuals ?? []) {
      m.set(r.gcp_id, r);
    }
    return m;
  }, [diag]);

  const scale = effectiveScale(transform);
  const pxScaleMeters = useMemo(() => {
    if (!diag?.coefficients) return 1;
    const sx = diag.coefficients.px_scale_x;
    const sy = diag.coefficients.px_scale_y;
    const avg = (sx + sy) * 0.5;
    return Number.isFinite(avg) && avg > 0 ? avg : 1;
  }, [diag]);

  const isJobRunning =
    executing ||
    ['pending', 'queued', 'running', 'cancelling'].includes(jobStatus?.status ?? '');

  if (!sessionId) {
    return (
      <section
        data-testid="georeferencing-workbench"
        className="border-t border-edge-subtle/60 px-3 py-3 text-xs text-ink-muted"
      >
        <h3 className="text-sm font-medium text-ink">{t('title')}</h3>
        <p className="mt-1">{t('noSession')}</p>
      </section>
    );
  }

  return (
    <section
      data-testid="georeferencing-workbench"
      aria-label={t('title')}
      className="border-t border-edge-subtle/60 px-3 py-3 space-y-3 text-xs"
    >
      <div>
        <h3 className="text-sm font-semibold text-ink">{t('title')}</h3>
        <p className="text-[11px] text-ink-muted">{t('subtitle')}</p>
      </div>

      {/* Intake & Source Selector */}
      <div className="space-y-1.5 rounded border border-edge-subtle/60 bg-surface p-2">
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex cursor-pointer items-center gap-1 rounded bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:opacity-90">
            <span>{t('uploadBtn')}</span>
            <input
              data-testid="georef-upload-input"
              type="file"
              accept=".png,.jpg,.jpeg,.tif,.tiff,image/png,image/jpeg,image/tiff"
              className="sr-only"
              disabled={uploading}
              onChange={onFileInputChange}
            />
          </label>
          <label className="flex items-center gap-1 text-[11px] text-ink-muted">
            <input
              data-testid="georef-ignore-exif-checkbox"
              type="checkbox"
              checked={ignoreExif}
              onChange={(e) => setIgnoreExif(e.target.checked)}
            />
            <span>{t('ignoreExifLabel')}</span>
          </label>
          {showIgnoreExifRetry && pendingRetryFile && (
            <button
              type="button"
              data-testid="georef-retry-ignore-exif-btn"
              onClick={() => void handleUpload(pendingRetryFile, true)}
              className="rounded border border-amber-500/60 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700"
            >
              {t('retryIgnoreExif')}
            </button>
          )}
        </div>

        {sources.length > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-ink-muted">{t('loadedSources')}:</span>
            <select
              data-testid="georef-source-select"
              aria-label={t('sourceSelectAria')}
              value={selectedSource?.source_id ?? ''}
              onChange={(e) => {
                const sid = e.target.value;
                // R2 #2186：切源先推进代次 —— 在飞的 pollOnce/handleUpload/
                // handleSaveDraft/handlePreview/handleExecute 回包此后即丢弃，
                // 不得覆盖新源的工作台状态或重启旧轮询。
                sessionUiEpochRef.current += 1;
                stopPolling();
                setExecuting(false);
                setSelectedSourceId(sid);
                setJobStatus(null);
                setBindMessage(null);
                setPartialBindRev(null);
                setPreviewDims(null);
                const match = plans.find((p) => p.source_id === sid);
                if (match) {
                  applyPlanToEditor(match);
                  if (match.active_job_id) {
                    setExecuting(true);
                    startPolling(match.plan_id);
                  }
                } else {
                  setActivePlan(null);
                  setGcps([]);
                  setUndoStack([]);
                  setDirty(false);
                  setSelectedGcpId(null);
                  setPreviewImgUrl(null);
                }
              }}
              className="min-w-0 flex-1 rounded border border-edge-subtle bg-surface-canvas px-1.5 py-0.5 text-[11px]"
            >
              {sources.map((s) => (
                <option key={s.source_id} value={s.source_id}>
                  {s.filename} ({s.width}×{s.height})
                </option>
              ))}
            </select>
            {selectedSource && (
              <button
                type="button"
                data-testid="georef-delete-source-btn"
                onClick={() => void handleDeleteSource()}
                className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px] text-status-critical hover:bg-status-critical/10"
              >
                {t('deleteSource')}
              </button>
            )}
          </div>
        )}

        {selectedSource && (
          <div className="space-y-1 text-[11px]" data-testid="georef-source-meta">
            <div className="text-ink-muted">
              {t('sourceMeta', {
                width: selectedSource.width,
                height: selectedSource.height,
                bands: selectedSource.bands,
                dtype: selectedSource.dtype,
              })}{' '}
              · SHA256 {selectedSource.normalized_sha256.slice(0, 10)}
            </div>
            {selectedSource.exif_orientation !== 1 && !selectedSource.exif_overridden && (
              <div className="rounded bg-sky-500/10 px-1.5 py-0.5 text-sky-700">
                {t('exifApplied', { orientation: selectedSource.exif_orientation })}
              </div>
            )}
            {selectedSource.existing_georef?.warnings?.map((warn, idx) => (
              <div
                key={idx}
                data-testid="georef-embedded-geo-warning"
                className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-800"
              >
                {t('embeddedGeoWarning')}: {warn}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Plan Configuration */}
      {selectedSource && (
        <div className="space-y-2 rounded border border-edge-subtle/60 bg-surface p-2">
          <div className="font-medium text-ink">{t('planSection')}</div>
          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-0.5">
              <span className="text-[11px] text-ink-muted">{t('targetCrs')}</span>
              <input
                data-testid="georef-target-crs-input"
                list="georef-crs-presets"
                value={targetCrs}
                onChange={(e) => {
                  setTargetCrs(e.target.value);
                  setDirty(true);
                }}
                className="w-full rounded border border-edge-subtle bg-surface-canvas px-1.5 py-1 text-[11px]"
              />
              <datalist id="georef-crs-presets">
                {CRS_PRESETS.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </label>
            <label className="space-y-0.5">
              <span className="text-[11px] text-ink-muted">{t('resampling')}</span>
              <select
                data-testid="georef-resampling-select"
                value={resampling}
                onChange={(e) => {
                  setResampling(e.target.value as 'bilinear' | 'nearest');
                  setDirty(true);
                }}
                className="w-full rounded border border-edge-subtle bg-surface-canvas px-1.5 py-1 text-[11px]"
              >
                <option value="bilinear">{t('resamplingBilinear')}</option>
                <option value="nearest">{t('resamplingNearest')}</option>
              </select>
            </label>
            <label className="space-y-0.5 col-span-2">
              <span className="text-[11px] text-ink-muted">{t('targetResM')}</span>
              <input
                data-testid="georef-target-res-input"
                type="number"
                step="any"
                min="0"
                placeholder="auto"
                value={targetResM}
                onChange={(e) => {
                  setTargetResM(e.target.value);
                  setDirty(true);
                }}
                className="w-full rounded border border-edge-subtle bg-surface-canvas px-1.5 py-1 text-[11px]"
              />
            </label>
          </div>
          <label className="flex items-center gap-1.5 text-[11px]">
            <input
              data-testid="georef-cog-checkbox"
              type="checkbox"
              checked={buildCog}
              onChange={(e) => {
                setBuildCog(e.target.checked);
                setDirty(true);
              }}
            />
            <span>{t('buildCog')}</span>
          </label>
        </div>
      )}

      {/* Dual-View Source Image Picker + SVG GCP & Residual Overlay */}
      {selectedSource && (
        <div className="space-y-2 rounded border border-edge-subtle/60 bg-surface p-2">
          <div className="flex flex-wrap items-center justify-between gap-1">
            <span className="font-medium text-ink">{t('viewerSection')}</span>
            <div className="flex flex-wrap gap-1">
              <button
                type="button"
                data-testid="georef-zoom-in-btn"
                onClick={() =>
                  setTransform((p) =>
                    zoomAroundPoint(p, p.zoom * 1.35, VIEWER_WIDTH / 2, VIEWER_HEIGHT / 2),
                  )
                }
                className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px]"
              >
                {t('zoomIn')}
              </button>
              <button
                type="button"
                data-testid="georef-zoom-out-btn"
                onClick={() =>
                  setTransform((p) =>
                    zoomAroundPoint(p, p.zoom / 1.35, VIEWER_WIDTH / 2, VIEWER_HEIGHT / 2),
                  )
                }
                className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px]"
              >
                {t('zoomOut')}
              </button>
              <button
                type="button"
                data-testid="georef-reset-view-btn"
                onClick={() =>
                  setTransform(
                    computeFitTransform(
                      { width: selectedSource.width, height: selectedSource.height },
                      { width: VIEWER_WIDTH, height: VIEWER_HEIGHT },
                    ),
                  )
                }
                className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px]"
              >
                {t('resetView')}
              </button>
              <button
                type="button"
                data-testid="georef-pan-toggle-btn"
                onClick={() => setPanMode((v) => !v)}
                className={`rounded border px-1.5 py-0.5 text-[11px] ${
                  panMode ? 'border-accent bg-accent/15 text-accent' : 'border-edge-subtle'
                }`}
              >
                {panMode ? t('panMode') : t('pickPixelActive')}
              </button>
            </div>
          </div>

          <div
            data-testid="georef-pixel-canvas"
            onWheel={handleCanvasWheel}
            onMouseDown={handleCanvasMouseDown}
            onMouseMove={handleCanvasMouseMove}
            onMouseUp={handleCanvasMouseUp}
            style={{ width: '100%', height: VIEWER_HEIGHT }}
            className={`relative overflow-hidden rounded border border-edge-subtle bg-neutral-900 select-none ${
              panMode ? 'cursor-grab' : 'cursor-crosshair'
            }`}
          >
            {sourceImgUrl && (
              // eslint-disable-next-line @next/next/no-img-element -- protected blob ObjectURL rendered at exact image-pixel transform
              <img
                src={sourceImgUrl}
                alt={selectedSource.filename}
                draggable={false}
                style={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  width: selectedSource.width,
                  height: selectedSource.height,
                  maxWidth: 'none',
                  maxHeight: 'none',
                  transformOrigin: '0 0',
                  transform: `translate(${transform.panX}px, ${transform.panY}px) scale(${scale})`,
                  imageRendering: scale > 3 ? 'pixelated' : 'auto',
                }}
              />
            )}
            <svg
              className="pointer-events-none absolute inset-0 h-full w-full"
              aria-hidden="true"
            >
              {gcps.map((g, idx) => {
                if (g.px == null || g.py == null) return null;
                const pos = imagePixelToContainer(g.px, g.py, transform);
                const res = residualById.get(g.gcp_id);
                const isOutlier = outlierSet.has(g.gcp_id);
                const isSelected = g.gcp_id === selectedGcpId;
                const color = !g.enabled
                  ? '#9ca3af'
                  : isOutlier
                    ? '#f43f5e'
                    : g.role === 'check'
                      ? '#f59e0b'
                      : '#10b981';
                const arrow =
                  res && res.dx != null && res.dy != null
                    ? residualArrowEnd(
                        pos.x,
                        pos.y,
                        res.dx / pxScaleMeters,
                        -res.dy / pxScaleMeters,
                        scale,
                      )
                    : null;
                return (
                  <g key={g.gcp_id}>
                    {arrow && (
                      <line
                        x1={pos.x}
                        y1={pos.y}
                        x2={arrow.x2}
                        y2={arrow.y2}
                        stroke={color}
                        strokeWidth={1.5}
                        strokeDasharray="3 2"
                      />
                    )}
                    <circle
                      cx={pos.x}
                      cy={pos.y}
                      r={isSelected ? 6 : 4.5}
                      fill="none"
                      stroke={color}
                      strokeWidth={isSelected ? 2.2 : 1.6}
                    />
                    <text
                      x={pos.x + 7}
                      y={pos.y - 4}
                      fontSize={10}
                      fill="#ffffff"
                      stroke="#111827"
                      strokeWidth={2.5}
                      paintOrder="stroke"
                    >
                      {g.label || `#${idx + 1}`}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>

          {/* Map pick + GCP Table toolbar */}
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              data-testid="georef-pick-map-btn"
              onClick={() =>
                setActiveMapTool(activeMapTool === 'georef_pick_map' ? null : 'georef_pick_map')
              }
              className={`rounded border px-2 py-0.5 text-[11px] font-medium ${
                activeMapTool === 'georef_pick_map'
                  ? 'border-accent bg-accent text-white'
                  : 'border-edge-subtle bg-surface-canvas text-ink'
              }`}
            >
              {activeMapTool === 'georef_pick_map' ? t('mapPickActive') : t('mapPickStart')}
            </button>
            <button
              type="button"
              data-testid="georef-add-gcp-btn"
              onClick={() => {
                const next = newEmptyGcp(gcpsRef.current.length);
                mutateGcps((prev) => [...prev, next]);
                setSelectedGcpId(next.gcp_id);
              }}
              className="rounded border border-edge-subtle px-2 py-0.5 text-[11px]"
            >
              {t('addGcp')}
            </button>
            <button
              type="button"
              data-testid="georef-undo-btn"
              disabled={undoStack.length === 0}
              onClick={handleUndo}
              className="rounded border border-edge-subtle px-2 py-0.5 text-[11px] disabled:opacity-40"
            >
              {t('undoGcp')}
            </button>
            {activePlan && (
              <>
                <button
                  type="button"
                  onClick={() => importJsonRef.current?.click()}
                  className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px]"
                >
                  {t('importJson')}
                </button>
                <button
                  type="button"
                  onClick={() => importCsvRef.current?.click()}
                  className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px]"
                >
                  {t('importCsv')}
                </button>
                <button
                  type="button"
                  onClick={() => ctx && void downloadGeorefGcps(ctx, activePlan.plan_id, 'json')}
                  className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px]"
                >
                  {t('exportJson')}
                </button>
                <button
                  type="button"
                  onClick={() => ctx && void downloadGeorefGcps(ctx, activePlan.plan_id, 'csv')}
                  className="rounded border border-edge-subtle px-1.5 py-0.5 text-[11px]"
                >
                  {t('exportCsv')}
                </button>
                <input
                  ref={importJsonRef}
                  type="file"
                  accept=".json"
                  className="sr-only"
                  onChange={(e) => void handleImportFile('json', e)}
                />
                <input
                  ref={importCsvRef}
                  type="file"
                  accept=".csv,.txt"
                  className="sr-only"
                  onChange={(e) => void handleImportFile('csv', e)}
                />
              </>
            )}
          </div>
          <p className="text-[11px] text-ink-muted">{t('selectRowHint')}</p>

          {/* GCP Pair Table */}
          <div className="max-h-48 overflow-auto rounded border border-edge-subtle/60">
            <table className="w-full border-collapse text-[11px]" data-testid="georef-gcp-table">
              <thead className="bg-surface-canvas text-ink-muted">
                <tr>
                  <th className="px-1 py-0.5 text-left">{t('colLabel')}</th>
                  <th className="px-1 py-0.5 text-right">{t('colPx')}</th>
                  <th className="px-1 py-0.5 text-right">{t('colPy')}</th>
                  <th className="px-1 py-0.5 text-right">{t('colLon')}</th>
                  <th className="px-1 py-0.5 text-right">{t('colLat')}</th>
                  <th className="px-1 py-0.5">{t('colRole')}</th>
                  <th className="px-1 py-0.5">{t('colEnabled')}</th>
                  <th className="px-1 py-0.5 text-right">{t('colResidual')}</th>
                  <th className="px-1 py-0.5">{t('colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {gcps.map((g, idx) => {
                  const res = residualById.get(g.gcp_id);
                  const selected = g.gcp_id === selectedGcpId;
                  return (
                    <tr
                      key={g.gcp_id}
                      data-testid={`georef-gcp-row-${idx}`}
                      onClick={() => setSelectedGcpId(g.gcp_id)}
                      className={`border-t border-edge-subtle/40 ${selected ? 'bg-accent/10' : ''}`}
                    >
                      <td className="px-1 py-0.5">
                        <input
                          value={g.label}
                          aria-label={`label-${idx}`}
                          onChange={(e) =>
                            mutateGcps((prev) =>
                              prev.map((x) =>
                                x.gcp_id === g.gcp_id ? { ...x, label: e.target.value } : x,
                              ),
                            )
                          }
                          className="w-10 rounded border border-edge-subtle/60 bg-surface-canvas px-1"
                        />
                      </td>
                      <td className="px-1 py-0.5">
                        <input
                          type="number"
                          step="any"
                          aria-label={`px-${idx}`}
                          value={g.px ?? ''}
                          onChange={(e) => {
                            const v = e.target.value === '' ? null : Number(e.target.value);
                            mutateGcps((prev) =>
                              prev.map((x) => (x.gcp_id === g.gcp_id ? { ...x, px: v } : x)),
                            );
                          }}
                          className="w-14 rounded border border-edge-subtle/60 bg-surface-canvas px-1 text-right font-mono"
                        />
                      </td>
                      <td className="px-1 py-0.5">
                        <input
                          type="number"
                          step="any"
                          aria-label={`py-${idx}`}
                          value={g.py ?? ''}
                          onChange={(e) => {
                            const v = e.target.value === '' ? null : Number(e.target.value);
                            mutateGcps((prev) =>
                              prev.map((x) => (x.gcp_id === g.gcp_id ? { ...x, py: v } : x)),
                            );
                          }}
                          className="w-14 rounded border border-edge-subtle/60 bg-surface-canvas px-1 text-right font-mono"
                        />
                      </td>
                      <td className="px-1 py-0.5">
                        <input
                          type="number"
                          step="any"
                          aria-label={`lon-${idx}`}
                          value={g.wgs84_lon ?? ''}
                          onChange={(e) => {
                            const v = e.target.value === '' ? null : Number(e.target.value);
                            mutateGcps((prev) =>
                              prev.map((x) =>
                                x.gcp_id === g.gcp_id
                                  ? { ...x, wgs84_lon: v, map_x: null, map_y: null }
                                  : x,
                              ),
                            );
                          }}
                          className="w-16 rounded border border-edge-subtle/60 bg-surface-canvas px-1 text-right font-mono"
                        />
                      </td>
                      <td className="px-1 py-0.5">
                        <input
                          type="number"
                          step="any"
                          aria-label={`lat-${idx}`}
                          value={g.wgs84_lat ?? ''}
                          onChange={(e) => {
                            const v = e.target.value === '' ? null : Number(e.target.value);
                            mutateGcps((prev) =>
                              prev.map((x) =>
                                x.gcp_id === g.gcp_id
                                  ? { ...x, wgs84_lat: v, map_x: null, map_y: null }
                                  : x,
                              ),
                            );
                          }}
                          className="w-16 rounded border border-edge-subtle/60 bg-surface-canvas px-1 text-right font-mono"
                        />
                      </td>
                      <td className="px-1 py-0.5">
                        <select
                          aria-label={`role-${idx}`}
                          value={g.role}
                          onChange={(e) =>
                            mutateGcps((prev) =>
                              prev.map((x) =>
                                x.gcp_id === g.gcp_id
                                  ? { ...x, role: e.target.value as 'fit' | 'check' }
                                  : x,
                              ),
                            )
                          }
                          className="rounded border border-edge-subtle/60 bg-surface-canvas px-0.5"
                        >
                          <option value="fit">{t('roleFit')}</option>
                          <option value="check">{t('roleCheck')}</option>
                        </select>
                      </td>
                      <td className="px-1 py-0.5 text-center">
                        <input
                          type="checkbox"
                          checked={g.enabled}
                          aria-label={t('gcpEnabledAria', { index: idx + 1 })}
                          onChange={(e) =>
                            mutateGcps((prev) =>
                              prev.map((x) =>
                                x.gcp_id === g.gcp_id ? { ...x, enabled: e.target.checked } : x,
                              ),
                            )
                          }
                        />
                      </td>
                      <td className="px-1 py-0.5 text-right font-mono">
                        {res?.radial_error != null ? `${res.radial_error.toFixed(2)}m` : '—'}
                      </td>
                      <td className="px-1 py-0.5 text-center">
                        <button
                          type="button"
                          onClick={(ev) => {
                            ev.stopPropagation();
                            mutateGcps((prev) => prev.filter((x) => x.gcp_id !== g.gcp_id));
                          }}
                          className="text-status-critical hover:underline"
                          aria-label={`delete-${idx}`}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              data-testid="georef-save-solve-btn"
              disabled={saving}
              onClick={() => void handleSaveDraft()}
              className="rounded bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {saving ? t('saving') : t('saveSolve')}
            </button>
            {dirty && <span className="text-[11px] text-amber-700">{t('dirtyBadge')}</span>}
          </div>
        </div>
      )}

      {/* Diagnostics & Honest Warnings */}
      {diag && (
        <div
          data-testid="georef-diagnostics"
          className="space-y-1.5 rounded border border-edge-subtle/60 bg-surface p-2 text-[11px]"
        >
          <div className="flex items-center justify-between">
            <span className="font-medium text-ink">{t('diagnosticsTitle')}</span>
            <span
              data-testid="georef-diag-status"
              className={`rounded px-1.5 py-0.5 font-medium ${
                diag.status === 'valid' && diag.warnings.length === 0
                  ? 'bg-emerald-500/15 text-emerald-700'
                  : diag.status === 'valid'
                    ? 'bg-amber-500/15 text-amber-700'
                    : 'bg-rose-500/15 text-rose-700'
              }`}
            >
              {diag.status === 'valid' && diag.warnings.length === 0
                ? t('statusValid')
                : diag.status === 'valid'
                  ? t('statusDegraded')
                  : t('statusInvalid')}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1 text-ink-muted">
            <div data-testid="georef-diag-fit-rmse">
              {t('fitRmse')}:{' '}
              {diag.fit_rmse != null ? `${diag.fit_rmse.toFixed(3)} m` : '—'}
            </div>
            <div data-testid="georef-diag-check-rmse">
              {t('checkRmse')}:{' '}
              {diag.check_rmse != null ? `${diag.check_rmse.toFixed(3)} m` : '—'}
            </div>
            <div>
              {t('conditionNum')}:{' '}
              {diag.condition_number != null ? diag.condition_number.toExponential(2) : '—'}
            </div>
            <div>
              {t('hullCoverage')}: {diag.hull_coverage_ratio != null ? `${(diag.hull_coverage_ratio * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
          {diag.blockers.map((b, i) => (
            <div key={`b-${i}`} className="rounded bg-rose-500/10 px-1.5 py-0.5 text-rose-700">
              {b}
            </div>
          ))}
          {diag.warnings.map((w, i) => (
            <div key={`w-${i}`} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-800">
              {w}
            </div>
          ))}
        </div>
      )}

      {/* Preview, Execute, MapSpec Bind & Deliverable Downloads */}
      {selectedSource && activePlan && (
        <div className="space-y-2 rounded border border-edge-subtle/60 bg-surface p-2">
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              data-testid="georef-preview-btn"
              disabled={previewing || isJobRunning}
              onClick={() => void handlePreview()}
              className="rounded border border-edge-subtle px-2 py-1 text-[11px] font-medium hover:bg-surface-canvas disabled:opacity-50"
            >
              {previewing ? t('previewing') : t('previewBtn')}
            </button>
            <button
              type="button"
              data-testid="georef-execute-btn"
              disabled={isJobRunning}
              onClick={() => void handleExecute()}
              className="rounded bg-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {t('executeBtn')}
            </button>
            {isJobRunning && (
              <button
                type="button"
                data-testid="georef-cancel-btn"
                onClick={() => void handleCancel()}
                className="rounded border border-rose-500 px-2 py-1 text-[11px] font-medium text-rose-600"
              >
                {t('cancelJobBtn')}
              </button>
            )}
          </div>

          {previewImgUrl && (
            <div className="space-y-1">
              <div className="text-[11px] text-ink-muted">
                {t('previewTitle', {
                  width: previewDims?.width ?? '—',
                  height: previewDims?.height ?? '—',
                })}
              </div>
              <div
                className="flex justify-center rounded border border-edge-subtle/60 p-1"
                style={{
                  backgroundImage:
                    'linear-gradient(45deg, #e5e7eb 25%, transparent 25%), linear-gradient(-45deg, #e5e7eb 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #e5e7eb 75%), linear-gradient(-45deg, transparent 75%, #e5e7eb 75%)',
                  backgroundSize: '12px 12px',
                  backgroundPosition: '0 0, 0 6px, 6px -6px, -6px 0',
                }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- protected blob ObjectURL */}
                <img
                  data-testid="georef-preview-img"
                  src={previewImgUrl}
                  alt="Warped preview"
                  className="max-h-40 max-w-full object-contain"
                />
              </div>
            </div>
          )}

          {jobStatus && (
            <div
              data-testid="georef-job-status"
              className="rounded border border-edge-subtle/60 bg-surface-canvas px-2 py-1 text-[11px]"
            >
              {['pending', 'queued', 'running', 'cancelling'].includes(jobStatus.status)
                ? t('jobRunning', { stage: jobStatus.stage, progress: jobStatus.progress })
                : jobStatus.status === 'completed'
                  ? t('jobSucceeded')
                  : jobStatus.status === 'cancelled'
                    ? t('jobCancelled')
                    : `${t('jobFailed')}: ${jobStatus.error_message || jobStatus.error_code || ''}`}
            </div>
          )}

          {activePlan.latest_artifact && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              <button
                type="button"
                data-testid="georef-bind-layer-btn"
                disabled={binding}
                onClick={() => void handleBindLayer()}
                className="rounded bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:opacity-90 disabled:opacity-50"
              >
                {binding ? t('binding') : t('bindLayerBtn')}
              </button>
              <button
                type="button"
                data-testid="georef-download-tif-btn"
                onClick={() =>
                  ctx &&
                  activePlan.latest_artifact &&
                  void downloadGeorefArtifact(ctx, activePlan.latest_artifact.artifact_id)
                }
                className="rounded border border-edge-subtle px-2 py-1 text-[11px] font-medium hover:bg-surface-canvas"
              >
                {t('downloadGeoTiff')}
              </button>
              <button
                type="button"
                data-testid="georef-download-report-btn"
                onClick={handleDownloadReport}
                className="rounded border border-edge-subtle px-2 py-1 text-[11px] font-medium hover:bg-surface-canvas"
              >
                {t('downloadReport')}
              </button>
            </div>
          )}

          {bindMessage && (
            <div className="rounded bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-800">
              {bindMessage}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
