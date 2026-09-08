import type { Layer } from "@/lib/types/layer";
import type { MapSpec, MapSpecLayer } from "@/lib/mapspec-compiler/types";
import { SUBLAYER_SEP } from "./adapter";

function equalStructured(left: unknown, right: unknown): boolean {
  if (typeof left === "number" && typeof right === "number") {
    return Number.isFinite(left) && Number.isFinite(right) && Math.abs(left - right) <= 1e-9;
  }
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => equalStructured(value, right[index]));
  }
  if (left && right && typeof left === "object" && typeof right === "object") {
    const leftRecord = left as Record<string, unknown>;
    const rightRecord = right as Record<string, unknown>;
    const leftKeys = Object.keys(leftRecord).sort();
    const rightKeys = Object.keys(rightRecord).sort();
    return equalStructured(leftKeys, rightKeys)
      && leftKeys.every((key) => equalStructured(leftRecord[key], rightRecord[key]));
  }
  return false;
}

function liveProperty(
  map: any,
  layer: any,
  layerId: string,
  group: "paint" | "layout",
  key: string,
): unknown {
  const getter = group === "paint" ? map.getPaintProperty : map.getLayoutProperty;
  if (typeof getter === "function") {
    try {
      return getter.call(map, layerId, key);
    } catch {
      return undefined;
    }
  }
  return layer?.[group]?.[key];
}

function layerConverged(map: any, expected: MapSpecLayer): boolean {
  const actual = map.getLayer?.(expected.id);
  if (
    !actual
    || String(actual.source ?? "") !== String(expected.source ?? "")
    || String(actual.type ?? "") !== String(expected.type ?? "")
  ) {
    return false;
  }
  const expectedRecord = expected as unknown as Record<string, unknown>;
  const actualRecord = actual as Record<string, unknown>;
  for (const key of ["filter", "source-layer", "minzoom", "maxzoom"] as const) {
    if (
      expectedRecord[key] !== undefined
      && !equalStructured(actualRecord[key], expectedRecord[key])
    ) {
      return false;
    }
  }
  for (const [key, value] of Object.entries(expected.paint ?? {})) {
    if (!equalStructured(liveProperty(map, actual, expected.id, "paint", key), value)) {
      return false;
    }
  }
  for (const [key, value] of Object.entries(expected.layout ?? {})) {
    if (!equalStructured(liveProperty(map, actual, expected.id, "layout", key), value)) {
      return false;
    }
  }
  return true;
}

function liveConstantOpacity(
  map: any,
  expectedLayers: Array<{ candidate: MapSpecLayer; actual: any }>,
): number | undefined {
  const values: number[] = [];
  for (const { candidate, actual } of expectedLayers) {
    if (!actual) return undefined;
    const opacityKeys = Object.keys(candidate.paint ?? {}).filter(
      (key) => key.endsWith("-opacity"),
    );
    for (const key of opacityKeys) {
      const value = liveProperty(map, actual, candidate.id, "paint", key);
      if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
      values.push(value);
    }
  }
  if (values.length === 0) return undefined;
  return values.every((value) => Math.abs(value - values[0]) <= 1e-9)
    ? values[0]
    : undefined;
}

function viewportOf(map: any): Record<string, unknown> {
  const center = map.getCenter?.();
  const bounds = map.getBounds?.();
  return {
    center: center && Number.isFinite(center.lng) && Number.isFinite(center.lat)
      ? [center.lng, center.lat]
      : undefined,
    zoom: map.getZoom?.(),
    bearing: map.getBearing?.(),
    pitch: map.getPitch?.(),
    bounds: bounds && typeof bounds.getWest === "function"
      ? [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]
      : undefined,
  };
}

/**
 * Compare the locally derived desired runtime spec with the live MapLibre map.
 * The result contains bounded metadata only: no source bodies or features.
 */
