import type { CommandEntry, MapCommandResult } from './types';
import { TILE_PROVIDERS } from '@/lib/providers';
import * as navigation from '@/lib/map-kit/navigation';
import * as renderer from '@/lib/map-kit/renderer'
import { unregisterCustomOverlay } from '@/lib/map-kit/custom-overlay-registry';
import { devOnly } from '@/lib/utils/logger';
import { parseFilter } from './parseFilter';
import { isMvtLayer } from '@/lib/store/layer-data';
import { getCommittedMapSpec } from '@/lib/mapspec/session-cursor';
import { noteAgentDisplayed } from '@/lib/chat/turn-focus';
import {
  isCustomSchemeMatch,
  isStoreSchemeMatch,
  matchMapLayers,
  resolveLayerTargetsByRef,
} from './layer-identity';
import {
  applyLayerVisibilityTransaction,
  boundedVisibilityRepair,
} from './visibility-transaction';

// 身份解析已集中到 layer-identity.ts（LayerIdentityResolver 单一深接口）；
// 此处 re-export 保持既有导入路径（tests / 兄弟命令）兼容。
export { resolveLayerTargetsByRef, matchMapLayers };

/**
 * Layer commands: vector/raster add-remove, base layer switch, visibility/style
 * updates, reorder, and filter.
 *
 * Each `run` body is the verbatim extraction of the corresponding `case` from
 * map-action-handler.tsx, reading from `ctx` instead of the closed-over scope.
 * `useHudStore.getState()` becomes `ctx.getHudState()`. Validators mirror the
 * old `REQUIRED_PARAMS` table in map-action-renderer.tsx.
 *
 * Casing: the component lowercases `action.command` before the catalogue lookup,
 * and `dispatchAction` normalizes to lowercase at entry, so the catalogue only
 * registers lowercase keys (e.g. `base_layer_change`, not `BASE_LAYER_CHANGE`).
 *
 * 可见性突变（show/hide/finalize）统一走 visibility-transaction.ts 的
 * LayerVisibilityTransaction：desired → runtime → durability → postcondition，
 * 不再维护 per-command 的第二套实现。
 */

/**
 * Store-layer sublayers (`${id}__*`) are owned by the async MapSpecRuntime
 * reconcile — when they cannot be verified synchronously the honest ack is
 * `store_updated:true` (the backend treats it as non-converging), never a
 * fabricated `confirmed`. Pure custom layers degrade to `mutation_failed`.
 */
function nonConfirmableAck(storeMatched: string[]): MapCommandResult {
  return storeMatched.length > 0
    ? { status: 'succeeded', result: { store_updated: true } }
    : { status: 'failed', error: 'mutation_failed' };
}

/** Resolve canonical `id` with fallback to legacy aliases (issue #935). */
function resolveTargetId(params: any, legacyKeys: string[]): string | undefined {
  for (const k of ['id', ...legacyKeys]) {
    const v = params?.[k];
    if (typeof v === 'string' && v.trim()) return v;
  }
  return undefined;
}

/**
 * 跨 id 体系的图层目标解析已迁移至 layer-identity.ts（单一深接口）。
 * 一个 ref 可背多层（product-heatmap + product-points 同源）——全部为目标。
 */

/** #668: extract a ['get', field] field name from a MapLibre filter expression. */
function extractFilterField(expr: any): string | null {
  if (!expr) return null;
  if (Array.isArray(expr)) {
    if (expr.length === 2 && expr[0] === 'get' && typeof expr[1] === 'string') return expr[1];
    for (const sub of expr) {
      const found = extractFilterField(sub);
      if (found) return found;
    }
  }
  return null;
}

