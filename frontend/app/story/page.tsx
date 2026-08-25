"use client"
import { useState, useEffect, useRef, useCallback, Suspense } from "react"
import { useSearchParams } from "next/navigation"
import dynamic from "next/dynamic"

const MapPanel = dynamic(
  () => import('@/components/map/map-panel').then((m) => ({ default: m.MapPanel })),
  { ssr: false, loading: () => <div className="w-full h-full bg-surface-canvas animate-pulse" /> }
)

const StoryMarkdown = dynamic(() => import('@/components/chat/story-markdown'), { ssr: false })

import { devOnly } from "@/lib/utils/logger";
import { useHudStore } from "@/lib/store/useHudStore";
import { apiFetch, describeApiError } from "@/lib/api/transport";
import { useMapAction } from "@/lib/contexts/map-action-context";
// #552: 地图还原（视口 + 底图 + 图层）在 lib/session/map-state-restore ——
// Next.js 页面文件只允许导出 page 组件与 route-segment 配置，helper 不得
// 作为页面导出（CI `next build` 会拒绝非 Page 导出字段）。
import { applyStoryMapState, type SessionMapState } from "@/lib/session/map-state-restore";
import { useToastStore } from "@/components/ui/toast";
import { Pause, Play, SkipBack, Share2 } from "lucide-react"

/** 播放模式下逐条消息推进的间隔（ms）。 */
const PLAY_INTERVAL_MS = 2500;

export default function StoryPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen text-ink-muted">Loading...</div>}>
      <StoryPageInner />
    </Suspense>
  )
}