export function collectCartographicRuntimeObservation(
  map: any,
  desired: MapSpec,
  hudLayers: Layer[],
  mapspecFingerprint: string,
  reconcileError = "",
  applied: MapSpec | null = null,
): Record<string, unknown> {
  const styleLoaded = !!map?.isStyleLoaded?.();
  // #462: hoisted OUT of the per-candidate convergence check — the old
  // `map.getStyle()` inside `expected.every` deep-cloned the whole style once
  // per candidate layer (worst site: layers × sublayers clones per round).
  // One clone per observation round is the accepted budget.
  const liveStyleSources = map.getStyle?.()?.sources;
  const layers = hudLayers.map((hud) => {
    const attested = hud._mapspecFingerprint === mapspecFingerprint;
    // v2(audit FE4)：期望子层族按**家族键联合**匹配 —— desired 的层 id
    // 有两种形态：HUD 行经 hudStateToMapSpec 展开为 `${hud.id}__*` 子层；
    // committed spec 层保持平铺 id（spec 层 id 或 _mapspecLayerId）。
    // 旧过滤只认 `hud.id+'__'`：ref-mounted 行（hud.id=geojson ref，
    // spec 层 id=product-*）与恢复行（hud.id=spec id 但 desired 是平铺
    // id）都得到空集 → 观察证据恒 visible:false（与真实地图无关），
    // 持续饿死感知/修复环。与 layer-identity 的别名规则保持一致。
    const familyKeys = (
      [hud.id, hud._mapspecLayerId].filter(Boolean) as string[]
    );
    const seen = new Set<string>();
    const expected = desired.layers.filter((candidate) => {
      if (seen.has(candidate.id)) return false;
      const matched = familyKeys.some(
        (key) => candidate.id === key
          || candidate.id.startsWith(`${key}${SUBLAYER_SEP}`),
      );
      if (matched) seen.add(candidate.id);
      return matched;
    });
    const liveByExpected = expected.map(
      (candidate) => ({ candidate, actual: map.getLayer?.(candidate.id) }),
    );
    const live = liveByExpected.filter(({ actual }) => !!actual);
    const visibility = liveByExpected.map(({ candidate, actual }) => (
      !!actual
      && liveProperty(map, actual, candidate.id, "layout", "visibility") !== "none"
    ));
    const sourceConverged = expected.length > 0 && expected.every((candidate) => {
      const desiredSource = desired.sources[candidate.source];
      const appliedSource = applied?.sources[candidate.source];
      const liveSource = map.getSource?.(candidate.source);
      const liveType = liveStyleSources?.[candidate.source]?.type ?? liveSource?.type;
      const expectedLiveType = desiredSource?.type === "raster"
        ? "image"
        : desiredSource?.type;
      return (
        !!desiredSource
        && appliedSource === desiredSource
        && !!liveSource
        && liveType === expectedLiveType
      );
    });
    const styleConverged = (
      attested
      && styleLoaded
      && !reconcileError
      && sourceConverged
      && expected.length === live.length
      && expected.every((candidate) => layerConverged(map, candidate))
    );
    // B2（workbench-v4）：attestation 只证明「观测代次与 store 盖章代次同一」，
    // 不是 runtime 收敛本身。用户 presentation 编辑清空 _mapspecFingerprint 后
    // attested 永假，但地图与 desired 可能完全一致 —— 状态派生若读
    // style_converged 会把这种行永久标成「待同步」。presentation_converged
    // 是**不含鉴权条款**的同一判定（additive 字段，服务端忽略未知键），
    // 供前端状态词表与假 stale 修复使用；修复域仍以 style_converged 为准。
    const presentationConverged = (
      styleLoaded
      && !reconcileError
      && sourceConverged
      && expected.length === live.length
      && expected.every((candidate) => layerConverged(map, candidate))
    );
    const rasterSource = (
      hud.source
      && typeof hud.source === "object"
      && "image" in hud.source
      && "bbox" in hud.source
    ) ? hud.source as { image: string; bbox: [number, number, number, number] } : null;
    return {
      id: hud._mapspecLayerId ?? hud.id,
      runtime_store_id: hud.id,
      name: hud.name,
      type: hud.type,
      group: hud.group,
      _refId: hud._refId,
      _descriptor: hud._descriptor,
      visible: visibility.length > 0 && visibility.every(Boolean),
      // Runtime quality evidence must come from MapLibre, not the potentially
      // stale HUD projection. Divergent/expression opacities remain unevaluated.
      opacity: liveConstantOpacity(map, liveByExpected),
      style: hud.style,
      legend_spec: hud.legend_spec,
      // A user presentation edit clears the server generation attestation in
      // the store. Never reuse the old projection digest for that new state.
      projection_fingerprint: attested ? hud._mapspecProjectionFingerprint : undefined,
      repair_action_id: hud._mapspecRepairActionId,
      intent_generation: hud._intentGeneration,
      raster_image: rasterSource?.image,
      raster_bbox: rasterSource?.bbox,
      source_converged: sourceConverged,
      style_converged: styleConverged,
      presentation_converged: presentationConverged,
      generation_attested: attested,
      runtime_layer_count: live.length,
      runtime_layer_ids: expected.map((candidate) => candidate.id).slice(0, 16),
      // V5 W5 rendered-state telemetry（optional，服务端逐层核对用）：
      // render_complete = 样式收敛 ∧ 全部源已加载完（无 pending 瓦片请求）；
      // source_status 三态 —— 'error' 仅当期望源在 live style 里整体缺席
      // （真实源失败；review R1 #3：spec 未收敛只是 pending，不是 error，
      // 否则与 source_converged warning 双报且互相矛盾）；
      // feature_count 仅对 geojson/vector 源发布（review R1 #4：栅格/
      // 图片源 querySourceFeatures 恒空，零值不是证据）。
      render_complete: styleConverged
        && expected.every((candidate) => map.isSourceLoaded?.(candidate.source) !== false),
      source_status: (() => {
        const missingSource = expected.some(
          (candidate) => !map.getSource?.(candidate.source));
        if (missingSource && expected.length > 0) return 'error';
        const allLoaded = expected.every(
          (candidate) => map.isSourceLoaded?.(candidate.source) !== false);
        return allLoaded ? 'loaded' : 'pending';
      })(),
      feature_count: (() => {
        // review R2 #5：仅对 queryable（geojson/vector）源求和 —— 栅格/
        // 图片源无要素语义；混合族不因一个栅格源丢掉整个计数。
        const sourceTypes = new Map<string, string>();
        for (const candidate of expected) {
          const sid = String(candidate.source ?? '');
          if (sid && !sourceTypes.has(sid)) {
            sourceTypes.set(
              sid, String(desired.sources[candidate.source]?.type ?? ''));
          }
        }
        const queryable = [...sourceTypes.entries()]
          .filter(([, t]) => t !== 'raster' && t !== 'image')
          .map(([sid]) => sid);
        if (!queryable.length) return undefined;
        let total = 0;
        for (const sourceId of queryable) {
          try {
            const feats = map.querySourceFeatures?.(sourceId);
            if (Array.isArray(feats)) total += feats.length;
          } catch { /* 源缺席/类型不支持 → 按未计数处理 */ }
        }
        return total;
      })(),
    };
  });
  return {
    mapspec_fingerprint: mapspecFingerprint,
    layers,
    viewport: viewportOf(map),
    style_loaded: styleLoaded,
    reconcile_error: reconcileError.slice(0, 500),
  };
}