export const layerCommands: Record<string, CommandEntry> = {
  cartographic_runtime_repair: {
    requiredParams: (p) => (
      typeof p.mapspec_fingerprint === 'string'
      && typeof p.observation_sequence === 'number'
      && Array.isArray(p.repair_patches)
      && p.repair_patches.length > 0
      && p.repair_patches.length <= 32
    ),
    run(ctx) {
      const { params, getHudState, actionId } = ctx;
      const fingerprint = params?.mapspec_fingerprint;
      const patches = params?.repair_patches;
      if (!fingerprint || !Array.isArray(patches)) {
        return { status: 'failed', error: 'invalid_params' };
      }
      if (!actionId) return { status: 'failed', error: 'missing_action_id' };

      const same = (left: unknown, right: unknown): boolean => {
        if (typeof left === 'number' && typeof right === 'number') {
          return Number.isFinite(left) && Number.isFinite(right)
            && Math.abs(left - right) <= 1e-9;
        }
        if (left === right) return true;
        try {
          return JSON.stringify(left) === JSON.stringify(right);
        } catch {
          return false;
        }
      };

      // Validate the entire patch before changing any layer. A user edit made
      // after the triggering observation supersedes the stale autonomous plan.
      for (const patch of patches) {
        const current = getHudState().layers.find((layer: any) => layer.id === patch.layer_id);
        if (!current || current._mapspecFingerprint !== fingerprint) {
          return { status: 'failed', error: 'superseded_by_user' };
        }
        for (const [key, observed] of Object.entries(patch.before ?? {})) {
          if (!same(current[key], observed)) {
            return { status: 'failed', error: 'superseded_by_user' };
          }
        }
      }

      for (const patch of patches) {
        const desired = patch.desired ?? {};
        const matched = matchMapLayers(ctx.map, patch.layer_id);
        if (matched.length === 0) {
          return { status: 'failed', error: 'target_not_found' };
        }
        for (const id of matched) {
          renderer.updateLayerStyle(ctx.map, id, {
            visibility: typeof desired.visible === 'boolean'
              ? (desired.visible ? 'visible' : 'none')
              : undefined,
            opacity: typeof desired.opacity === 'number' ? desired.opacity : undefined,
            ...(
              desired.style && typeof desired.style === 'object'
                ? desired.style as Record<string, unknown>
                : {}
            ),
          });
        }
        getHudState().updateLayer(patch.layer_id, {
          ...desired,
          _mapspecFingerprint: fingerprint,
          _mapspecRepairActionId: actionId,
          _mapspecGenerationAt: Date.now(),
        });
      }
      return {
        status: 'succeeded',
        result: {
          confirmed: true,
          repair_action_id: actionId,
          observation_sequence: params.observation_sequence,
        },
      };
    },
  },

  add_layer: {
    // run body reads `layerId` (tests + AI emissions); `id` tolerated for legacy emissions
    requiredParams: (p) => typeof p.layerId === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { layerId, type, geojson, style, flyTo } = params;
      // V3: silent no-ops become explicit failed results (design §6) — missing
      // target → target_not_found, missing payload data → invalid_params.
      // Legacy emissions use `id` — read it as the layerId fallback (the
      // validator already accepts both forms).
      const targetId = (layerId as string | undefined) ?? (params.id as string | undefined);
      if (!targetId) return { status: 'failed', error: 'target_not_found' };
      if (!geojson) return { status: 'failed', error: 'invalid_params' };

      const id = `custom-${targetId}`;
      renderer.addGeoJsonSource(map, id, geojson);

      if (style && ((style as any).type === 'choropleth' || (style as any).type === 'lisa')) {
        renderer.addThematicLayer(map, id, geojson, style as any);
      } else {
        renderer.addVectorLayer(map, {
          id,
          type: (type || 'fill') as any,
          source: id,
          paint: style || {}
        });
      }

      if (flyTo) {
        // #668: descriptor.bbox is the fast path for MVT-backed large layers — full-FC scan only as fallback
        let bbox: [number, number, number, number] | null = null;
        try {
          const existing = ctx.getHudState?.()?.layers?.find?.((l: any) => l.id === targetId) as any;
          if (existing?._descriptor?.bbox && Array.isArray(existing._descriptor.bbox) && existing._descriptor.bbox.length === 4) {
            bbox = existing._descriptor.bbox as any;
          } else if (existing?.source && Array.isArray((existing.source as any).bbox) && (existing.source as any).bbox.length === 4) {
            bbox = (existing.source as any).bbox as any;
          } else if (geojson && Array.isArray((geojson as any).bbox) && (geojson as any).bbox.length === 4) {
            bbox = (geojson as any).bbox as any;
          } else {
            bbox = navigation.calculateBBox(geojson);
          }
        } catch {
          bbox = navigation.calculateBBox(geojson);
        }
        if (bbox) {
          navigation.fitBounds(map, bbox, 50);
        }
      }
      // V3 round-2 FIX-B: real post-mutation verification — the source must
      // actually exist on the map before we claim confirmed.
      if (!map.getSource?.(id)) return { status: 'failed', error: 'mutation_failed' };
      // V3: verifiable marker so the harness convergence metric has evidence.
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },

  add_raster_layer: {
    requiredParams: (p) => typeof p.url === 'string' || typeof p.image === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { id, url, image, bbox, opacity = 1.0 } = params;
      const imageUrl = image || url;
      // V3: silent no-ops become explicit failed results (design §6).
      if (!id) return { status: 'failed', error: 'target_not_found' };
      if (!imageUrl || !bbox) return { status: 'failed', error: 'invalid_params' };

      const sourceId = `custom-${id}`;
      const layerId = `${sourceId}-layer`;

      // bbox should be [west, south, east, north]
      const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
        [bbox[0], bbox[3]], // top-left
        [bbox[2], bbox[3]], // top-right
        [bbox[2], bbox[1]], // bottom-right
        [bbox[0], bbox[1]]  // bottom-left
      ];

      renderer.addImageSource(map, sourceId, imageUrl, coordinates);
      renderer.addVectorLayer(map, {
        id: layerId,
        type: 'raster',
        source: sourceId,
        paint: {
          'raster-opacity': opacity,
          'raster-fade-duration': 500
        }
      });

      navigation.fitBounds(map, bbox, 80);
      // V3 round-2 FIX-B: real post-mutation verification — the image source
      // must exist on the map before we claim confirmed.
      if (!map.getSource?.(sourceId)) return { status: 'failed', error: 'mutation_failed' };
      // V3: verifiable marker (layer add — harness convergence evidence).
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },

  remove_layer: {
    // run body reads `layer_id || layerId`; `id` tolerated for legacy emissions
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.layerId === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const target = resolveTargetId(params, ['layer_id', 'layerId']);
      // V3: missing target → explicit failed result (was a silent return).
      if (!target) return { status: 'failed', error: 'target_not_found' };

      // 身份解析与 visibility 对称（此前 remove 不做 ref 展开：恢复会话里
      // ref 目标假 target_not_found；一个 ref 背的多层只删一层留下残件）。
      const targetIds = resolveLayerTargetsByRef(target, getHudState);
      const effectiveTargets = targetIds.length > 0 ? targetIds : [target];

      const specLayerIds = new Set(
        ((getCommittedMapSpec()?.layers || []) as any[]).map((l) => String(l.id)),
      );
      const anyMatch = effectiveTargets.some(
        (id) => matchMapLayers(map, id).length > 0
          || (getHudState().layers?.some?.((l: any) => l.id === id) ?? false),
      );
      if (!anyMatch) return { status: 'failed', error: 'target_not_found' };

      let sawFailure = false;
      const storeMatchedAll: string[] = [];
      const matchedAll: string[] = [];
      const removeDurabilityTargets: string[] = [];
      let runtimeRemovedAny = false;

      for (const tgt of effectiveTargets) {
        // V3 round-2 FIX-B: resolve the target across BOTH id schemes (custom-…
        // stack and store `…__…` sublayers) before declaring a miss.
        const matched = matchMapLayers(map, tgt);
        const storeHasLayer = getHudState().layers?.some?.((l: any) => l.id === tgt) ?? false;
        matchedAll.push(...matched);
        const storeMatched = matched.filter((id) => isStoreSchemeMatch(tgt, id));
        storeMatchedAll.push(...storeMatched);

        // 供 durability 用：先于 removeLayer 捕获 spec 层 id（两种退出路径皆需）
        const preLayer0 = getHudState().layers?.find?.((l: any) => l.id === tgt);
        const preSpecId0 = String((preLayer0 as any)?._mapspecLayerId ?? tgt);
        if (specLayerIds.has(preSpecId0)) removeDurabilityTargets.push(preSpecId0);
        if (matched.length === 0 && !storeHasLayer) continue;
        const customId = `custom-${tgt}`;
        const customMatched = matched.filter((id) => isCustomSchemeMatch(tgt, id));

        if (matched.length === 0) {
          // Store-only: the MapSpecRuntime owns the map sublayers. Dropping the
          // store entry lets the reconcile clean the map; honest store_updated.
          getHudState().removeLayer?.(tgt);
          continue;
        }
        runtimeRemovedAny = true;

        // 1. custom stack → renderer.removeLayerStack (layers + sources).
        if (customMatched.length > 0 || map.getLayer?.(customId) || map.getSource?.(customId)) {
          try {
            const ok = renderer.removeLayerStack(map, customId, true);
            if (!ok) {
              devOnly.warn('[MapActionHandler] REMOVE_LAYER failed to remove custom stack:', customId);
              sawFailure = true;
              continue;
            }
          } catch (e) {
            devOnly.warn('[MapActionHandler] REMOVE_LAYER failed:', e);
            sawFailure = true;
            continue;
          }
        }
        // 2. store sublayers + bare source → remove directly (the runtime's
        //    next reconcile is a no-op for already-gone ids).后端直写图层的
        //    source 键可能与目标 id 不同名——按 spec 反查引用该目标的全部
        //    source 键一并清（防孤儿 source）。
        for (const id of storeMatched) {
          if (map.getLayer?.(id)) {
            try { map.removeLayer(id); } catch { /* already gone */ }
            renderer.noteStyleLayerRemoved(map, id);
          }
        }
        const sourceKeys = new Set<string>([tgt]);
        for (const [sid] of Object.entries(
          (getCommittedMapSpec()?.sources ?? {}) as Record<string, any>,
        )) {
          const refsLayers = ((getCommittedMapSpec()?.layers || []) as any[]).some(
            (l) => l.source === sid && (l.id === tgt || l.id.startsWith(`${tgt}__`)),
          );
          if (refsLayers) sourceKeys.add(sid);
        }
        for (const sid of sourceKeys) {
          if (map.getSource?.(sid)) {
            try { map.removeSource(sid); } catch { /* already gone */ }
          }
          // v2(#1078 FE3)：source 移除同步注销裁剪/挂载两本账 —— 否则
          // viewport 刷新持续探测死 id，style 重挂会复活已删覆盖层。
          // review R4-P0：挂载账本以完整 custom- id 记账 —— 用裸 tgt 注销
          // 永不匹配（'custom-foo' 不以 'foo-' 开头），删除的覆盖层会在
          // 下一次 style 重载复活且账本无界增长。
          renderer.unregisterGeoJsonSource(sid);
          unregisterCustomOverlay(`custom-${tgt}`);
        }
        // 3. store 行同步（含一个 ref 背多层的姊妹行）
        getHudState().removeLayer?.(tgt);
      }

      // 4. durability：MapSpec 拥有的层同步删除后端 desired state（此前
      //    Agent remove 不动 spec——backend 修复/reconcile 会复活图层）。
      //    先捕获的 specLayerIds 避免 store 行删除后丢失映射。
      //    ST-P1-1：经 removeLayerFromSpec（superseded 重试一次）而非
      //    commitMapSpecMutation（吞 409 → 服务端仍含被删层 + pending 被清
      //    → reconcile 复活僵尸）。双 superseded 保留 pending 压制 compose。
      //    P2-2（会话守卫）：在途时切会话不得把 A 会话的 layer_id POST 到 B 的
      //    mutations 端点——enqueuedSessionId 与执行时 session 比对，丢弃跨会话
      //     durability。
      void (async () => {
        const { getMapSpecSessionCursor } = await import('@/lib/mapspec/session-cursor');
        const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
        const { removeLayerFromSpec } = await import('@/lib/mapspec/user-mutation');
        const { markPendingRemoved, clearPendingRemoved } = await import('@/lib/mapspec/session-cursor');
        for (const specLayerId of removeDurabilityTargets) {
          if (!enqueuedSessionId || getMapSpecSessionCursor().sessionId !== enqueuedSessionId) break;
          markPendingRemoved(specLayerId);
          try {
            const outcome = await removeLayerFromSpec(specLayerId);
            if (outcome === 'unsynced') {
              devOnly.warn('[remove_layer] backend spec removal unsynced; keeping pendingRemoved:', specLayerId);
              continue;
            }
          } catch (err) {
            devOnly.warn('[remove_layer] backend spec removal failed:', err);
          }
          clearPendingRemoved(specLayerId);
        }
      })();

      // 5. V3 round-2 FIX-B: post-mutation verification — the resolved stack
      //    must be gone from the map. (#462: registry read.)
      const layersAfter = renderer.getStyleLayerIds(map);
      const stillPresent = matchedAll.some(
        (id) => !!map.getLayer?.(id) || !!map.getSource?.(id) || layersAfter.includes(id),
      );
      if (sawFailure) {
        return storeMatchedAll.length > 0
          ? { status: 'succeeded', result: { store_updated: true } }
          : { status: 'failed', error: 'mutation_failed' };
      }
      if (!runtimeRemovedAny) {
        // 全部目标是 store-only（reconcile 拥有 map 子层）→ 无同步可验证的
        // map 状态，诚实 store_updated（后端视作未收敛）。
        return { status: 'succeeded', result: { store_updated: true } };
      }
      if (stillPresent) return nonConfirmableAck(storeMatchedAll);
      // V3: verifiable marker (layer remove — harness convergence evidence).
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },

  base_layer_change: {
    requiredParams: (p) => typeof p.name === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params, setSelectedBaseLayer, getHudState } = ctx;
      const name = resolveTargetId(params, ['name']);
      // V3: a missing name is a param failure, not a target miss.
      if (!name) return { status: 'failed', error: 'invalid_params' };
      const search = name.toLowerCase();

      // 1. Exact name match (case-insensitive)
      let idx = TILE_PROVIDERS.findIndex(p => p.name.toLowerCase() === search);

      // 2. Bidirectional substring match
      if (idx === -1) {
        idx = TILE_PROVIDERS.findIndex(p => {
          const n = p.name.toLowerCase();
          return n.includes(search) || search.includes(n);
        });
      }

      // 3. Keyword index — ai команды like "卫星"/"dark"/"osm"命中对应条目
      if (idx === -1) {
        idx = TILE_PROVIDERS.findIndex(p =>
          p.keywords.some(k => search.includes(k.toLowerCase())),
        );
      }

      if (idx === -1) {
        devOnly.warn('[MapActionHandler] Could not match base layer name:', name);
        // V3: no provider matched → explicit failed result (was a silent no-op
        // with only a dev warning).
        return { status: 'failed', error: 'target_not_found' };
      }

      const provider = TILE_PROVIDERS[idx];
      // V3 round-2 FIX-B: the store already points at this provider → no style
      // swap needed → resolve succeeded immediately (no async wait).
      if (getHudState().baseLayer === provider.name) {
        return { status: 'succeeded' };
      }
      setSelectedBaseLayer(idx);
      // QA-2026-05-20 ISSUE-002 fix: keep useHudStore.baseLayer in sync so
      // the dropdown button label, HUD panel, and status bar all show the
      // canonical name after an AI-driven switch_base_layer call.
      getHudState().setBaseLayer(provider.name);
      // V3 round-2 FIX-B: the style swap is async — the ack must not claim
      // success before the new style actually loads. Resolve on the next
      // `style.load`, fail on style error / 15s timeout.
      return waitForStyleLoad(map);
    },
  },

  layer_visibility_update: {
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { params } = ctx;
      const layer_id = resolveTargetId(params, ['layer_id']);
      const { visible, opacity, name, color } = (params || {}) as any;
      // V3: missing target → explicit failed result (was a silent return).
      if (!layer_id) return { status: 'failed', error: 'target_not_found' };

      // 「地图随对话」：agent 显式展示 → 先标记当前轮再收起旧轮（同 ref
      // 的多层同属当前轮展示集，互不收起）——与事务解耦，事务内不重复。
      if (visible === true) {
        for (const id of resolveLayerTargetsByRef(layer_id, ctx.getHudState)) {
          noteAgentDisplayed(id);
        }
      }

      // 可见性突变统一事务：desired(store+pending) → runtime(setLayoutProperty)
      // → durability(后端 MapSpec CAS 提交) → postcondition(读回验证)。
      // #609: JSON null = "不修改该属性"（`!= null` 判存在性）。
      return applyLayerVisibilityTransaction(ctx, {
        layerId: layer_id,
        visible: visible ?? null,
        opacity: opacity ?? null,
        name,
        color: color as string | undefined,
      });
    },
  },

  /**
   * 最终图层显示管理（finalize_display 钩子的执行面）：展示 show_layer_ids
   * （跨 id 体系解析，一个 ref 可展开多层），隐藏其余所有可见分析图层。
   * 与逐层 set_layer_status 的区别：一次调用原子收口 —— Agent 在分析收尾
   * 时声明"本轮最终成图集合"，中间层（点云/边界/裁剪残料）不再由用户手动
   * 关闭。展示集同步标记当前对话轮（turn-focus 联动）。
   *
   * 终态确认（Goal C）：每层经同一可见性事务（runtime 突变 + 后端 desired
   * 持久化 + 读回验证），ack 携带证据（confirmed / visible/hidden/
   * unresolved layer ids）——不再是无验证的 {shown, hidden} 假成功。
   * store-owned 子层（reconcile 未落）走一次有界重验（bounded repair，
   * 最多一次），绝不无限循环。
   */
  finalize_display: {
    requiredParams: (p) => Array.isArray(p?.show_layer_ids) && p.show_layer_ids.length > 0,
    run(ctx) {
      const { getHudState, params } = ctx;
      const raw = ((params as any)?.show_layer_ids ?? []) as string[];
      if (raw.length === 0) return { status: 'failed', error: 'invalid_params' };

      const { layers } = getHudState();
      const show = new Set(raw.flatMap((id) => resolveLayerTargetsByRef(id, getHudState)));
      if (show.size === 0) return { status: 'failed', error: 'target_not_found' };
      // v2(review R2-P1-1)：服务端守卫拒绝集（用户手动设定的层）—— 本地
      // 不得翻转呈现（服务端已保留用户决策，本地翻转会分叉且 pending 永存）。
      // 服务端给的是 spec 层 id；本地 show/hideTargets 是 HUD 行 id ——
      // 经 _mapspecLayerId 别名双向展开。
      const respectSpec = new Set(
        ((params as any)?.respect_layer_ids ?? []) as string[],
      );
      const respect = new Set<string>(respectSpec);
      for (const l of layers) {
        const specId = (l as any)?._mapspecLayerId;
        if (specId && respectSpec.has(String(specId))) respect.add(String(l.id));
      }

      // 收口豁免：行政区边界（制图语境常显）+ 用户 pin 的层（用户手动点开
      // 且未手动隐藏——不与用户对抗）+ 非 analysis 组（base 等）。
      const boundaryRole = new Set(
        ((getCommittedMapSpec()?.layers || []) as any[])
          .filter((l) => l?.context_role === 'boundary')
          .map((l) => String(l.id)),
      );
      const hideTargets: string[] = [];
      for (const layer of layers ?? []) {
        if (show.has(layer.id)) continue;
        if ((layer.group ?? 'analysis') !== 'analysis' || !layer.visible) continue;
        const specId = String((layer as any)._mapspecLayerId ?? layer.id);
        if (boundaryRole.has(specId)) continue;
        // 用户手动点开且仍 pin 的层不自动隐藏（用户优先，不与用户对抗）
        if ((layer as any)._userPinned) continue;
        hideTargets.push(layer.id);
      }

      // turn-focus：展示集即本轮主题（先全部标记再收口 —— 同集互不收起）
      for (const id of show) {
        noteAgentDisplayed(id);
      }

      // 逐层事务：展示集与收口集都只做本地呈现（durable:false）。
      // v2(audit H2)：服务端 finalize_display 已用 GISMutationBatch 以
      // agent origin 一次性落盘展示集+隐藏集（layout.visibility +
      // presentation_owner=agent）。旧路径把 agent 决定的隐藏经 user
      // mutation 路由提交（durable:true），洗白为 presentation_owner="user"
      // —— 此后 agent 无法翻回自己的收口决策（user-wins 误伤）且"谁决策"
      // 溯源失真。本地命令现在只负责即刻呈现（pending overlay + store 行），
      // 持久收敛由 step_result 携带的 mapspec/mutation_revision 完成。
      const visibleLayerIds: string[] = [];
      const hiddenLayerIds: string[] = [];
      const unresolvedLayerIds: string[] = [];
      const storePendingRepair: { layerId: string; visible: boolean }[] = [];

      for (const id of show) {
        if (respect.has(id)) continue; // 用户手动隐藏的展示目标：保留用户决策
        const res = applyLayerVisibilityTransaction(ctx, { layerId: id, visible: true, durable: false });
        if (res.status === 'failed') {
          unresolvedLayerIds.push(id);
        } else {
          visibleLayerIds.push(id);
          if (res.result?.store_updated) storePendingRepair.push({ layerId: id, visible: true });
        }
      }
      for (const id of hideTargets) {
        if (respect.has(id)) continue; // 用户手动显/隐的收口目标：同上
        const res = applyLayerVisibilityTransaction(ctx, { layerId: id, visible: false, durable: false });
        if (res.status === 'failed') {
          unresolvedLayerIds.push(id);
        } else {
          hiddenLayerIds.push(id);
          if (res.result?.store_updated) storePendingRepair.push({ layerId: id, visible: false });
        }
      }

      // 证据契约：confirmed = 展示集全部验证通过（读回一致）；store-owned
      // 目标如实标记 store_updated（后端 harness 视作未收敛，observation
      // 循环续证）——绝不把未验证的层报成 confirmed。
      const confirmed = visibleLayerIds.length === show.size && storePendingRepair.length === 0;

      if (storePendingRepair.length > 0) {
        // 有界修复：等 reconcile 去抖周期后重验一次（再应用一次期望值），
        // 结果只进 dev 日志与 observation 循环——ack 先行，不阻塞队列。
        void boundedVisibilityRepair(ctx, storePendingRepair).then(({ unresolved }) => {
          if (unresolved.length > 0) {
            devOnly.warn(
              '[finalize_display] bounded repair left unresolved layers:',
              unresolved,
            );
          }
        });
      }

      return {
        status: unresolvedLayerIds.length > 0 && visibleLayerIds.length === 0
          ? 'failed'
          : 'succeeded',
        error: unresolvedLayerIds.length > 0 && visibleLayerIds.length === 0
          ? 'target_not_found'
          : undefined,
        result: {
          shown: visibleLayerIds.length,
          hidden: hiddenLayerIds.length,
          confirmed,
          store_updated: !confirmed && storePendingRepair.length > 0,
          visible_layer_ids: visibleLayerIds,
          hidden_layer_ids: hiddenLayerIds,
          unresolved_layer_ids: unresolvedLayerIds,
        },
      };
    },
  },

  layer_style_update: {
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const p = params as any;
      const layer_id = resolveTargetId(p, ['layer_id']);
      const { style, field, colorMap, baseStyle } = p || {};
      // V3: silent no-ops become explicit failed results (design §6).
      if (!layer_id) return { status: 'failed', error: 'target_not_found' };

      // 跨 id 体系解析（同 layer_visibility_update：ref → 恢复层 id）。
      const targetIds = resolveLayerTargetsByRef(layer_id, getHudState);
      const effectiveId = targetIds[0] ?? layer_id;

      // V3 round-2 FIX-B: resolve across BOTH id schemes (custom-… + store …__…).
      const matched = Array.from(new Set(targetIds.flatMap((id) => matchMapLayers(map, id))));
      // V3: no matching map layer AND no store layer → genuine miss → failed
      // result (was: silent no-op forEach + void success).
      if (matched.length === 0 && targetIds.length === 0) return { status: 'failed', error: 'target_not_found' };

      // Store-layer sublayers are owned by the async MapSpecRuntime reconcile.
      const storeMatched = matched.filter((id) => isStoreSchemeMatch(effectiveId, id));

      const updateStoreStyle = (patch: Record<string, unknown>) => {
        for (const id of targetIds) {
          const existing = getHudState().layers.find((l: any) => l.id === id);
          getHudState().updateLayer(id, { style: { ...(existing?.style ?? {}), ...patch } });
        }
      };

      // #557 断点 1/3：categorical 符号化（SSE apply_template mode=categorical
      // 发出 field+colorMap+baseStyle，没有 style 键）—— 走 match 表达式。
      // 修复前 `if (!style) invalid_params` 让整条分类符号化路径静默失败。
      if (field && colorMap) {
        const catStyle: Record<string, unknown> = {
          categorical: {
            field,
            colorMap,
            fillOpacity: (baseStyle as any)?.fillOpacity,
            strokeWidth: (baseStyle as any)?.strokeWidth,
          },
        };
        for (const id of matched) {
          renderer.updateLayerStyle(map, id, { categorical: catStyle.categorical as any });
        }
        updateStoreStyle({
          categorical: { field, colorMap },
          ...((baseStyle as any)?.fillOpacity !== undefined && { fillOpacity: (baseStyle as any).fillOpacity }),
        });
        if (matched.length === 0) {
          return { status: 'succeeded', result: { store_updated: true } };
        }
        for (const id of matched) {
          if (!map.getLayer?.(id)) return nonConfirmableAck(storeMatched);
        }
        return { status: 'succeeded', result: { confirmed: true } };
      }

      if (!style) return { status: 'failed', error: 'invalid_params' };
      const s = style as any;

      // Sync style changes back to store so LayersTab swatch stays in sync
      const styleUpdates: Record<string, any> = {};
      // #557 断点 5：fillOpacity 加入转发键集（此前在 3 处前端链路被丢弃）。
      for (const key of ['color', 'strokeColor', 'strokeWidth', 'pointSize', 'dashArray', 'fill', 'fillOpacity']) {
        if (s[key] !== undefined && s[key] !== null) styleUpdates[key] = s[key];
      }

      if (matched.length === 0) {
        // Store-only: the reconcile owns the map sublayers → honest store_updated.
        if (Object.keys(styleUpdates).length > 0) {
          updateStoreStyle(styleUpdates);
        }
        return { status: 'succeeded', result: { store_updated: true } };
      }

      for (const id of matched) {
        renderer.updateLayerStyle(map, id, {
          color: s.color,
          strokeColor: s.strokeColor,
          strokeWidth: s.strokeWidth,
          pointSize: s.pointSize,
          dashArray: s.dashArray,
          fill: s.fill,
          fillOpacity: s.fillOpacity,
        });
      }
      if (Object.keys(styleUpdates).length > 0) {
        updateStoreStyle(styleUpdates);
      }
      // V3 round-2 FIX-B: post-mutation verification — the matched layer must
      // exist on the map (getLayer) before we claim confirmed.
      for (const id of matched) {
        if (!map.getLayer?.(id)) return nonConfirmableAck(storeMatched);
      }
      // #1077: spec 承载层的样式命令没有持久通道（committed MapSpec 的
      // paint 不被本命令改写，下一次同层 recompile 即回滚）—— 诚实返回
      // store_updated 上限而非 confirmed（后者让 harness 视作已收敛，
      // 随后的静默回滚与之矛盾）。需要持久样式时走重新 authoring。
      const specLayerIds = new Set(
        ((getCommittedMapSpec()?.layers || []) as any[]).map((l) => String(l.id)),
      );
      const specBackedTarget = matched.some(
        (id) => specLayerIds.has(String(id))
          || [...specLayerIds].some((sid) => String(id).startsWith(`${sid}__`)),
      );
      if (specBackedTarget) {
        return {
          status: 'succeeded',
          result: {
            store_updated: true,
            durable: false,
            note: '样式已应用到当前渲染与面板；该层由 MapSpec 承载，未写入 committed paint —— 同层任何重编译会回滚。需持久请重新 authoring 该层。',
          },
        };
      }
      // V3: verifiable marker (layer style update — harness convergence).
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },

  reorder_layer: {
    // run body reads `layer_id` + `position` (backend REORDER_LAYER emission);
    // the old validator (layers/order arrays) matched no actual run contract
    requiredParams: (p) => typeof p.layer_id === 'string' && typeof p.position === 'string',
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const { layer_id, position, before_id } = params || {};
      // V3: silent no-ops become explicit failed results (design §6).
      if (!layer_id || typeof layer_id !== 'string' || !layer_id.trim() || layer_id === 'ref:' || layer_id === 'custom-') {
        return { status: 'failed', error: 'target_not_found' };
      }
      if (!position) return { status: 'failed', error: 'invalid_params' };
      // Issue #393: tighten position validation up-front so neither scheme path
      // mutates the map/store before discovering the request is malformed.
      if (!['top', 'bottom', 'up', 'down', 'before'].includes(position)) {
        return { status: 'failed', error: 'invalid_params' };
      }
      if (position === 'before' && !before_id) return { status: 'failed', error: 'invalid_params' };

      // V3 round-2 FIX-B: resolve the target across BOTH id schemes (custom-…
      // stack and store `…__…` sublayers). The old custom-only matcher never saw
      // a MapSpec layer (`${id}__${sub}`) and failed target_not_found for every
      // current layer.
      const matched = matchMapLayers(map, layer_id);
      const storeHasLayer = getHudState().layers?.some?.((l: any) => l.id === layer_id) ?? false;
      if (matched.length === 0 && !storeHasLayer) return { status: 'failed', error: 'target_not_found' };

      const customMatched = matched.filter((id) => isCustomSchemeMatch(layer_id, id));
      const storeMatched = matched.filter((id) => isStoreSchemeMatch(layer_id, id));

      // 1. custom stack → legacy algorithm: move the group on the map within the
      //    custom stack (the runtime does not own these layers).
      let customMoved = false;
      if (customMatched.length > 0) {
        // #462: registry read — no per-command style deep-clone.
        const customIds = renderer
          .getStyleLayerIds(map)
          .filter((id: string) => id.startsWith('custom-'));

        const firstSubIdx = customIds.indexOf(customMatched[0]);
        let beforeAnchor: string | undefined;

        if (position === 'top') {
          beforeAnchor = undefined; // moveLayer with no anchor -> top
        } else if (position === 'bottom') {
          const bottomCandidate = customIds.find((id: string) => !customMatched.includes(id));
          beforeAnchor = bottomCandidate;
        } else if (position === 'up') {
          // Find next custom group above
          for (let i = firstSubIdx - 1; i >= 0; i--) {
            if (!customMatched.includes(customIds[i])) {
              // Place customMatched before the layer that sits above customIds[i]
              beforeAnchor = customIds[i];
              break;
            }
          }
        } else if (position === 'down') {
          for (let i = firstSubIdx + customMatched.length; i < customIds.length; i++) {
            if (!customMatched.includes(customIds[i])) {
              beforeAnchor = customIds[i + 1];
              break;
            }
          }
        } else if (position === 'before' && before_id) {
          const targetGroup = customIds.find((id: string) => id === `custom-${before_id}` || id.startsWith(`custom-${before_id}-`));
          beforeAnchor = targetGroup;
        }

        try {
          for (const id of customMatched) {
            map.moveLayer(id, beforeAnchor);
          }
          customMoved = true;
          // #462: anchored moves are not mirrored by the registry's note
          // hooks — drop it so the next read re-seeds from one cold getStyle
          // (reorders are rare commands; the cold re-seed is the budget).
          renderer.clearStyleLayerIds(map);
        } catch (e) {
          devOnly.warn('[MapActionHandler] REORDER_LAYER failed:', e);
          return { status: 'failed', error: 'reorder_failed' };
        }
      }

      // 2. store-scheme → durable reorder of the HUD store's layers array (index
      //    0 = topmost). The MapSpecRuntime derives the map z-order from that
      //    array (adapter → spec → reconcile → renderer.syncLayerZOrder), so a
      //    direct moveLayer on the sublayers would be reverted by the next
      //    reconcile — reordering the store is the change that sticks.
      let storeReordered = false;
      if (storeHasLayer) {
        const storeOrder = [...(getHudState().layers as any[])];
        const fromIdx = storeOrder.findIndex((l: any) => l.id === layer_id);
        if (fromIdx !== -1) {
          const [moved] = storeOrder.splice(fromIdx, 1);
          let toIdx: number;
          if (position === 'top') {
            toIdx = 0;
          } else if (position === 'bottom') {
            toIdx = storeOrder.length;
          } else if (position === 'up') {
            toIdx = Math.max(0, fromIdx - 1);
          } else if (position === 'down') {
            toIdx = Math.min(storeOrder.length, fromIdx + 1);
          } else {
            // position === 'before' (validated above): directly below before_id.
            const beforeIdx = storeOrder.findIndex((l: any) => l.id === before_id);
            if (beforeIdx === -1) return { status: 'failed', error: 'target_not_found' };
            toIdx = beforeIdx + 1;
          }
          storeOrder.splice(toIdx, 0, moved);
          getHudState().reorderLayers(storeOrder);
          storeReordered = true;
        }
      }

      // V3 round-2 FIX-B: post-mutation verification — the target sublayers
      // must still exist on the map (moveLayer on a stale style index is a
      // silent no-op otherwise). (#462: registry read; after the anchored-move
      // clear above this re-seeds cold from the live style — truthful.)
      const layersAfter = renderer.getStyleLayerIds(map);
      const targetGone = matched.some(
        (id) => !map.getLayer?.(id) && !layersAfter.includes(id),
      );
      if (targetGone) return nonConfirmableAck(storeMatched);

      // V3: verifiable markers (layer reorder — harness convergence). Custom
      // layers are verified synchronously (confirmed); store layers are owned by
      // the runtime reconcile (store_updated — the backend treats it as
      // non-converging until the next observation confirms the map z-order).
      const result: Record<string, unknown> = {};
      if (customMoved) result.confirmed = true;
      if (storeReordered) result.store_updated = true;
      return { status: 'succeeded', result };
    },
  },

  apply_layer_filter: {
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const layer_id = resolveTargetId(params, ['layer_id']);
      const { filter } = (params || {}) as any;
      // V3: missing target → explicit failed result (was a silent return).
      if (!layer_id) return { status: 'failed', error: 'target_not_found' };

      // V3 round-2 FIX-B: resolve the target across BOTH id schemes (custom-…
      // stack and store `…__…` sublayers) before declaring a miss. The old code
      // called setFilter on the bare id, which does not exist on a MapSpec map
      // (`${id}__fill/__line/__point` are the real layer ids); MapLibre emits an
      // ErrorEvent instead of throwing, so run() returned void and the dispatcher
      // acked `succeeded` for a filter that never landed.
      const matched = matchMapLayers(map, layer_id);
      const storeHasLayer = getHudState().layers?.some?.((l: any) => l.id === layer_id) ?? false;
      if (matched.length === 0 && !storeHasLayer) return { status: 'failed', error: 'target_not_found' };

      const parsed = parseFilter(filter);
      const storeMatched = matched.filter((id) => isStoreSchemeMatch(layer_id, id));

      // Apply to every matched sublayer BEFORE touching the store — a failed map
      // mutation must not leave a store-only filter that the next reconcile
      // would force onto the map anyway.
      if (matched.length > 0) {
        for (const id of matched) {
          if (!map.getLayer?.(id)) return nonConfirmableAck(storeMatched);
          try {
            map.setFilter(id, parsed as any);
          } catch (e) {
            // Raster layers reject filters — surface it instead of fake-acking.
            devOnly.warn('[MapActionHandler] APPLY_LAYER_FILTER failed on layer:', id, e);
            return nonConfirmableAck(storeMatched);
          }
        }
        // FIX-B: the filter must actually be recorded on the layer — setFilter
        // on an unsupported (raster) or stale id silently no-ops otherwise.
        for (const id of matched) {
          const live = map.getFilter?.(id);
          if (parsed && !live) return nonConfirmableAck(storeMatched);
        }
      }

      // Persist the filter on the store layer so the MapSpecRuntime reconcile
      // keeps it (the adapter re-emits `layer.filter`; a bare setFilter would be
      // rolled back). null/'' clears it — the backend documents that contract.
      getHudState().updateLayer(layer_id, { filter: parsed ?? null });

      if (matched.length === 0) {
        // Store-only: the reconcile owns the map sublayers → honest store_updated.
        return { status: 'succeeded', result: { store_updated: true } };
      }
      // #667/#668 MVT honest ack: tiles may not carry the filtered field → filter is
      // stored in HUD but renderer can only apply what tiles carry. Whitelist check
      // keeps the ack honest: field present → filter can work, field absent → degraded
      // (both are store_updated, never confirmed).
      const targetLayer: any = getHudState().layers?.find?.((l: any) => l.id === layer_id);
      if (targetLayer && isMvtLayer(targetLayer as any)) {
        const _field = extractFilterField(parsed);
        const whitelist: string[] | null | undefined = targetLayer._descriptor?.filterable_fields;
        if (_field && Array.isArray(whitelist) && whitelist.length > 0 && !whitelist.includes(_field)) {
          devOnly.warn('[apply_layer_filter] MVT field not in tile whitelist:', _field);
        }
        return { status: 'succeeded', result: { store_updated: true } };
      }
      // V3: verifiable marker (layer filter — harness convergence).
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },
};

