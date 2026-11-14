'use client';

/**
 * G36 视觉附图条（ADR-0291）—— 聊天 composer 的附图入口。
 *
 * 三个来源（任务必需核心）：
 * - 当前地图视口：受控采集（渲染稳定门 + captureMapCanvas），如实标注
 *   「不含页面 DOM 图例」（V07）。
 * - 框选局部：先采集整幅，再在截图上拖选矩形 —— 坐标链显式
 *   （预览 CSS px → 原图设备 px × dpr；V03）。
 * - 上传参考图：PNG/JPEG 静态文件（服务端真实解码/EXIF 校正；V12/V13/V15）。
 *
 * 纪律：ref-only —— 上传回执只落 evidence id + 元数据；发送由
 * use-sse-stream 随消息携 refs（bytes 绝不进聊天请求体）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ImagePlus, Loader2, Map as MapIcon, Scissors, Upload, X } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import {
  useVisualAttachStore,
  type VisualAttachmentDraft,
} from '@/lib/visual-context/attach-store';
import { captureCurrentMap, type MapCaptureResult } from '@/lib/visual-context/map-capture';
import { uploadVisualEvidence } from '@/lib/api/visual-context';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

interface RegionRect { x: number; y: number; w: number; h: number }

export function VisualAttachBar({ sessionId, ownerToken }: {
  sessionId?: string | null;
  ownerToken?: string | null;
}) {
  const t = useT();
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [regionCapture, setRegionCapture] = useState<{
    capture: MapCaptureResult;
    url: string;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const drafts = useVisualAttachStore((s) => s.drafts);
  const begin = useVisualAttachStore((s) => s.begin);
  const setSession = useVisualAttachStore((s) => s.setSession);
  const addDraft = useVisualAttachStore((s) => s.addDraft);
  const updateDraft = useVisualAttachStore((s) => s.updateDraft);
  const removeDraft = useVisualAttachStore((s) => s.removeDraft);

  useEffect(() => {
    if (sessionId) {
      begin(sessionId);
    } else {
      setSession(null);
    }
  }, [sessionId, begin, setSession]);

  const sessionReady = typeof sessionId === 'string' && sessionId.length > 0;

  const mapIdentity = useCallback(() => {
    const cursor = getMapSpecSessionCursor();
    return {
      revision: cursor.revision ?? 0,
      fingerprint: cursor.mapspecFingerprint ?? '',
    };
  }, []);

  const upload = useCallback(async (opts: {
    file: Blob;
    sourceType: VisualAttachmentDraft['sourceType'];
    label: string;
    viewport?: MapCaptureResult['viewport'];
    dpr?: number;
    crop?: RegionRect;
    localThumb?: string;
  }) => {
    if (!sessionId) return;
    // 上传中占位（临时 id；回执后替换为真实 evidence id）。
    const tempId = `pending-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    addDraft({
      evidenceId: tempId,
      sourceType: opts.sourceType,
      label: opts.label,
      thumbUrl: opts.localThumb,
      uploading: true,
    });
    const { revision, fingerprint } = mapIdentity();
    try {
      const dto = await uploadVisualEvidence({
        sessionId,
        ownerToken,
        file: opts.file,
        sourceType: opts.sourceType,
        label: opts.label,
        mapspecRevision: opts.sourceType === 'reference_upload' ? undefined : revision,
        mapspecFingerprint: opts.sourceType === 'reference_upload' ? undefined : fingerprint,
        viewport: opts.viewport,
        dpr: opts.dpr,
        crop: opts.crop,
      });
      removeDraft(tempId);
      addDraft({
        evidenceId: dto.evidence_id,
        sourceType: opts.sourceType,
        label: opts.label || dto.label || '',
        thumbUrl: opts.localThumb,
        mapspecRevision: dto.mapspec_revision ?? null,
        viewportHash: dto.viewport_hash,
      });
    } catch (err) {
      const reason = err instanceof Error && err.message ? err.message.slice(0, 120) : 'upload_failed';
      updateDraft(tempId, { uploading: false, error: reason });
      window.setTimeout(() => removeDraft(tempId), 6000);
    }
  }, [sessionId, ownerToken, addDraft, removeDraft, updateDraft, mapIdentity]);

  const attachMap = useCallback(async () => {
    setMenuOpen(false);
    if (!sessionReady) return;
    setBusy(true);
    setNotice('');
    try {
      const capture = await captureCurrentMap();
      if (!capture) {
        setNotice(t('sidebar.chat.visualAttach.mapUnavailable'));
        return;
      }
      await upload({
        file: capture.blob,
        sourceType: 'map_viewport',
        label: t('sidebar.chat.visualAttach.chipViewport'),
        viewport: capture.viewport,
        dpr: capture.dpr,
        localThumb: URL.createObjectURL(capture.blob),
      });
    } finally {
      setBusy(false);
    }
  }, [sessionReady, upload, t]);

  const startRegion = useCallback(async () => {
    setMenuOpen(false);
    if (!sessionReady) return;
    setBusy(true);
    setNotice('');
    try {
      const capture = await captureCurrentMap();
      if (!capture) {
        setNotice(t('sidebar.chat.visualAttach.mapUnavailable'));
        return;
      }
      setRegionCapture({ capture, url: URL.createObjectURL(capture.blob) });
    } finally {
      setBusy(false);
    }
  }, [sessionReady, t]);

  const onFilePicked = useCallback(async (files: FileList | null) => {
    setMenuOpen(false);
    if (!files || files.length === 0) return;
    for (const file of Array.from(files).slice(0, 4 - drafts.length)) {
      await upload({
        file,
        sourceType: 'reference_upload',
        label: file.name.slice(0, 120),
        localThumb: URL.createObjectURL(file),
      });
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [upload, drafts.length]);

  const chipLabel = (d: VisualAttachmentDraft) => {
    const key = d.sourceType === 'map_viewport' ? 'chipViewport'
      : d.sourceType === 'map_region' ? 'chipRegion' : 'chipReference';
    return t(`sidebar.chat.visualAttach.${key}`);
  };

  return (
    <div className="flex flex-col gap-1" data-testid="visual-attach-bar">
      {/* 草稿 chips */}
      {drafts.length > 0 && (
        <div className="flex flex-wrap items-center gap-1" data-testid="visual-attach-chips">
          {drafts.map((d) => (
            <span
              key={d.evidenceId}
              data-testid={`visual-attach-chip-${d.evidenceId}`}
              className={`inline-flex items-center gap-1 rounded-pill border px-1.5 py-0.5 text-meta ${
                d.error
                  ? 'border-status-critical-border text-status-critical'
                  : 'border-border-default bg-surface-sunken text-ink-muted'
              }`}
              title={d.error ? `${t('sidebar.chat.visualAttach.uploadFailed')}: ${d.error}` : d.label}
            >
              {d.thumbUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={d.thumbUrl} alt="" className="h-5 w-5 rounded-sm object-cover" />
              ) : (
                <ImagePlus size={10} aria-hidden />
              )}
              {d.uploading ? t('sidebar.chat.visualAttach.uploading') : chipLabel(d)}
              {d.mapspecRevision != null && !d.uploading && (
                <span className="text-ink-disabled">r{d.mapspecRevision}</span>
              )}
              <button
                type="button"
                aria-label={t('sidebar.chat.visualAttach.chipRemove')}
                onClick={() => removeDraft(d.evidenceId)}
                className="cursor-pointer rounded-full p-0.5 text-ink-muted hover:bg-surface-hover hover:text-ink"
              >
                <X size={10} aria-hidden />
              </button>
            </span>
          ))}
        </div>
      )}
      {notice && <span className="px-1 text-meta text-status-critical">{notice}</span>}

      {/* 附图按钮 + 菜单 */}
      <div className="relative flex items-center">
        <button
          type="button"
          aria-label={t('sidebar.chat.visualAttach.attachAria')}
          title={t('sidebar.chat.visualAttach.attachMenu')}
          data-testid="visual-attach-button"
          disabled={busy || !sessionReady || drafts.length >= 4}
          onClick={() => setMenuOpen((v) => !v)}
          className="cursor-pointer rounded-sm p-1 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <ImagePlus size={14} aria-hidden />}
        </button>
        {!sessionReady && (
          <span className="pl-1 text-meta text-ink-disabled">{t('sidebar.chat.visualAttach.mapUnavailable')}</span>
        )}
        {sessionReady && drafts.length >= 4 && (
          <span className="pl-1 text-meta text-ink-disabled">{t('sidebar.chat.visualAttach.draftFull')}</span>
        )}
        {menuOpen && (
          <div
            className="absolute bottom-full left-0 z-30 mb-1 w-56 rounded-md border border-border-default bg-surface-raised p-1 shadow-lg"
            data-testid="visual-attach-menu"
          >
            <MenuItem
              icon={<MapIcon size={13} aria-hidden />}
              title={t('sidebar.chat.visualAttach.attachMap')}
              hint={t('sidebar.chat.visualAttach.attachMapHint')}
              onClick={() => void attachMap()}
            />
            <MenuItem
              icon={<Scissors size={13} aria-hidden />}
              title={t('sidebar.chat.visualAttach.attachRegion')}
              hint={t('sidebar.chat.visualAttach.attachRegionHint')}
              onClick={() => void startRegion()}
            />
            <MenuItem
              icon={<Upload size={13} aria-hidden />}
              title={t('sidebar.chat.visualAttach.attachReference')}
              hint={t('sidebar.chat.visualAttach.attachReferenceHint')}
              onClick={() => fileInputRef.current?.click()}
            />
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg"
          multiple
          className="hidden"
          onChange={(e) => void onFilePicked(e.target.files)}
          aria-label={t('sidebar.chat.visualAttach.attachReference')}
        />
      </div>

      {/* 框选局部模态 */}
      {regionCapture && (
        <RegionCropModal
          imageUrl={regionCapture.url}
          deviceWidth={regionCapture.capture.deviceWidth}
          deviceHeight={regionCapture.capture.deviceHeight}
          dpr={regionCapture.capture.dpr}
          title={t('sidebar.chat.visualAttach.regionTitle')}
          confirmLabel={t('sidebar.chat.visualAttach.regionConfirm')}
          cancelLabel={t('sidebar.chat.visualAttach.regionCancel')}
          dragHint={t('sidebar.chat.visualAttach.regionDragHint')}
          onCancel={() => {
            URL.revokeObjectURL(regionCapture.url);
            setRegionCapture(null);
          }}
          onConfirm={async (cropCss) => {
            // CSS px → 原图设备 px（V03 坐标链：modal 显示尺寸 → 原始画布）。
            const crop: RegionRect = {
              x: Math.max(0, Math.round(cropCss.x * regionCapture.capture.deviceWidth)),
              y: Math.max(0, Math.round(cropCss.y * regionCapture.capture.deviceHeight)),
              w: Math.max(1, Math.round(cropCss.w * regionCapture.capture.deviceWidth)),
              h: Math.max(1, Math.round(cropCss.h * regionCapture.capture.deviceHeight)),
            };
            const cap = regionCapture;
            URL.revokeObjectURL(cap.url);
            setRegionCapture(null);
            await upload({
              file: cap.capture.blob,
              sourceType: 'map_region',
              label: t('sidebar.chat.visualAttach.chipRegion'),
              viewport: cap.capture.viewport,
              dpr: cap.capture.dpr,
              crop,
              localThumb: URL.createObjectURL(cap.capture.blob),
            });
          }}
        />
      )}
    </div>
  );
}