function StoryPageInner() {
  const searchParams = useSearchParams()
  const sessionId = searchParams.get("session_id")

  const [messages, setMessages] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  // #552: 之前恢复失败被 isApiError 过滤器静默吞掉 → 匿名 / 无权限分享时整页
  // 空白无任何交代。现在任何失败都进入可见错误态。
  const [loadError, setLoadError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const layers = useHudStore((s) => s.layers)
  const removeLayer = useHudStore((s) => s.removeLayer)
  const toggleLayer = useHudStore((s) => s.toggleLayer)
  const { dispatchAction } = useMapAction()

  // 播放器状态：activeIndex 驱动 scroll；playing 用自续期 timeout 逐条推进。
  const [activeIndex, setActiveIndex] = useState(0)
  const [playing, setPlaying] = useState(false)

  const scrollToMessage = useCallback((index: number) => {
    const container = containerRef.current;
    if (!container) return;
    const nodes = container.querySelectorAll<HTMLElement>('[data-story-message]');
    const target = nodes[index];
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  // #552: 播放按钮此前无 onClick —— 接上逐条消息自动推进；到头自动停止。
  useEffect(() => {
    if (!playing) return;
    if (messages.length === 0 || activeIndex >= messages.length - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setActiveIndex((cur) => Math.min(messages.length - 1, cur + 1)), PLAY_INTERVAL_MS);
    return () => clearTimeout(t);
  }, [playing, activeIndex, messages.length]);

  useEffect(() => {
    scrollToMessage(activeIndex);
  }, [activeIndex, scrollToMessage]);

  // #552: 分享按钮此前无 onClick —— 复制当前回放 URL 到剪贴板。
  const handleShare = useCallback(() => {
    const url = typeof window !== 'undefined' ? window.location.href : '';
    if (!url) return;
    const report = (ok: boolean) => {
      useToastStore.getState().addToast(
        ok ? '已复制分享链接' : '复制失败，请手动复制地址栏链接',
        ok ? 'success' : 'error'
      );
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(() => report(true)).catch(() => report(false));
    } else {
      report(false);
    }
  }, []);

  const handlePrevious = useCallback(() => {
    setPlaying(false);
    setActiveIndex((cur) => Math.max(0, cur - 1));
  }, []);

  const togglePlay = useCallback(() => {
    if (playing) {
      setPlaying(false);
      return;
    }
    // U-7（#889）：续播 —— 暂停后再按播放从当前消息继续；仅当已播到最后
    // 一条时回到开头（此前每次播放都强制 setActiveIndex(0)，暂停在第 5 条
    // 再按播放跳回第 1 条，违背播放/暂停按钮直觉；从头重播由 Previous 键
    // 逐条回退承担）。
    setActiveIndex((cur) => (cur >= messages.length - 1 ? 0 : cur));
    setPlaying(true);
  }, [playing, messages.length]);

  useEffect(() => {
    const controller = new AbortController();
    // 每次 sessionId 变化：清掉上一会话的地图残留，避免分享页串会话。
    useHudStore.getState().clearLayers();
    setLoadError(null);
    setMessages([]);
    setPlaying(false);
    setActiveIndex(0);

    if (!sessionId) {
      setLoading(false);
      setMessages([
        { role: "assistant", content: "# StoryMap 回放模式\n以叙事形式重现 GeoAgent 的分析推演过程。" },
        { role: "assistant", content: "您可以尝试在 URL 中追加 `?session_id=您的会话ID` 来回放之前的分析推演。" },
      ]);
      return () => controller.abort();
    }

    setLoading(true);
    (async () => {
      try {
        const data = await apiFetch<{ messages?: any[] }>(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`, {
          signal: controller.signal,
          label: 'Story session error',
        });
        if (controller.signal.aborted) return;
        setMessages(data.messages && data.messages.length > 0 ? data.messages : []);

        // 地图：恢复会话 map-state（观察态优先），让分享页渲染同一份最终快照。
        const stateData = await apiFetch<{ map_state?: SessionMapState }>(
          `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/map-state`,
          { signal: controller.signal, label: 'Story map state error' }
        );
        if (controller.signal.aborted) return;
        if (stateData?.map_state) {
          await applyStoryMapState(stateData.map_state, sessionId, controller.signal, dispatchAction);
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        // #552: 不静默 —— ApiError（匿名/无权限 404 等）同样渲染成错误态。
        setLoadError(describeApiError(err, '加载会话失败'));
        devOnly.error('Story session load failed:', err);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();

    return () => controller.abort();
  }, [sessionId, dispatchAction])

  // ScrollSpy - Parse specific locations from markdown text and fly to them
  const handleScroll = () => {
    // Advanced ScrollSpy logic can be added here to trigger camera flyTo
    // based on visible Markdown headers.
  }

  if (loading) {
    return <div className="h-screen w-screen bg-surface-canvas flex items-center justify-center text-status-info font-mono relative">
      <div className="absolute inset-0 z-[1] opacity-[0.015] bg-grid-agent bg-[size:60px_60px]"></div>
      <div className="animate-pulse">Loading StoryMap CNS...</div>
    </div>
  }

  return (
    <div className="h-screen w-screen overflow-hidden bg-surface-canvas relative flex">
      {/* Grid overlay for depth */}
      <div className="absolute inset-0 pointer-events-none z-[1] opacity-[0.015] bg-grid-agent bg-[size:60px_60px]" />

      {/* Narrative Panel (Left) */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="w-[400px] xl:w-[500px] h-full z-20 bg-surface-panel border-r border-edge-subtle overflow-y-auto overflow-x-hidden flex flex-col relative"
      >
        <div className="sticky top-0 p-6 bg-surface-panel border-b border-edge-subtle z-10 flex justify-between items-center">
          <h1 className="text-status-info font-semibold tracking-widest text-heading flex items-center gap-2">
            STORY<span className="text-ink-muted">MAP</span>
          </h1>
          <div className="flex gap-2">
            {/* #552: 三个按钮此前全部无 onClick —— 接上真实行为：上一条 /
                播放暂停（逐条自动推进）/ 复制分享链接。 */}
            <button
              aria-label="上一个"
              title="上一条消息"
              onClick={handlePrevious}
              disabled={messages.length === 0 || activeIndex === 0}
              className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info disabled:opacity-40 disabled:pointer-events-none"
            >
              <SkipBack className="h-4 w-4" />
            </button>
            <button
              aria-label={playing ? '暂停' : '播放'}
              title={playing ? '暂停播放' : '播放故事'}
              onClick={togglePlay}
              disabled={messages.length === 0}
              className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info disabled:opacity-40 disabled:pointer-events-none"
            >
              {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            </button>
            <button
              aria-label="分享"
              title="复制分享链接"
              onClick={handleShare}
              className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"
            >
              <Share2 className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="p-8 pb-32 flex flex-col gap-12 font-sans">
          {loadError ? (
            <div role="alert" className="rounded-md border border-status-critical-border bg-status-critical-soft p-5">
              <p className="text-body font-semibold text-status-critical">无法加载该会话</p>
              <p className="mt-2 text-meta text-ink-secondary">{loadError}</p>
              <p className="mt-2 text-meta text-ink-muted">
                匿名会话暂不支持跨页面分享（出于安全考虑，不将会话凭证放入 URL）；请登录后重试，或确认链接中的会话 ID 是否正确。
              </p>
            </div>
          ) : messages.length === 0 ? (
            <p className="text-body text-ink-muted">该会话暂无内容。</p>
          ) : (
            messages.map((msg, idx) => (
              <div
                key={idx}
                data-story-message
                data-story-active={idx === activeIndex ? 'true' : undefined}
                className={`prose prose-agent prose-headings:text-status-info prose-a:text-status-info max-w-none transition-opacity duration-700
                  ${msg.role === 'user' ? 'opacity-50 border-l-2 border-edge-subtle pl-4 italic text-body' : 'opacity-100'}
                  ${idx === activeIndex ? 'story-message-active' : (msg.role === 'user' ? '' : 'story-message-idle')}`}
              >
                {msg.role === 'user' ? (
                  <p className="m-0 font-mono">USER: {msg.content}</p>
                ) : (
                  <StoryMarkdown text={msg.content} />
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Map Panel (Right) */}
      <div className="flex-1 h-full relative z-0 relative shadow-[-20px_0_40px_rgba(0,0,0,0.8)]">
        {/* Adds Cinematic Gradient */}
        <div className="absolute inset-y-0 left-0 w-32 bg-gradient-to-r from-surface-canvas to-transparent z-10 pointer-events-none" />

        <MapPanel
          layers={layers}
          onRemoveLayer={removeLayer}
          onToggleLayer={toggleLayer}
        />
      </div>
    </div>
  )
}
