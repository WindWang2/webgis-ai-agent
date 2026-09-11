'use client';

import type { SessionMapState } from '@/lib/session/map-state-restore';

/**
 * StoryMap 章节模型（ADR-0147）。
 *
 * 派生模型：章节默认从会话消息逐条派生（1 消息 = 1 章节候选，与既有线性
 * 播放器同序）；用户编排（重排/重命名/隐藏）以 **覆盖层** 形式持久化到
 * localStorage（key 含 sessionId），不篡改会话真相源（后端 transcripts）。
 * 兼容读取：编排里引用的 messageIndex 越界 / 缺失时按派生序兜底（回滚面
 * = 覆盖层，删除编排即回默认视图）。
 */

export interface StoryChapter {
  /** 稳定 id：`ch-{messageIndex}`（编排层引用它，而非下标）。 */
  id: string;
  messageIndex: number;
  /** 默认标题从内容派生；用户改名后为 override。 */
  title: string;
  visible: boolean;
  role: 'user' | 'assistant' | string;
}

/** localStorage 编排覆盖层 schema（v1）。 */
export interface ChapterOrchestration {
  version: 1;
  /** key = chapter id（ch-{messageIndex}）。 */
  overrides: Record<
    string,
    {
      title?: string;
      visible?: boolean;
      /** 编排后的显示顺序；缺省按派生序。 */
      order?: number;
    }
  >;
}

export function chapterIdFor(messageIndex: number): string {
  return `ch-${messageIndex}`;
}

/** 从消息内容派生默认标题：首个 markdown 标题 > 首行截断。 */
export function deriveTitle(content: string, role: string): string {
  const heading = content.match(/^#{1,4}\s+(.{1,60})$/m);
  if (heading) return heading[1].trim();
  const firstLine =
    content
      .split('\n')
      .map((l) => l.trim())
      .find((l) => l.length > 0) ?? '';
  const stripped = firstLine.replace(/^USER:\s*/i, '').replace(/[#*`>\-]/g, '').trim();
  const prefix = role === 'user' ? '问：' : '';
  return prefix + (stripped.slice(0, 24) || '未命名章节');
}

export function deriveChapters(messages: Array<{ role: string; content: string }>): StoryChapter[] {
  return messages.map((msg, index) => ({
    id: chapterIdFor(index),
    messageIndex: index,
    title: deriveTitle(msg.content ?? '', msg.role),
    visible: true,
    role: msg.role,
  }));
}

/** 应用编排覆盖层（越界/缺失按派生序兜底 —— 兼容读取）。 */
export function applyOrchestration(
  chapters: StoryChapter[],
  orch: ChapterOrchestration | null,
): StoryChapter[] {
  if (!orch || orch.version !== 1) return chapters;
  const overlaid = chapters.map((ch) => {
    const o = orch.overrides[ch.id];
    if (!o) return ch;
    return {
      ...ch,
      title: o.title ?? ch.title,
      visible: o.visible ?? ch.visible,
    };
  });
  const orderOf = (ch: StoryChapter): number => orch.overrides[ch.id]?.order ?? ch.messageIndex;
  return [...overlaid].sort((a, b) => orderOf(a) - orderOf(b));
}

const ORCH_KEY_PREFIX = 'geoagent-story-orchestration-v1:';

export function loadOrchestration(sessionId: string | null): ChapterOrchestration | null {
  if (!sessionId || typeof localStorage === 'undefined') return null;
  try {
    const raw = localStorage.getItem(ORCH_KEY_PREFIX + sessionId);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return parsed as ChapterOrchestration;
  } catch {
    return null;
  }
}

export function saveOrchestration(sessionId: string, orch: ChapterOrchestration): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(ORCH_KEY_PREFIX + sessionId, JSON.stringify(orch));
  } catch {
    /* 配额/隐私模式：编排降级为会话内态 */
  }
}

// ── 产物（artifact）ref 与相机提取 ──

const REF_RE = /\bref:(?:chart|table|stats|grid|admin)-[A-Za-z0-9_-]{1,64}\b/g;

/** 从文本提取产物 ref（去重，保持出现序）。 */
export function collectRefsFromText(text: string): string[] {
  const hits = text?.match(REF_RE) ?? [];
  return [...new Set(hits)];
}

/** 深走 JSON 树收集产物 ref（mapspec schema 松散，按值扫描抗漂移）。 */
export function collectRefsFromValue(value: unknown, seen = new Set<unknown>()): string[] {
  if (typeof value === 'string') return collectRefsFromText(value);
  if (value === null || typeof value !== 'object' || seen.has(value)) return [];
  seen.add(value);
  const out: string[] = [];
  if (Array.isArray(value)) {
    for (const item of value) out.push(...collectRefsFromValue(item, seen));
  } else {
    for (const v of Object.values(value as Record<string, unknown>)) {
      out.push(...collectRefsFromValue(v, seen));
    }
  }
  return [...new Set(out)];
}

/**
 * 章节产物归属：消息文本提及的 ref 归该章节；mapstate（会话终态 mapspec）
 * 里剩余的 ref 追加到最后一个可见章节（产物附录），不丢任何产物。
 */
export function assignChapterArtifacts(
  messages: Array<{ role: string; content: string }>,
  mapState: SessionMapState | null,
): Record<string, string[]> {
  const byChapter: Record<string, string[]> = {};
  const claimed = new Set<string>();
  messages.forEach((msg, index) => {
    const refs = collectRefsFromText(msg.content ?? '');
    if (refs.length) {
      byChapter[chapterIdFor(index)] = refs;
      refs.forEach((r) => claimed.add(r));
    }
  });
  const rest = collectRefsFromValue(mapState).filter((r) => !claimed.has(r));
  if (rest.length && messages.length > 0) {
    const last = chapterIdFor(messages.length - 1);
    byChapter[last] = [...(byChapter[last] ?? []), ...rest];
  }
  return byChapter;
}

export interface ChapterCamera {
  center: [number, number];
  zoom?: number;
}

/**
 * 从消息内容提取 fly_to 相机（```json 围栏里的地图动作）。
 * 解析失败一律返回 null（章节回退为「保持当前相机」，不猜）。
 */
export function extractFlyTo(content: string): ChapterCamera | null {
  if (!content) return null;
  const fenceRe = /```json\s*([\s\S]*?)```/g;
  let m: RegExpExecArray | null;
  while ((m = fenceRe.exec(content)) !== null) {
    try {
      const parsed: unknown = JSON.parse(m[1]);
      if (parsed && typeof parsed === 'object') {
        const obj = parsed as { command?: string; params?: { center?: unknown; zoom?: unknown } };
        if (
          typeof obj.command === 'string' &&
          obj.command.toLowerCase() === 'fly_to' &&
          obj.params &&
          Array.isArray(obj.params.center) &&
          obj.params.center.length >= 2 &&
          obj.params.center.every((n) => typeof n === 'number')
        ) {
          return {
            center: [obj.params.center[0], obj.params.center[1]],
            zoom: typeof obj.params.zoom === 'number' ? obj.params.zoom : undefined,
          };
        }
      }
    } catch {
      /* 非 JSON 围栏跳过 */
    }
  }
  return null;
}

export function extractChapterCameras(messages: Array<{ content: string }>): Record<string, ChapterCamera | null> {
  const out: Record<string, ChapterCamera | null> = {};
  messages.forEach((msg, index) => {
    out[chapterIdFor(index)] = extractFlyTo(msg.content ?? '');
  });
  return out;
}
