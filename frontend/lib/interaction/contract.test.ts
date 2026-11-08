/**
 * Interaction IR 契约双端漂移闸（前端侧，ADR-0227；模式同
 * lib/carto-ir 的 contract-manifest 测试）：断言
 * tests/unit/gis_harness/interaction_ir/contract_manifest.json ≡ 本包常量。
 * 后端 pytest 断言同一 JSON ≡ contracts.py —— 任一侧改词表不更新 JSON 即红。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  CONTRACT_INTERACTION_KINDS,
  CONTRACT_KIND_COALESCE,
  CONTRACT_MAX_EVENTS_PER_BATCH,
  CONTRACT_MAX_CATEGORIES,
  CONTRACT_MAX_SELECTED_IDS,
  CONTRACT_INTERACTION_SCHEMA_VERSION,
} from './contract-manifest';
import {
  KIND_COALESCE_POLICY,
  MAX_CATEGORIES_EVENT,
  MAX_EVENTS_PER_BATCH,
  MAX_SELECTED_IDS_EVENT,
  INTERACTION_SCHEMA_VERSION,
} from './contract';

/* vitest 跑在 frontend/ 下 —— manifest 位于仓库根 tests/ 下（同
 * lib/carto-ir/corpus.golden.test.ts 的 REPO_ROOT 解析方式）。 */
const MANIFEST_PATH = resolve(
  process.cwd(),
  '../tests/unit/gis_harness/interaction_ir/contract_manifest.json',
);

const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf-8')) as {
  schema_version: string;
  kinds: string[];
  kind_specs: Record<string, { coalesce: string }>;
  bounds: {
    max_events_per_batch: number;
    max_selected_ids_payload: number;
    max_categories_payload: number;
    max_payload_bytes: number;
    max_payload_bytes_selection: number;
  };
};

describe('Interaction IR contract manifest（双端闸 · 前端侧）', () => {
  it('schema_version 一致', () => {
    expect(manifest.schema_version).toBe(CONTRACT_INTERACTION_SCHEMA_VERSION);
    expect(manifest.schema_version).toBe(INTERACTION_SCHEMA_VERSION);
  });

  it('kind 词表一致（排序后集合相等）', () => {
    expect([...CONTRACT_INTERACTION_KINDS].sort()).toEqual(manifest.kinds);
  });

  it('每 kind 的合并策略一致', () => {
    for (const kind of manifest.kinds) {
      expect(KIND_COALESCE_POLICY[kind as keyof typeof KIND_COALESCE_POLICY]).toBe(
        manifest.kind_specs[kind].coalesce,
      );
      expect(CONTRACT_KIND_COALESCE[kind as keyof typeof CONTRACT_KIND_COALESCE]).toBe(
        manifest.kind_specs[kind].coalesce,
      );
    }
  });

  it('批量上限一致', () => {
    expect(manifest.bounds.max_events_per_batch).toBe(CONTRACT_MAX_EVENTS_PER_BATCH);
    expect(manifest.bounds.max_events_per_batch).toBe(MAX_EVENTS_PER_BATCH);
  });

  // 复审 P2-1：三方对齐上限必须在前端侧也有闸（否则本包改常量不红）。
  it('选择 ids / 类别上限与后端 manifest 一致（三方对齐闸）', () => {
    expect(manifest.bounds.max_selected_ids_payload).toBe(CONTRACT_MAX_SELECTED_IDS);
    expect(manifest.bounds.max_selected_ids_payload).toBe(MAX_SELECTED_IDS_EVENT);
    expect(manifest.bounds.max_categories_payload).toBe(CONTRACT_MAX_CATEGORIES);
    expect(manifest.bounds.max_categories_payload).toBe(MAX_CATEGORIES_EVENT);
  });

  it('选择 kind 宽预算存在且分层（宽 > 通用）', () => {
    expect(manifest.bounds.max_payload_bytes_selection).toBeGreaterThan(
      manifest.bounds.max_payload_bytes,
    );
  });
});
