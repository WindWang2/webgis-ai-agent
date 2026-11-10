'use client';

import { useSyncExternalStore } from 'react';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { getCommittedMapSpec, subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';
import { commitComponentPatch } from '@/lib/mapspec/component-mutation';

/**
 * Map Studio ↔ MapSpec 布局投影（PR-C：Studio 是主制图编辑面）。
 *
 * 单一真相：标题/副标题/指北针/比例尺/图例 = committed MapSpec
 * ``layout.components``。Studio 只读这里、只经 user mutation
 * （patch_component + expected_revision CAS，409 superseded 静默收敛）写回；
 * 导出也只读这里 —— 不再有 HUD exportSettings 的平行副本（此前 Studio
 * 改的标题 Agent 看不见，Agent 加的指北针又被 Studio 的 showCompass 掩码
 * 关掉）。DPI/纸张/朝向/格式/作者/数据来源/水印/元数据/经纬网仍是本地
 * 输出参数（输出设备属性，不是制图意图）。
 */

export const STUDIO_TOGGLE_KEYS = ['showCompass', 'showScale', 'showLegend'] as const;
export type StudioToggleKey = (typeof STUDIO_TOGGLE_KEYS)[number];

type ComponentType = MapSpecComponent['type'];

/** toggle → 组件类型族（首个为 upsert 新建时使用的类型）。 */
export const STUDIO_TOGGLE_TYPES: Record<StudioToggleKey, readonly ComponentType[]> = {
  showCompass: ['north_arrow'],
  showScale: ['scale_bar'],
  showLegend: ['legend', 'categorical_legend', 'continuous_colorbar'],
};

export interface StudioLayout {
  /** committed MapSpec 在场（无会话/无 spec 时 Studio 只读展示、不提交）。 */
  hasSpec: boolean;
  title: string;
  subtitle: string;
  showCompass: boolean;
  showScale: boolean;
  showLegend: boolean;
}

function componentsOf(spec: MapSpec | null | undefined): MapSpecComponent[] {
  const list = spec?.layout?.components;
  return Array.isArray(list) ? list : [];
}

function textOf(c: MapSpecComponent | undefined): string {
  if (!c || c.enabled === false) return '';
  const text = c.options?.['text'];
  return typeof text === 'string' ? text : '';
}

/**
 * 纯投影：MapSpec → Studio 视图。缺席的指北针/比例尺/图例视为开启 ——
 * 与导出侧 autofill（export-chrome 类型缺席注入）及 live 默认一致；
 * 存在即以 enabled 为准（图例族任一启用即开）。
 */
export function readStudioLayout(spec: MapSpec | null | undefined): StudioLayout {
  const comps = componentsOf(spec);
  const toggle = (key: StudioToggleKey): boolean => {
    const family = comps.filter((c) => STUDIO_TOGGLE_TYPES[key].includes(c.type));
    if (family.length === 0) return true;
    return family.some((c) => c.enabled !== false);
  };
  return {
    hasSpec: spec != null,
    title: textOf(comps.find((c) => c.type === 'title')),
    subtitle: textOf(comps.find((c) => c.type === 'subtitle')),
    showCompass: toggle('showCompass'),
    showScale: toggle('showScale'),
    showLegend: toggle('showLegend'),
  };
}

let cachedSpec: MapSpec | null | undefined;
let cachedLayout: StudioLayout = readStudioLayout(null);

function snapshot(): StudioLayout {
  const spec = getCommittedMapSpec();
  if (spec !== cachedSpec) {
    cachedSpec = spec;
    cachedLayout = readStudioLayout(spec);
  }
  return cachedLayout;
}

const SERVER_SNAPSHOT = readStudioLayout(null);

/** React 订阅：committed MapSpec 每次回流（agent 提交/用户提交/会话切换）即重投影。 */
export function useStudioLayout(): StudioLayout {
  return useSyncExternalStore(subscribeMapSpecLive, snapshot, () => SERVER_SNAPSHOT);
}

/** 写：标题/副标题文本。空串 → 禁用组件（保留 placement/样式，便于再次开启）。 */
export async function commitStudioText(kind: 'title' | 'subtitle', text: string): Promise<void> {
  const spec = getCommittedMapSpec();
  if (!spec) return;
  const trimmed = text.trim();
  const existing = componentsOf(spec).find((c) => c.type === kind);
  if (!existing && !trimmed) return;
  if (existing && textOf(existing) === trimmed && (existing.enabled !== false) === Boolean(trimmed)) {
    return;
  }
  await commitComponentPatch(existing?.id ?? kind, {
    enabled: Boolean(trimmed),
    ...(trimmed ? { options: { text: trimmed } } : {}),
    ...(existing ? {} : { componentType: kind, upsert: true }),
  });
}

/**
 * 写：装饰开关。族内已有组件 → 逐个 patch enabled（同一串行 CAS 链）；
 * 族缺席 → 按首类型 upsert 新建（关闭时也新建一个 disabled 组件，使「关」
 * 成为 spec 中的显式意图，而非被导出 autofill 重新补回）。
 */
export async function commitStudioToggle(key: StudioToggleKey, enabled: boolean): Promise<void> {
  const spec = getCommittedMapSpec();
  if (!spec) return;
  const types = STUDIO_TOGGLE_TYPES[key];
  const family = componentsOf(spec).filter((c) => types.includes(c.type));
  if (family.length === 0) {
    const type = types[0];
    await commitComponentPatch(type.replace(/_/g, '-'), { enabled, componentType: type, upsert: true });
    return;
  }
  for (const c of family) {
    if ((c.enabled !== false) === enabled) continue;
    await commitComponentPatch(c.id, { enabled });
  }
}

/** Studio 本地输出参数（不进 MapSpec）。 */
const LOCAL_OUTPUT_KEYS = [
  'format', 'paperSize', 'orientation', 'dpi',
  'author', 'dataSource', 'showWatermark', 'showMetadata', 'showGraticules',
  'atlasEnabled', 'atlasDriver', 'atlasLayerId', 'atlasCategoryProperty',
  'atlasIncludeCover', 'atlasTitle', 'atlasBatch',
] as const;

/**
 * 导出请求参数：制图内容（标题/副标题/指北针/比例尺/图例）只取 MapSpec，
 * 本地只贡献输出参数。HUD exportSettings 里的 title/showCompass 等旧字段
 * 不再进入请求 —— 它们曾与 spec 并行，导致「Studio 显示的 ≠ 导出的」。
 */
export function studioExportParams(
  settings: Record<string, unknown>,
  spec: MapSpec | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of LOCAL_OUTPUT_KEYS) {
    if (settings[key] !== undefined) out[key] = settings[key];
  }
  const layout = readStudioLayout(spec);
  out['title'] = layout.title;
  out['subtitle'] = layout.subtitle;
  out['showCompass'] = layout.showCompass;
  out['showScale'] = layout.showScale;
  out['showLegend'] = layout.showLegend;
  return out;
}
