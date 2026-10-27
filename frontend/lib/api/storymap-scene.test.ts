/**
 * G3D05（ADR-0275 D6）：StoryMap v1.1 视图映射 —— keyframe.scene 与
 * chapter.visible_layer_ids 的播放端投影。
 */
import { describe, expect, it } from 'vitest';
import { isValidStorySpecDto, specToNarratorView, type StoryMapSpecDto } from './storymap';

const spec: StoryMapSpecDto = {
  schema_version: '1.1',
  metadata: { title: '三维地形专报' },
  chapters: [
    {
      id: 'ch-1', title: '引言', narrative: '开场', arc_role: 'introduction',
      visible_layer_ids: [],
    },
    {
      id: 'ch-2', title: '重点解剖', narrative: '剖面', arc_role: 'focus_dissection',
      visible_layer_ids: ['dem', 'route', 'viewshed'],
    },
  ],
  camera_keyframes: [
    {
      chapter_id: 'ch-1', t: 0, center: [104, 30.6], zoom: 11,
      pitch: 10, bearing: 0, scene: { mode: '2d' },
    },
    {
      chapter_id: 'ch-2', t: 0, center: [104.1, 30.7], zoom: 13,
      pitch: 45, bearing: 15, scene: { mode: '2.5d', exaggeration: 1.5 },
    },
  ],
};

describe('specToNarratorView v1.1 scene chapters', () => {
  it('maps keyframe scene onto view scenes and chapters', () => {
    const view = specToNarratorView(spec);
    expect(view.scenes['ch-1']).toEqual({ mode: '2d' });
    expect(view.scenes['ch-2']).toEqual({ mode: '2.5d', exaggeration: 1.5 });
    const ch2 = view.chapters.find((c) => c.id === 'ch-2');
    expect(ch2?.scene?.mode).toBe('2.5d');
    expect(ch2?.scene?.exaggeration).toBe(1.5);
  });

  it('maps visible_layer_ids; empty list stays empty (不改可见性语义)', () => {
    const view = specToNarratorView(spec);
    expect(view.chapters.find((c) => c.id === 'ch-1')?.visibleLayerIds).toEqual([]);
    expect(view.chapters.find((c) => c.id === 'ch-2')?.visibleLayerIds)
      .toEqual(['dem', 'route', 'viewshed']);
  });

  it('v1.0 spec without scene fields maps scenes to null', () => {
    const legacy = JSON.parse(JSON.stringify(spec)) as StoryMapSpecDto;
    legacy.camera_keyframes?.forEach((kf) => { delete (kf as { scene?: unknown }).scene; });
    const view = specToNarratorView(legacy);
    expect(view.scenes['ch-1']).toBeNull();
    expect(view.chapters.find((c) => c.id === 'ch-2')?.scene ?? null).toBeNull();
  });

  it('scene-bearing v1.1 spec passes the DTO guard', () => {
    expect(isValidStorySpecDto(spec)).toBe(true);
  });
});