/** Round-2 FIX-B: base layer style swap ack deadline. */
const BASE_LAYER_SWAP_TIMEOUT_MS = 15000;

/**
 * Resolves once the map's next style finishes loading (`style.load`), rejects
 * the swap on a style-level error, and fails on a 15s timeout — the ack can
 * never stall the queue. Tile/fetch errors during load are ignored (they must
 * not cancel a legit swap).
 */
function waitForStyleLoad(map: any, timeoutMs: number = BASE_LAYER_SWAP_TIMEOUT_MS): Promise<MapCommandResult> {
  return new Promise((resolve) => {
    let settled = false;
    // Holder object (repo style — viewCommands.ts): `timer` is assigned after
    // `settle` is defined, and a `let` assigned exactly once trips prefer-const.
    const handles: { timer?: ReturnType<typeof setTimeout> } = {};
    const settle = (result: MapCommandResult) => {
      if (settled) return;
      settled = true;
      if (handles.timer) clearTimeout(handles.timer);
      map.off?.('style.load', onLoad);
      map.off?.('error', onError);
      resolve(result);
    };
    const onLoad = () => settle({ status: 'succeeded' });
    const onError = (e: any) => {
      // Only style-relevant errors fail the swap — tile/fetch errors during
      // loading must not cancel it.
      const err = e?.error ?? e;
      const isTileError = !!e?.tile || /tile|fetch|network|worker/i.test(String(err?.message ?? ''));
      if (!isTileError) settle({ status: 'failed', error: 'style_error' });
    };
    handles.timer = setTimeout(() => settle({ status: 'failed', error: 'timeout' }), timeoutMs);
    map.once?.('style.load', onLoad);
    map.once?.('error', onError);
  });
}
