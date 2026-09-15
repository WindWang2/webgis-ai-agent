/**
 * StoryMap 编排 API 客户端（ADR-0196 §6）。
 *
 * DTO 是后端 StoryMapSpec 的手写镜像（snake_case 原样透传，additive-only）；
 * isValidStorySpecDto 是防垃圾响应的门卫 —— /story 对后端编排严格降级：
 * 编译失败/形状不符一律回退 ADR-0147 本地派生。
 */
import { apiFetch, apiFetchBlob } from '@/lib/api/transport';
import type { DashboardWidget } from '@/components/story/story-dashboard';
import type { NarratorChapter } from '@/components/story/story-narrator';

export interface StoryChapterDto {
  id: string;
  title: string;
  narrative: string;
  arc_role: string;
  source_stage_ids?: number[];
  linked_widget_ids?: string[];
  highlight_refs?: string[];
  duration_hint_s?: number;
}

export interface CameraKeyframeDto {
  chapter_id: string;
  t: number;
  center: [number, number];
  zoom: number;
  pitch: number;
  bearing: number;
  easing?: string;
}

export interface LinkedWidgetDto {
  id: string;
  kind: 'chart' | 'table' | 'kpi' | 'stats' | string;
  ref?: string;
  title?: string;
  chapter_id?: string;
  data?: Record<string, unknown>;
}

export interface StoryMapSpecDto {
  schema_version: string;
  metadata?: {
    title?: string;
    summary?: string;
    session_id?: string;
    turn_id?: string;
    generated_at?: string;
  };
  chapters: StoryChapterDto[];
  camera_keyframes?: CameraKeyframeDto[];
  linked_widgets?: LinkedWidgetDto[];
}

export interface StoryCompilePayload {
  session_id?: string;
  turn_id?: string;
  trace?: Record<string, unknown>;
  messages?: Array<{ role: string; content: string }>;
  title?: string;
}

/** POST /api/v1/storymap/compile —— 证据链/消息 → StoryMapSpec。 */
export function compileStorySpec(
  payload: StoryCompilePayload,
  init?: { signal?: AbortSignal },
): Promise<StoryMapSpecDto> {
  return apiFetch<StoryMapSpecDto>('/api/v1/storymap/compile', {
    method: 'POST',
    body: payload as unknown as Record<string, unknown>,
    label: 'StoryMap 编译失败',
    signal: init?.signal,
  });
}

/** POST /api/v1/storymap/export —— 自包含离线专报（HTML 单文件 Blob）。 */
export function exportStoryBundle(
  spec: StoryMapSpecDto,
  options?: { layers?: Array<Record<string, unknown>>; mapspec?: Record<string, unknown> },
): Promise<{ blob: Blob; filename: string | null }> {
  return apiFetchBlob('/api/v1/storymap/export', {
    method: 'POST',
    body: {
      spec: spec as unknown as Record<string, unknown>,
      layers: options?.layers ?? [],
      format: 'html',
    },
    label: 'StoryMap 导出失败',
  });
}

/** 响应形状门卫：不合法的 spec 一律按「无编排」处理（静默降级）。 */
export function isValidStorySpecDto(value: unknown): value is StoryMapSpecDto {
  if (!value || typeof value !== 'object') return false;
  const v = value as Partial<StoryMapSpecDto>;
  return (
    typeof v.schema_version === 'string' &&
    Array.isArray(v.chapters) &&
    v.chapters.length > 0 &&
    v.chapters.every(
      (c) => c && typeof c.id === 'string' && typeof c.narrative === 'string',
    )
  );
}

export interface StorySpecView {
  title: string;
  summary: string;
  chapters: NarratorChapter[];
  /** chapter_id → 相机关键帧（取该章 t 最大者，即章节落点镜头）。 */
  cameras: Record<string, NarratorChapter['camera']>;
  widgets: DashboardWidget[];
}

/** DTO → 视图模型（narrator / dashboard 消费）。 */
export function specToNarratorView(spec: StoryMapSpecDto): StorySpecView {
  const cameras: StorySpecView['cameras'] = {};
  for (const kf of spec.camera_keyframes ?? []) {
    const cur = cameras[kf.chapter_id];
    if (!cur || (kf.t ?? 0) >= 0) {
      cameras[kf.chapter_id] = {
        center: [kf.center[0], kf.center[1]],
        zoom: kf.zoom,
        pitch: kf.pitch,
        bearing: kf.bearing,
      };
    }
  }
  const chapters: NarratorChapter[] = (spec.chapters ?? []).map((c) => ({
    id: c.id,
    title: c.title,
    text: c.narrative,
    arcRole: c.arc_role,
    camera: cameras[c.id],
    widgetIds: c.linked_widget_ids ?? [],
    durationHintS: c.duration_hint_s,
  }));
  const widgets: DashboardWidget[] = (spec.linked_widgets ?? []).map((w) => ({
    id: w.id,
    kind: (w.kind as DashboardWidget['kind']) ?? 'chart',
    title: w.title ?? '',
    data: (w.data as Record<string, unknown>) ?? {},
  }));
  return {
    title: spec.metadata?.title ?? 'StoryMap',
    summary: spec.metadata?.summary ?? '',
    chapters,
    cameras,
    widgets,
  };
}
