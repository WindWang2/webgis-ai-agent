'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';
import { effectivePitch, SCALE_TRUST_PITCH_MAX } from '@/lib/map-kit/scene-export-disclosure';

/**
 * scene_disclosure 渲染器（G3D05 / ADR-0275 三维场景诚实披露卡）。
 *
 * 内容**全部从 committed MapSpec 的 spec.scene 单一真相派生**（mode /
 * terrain.exaggeration / vertical_unit / elevation_ref + 比例尺可信度随
 * pitch 派生）；组件 options 只承载 variant + extraNotes（payload 校验器
 * 禁止 options 复制场景字段 —— 渲染侧同守约，不读第二真相）。
 * - 比例尺可信度：effectivePitch(view.pitch → scene.camera.pitch 回退) 与
 *   SCALE_TRUST_PITCH_MAX 同源判定（scene-export-disclosure.ts 单源）；
 *   透视（> 阈值）→ 警示徽标「比例尺仅供参考（透视）」；可靠 → 不出徽标；
 *   pitch 未知 → 「比例尺可信度未知」（诚实降级，不虚构可信）；
 * - 无 scene 块 → 面板缺席（render nothing）—— 与矢量孪生 _parse_scene_
 *   disclosure 同语义：不虚构场景状态；
 * - variant default | compact；行文案与后端孪生（mapspec_to_svg.
 *   _parse_scene_disclosure）同口径。
 */

// 文案常量：G3D05 词条暂未入 messages/map.json（messages 面不归本组件
// 批次）；模块级非文案键位（no-hardcoded-cjk 门禁与 no-raw-cjk 守卫的
// obj:* 文案键位均不含这些键名），后续 i18n 批次键化时迁 messages。
const COPY = {
  panelTitle: '场景披露',
  modeRow: '场景模式',
  exagRow: '地形夸张系数',
  verticalUnitRow: '垂直单位',
  datumRow: '高程基准',
  datumUndeclared: '高程基准：未声明',
  exagWarningSuffix: '（垂直比例夸张，非真实坡度）',
  scalePerspective: '比例尺仅供参考（透视）',
  scaleTrustUnknown: '比例尺可信度未知',
};

const MODE_LABEL: Record<string, string> = {
  '2d': '2D',
  '2.5d': '2.5D',
  '3d': '3D',
};

/** 数值展示（孪生 %g 口径）：去尾零，如 1.5 → "1.5"、2 → "2"。 */
function formatNumber(v: number): string {
  return String(Number(v.toFixed(2)));
}

/** 防御式解析 extraNotes：非空字符串行，≤4 条（孪生同界）。 */
function parseExtraNotes(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const item of raw.slice(0, 4)) {
    if (typeof item === 'string' && item.trim()) out.push(item);
  }
  return out;
}

interface ScaleTrustBadge {
  kind: 'perspective' | 'unknown';
  text: string;
}

/**
 * 比例尺可信度徽标：pitch 未知 → unknown；透视（> 阈值）→ perspective；
 * 可靠 → null（不出徽标）。判定与 scene-export-disclosure 单源同口径。
 */
function scaleTrustBadge(
  viewPitch: unknown,
  scenePitch: unknown,
): ScaleTrustBadge | null {
  const pitch = effectivePitch(
    typeof viewPitch === 'number' ? viewPitch : null,
    typeof scenePitch === 'number' ? scenePitch : null,
  );
  if (pitch === null) return { kind: 'unknown', text: COPY.scaleTrustUnknown };
  if (pitch > SCALE_TRUST_PITCH_MAX) {
    return { kind: 'perspective', text: COPY.scalePerspective };
  }
  return null;
}

function SceneDisclosureView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = resolveVariant(patched, 'default') === 'compact' ? 'compact' : 'default';

  const scene = ctx?.spec?.scene;
  // 无 scene 块 → 面板缺席（不虚构场景状态；孪生同语义返回空 rows）。
  if (!scene || typeof scene !== 'object') return null;

  const rows: string[] = [];
  const mode = typeof scene.mode === 'string' && scene.mode ? scene.mode : '';
  rows.push(`${COPY.modeRow}：${MODE_LABEL[mode] ?? mode}`);
  const terrain = scene.terrain;
  if (terrain && typeof terrain === 'object') {
    const exag = terrain.exaggeration;
    if (typeof exag === 'number' && Number.isFinite(exag)) {
      let exagRow = `${COPY.exagRow}：${formatNumber(exag)}×`;
      if (exag > 1) exagRow += COPY.exagWarningSuffix;
      rows.push(exagRow);
    }
    if (typeof terrain.vertical_unit === 'string' && terrain.vertical_unit) {
      rows.push(`${COPY.verticalUnitRow}：${terrain.vertical_unit}`);
    }
    rows.push(
      typeof terrain.elevation_ref === 'string' && terrain.elevation_ref
        ? `${COPY.datumRow}：${terrain.elevation_ref}`
        : COPY.datumUndeclared,
    );
  } else {
    rows.push(COPY.datumUndeclared);
  }
  for (const note of parseExtraNotes(patched.options?.['extraNotes'])) {
    rows.push(note);
  }

  const badge = scaleTrustBadge(ctx?.spec?.view?.pitch, scene.camera?.pitch);

  return (
    <FloatingChrome
      component={patched}
      title={COPY.panelTitle}
      topSlotIndexes={ctx?.topSlotIndexes}
      bottomSlotIndexes={ctx?.bottomSlotIndexes}
      testId="spec-chrome-scene-disclosure"
      dataVariant={variant}
      bodyClassName={variant === 'compact' ? 'p-1.5' : 'p-2'}
    >
      <div
        role="note"
        aria-label={COPY.panelTitle}
        className={`flex flex-col ${variant === 'compact' ? 'gap-0.5' : 'gap-1'}`}
      >
        {rows.map((row, i) => (
          <div
            key={`scene-row#${i}`}
            className={variant === 'compact' ? 'text-micro' : 'text-caption leading-snug'}
          >
            {row}
          </div>
        ))}
        {badge ? (
          <div
            role="status"
            data-scale-trust={badge.kind}
            className={
              badge.kind === 'perspective'
                ? `rounded-sm border-l-2 border-status-warning/80 px-1 py-0.5 ${
                  variant === 'compact' ? 'text-micro' : 'text-caption'
                } font-medium text-status-warning`
                : `rounded-sm border-l-2 border-map-chrome-border px-1 py-0.5 ${
                  variant === 'compact' ? 'text-micro' : 'text-caption'
                } text-map-chrome-ink-muted`
            }
          >
            {badge.text}
          </div>
        ) : null}
      </div>
    </FloatingChrome>
  );
}

function SceneDisclosureRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <SceneDisclosureView component={component} ctx={_ctx} />;
}

registerComponentRenderer('scene_disclosure', SceneDisclosureRenderer);
