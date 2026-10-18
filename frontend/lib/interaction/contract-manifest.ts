/**
 * Interaction IR 契约词表的**前端镜像常量**（ADR-0227，carto_ir manifest 模式）。
 *
 * 权威在 app/services/gis_harness/interaction_ir/contracts.py；本文件是
 * 浏览器端镜像。两侧由
 * tests/unit/gis_harness/interaction_ir/contract_manifest.json **双向漂移闸**
 * 锁定：backend pytest 断言 JSON ≡ contracts.py 常量，frontend vitest 断言
 * JSON ≡ 本常量 —— 任意一侧改词表不更新 JSON 即红。
 */
import {
  INTERACTION_SCHEMA_VERSION,
  KIND_COALESCE_POLICY,
  MAX_EVENTS_PER_BATCH,
  type InteractionEventKind,
} from './contract';

export const CONTRACT_INTERACTION_SCHEMA_VERSION = INTERACTION_SCHEMA_VERSION;

/** kind 词表（= 后端 ALLOWED_KINDS 排序镜像）。 */
export const CONTRACT_INTERACTION_KINDS = Object.keys(KIND_COALESCE_POLICY)
  .sort() as InteractionEventKind[];

/** kind → 合并策略（= 后端 INTERACTION_KINDS[].coalesce）。 */
export const CONTRACT_KIND_COALESCE = KIND_COALESCE_POLICY;

/** 批量上限（= 后端 MAX_EVENTS_PER_BATCH）。 */
export const CONTRACT_MAX_EVENTS_PER_BATCH = MAX_EVENTS_PER_BATCH;
