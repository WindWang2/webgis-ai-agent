/**
 * StoryMap DTO 门卫与视图适配纯函数测试（ADR-0196 加固）。
 *
 * 对抗评审发现：旧门卫只查 schema_version/chapters[].{id,narrative}，
 * camera_keyframes 缺字段会穿过门卫并在 specToNarratorView 解引用时抛错
 * （渲染期崩溃，无 error boundary 兜底）；取帧条件 `(kf.t ?? 0) >= 0`
 * 恒真 = 死逻辑（应取章内 t 最大者）。
 */
import { describe, expect, it } from 'vitest';
import { isValidStorySpecDto, specToNarratorView, type StoryMapSpecDto } from './storymap';

function baseSpec(): StoryMapSpecDto {
  return {
    schema_version: '1.0',
    chapters: [
      { id: 'arc-introduction', title: '引言', narrative: 'n1', arc_role: 'introduction' },
      { id: 'arc-recommendation', title: '建言', narrative: 'n2', arc_role: 'recommendation' },
    ],
    camera_keyframes: [
      { chapter_id: 'arc-introduction', t: 0, center: [116, 39], zoom: 5, pitch: 10, bearing: 0 },
      { chapter_id: 'arc-recommendation', t: 0, center: [117, 40], zoom: 6, pitch: 20, bearing: 5 },
    ],
    linked_widgets: [{ id: 'w1', kind: 'chart', title: 'W', data: {} }],
  };
}

describe('isValidStorySpecDto（深门卫）', () => {
  it('接受形状完整的 spec', () => {
    expect(isValidStorySpecDto(baseSpec())).toBe(true);
  });

  it('拒绝缺字段的 camera_keyframes（旧门卫放行 → 渲染期 TypeError）', () => {
    expect(isValidStorySpecDto({ ...baseSpec(), camera_keyframes: [{}] })).toBe(false);
    expect(
      isValidStorySpecDto({
        ...baseSpec(),
        camera_keyframes: [{ chapter_id: 'arc-introduction', t: 0, center: [1], zoom: 5, pitch: 0, bearing: 0 }],
      }),
    ).toBe(false);
    expect(
      isValidStorySpecDto({
        ...baseSpec(),
        camera_keyframes: [{ chapter_id: 'arc-introduction', t: 0, center: [Number.NaN, 1], zoom: 5, pitch: 0, bearing: 0 }],
      }),
    ).toBe(false);
    expect(
      isValidStorySpecDto({
        ...baseSpec(),
        camera_keyframes: [{ chapter_id: 'arc-introduction', t: 0, center: [1, 1], zoom: 'x', pitch: 0, bearing: 0 }],
      }),
    ).toBe(false);
  });

  it('拒绝缺 id 的 linked_widgets', () => {
    expect(isValidStorySpecDto({ ...baseSpec(), linked_widgets: [{ kind: 'chart' }] })).toBe(false);
    expect(isValidStorySpecDto({ ...baseSpec(), linked_widgets: [{ id: 42 }] })).toBe(false);
  });

  it('拒绝重复 / 空 chapter id', () => {
    const dup = baseSpec();
    dup.chapters = [
      { id: 'same', title: 'a', narrative: 'n', arc_role: 'introduction' },
      { id: 'same', title: 'b', narrative: 'm', arc_role: 'recommendation' },
    ];
    expect(isValidStorySpecDto(dup)).toBe(false);
    const empty = baseSpec();
    empty.chapters = [{ id: '  ', title: 'a', narrative: 'n', arc_role: 'introduction' }];
    expect(isValidStorySpecDto(empty)).toBe(false);
  });

  it('垃圾形状与空值一律拒绝', () => {
    expect(isValidStorySpecDto(null)).toBe(false);
    expect(isValidStorySpecDto({ messages: [] })).toBe(false);
    expect(isValidStorySpecDto({ schema_version: '1.0', chapters: [] })).toBe(false);
  });

  it('#1367：可选数组字段为非数组时判 false，绝不抛出', () => {
    for (const bad of [{}, 7, 'keyframes']) {
      expect(
        isValidStorySpecDto({ ...baseSpec(), camera_keyframes: bad } as unknown),
      ).toBe(false);
      expect(
        isValidStorySpecDto({ ...baseSpec(), linked_widgets: bad } as unknown),
      ).toBe(false);
    }
  });

  it('#1367：渲染消费的 title/data 字段必须可安全渲染', () => {
    const objectChapterTitle = baseSpec();
    objectChapterTitle.chapters[0] = {
      ...objectChapterTitle.chapters[0],
      title: { text: '对象标题' } as unknown as string,
    };
    expect(isValidStorySpecDto(objectChapterTitle)).toBe(false);

    const objectWidgetTitle = baseSpec();
    objectWidgetTitle.linked_widgets = [{
      id: 'w1', kind: 'chart', title: { text: '对象标题' } as unknown as string,
    }];
    expect(isValidStorySpecDto(objectWidgetTitle)).toBe(false);

    const primitiveWidgetData = baseSpec();
    primitiveWidgetData.linked_widgets = [{
      id: 'w1', kind: 'chart', data: 'not-an-object' as unknown as Record<string, unknown>,
    }];
    expect(isValidStorySpecDto(primitiveWidgetData)).toBe(false);
  });
});

describe('specToNarratorView（取帧语义）', () => {
  it('同章多帧取 t 最大者（旧实现恒取数组末条）', () => {
    const spec = baseSpec();
    spec.camera_keyframes = [
      { chapter_id: 'arc-introduction', t: 0.7, center: [7, 7], zoom: 9, pitch: 30, bearing: 1 },
      { chapter_id: 'arc-introduction', t: 0.2, center: [2, 2], zoom: 5, pitch: 10, bearing: 0 },
    ];
    const view = specToNarratorView(spec);
    expect(view.cameras['arc-introduction']?.center).toEqual([7, 7]);
    expect(view.cameras['arc-introduction']?.zoom).toBe(9);
    expect(view.cameras['arc-introduction']?.t).toBe(0.7);
  });

  it('章节/widget 映射到视图模型', () => {
    const view = specToNarratorView(baseSpec());
    expect(view.chapters.map((c) => c.id)).toEqual(['arc-introduction', 'arc-recommendation']);
    expect(view.widgets).toHaveLength(1);
    expect(view.cameras['arc-recommendation']?.pitch).toBe(20);
  });
});
