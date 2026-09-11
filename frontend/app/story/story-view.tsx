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

/** 播放模式下逐章节推进的间隔（ms）；PDF 逐章定位等待同源。 */
const PLAY_INTERVAL_MS = 2500;
const PDF_SETTLE_MS = 900;

interface StoryMessage {
  role: 'user' | 'assistant' | 'system' | string;
  content: string;
}

export function StoryView(): React.ReactElement {
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

  // 播放器
  const [activeId, setActiveId] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [pdfProgress, setPdfProgress] = useState<string | null>(null);
  const [mapFade, setMapFade] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const sessionIdRef = useRef<string | null>(sessionId);
  sessionIdRef.current = sessionId;

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
      const target = visibleChapters[pos];
      if (target) setActiveId(target.id);
    },
    [visibleChapters],
  );

  // 播放：自续期 timeout 逐章节推进（#552 续播语义保留）；到头自停。
  useEffect(() => {
    if (!playing) return;
    if (visibleChapters.length === 0 || activePos >= visibleChapters.length - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => {
      const next = visibleChapters[activePos + 1];
      if (next) setActiveId(next.id);
    }, PLAY_INTERVAL_MS);
    return () => clearTimeout(t);
  }, [playing, activePos, visibleChapters]);

  // 章节切换：滚动跟随 + 地图相机缓动（fly_to；reduced-motion 只做定位级
  // 跳切，不做装饰性过渡）+ 图层容器轻淡入。
  useEffect(() => {
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
  }, [activePos, reducedMotion]);

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
        { role: 'assistant', content: '# StoryMap 回放模式\n以叙事形式重现 GeoAgent 的分析推演过程。' },
        { role: 'assistant', content: '您可以尝试在 URL 中追加 `?session_id=您的会话ID` 来回放之前的分析推演。' },
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
    setActiveId((cur) => {
      const atEnd = cur !== null && visibleChapters.length > 0 && visibleChapters[visibleChapters.length - 1].id === cur;
      return atEnd ? visibleChapters[0].id : (cur ?? visibleChapters[0]?.id ?? null);
    });
    setPlaying(true);
  }, [playing, visibleChapters]);

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
    if (visibleChapters.length === 0) {
      useToastStore.getState().addToast('没有可导出的章节', 'error');
      return;
    }
    setPlaying(false);
    setPdfProgress('准备导出…');
    try {
      const blob = await exportNarrativePdf(
        visibleChapters.map((c) => ({ id: c.id, title: c.title })),
        async (chapter) => {
          setActiveId(chapter.id);
          // 等待 fly_to 相机与图层渲染稳定（经验窗；GL 渲染无完成事件可等）
          await new Promise((r) => setTimeout(r, PDF_SETTLE_MS));
          return captureMapCanvas(map);
        },
        'GeoAgent 叙事导出',
        (p) => setPdfProgress(`渲染章节 ${p.current}/${p.total}：${p.chapterTitle}`),
      );
      downloadBlob(blob, `storymap-narrative-${Date.now()}.pdf`);
      useToastStore.getState().addToast(`叙事 PDF 已导出（${visibleChapters.length} 章）`, 'success');
    } catch (err) {
      devOnly.error('narrative pdf failed:', err);
      useToastStore.getState().addToast(describeApiError(err, '叙事 PDF 导出失败'), 'error');
    } finally {
      setPdfProgress(null);
    }
  }, [getMapInstance, visibleChapters]);

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

      {/* Narrative Panel (Left) */}
      <div
        ref={containerRef}
        className="w-[400px] xl:w-[500px] h-full z-20 bg-surface-panel border-r border-edge-subtle overflow-y-auto overflow-x-hidden flex flex-col relative"
      >
        <div className="sticky top-0 p-4 bg-surface-panel border-b border-edge-subtle z-10">
          <div className="flex justify-between items-center">
            <h1 className="text-status-info font-semibold tracking-widest text-heading flex items-center gap-2">
              STORY<span className="text-ink-muted">MAP</span>
            </h1>
            <div className="flex gap-1">
              <button
                aria-label="分享"
                title="复制分享链接"
                onClick={handleShare}
                className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"
              >
                <Share2 className="h-4 w-4" />
              </button>
              <button
                aria-label="生成分享卡"
                title="生成 OG 分享卡（PNG）"
                onClick={() => void handleShareCard()}
                className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"
              >
                <ImageDown className="h-4 w-4" />
              </button>
              <button
                aria-label="导出叙事 PDF"
                title="逐章快照导出叙事 PDF"
                onClick={() => void handleNarrativePdf()}
                disabled={Boolean(pdfProgress)}
                className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info disabled:opacity-40"
              >
                <FileDown className="h-4 w-4" />
              </button>
              <button
                aria-label="章节编排"
                title="章节重排 / 重命名 / 隐藏"
                aria-expanded={orchPanelOpen}
                onClick={() => setOrchPanelOpen((v) => !v)}
                className={`rounded-md p-2 transition-colors hover:bg-surface-hover ${
                  orchPanelOpen ? 'text-status-info' : 'text-ink-muted hover:text-status-info'
                }`}
              >
                <ListTree className="h-4 w-4" />
              </button>
            </div>
          </div>
          <ChapterScrubber
            total={visibleChapters.length}
            activeIndex={activePos}
            titles={visibleChapters.map((c) => c.title)}
            playing={playing}
            onSeek={seek}
            onTogglePlay={togglePlay}
            onPrev={() => {
              setPlaying(false);
              seek(Math.max(0, activePos - 1));
            }}
            onNext={() => {
              setPlaying(false);
              seek(Math.min(visibleChapters.length - 1, activePos + 1));
            }}
          />
          {pdfProgress ? (
            <p role="status" className="px-1 pb-1 text-caption text-status-info" data-testid="story-pdf-progress">
              {pdfProgress}
            </p>
          ) : null}
        </div>

        {/* 章节编排面板 */}
        {orchPanelOpen ? (
          <div className="border-b border-edge-subtle bg-surface-sunken p-3" data-testid="story-orchestration">
            <div className="flex items-center justify-between pb-2">
              <h2 className="text-caption font-medium uppercase tracking-wide text-ink-muted">章节编排（本地保存）</h2>
              <button
                type="button"
                aria-label="关闭章节编排"
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
                      aria-label="章节名称"
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
                    aria-label={`重命名章节 ${ch.title}`}
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
                    aria-label={ch.visible ? `隐藏章节 ${ch.title}` : `显示章节 ${ch.title}`}
                    onClick={() => mutateChapters((list) => list.map((c) => (c.id === ch.id ? { ...c, visible: !c.visible } : c)))}
                    className="rounded-sm p-1 text-ink-muted hover:text-ink"
                  >
                    {ch.visible ? <Eye size={12} aria-hidden /> : <EyeOff size={12} aria-hidden />}
                  </button>
                  <button
                    type="button"
                    aria-label={`上移章节 ${ch.title}`}
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
                    aria-label={`下移章节 ${ch.title}`}
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

        <div className="p-8 pb-32 flex flex-col gap-12 font-sans">
          {loadError ? (
            <div role="alert" className="rounded-md border border-status-critical-border bg-status-critical-soft p-5">
              <p className="text-body font-semibold text-status-critical">无法加载该会话</p>
              <p className="mt-2 text-meta text-ink-secondary">{loadError}</p>
              <p className="mt-2 text-meta text-ink-muted">
                匿名会话暂不支持跨页面分享（出于安全考虑，不将会话凭证放入 URL）；请登录后重试，或确认链接中的会话 ID 是否正确。
              </p>
            </div>
          ) : visibleChapters.length === 0 ? (
            <p className="text-body text-ink-muted">该会话暂无内容。</p>
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
      </div>

      {/* Map Panel (Right) — 章节缓动：容器透明度过渡；reduced-motion 直接跳切 */}
      <div
        className={`flex-1 h-full relative z-0 shadow-[-20px_0_40px_rgba(0,0,0,0.8)] ${
          reducedMotion ? '' : 'transition-opacity duration-500'
        } ${mapFade && !reducedMotion ? 'opacity-70' : 'opacity-100'}`}
        data-testid="story-map-container"
      >
        <div className="absolute inset-y-0 left-0 w-32 bg-gradient-to-r from-surface-canvas to-transparent z-10 pointer-events-none" />
        <MapErrorBoundary>
          <MapPanel layers={layers} onRemoveLayer={removeLayer} onToggleLayer={toggleLayer} />
        </MapErrorBoundary>
      </div>
    </div>
  );
}
