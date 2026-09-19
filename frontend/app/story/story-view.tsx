'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { useSearchParams } from 'next/navigation';
import { useMap } from 'react-map-gl/maplibre';
import {
  ArrowDown,
  ArrowUp,
  Eye,
  EyeOff,
  FileDown,
  ImageDown,
  ListTree,
  Maximize2,
  Minimize2,
  HardDriveDownload,
  Pencil,
  Share2,
  X,
} from 'lucide-react';

const MapPanel = dynamic(
  () => import('@/components/map/map-panel').then((m) => ({ default: m.MapPanel })),
  { ssr: false, loading: () => <div className="w-full h-full bg-surface-canvas animate-pulse" /> },
);

const StoryMarkdown = dynamic(() => import('@/components/chat/story-markdown'), { ssr: false });

import { devOnly } from '@/lib/utils/logger';
import { useHudStore } from '@/lib/store/useHudStore';
import { apiFetch, describeApiError } from '@/lib/api/transport';
import { useMapAction } from '@/lib/contexts/map-action-context';
// #552: 地图还原（视口 + 底图 + 图层）在 lib/session/map-state-restore ——
// helper 不得作为页面导出（CI `next build` 拒绝非 Page 导出字段）。
import { applyStoryMapState, type SessionMapState } from '@/lib/session/map-state-restore';
import { useToastStore } from '@/components/ui/toast';
import { MapErrorBoundary } from '@/components/map/map-error-boundary';
import { useT } from '@/lib/i18n/useT';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';
import { captureMapCanvas } from '@/lib/map-kit/exporter';
import type { ChapterCamera } from './chapters';
import {
  applyOrchestration,
  assignChapterArtifacts,
  deriveChapters,
  extractChapterCameras,
  loadOrchestration,
  saveOrchestration,
  type ChapterOrchestration,
  type StoryChapter,
} from './chapters';
import { ChapterArtifact } from './chapter-artifact';
import { ChapterScrubber } from './chapter-scrubber';
import { composeShareCard, downloadBlob, exportNarrativePdf } from './narrative-export';
// ADR-0196：后端编排优先、本地派生兜底 —— 编排器给出的 StoryMapSpec 驱动
// 滚动叙事列（StoryNarrator）、联动看板（StoryDashboard）与全参相机漫游。
import {
  compileStorySpec,
  exportStoryBundle,
  isValidStorySpecDto,
  specToNarratorView,
  type StoryMapSpecDto,
} from '@/lib/api/storymap';
import { StoryNarrator } from '@/components/story/story-narrator';
import { StoryDashboard } from '@/components/story/story-dashboard';

/** 播放模式下逐章节推进的间隔（ms）；PDF 逐章定位等待同源。 */
const PLAY_INTERVAL_MS = 2500;
const PDF_SETTLE_MS = 900;

interface StoryMessage {
  role: 'user' | 'assistant' | 'system' | string;
  content: string;
}