function MenuItem({ icon, title, hint, onClick }: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left transition-colors hover:bg-surface-hover"
    >
      <span className="flex items-center gap-1.5 text-body text-ink">{icon}{title}</span>
      <span className="text-meta text-ink-muted">{hint}</span>
    </button>
  );
}

/** 截图上拖选矩形（归一化 0..1 坐标返回；显示与原图解耦）。 */
function RegionCropModal({ imageUrl, title, confirmLabel, cancelLabel, dragHint, onConfirm, onCancel }: {
  imageUrl: string;
  deviceWidth: number;
  deviceHeight: number;
  dpr: number;
  title: string;
  confirmLabel: string;
  cancelLabel: string;
  dragHint: string;
  onCancel: () => void;
  onConfirm: (cropNormalized: { x: number; y: number; w: number; h: number }) => Promise<void> | void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [rect, setRect] = useState<RegionRect | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);

  const toLocal = (e: React.MouseEvent) => {
    const img = imgRef.current;
    if (!img) return { x: 0, y: 0 };
    const bounds = img.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - bounds.left) / bounds.width)),
      y: Math.min(1, Math.max(0, (e.clientY - bounds.top) / bounds.height)),
    };
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-label={title}
      data-testid="region-crop-modal"
    >
      <div className="flex max-h-full w-full max-w-3xl flex-col gap-2 rounded-md border border-border-default bg-surface-raised p-3">
        <div className="flex items-center justify-between">
          <span className="text-body font-medium text-ink">{title}</span>
          <span className="text-meta text-ink-muted">{dragHint}</span>
        </div>
        <div className="relative min-h-0 flex-1 overflow-auto">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={imgRef}
            src={imageUrl}
            alt={title}
            className="max-h-[60vh] w-auto select-none cursor-crosshair"
            draggable={false}
            onMouseDown={(e) => {
              e.preventDefault();
              const p = toLocal(e);
              dragStartRef.current = p;
              setRect({ x: p.x, y: p.y, w: 0, h: 0 });
            }}
            onMouseMove={(e) => {
              if (!dragStartRef.current) return;
              const p = toLocal(e);
              const s = dragStartRef.current;
              setRect({
                x: Math.min(s.x, p.x), y: Math.min(s.y, p.y),
                w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y),
              });
            }}
            onMouseUp={() => { dragStartRef.current = null; }}
            onMouseLeave={() => { dragStartRef.current = null; }}
          />
          {rect && rect.w > 0.002 && rect.h > 0.002 && (
            <div
              className="pointer-events-none absolute border-2 border-status-accent bg-status-accent/10"
              style={{
                left: `${rect.x * 100}%`,
                top: `${rect.y * 100}%`,
                width: `${rect.w * 100}%`,
                height: `${rect.h * 100}%`,
              }}
              data-testid="region-crop-rect"
            />
          )}
        </div>
        <div className="flex items-center justify-end gap-2">
          <button type="button" onClick={onCancel} className="cursor-pointer rounded-sm border border-border-default px-2 py-1 text-body text-ink hover:bg-surface-hover">
            {cancelLabel}
          </button>
          <button
            type="button"
            data-testid="region-crop-confirm"
            disabled={!rect || rect.w <= 0.002 || rect.h <= 0.002}
            onClick={() => { if (rect) void onConfirm(rect); }}
            className="cursor-pointer rounded-sm bg-status-accent px-2 py-1 text-body text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
