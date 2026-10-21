/**
 * Changed-lane mapping guard for the copy → selector path (#1728).
 *
 * The journey suite locates chrome by the strings users see —
 * `button[aria-label="播放"]`, `已复制分享链接` — and those strings live in
 * `frontend/messages/*.json` (story.json playAria/shareCopied). A diff that
 * only touches messages used to fall through `PREFIX_MAP` and return
 * `run: false`, so a retranslated label broke a journey with a green lane.
 *
 * The exhaustive key-by-key matrix lives in `frontend/tests/e2e-lanes/
 * changed-lanes.test.ts`; this file pins the one key that omission left dead.
 */
import { describe, expect, it } from 'vitest';
import { mapChangedFiles, SMOKE_JOURNEYS } from '../e2e/tools/changed-lanes.mjs';

const smokeDecision = (files: string[]) => {
  const d = mapChangedFiles(files);
  expect(d.run, `expected smoke for ${JSON.stringify(files)}: ${d.reason}`).toBe(true);
  expect(d.grep).toBe('@smoke');
  expect(d.journeys).toEqual(SMOKE_JOURNEYS);
  return d;
};

describe('changed-lane mapping — frontend/messages/ (#1728)', () => {
  it('a copy-only diff runs the smoke set instead of falling through', () => {
    // The #1728 shape: the ONLY changed file is a messages file.
    smokeDecision(['frontend/messages/zh-CN/story.json']);
  });

  it('matches messages files at any locale and nesting depth', () => {
    smokeDecision(['frontend/messages/en-US/sidebar.json']);
    smokeDecision(['frontend/messages/zh-CN/lakehouse.json']);
  });

  it('a messages file mixed into a larger diff still runs', () => {
    smokeDecision(['app/main.py', 'frontend/messages/zh-CN/sidebar.json', 'README.md']);
  });

  it('does not match a same-named file outside the directory', () => {
    // Prefix precision: `frontend/messages.ts` (a module, not the copy dir) is
    // not a copy change and must not widen the lane on its own.
    expect(mapChangedFiles(['frontend/messages.ts']).run).toBe(false);
  });
});