export function StoryView(): React.ReactElement {
  // master 的 story.* i18n 键化（ADR-0144）移植进章节模型视图；无 provider
  // 时（单测裸渲染）useT 回落 zh 默认，既有中文断言零改造。
  const t = useT('story');
  const layers = useHudStore((s) => s.layers);
  const removeLayer = useHudStore((s) => s.removeLayer);
  const toggleLayer = useHudStore((s) => s.toggleLayer);
  const { dispatchAction } = useMapAction();
  const reducedMotion = usePrefersReducedMotion();
  const mapRefs = useMap();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get('session_id');

  // 会话数据（#552 语义不变）
  const [messages, setMessages] = useState<StoryMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mapState, setMapState] = useState<SessionMapState | null>(null);

  // 章节编排
  const [chapterState, setChapterState] = useState<StoryChapter[] | null>(null);
  const [orchPanelOpen, setOrchPanelOpen] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);

  // ADR-0196：后端编排 spec（null = 本地派生兜底）与排版模式
  const [storySpec, setStorySpec] = useState<StoryMapSpecDto | null>(null);
  const [immersive, setImmersive] = useState(false);
  const [bundleExporting, setBundleExporting] = useState(false);

  // 播放器
  const [activeId, setActiveId] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [pdfProgress, setPdfProgress] = useState<string | null>(null);
  const [mapFade, setMapFade] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const sessionIdRef = useRef<string | null>(sessionId);
  sessionIdRef.current = sessionId;
  // 用户是否已主动导航（seek/播放）；spec 迟到落位不得覆盖用户选择
  const userNavigatedRef = useRef(false);

  const derived = useMemo(() => deriveChapters(messages), [messages]);
  const chapters = useMemo(
    () => chapterState ?? applyOrchestration(derived, loadOrchestration(sessionId)),
    [chapterState, derived, sessionId],
  );
  const visibleChapters = useMemo(() => chapters.filter((c) => c.visible), [chapters]);
  const activePos = useMemo(() => {
    const idx = visibleChapters.findIndex((c) => c.id === activeId);
    return idx >= 0 ? idx : 0;
  }, [visibleChapters, activeId]);
  const activeChapter = visibleChapters[activePos] ?? null;

  const cameras = useMemo<Record<string, ChapterCamera | null>>(
    () => extractChapterCameras(messages),
    [messages],
  );
  const artifactsByChapter = useMemo(
    () => assignChapterArtifacts(messages, mapState),
    [messages, mapState],
  );

  // ADR-0196：编排视图模型 —— spec 形状合法才启用编排模式，否则本地派生兜底。
  // try/catch 双保险：门卫之外任何映射期异常都降级本地派生，绝不把 /story 渲染打崩。
  const specView = useMemo(() => {
    if (!storySpec || !isValidStorySpecDto(storySpec)) return null;
    try {
      return specToNarratorView(storySpec);
    } catch {
      return null;
    }
  }, [storySpec]);
  const specChapters = useMemo(() => specView?.chapters ?? [], [specView]);
  const specWidgets = specView?.widgets ?? [];
  const specActiveId = useMemo(() => {
    if (specChapters.some((c) => c.id === activeId)) return activeId;
    // 用户已 seek/播放且其本地章节 id 不在 spec 中时，不能把迟到 spec 的
    // 第 0 章当作当前位置 —— 那会立刻触发一次相机 fly_to 劫持。
    return userNavigatedRef.current ? null : specChapters[0]?.id ?? null;
  }, [specChapters, activeId]);
  const activeWidgetIds = useMemo(
    () => specChapters.find((c) => c.id === specActiveId)?.widgetIds ?? [],
    [specChapters, specActiveId],
  );
  // 统一播放/定位序列：编排模式用 spec 章节，本地模式用可见消息章节
  const playlist = useMemo(
    () => (specView ? specChapters.map((c) => c.id) : visibleChapters.map((c) => c.id)),
    [specView, specChapters, visibleChapters],
  );
  const playPos = specView ? playlist.indexOf(specActiveId ?? '') : activePos;

  const persist = useCallback((next: StoryChapter[]) => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    const overrides: ChapterOrchestration['overrides'] = {};
    next.forEach((ch, order) => {
      overrides[ch.id] = { title: ch.title, visible: ch.visible, order };
    });
    saveOrchestration(sid, { version: 1, overrides });
  }, []);

  const mutateChapters = useCallback(
    (mutator: (list: StoryChapter[]) => StoryChapter[]) => {
      setChapterState((prev) => {
        const base = prev ?? applyOrchestration(derived, loadOrchestration(sessionIdRef.current));
        const next = mutator([...base]);
        persist(next);
        return next;
      });
    },
    [derived, persist],
  );

  const seek = useCallback(
    (pos: number) => {
      const target = playlist[pos];
      if (target) {
        userNavigatedRef.current = true;
        setActiveId(target);
      }
    },
    [playlist],
  );

  // 播放：自续期 timeout 逐章节推进（#552 续播语义保留）；到头自停。
  useEffect(() => {
    if (!playing) return;
    // A late spec can make the current local chapter absent from the spec
    // playlist. Do not reinterpret index -1 as chapter 0 and jump the camera.
    if (playPos < 0) return;
    if (playlist.length === 0 || playPos >= playlist.length - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setActiveId(playlist[playPos + 1]), PLAY_INTERVAL_MS);
    return () => clearTimeout(t);
  }, [playing, playPos, playlist]);

  // 章节切换：编排模式 → spec 相机全参 fly_to（center/zoom/pitch/bearing，
  // 滚动定位由 StoryNarrator 自持）；本地模式保持 scrollIntoView + 缓动 +
  // 图层容器轻淡入（reduced-motion 只做定位级跳切）。
  useEffect(() => {
    if (specView) {
      const cam = specActiveId ? specView.cameras[specActiveId] : undefined;
      if (cam) {
        dispatchAction({
          command: 'fly_to',
          params: {
            center: cam.center,
            ...(cam.zoom !== undefined ? { zoom: cam.zoom } : {}),
            ...(cam.pitch !== undefined ? { pitch: cam.pitch } : {}),
            ...(cam.bearing !== undefined ? { bearing: cam.bearing } : {}),
          },
        });
      }
      return;
    }
    const active = visibleChapters[activePos];
    if (!active) return;
    const nodes = containerRef.current?.querySelectorAll<HTMLElement>('[data-story-chapter]');
    nodes?.forEach((n) => {
      if (n.getAttribute('data-story-chapter') === active.id) {
        n.scrollIntoView?.({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
      }
    });
    const cam = cameras[active.id];
    if (cam) {
      dispatchAction({
        command: 'fly_to',
        params: { center: cam.center, ...(cam.zoom !== undefined ? { zoom: cam.zoom } : {}) },
      });
    }
    if (!reducedMotion) {
      setMapFade(true);
      const raf = requestAnimationFrame(() => setMapFade(false));
      return () => cancelAnimationFrame(raf);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- cameras/visibleChapters 由 activePos 驱动即可
  }, [activePos, reducedMotion, specView, specActiveId]);

  // 会话装载（#552：错误可见、清残留、匿名引导 —— 契约与测试锁定）
  useEffect(() => {
    const controller = new AbortController();
    useHudStore.getState().clearLayers();
    setLoadError(null);
    setMessages([]);
    setPlaying(false);
    setActiveId(null);
    setChapterState(null);
    setMapState(null);
    setStorySpec(null);
    // 会话级残留（评审 P2-2）：重命名态 / PDF 进度 / 淡入残留 / 导航标记
    setRenamingId(null);
    setPdfProgress(null);
    setMapFade(false);
    userNavigatedRef.current = false;

    // 首章落位在装载路径内同步完成（派生+编排为纯函数，可在此直接计算）。
    // 不用 effect 事后落位——它会与用户 seek 竞争提交顺序（flaky 源）。
    const landFirstChapter = (msgs: StoryMessage[]) => {
      const firstVisible = applyOrchestration(deriveChapters(msgs), loadOrchestration(sessionId)).find(
        (c) => c.visible,
      );
      setActiveId(firstVisible?.id ?? null);
    };

    if (!sessionId) {
      setLoading(false);
      const intro: StoryMessage[] = [
        { role: 'assistant', content: t('introTitle') },
        { role: 'assistant', content: t('introHint') },
      ];
      setMessages(intro);
      landFirstChapter(intro);
      return () => controller.abort();
    }

    setLoading(true);
    (async () => {
      try {
        const data = await apiFetch<{ messages?: StoryMessage[] }>(
          `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`,
          { signal: controller.signal, label: 'Story session error' },
        );
        if (controller.signal.aborted) return;
        const msgs = data.messages && data.messages.length > 0 ? data.messages : [];
        setMessages(msgs);
        landFirstChapter(msgs);

        const stateData = await apiFetch<{ map_state?: SessionMapState }>(
          `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/map-state`,
          { signal: controller.signal, label: 'Story map state error' },
        );
        if (controller.signal.aborted) return;
        setMapState(stateData?.map_state ?? null);
        if (stateData?.map_state) {
          await applyStoryMapState(stateData.map_state, sessionId, controller.signal, dispatchAction);
        }

        // ADR-0196：编排编译严格排在既有两次请求之后，且独立吞错 —— 失败
        // （旧后端/断网/非 JSON）静默降级本地派生，绝不进外层 catch 把
        // 「无编排能力」误报成会话加载失败。先释放装载态：spec 允许迟到，
        // 用户在此期间 seek/播放必须被视为有效导航。
        if (msgs.length > 0 && !controller.signal.aborted) {
          setLoading(false);
          try {
            const spec = await compileStorySpec(
              {
                session_id: sessionId,
                messages: msgs.map((m) => ({ role: m.role, content: m.content })),
              },
              { signal: controller.signal },
            );
            if (!controller.signal.aborted && isValidStorySpecDto(spec)) {
              setStorySpec(spec);
              // 迟到落位（评审 P2-3）：慢后端响应不得覆盖用户已进行的 seek/播放
              if (!userNavigatedRef.current) {
                setActiveId(spec.chapters[0]?.id ?? null);
              }
            }
          } catch {
            /* 编排降级：回放主链路不受影响 */
          }
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        setLoadError(describeApiError(err, '加载会话失败'));
        devOnly.error('Story session load failed:', err);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();

    return () => controller.abort();
  }, [sessionId, dispatchAction]);

  const togglePlay = useCallback(() => {
    if (playing) {
      setPlaying(false);
      return;
    }
    // 用户启动播放与显式 seek 同级：spec 迟到落位不得重写到当前位置。
    userNavigatedRef.current = true;
    setActiveId((cur) => {
      const atEnd = cur !== null && playlist.length > 0 && playlist[playlist.length - 1] === cur;
      return atEnd ? playlist[0] : (cur ?? playlist[0] ?? null);
    });
    setPlaying(true);
  }, [playing, playlist]);

  const handleShare = useCallback(() => {
    const url = typeof window !== 'undefined' ? window.location.href : '';
    if (!url) return;
    const report = (ok: boolean) => {
      useToastStore
        .getState()
        .addToast(ok ? '已复制分享链接' : '复制失败，请手动复制地址栏链接', ok ? 'success' : 'error');
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(() => report(true)).catch(() => report(false));
    } else {
      report(false);
    }
  }, []);

  const getMapInstance = useCallback(() => {
    const mapRef = Object.values(mapRefs ?? {})[0];
    try {
      return mapRef?.getMap() ?? null;
    } catch {
      return null;
    }
  }, [mapRefs]);

  const handleShareCard = useCallback(async () => {
    const map = getMapInstance();
    if (!map) {
      useToastStore.getState().addToast('地图尚未就绪，无法生成分享卡', 'error');
      return;
    }
    try {
      const blob = await composeShareCard(map, {
        title: activeChapter?.title ?? 'StoryMap',
        subtitle: `章节 ${activePos + 1}/${visibleChapters.length}`,
      });
      downloadBlob(blob, `storymap-card-${Date.now()}.png`);
      useToastStore.getState().addToast('分享卡已生成', 'success');
    } catch (err) {
      devOnly.error('share card failed:', err);
      useToastStore.getState().addToast(describeApiError(err, '分享卡生成失败'), 'error');
    }
  }, [getMapInstance, activeChapter, activePos, visibleChapters.length]);

  const handleNarrativePdf = useCallback(async () => {
    const map = getMapInstance();
    if (!map) {
      useToastStore.getState().addToast('地图尚未就绪，无法导出叙事 PDF', 'error');
      return;
    }
    const exportChapters = specView
      ? specChapters.map((c) => ({ id: c.id, title: c.title }))
      : visibleChapters.map((c) => ({ id: c.id, title: c.title }));
    if (exportChapters.length === 0) {
      useToastStore.getState().addToast('没有可导出的章节', 'error');
      return;
    }
    // 会话快照（评审 P2-2）：逐章渲染期间切会话即中止，不向新会话写旧章节状态
    const sid = sessionIdRef.current;
    setPlaying(false);
    setPdfProgress('准备导出…');
    try {
      const blob = await exportNarrativePdf(
        exportChapters,
        async (chapter) => {
          if (sessionIdRef.current !== sid) throw new Error('session switched');
          setActiveId(chapter.id);
          // 等待 fly_to 相机与图层渲染稳定（经验窗；GL 渲染无完成事件可等）
          await new Promise((r) => setTimeout(r, PDF_SETTLE_MS));
          return captureMapCanvas(map);
        },
        'GeoAgent 叙事导出',
        (p) => {
          if (sessionIdRef.current === sid) {
            setPdfProgress(`渲染章节 ${p.current}/${p.total}：${p.chapterTitle}`);
          }
        },
      );
      if (sessionIdRef.current !== sid) return;
      downloadBlob(blob, `storymap-narrative-${Date.now()}.pdf`);
      useToastStore.getState().addToast(`叙事 PDF 已导出（${exportChapters.length} 章）`, 'success');
    } catch (err) {
      if (sessionIdRef.current !== sid) return; // 切会话导致的中止静默
      devOnly.error('narrative pdf failed:', err);
      useToastStore.getState().addToast(describeApiError(err, '叙事 PDF 导出失败'), 'error');
    } finally {
      if (sessionIdRef.current === sid) setPdfProgress(null);
    }
  }, [getMapInstance, specView, specChapters, visibleChapters]);

  // ADR-0196：一键导出自包含离线交互专报（后端脱敏打包 → HTML 单文件）
  const handleExportBundle = useCallback(async () => {
    if (!storySpec) return;
    const sid = sessionIdRef.current;
    setBundleExporting(true);
    try {
      const { blob } = await exportStoryBundle(storySpec);
      if (sessionIdRef.current !== sid) return;
      downloadBlob(blob, `storymap-bundle-${Date.now()}.html`);
      useToastStore.getState().addToast('离线专报已导出（单文件 HTML）', 'success');
    } catch (err) {
      if (sessionIdRef.current !== sid) return;
      devOnly.error('story bundle export failed:', err);
      useToastStore.getState().addToast(describeApiError(err, '离线专报导出失败'), 'error');
    } finally {
      setBundleExporting(false);
    }
  }, [storySpec]);

  if (loading) {
    return (
      <div className="h-screen w-screen bg-surface-canvas flex items-center justify-center text-status-info font-mono relative">
        <div className="absolute inset-0 z-[1] opacity-[0.015] bg-grid-agent bg-[size:60px_60px]"></div>
        <div className="animate-pulse">Loading StoryMap CNS...</div>
      </div>
    );
  }

  return (
    <div className="h-screen w-screen overflow-hidden bg-surface-canvas relative flex">
      <div className="absolute inset-0 pointer-events-none z-[1] opacity-[0.015] bg-grid-agent bg-[size:60px_60px]" />

      {/* Narrative Panel (Left) — ADR-0196 双排版：split 左图右文 / immersive 全屏地图 + 右浮叙事列。
          position 必须整体进分支：relative 与 absolute 同类并存时 CSS 声明序判 relative 胜（Tailwind 顺序）。 */}
      <div
        ref={containerRef}
        data-testid="story-narrative-panel"
        className={`z-20 bg-surface-panel border-edge-subtle flex flex-col ${
          immersive
            ? 'absolute right-0 top-0 h-full w-[400px] xl:w-[440px] border-l bg-surface-panel/90 backdrop-blur-sm'
            : 'relative w-[400px] xl:w-[500px] h-full border-r'
        }`}
      >
        <div className="sticky top-0 p-4 bg-surface-panel border-b border-edge-subtle z-10">
          <div className="flex justify-between items-center">
            <div className="flex items-center gap-2">
              <h1 className="text-status-info font-semibold tracking-widest text-heading flex items-center gap-2">
                STORY<span className="text-ink-muted">MAP</span>
              </h1>
              {specView ? (
                <span
                  data-testid="story-orchestrated"
                  className="rounded-full border border-status-info-border px-2 py-0.5 text-meta text-status-info whitespace-nowrap"
                >
                  {t('orchestrated')}
                </span>
              ) : null}
            </div>
            <div className="flex gap-1">
              <button
                aria-label={t('share')}
                title={t('shareTitle')}
                onClick={handleShare}
                className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"
              >
                <Share2 className="h-4 w-4" />
              </button>
              <button
                aria-label={t('shareCardAria')}
                title={t('shareCardTitle')}
                onClick={() => void handleShareCard()}
                className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"
              >
                <ImageDown className="h-4 w-4" />
              </button>
              <button
                aria-label={t('exportPdfAria')}
                title={t('exportPdfTitle')}
                onClick={() => void handleNarrativePdf()}
                disabled={Boolean(pdfProgress)}
                className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info disabled:opacity-40"
              >
                <FileDown className="h-4 w-4" />
              </button>
              {specView ? (
                <button
                  aria-label={t('exportBundle')}
                  title={t('exportBundleTitle')}
                  onClick={() => void handleExportBundle()}
                  disabled={bundleExporting || Boolean(pdfProgress)}
                  className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info disabled:opacity-40"
                >
                  <HardDriveDownload className="h-4 w-4" />
                </button>
              ) : null}
              <button
                aria-label={t('immersive')}
                title={t('immersiveTitle')}
                aria-pressed={immersive}
                onClick={() => setImmersive((v) => !v)}
                className={`rounded-md p-2 transition-colors hover:bg-surface-hover ${
                  immersive ? 'text-status-info' : 'text-ink-muted hover:text-status-info'
                }`}
              >
                {immersive ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
              </button>
              {!specView ? (
                <button
                  aria-label={t('orchAria')}
                  title={t('orchTitle')}
                  aria-expanded={orchPanelOpen}
                  onClick={() => setOrchPanelOpen((v) => !v)}
                  className={`rounded-md p-2 transition-colors hover:bg-surface-hover ${
                    orchPanelOpen ? 'text-status-info' : 'text-ink-muted hover:text-status-info'
                  }`}
                >
                  <ListTree className="h-4 w-4" />
                </button>
              ) : null}
            </div>
          </div>
          <ChapterScrubber
            total={playlist.length}
            activeIndex={playPos}
            titles={
              specView
                ? specChapters.map((c) => c.title)
                : visibleChapters.map((c) => c.title)
            }
            playing={playing}
            onSeek={(pos) => {
              setPlaying(false);
              seek(pos);
            }}
            onTogglePlay={togglePlay}
            onPrev={() => {
              setPlaying(false);
              seek(Math.max(0, playPos - 1));
            }}
            onNext={() => {
              setPlaying(false);
              seek(Math.min(playlist.length - 1, playPos + 1));
            }}
          />
          {pdfProgress ? (
            <p role="status" className="px-1 pb-1 text-caption text-status-info" data-testid="story-pdf-progress">
              {pdfProgress}
            </p>
          ) : null}
        </div>

        {/* 章节编排面板（本地派生模式限定；编排模式下章节结构由后端权威给出） */}
        {!specView && orchPanelOpen ? (
          <div className="border-b border-edge-subtle bg-surface-sunken p-3" data-testid="story-orchestration">
            <div className="flex items-center justify-between pb-2">
              <h2 className="text-caption font-medium uppercase tracking-wide text-ink-muted">{t('orchPanelTitle')}</h2>
              <button
                type="button"
                aria-label={t('orchCloseAria')}
                onClick={() => setOrchPanelOpen(false)}
                className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
              >
                <X size={13} aria-hidden />
              </button>
            </div>
            <ul className="space-y-1">
              {chapters.map((ch, index) => (
                <li key={ch.id} className="flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-surface-hover">
                  {renamingId === ch.id ? (
                    <input
                      data-autofocus
                      defaultValue={ch.title}
                      aria-label={t('chapterNameAria')}
                      className="min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-raised px-1.5 py-0.5 text-body-sm text-ink outline-none"
                      onBlur={(e) => {
                        const title = e.target.value.trim() || ch.title;
                        mutateChapters((list) => list.map((c) => (c.id === ch.id ? { ...c, title } : c)));
                        setRenamingId(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                        if (e.key === 'Escape') setRenamingId(null);
                      }}
                    />
                  ) : (
                    <span className={`min-w-0 flex-1 truncate text-body-sm ${ch.visible ? 'text-ink-secondary' : 'text-ink-muted line-through'}`}>
                      {index + 1}. {ch.title}
                    </span>
                  )}
                  <button
                    type="button"
                    aria-label={t('renameChapterAria', { title: ch.title })}
                    onClick={() => {
                      setRenamingId(ch.id);
                      // jsx-a11y/no-autofocus：重命名渲染后手动聚焦等价物
                      requestAnimationFrame(() => {
                        containerRef.current
                          ?.querySelector<HTMLInputElement>('input[data-autofocus]')
                          ?.focus();
                      });
                    }}
                    className="rounded-sm p-1 text-ink-muted hover:text-ink"
                  >
                    <Pencil size={12} aria-hidden />
                  </button>
                  <button
                    type="button"
                    aria-label={ch.visible ? t('hideChapterAria', { title: ch.title }) : t('showChapterAria', { title: ch.title })}
                    onClick={() => mutateChapters((list) => list.map((c) => (c.id === ch.id ? { ...c, visible: !c.visible } : c)))}
                    className="rounded-sm p-1 text-ink-muted hover:text-ink"
                  >
                    {ch.visible ? <Eye size={12} aria-hidden /> : <EyeOff size={12} aria-hidden />}
                  </button>
                  <button
                    type="button"
                    aria-label={t('moveChapterUpAria', { title: ch.title })}
                    disabled={index === 0}
                    onClick={() =>
                      mutateChapters((list) => {
                        const next = [...list];
                        [next[index - 1], next[index]] = [next[index], next[index - 1]];
                        return next;
                      })
                    }
                    className="rounded-sm p-1 text-ink-muted hover:text-ink disabled:opacity-30"
                  >
                    <ArrowUp size={12} aria-hidden />
                  </button>
                  <button
                    type="button"
                    aria-label={t('moveChapterDownAria', { title: ch.title })}
                    disabled={index === chapters.length - 1}
                    onClick={() =>
                      mutateChapters((list) => {
                        const next = [...list];
                        [next[index + 1], next[index]] = [next[index], next[index + 1]];
                        return next;
                      })
                    }
                    className="rounded-sm p-1 text-ink-muted hover:text-ink disabled:opacity-30"
                  >
                    <ArrowDown size={12} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {!specView ? (
          <div className="p-8 pb-32 flex flex-col gap-12 font-sans overflow-y-auto overflow-x-hidden flex-1">
            {loadError ? (
              <div role="alert" className="rounded-md border border-status-critical-border bg-status-critical-soft p-5">
                <p className="text-body font-semibold text-status-critical">{t('loadFailed')}</p>
                <p className="mt-2 text-meta text-ink-secondary">{loadError}</p>
                <p className="mt-2 text-meta text-ink-muted">
                  {t('anonShare')}
                </p>
              </div>
            ) : visibleChapters.length === 0 ? (
              <p className="text-body text-ink-muted">{t('empty')}</p>
            ) : (
              visibleChapters.map((ch) => {
                const isActive = ch.id === activeChapter?.id;
                const refs = artifactsByChapter[ch.id] ?? [];
                // 自动派生标题与正文首行重复，不重复渲染；用户改名后才出现 chip。
                const renamed = derived[ch.messageIndex]?.title !== ch.title;
                return (
                  <div key={ch.id}>
                    <article
                      data-story-chapter={ch.id}
                      data-story-active={isActive ? 'true' : undefined}
                      data-story-message
                      className={`prose prose-agent prose-headings:text-status-info prose-a:text-status-info max-w-none transition-opacity duration-700
                        ${ch.role === 'user' ? 'opacity-70 border-l-2 border-edge-subtle pl-4 italic text-body' : 'opacity-100'}
                        ${isActive ? 'story-message-active' : 'story-message-idle'}`}
                    >
                      {renamed ? (
                        <p className="text-meta font-medium tracking-wide text-ink-muted uppercase">◈ {ch.title}</p>
                      ) : null}
                      {ch.role === 'user' ? (
                        <p className="m-0 font-mono">USER: {messages[ch.messageIndex]?.content}</p>
                      ) : (
                        <StoryMarkdown text={messages[ch.messageIndex]?.content ?? ''} />
                      )}
                    </article>
                    {refs.length > 0 ? (
                      <div className="mt-2 space-y-2" data-testid="chapter-artifacts">
                        {refs.map((ref) => (
                          <ChapterArtifact key={ref} ref={ref} />
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        ) : (
          // ADR-0196 编排模式：滚动驱动叙事列（滚动 → 活跃章节 → 相机漫游 + 看板高亮）
          <StoryNarrator
            chapters={specChapters}
            activeId={specActiveId}
            onActiveChange={setActiveId}
            ariaLabel={t('narratorLabel')}
            renderBody={(ch) => <StoryMarkdown text={ch.text} />}
            className="min-h-0"
          />
        )}
      </div>

      {/* Map Panel (Right) — 章节缓动：容器透明度过渡；reduced-motion 直接跳切。
          immersive 排版：地图铺满全屏（position 进分支，避免 relative/absolute 并存被 CSS 序判负）。 */}
      <div
        className={`flex-1 h-full z-0 shadow-[-20px_0_40px_rgba(0,0,0,0.8)] ${
          immersive ? 'absolute inset-0' : 'relative'
        } ${reducedMotion ? '' : 'transition-opacity duration-500'} ${
          mapFade && !reducedMotion ? 'opacity-70' : 'opacity-100'
        }`}
        data-testid="story-map-container"
      >
        <div className="absolute inset-y-0 left-0 w-32 bg-gradient-to-r from-surface-canvas to-transparent z-10 pointer-events-none" />
        <MapErrorBoundary>
          <MapPanel layers={layers} onRemoveLayer={removeLayer} onToggleLayer={toggleLayer} />
        </MapErrorBoundary>
        {specView ? (
          <div
            data-testid="story-dashboard-mount"
            className={`absolute bottom-4 z-20 w-[320px] max-h-[52%] overflow-y-auto pointer-events-auto ${
              immersive ? 'right-[calc(400px+1rem)] xl:right-[calc(440px+1rem)]' : 'right-4'
            }`}
          >
            <StoryDashboard widgets={specWidgets} highlightedIds={activeWidgetIds} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
