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

/** 响应形状门卫：不合法的 spec 一律按「无编排」处理（静默降级）。

    深校验（对抗评审加固）：chapters id 非空且唯一、camera_keyframes 的
    center 为有限数值对且 zoom/pitch/bearing 有限、linked_widgets 带非空 id。
    只查 schema_version/chapters 的旧实现会让缺字段 keyframe 穿过门卫，
    在 specToNarratorView 解引用时把整个 /story 渲染打崩。
*/
export function isValidStorySpecDto(value: unknown): value is StoryMapSpecDto {
  // Total guard contract: malformed DTOs return false for the silent local
  // fallback. Iterating an optional field without first proving it is an array
  // can throw TypeError, so every branch below must stay inside this boundary.
  try {
    if (!value || typeof value !== 'object') return false;
    const v = value as Partial<StoryMapSpecDto>;
    if (typeof v.schema_version !== 'string') return false;
    if (!Array.isArray(v.chapters) || v.chapters.length === 0) return false;
    const ids = new Set<string>();
    for (const c of v.chapters) {
      if (!c || typeof c.id !== 'string' || c.id.trim() === '') return false;
      if (typeof c.title !== 'string') return false;
      if (typeof c.narrative !== 'string') return false;
      if (ids.has(c.id)) return false;
      ids.add(c.id);
    }
    if (v.metadata !== undefined) {
      if (!v.metadata || typeof v.metadata !== 'object') return false;
      if (v.metadata.title !== undefined && typeof v.metadata.title !== 'string') return false;
      if (v.metadata.summary !== undefined && typeof v.metadata.summary !== 'string') return false;
    }
    if (v.camera_keyframes !== undefined && !Array.isArray(v.camera_keyframes)) return false;
    for (const kf of v.camera_keyframes ?? []) {
      if (!kf || typeof kf.chapter_id !== 'string' || kf.chapter_id === '') return false;
      if (!isFinitePair(kf.center)) return false;
      if (![kf.zoom, kf.pitch, kf.bearing].every((n) => Number.isFinite(n))) return false;
    }
    if (v.linked_widgets !== undefined && !Array.isArray(v.linked_widgets)) return false;
    for (const w of v.linked_widgets ?? []) {
      if (!w || typeof w.id !== 'string' || w.id === '') return false;
      if (w.title !== undefined && typeof w.title !== 'string') return false;
      if (w.data !== undefined && (!w.data || typeof w.data !== 'object' || Array.isArray(w.data))) return false;
    }
    return true;
  } catch {
    return false;
  }
}

function isFinitePair(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
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
  // 同章多帧取 t 最大者（章节落点镜头）；旧实现 `(kf.t ?? 0) >= 0` 恒真
  // = 死逻辑（恒取数组末条），多帧一上就静默取错相机。
  const picked = new Map<string, { t: number; camera: NonNullable<NarratorChapter['camera']> }>();
  for (const kf of spec.camera_keyframes ?? []) {
    const t = typeof kf.t === 'number' && Number.isFinite(kf.t) ? kf.t : 0;
    const cur = picked.get(kf.chapter_id);
    if (!cur || t >= cur.t) {
      picked.set(kf.chapter_id, {
        t,
        camera: {
          center: [kf.center[0], kf.center[1]],
          zoom: kf.zoom,
          pitch: kf.pitch,
          bearing: kf.bearing,
          t,
        },
      });
    }
  }
  const cameras: StorySpecView['cameras'] = {};
  picked.forEach((entry, chapterId) => {
    cameras[chapterId] = entry.camera;
  });
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
