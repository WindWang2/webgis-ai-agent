/**
 * MapSpec v1.7（G3D02）：model3d 源 × runtime reconcile × per-source ACK 集成。
 *
 * House style（runtime.test.ts 同款）：手写 stub map 记账 —— 断言
 * model3d 源**不进** MapLibre style sources（scene3d 管理器全权），但
 * applied spec 携带完整源定义供 buildRenderApplyAck 的 per-source 对账。
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MapSpecRuntime } from "./runtime";
import type { MapSpec } from "@/lib/mapspec-compiler/types";
import { buildRenderApplyAck } from "@/lib/render-protocol/render-apply-ack";
import { parseRenderWorkEnvelope } from "@/lib/render-protocol/render-work";
import { resetSceneAssetStatusForTests, recordSceneAssetStatus } from "@/lib/map-kit/scene3d/status";

function makeMockMap() {
  const sources: Record<string, any> = {};
  const layers: any[] = [];
  const calls = {
    addSource: [] as Array<{ id: string; def: any }>,
    removeSource: [] as string[],
    addLayer: [] as Array<{ def: any }>,
    removeLayer: [] as string[],
  };
  const map: any = {
    isStyleLoaded: () => true,
    getStyle: () => ({ sources, layers }),
    getSource: (id: string) => sources[id],
    getLayer: (id: string) => layers.find((l) => l.id === id),
    addSource: (id: string, def: any) => {
      sources[id] = { ...def, setData: vi.fn() };
      calls.addSource.push({ id, def });
    },
    removeSource: (id: string) => {
      delete sources[id];
      calls.removeSource.push(id);
    },
    addLayer: (def: any) => {
      layers.push(def);
      calls.addLayer.push({ def });
    },
    removeLayer: (id: string) => {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
      calls.removeLayer.push(id);
    },
    moveLayer: vi.fn(),
    setPaintProperty: vi.fn(),
    setLayoutProperty: vi.fn(),
    setFilter: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    _fire: (ev: string) => {
      for (const h of map._listeners.get(ev) ?? []) h();
    },
    _listeners: new Map<string, Array<() => void>>(),
  };
  return { map, calls };
}

function specWithModel3d(): MapSpec {
  return {
    version: "1.7",
    view: { center: [104.06, 30.67], zoom: 14 },
    sources: {
      "model-a": {
        type: "model3d",
        format: "glb",
        url: "https://assets.test/campus.glb",
        content_revision: 3,
        data_fingerprint: "sha256:deadbeef",
      },
    },
    layers: [],
    scene: {
      mode: "3d",
      assets: [
        {
          id: "campus",
          source: "model-a",
          placement: { anchor: [104.06, 30.67], altitude_m: 500 },
        },
      ],
    } as never,
  } as MapSpec;
}

describe("runtime × model3d source", () => {
  beforeEach(() => {
    resetSceneAssetStatusForTests();
  });

  it("reconcile skips MapLibre source mounting but records applied spec", async () => {
    const { map, calls } = makeMockMap();
    const rt = new MapSpecRuntime(map);
    await rt.reconcileAsync(specWithModel3d());
    // model3d 绝不进 MapLibre style sources
    expect(calls.addSource.map((c) => c.id)).not.toContain("model-a");
    expect(map.getSource("model-a")).toBeUndefined();
    // applied spec 携带完整源定义（ACK 对账基准）
    const applied = rt.getAppliedSpec();

    expect(applied?.sources?.["model-a"]).toMatchObject({
      type: "model3d",
      format: "glb",
      content_revision: 3,
    });
    expect(applied?.scene?.assets?.[0]?.id).toBe("campus");
    rt.dispose();
  });

  it("reconcile is idempotent for model3d (no churn on repeat)", async () => {
    const { map, calls } = makeMockMap();
    const rt = new MapSpecRuntime(map);
    const spec = specWithModel3d();
    await rt.reconcileAsync(spec);
    const addsAfter1 = calls.addSource.length;
    await rt.reconcileAsync(spec);
    expect(calls.addSource.length).toBe(addsAfter1);
    rt.dispose();
  });

  it("per-source ACK reconciles model3d data dependencies (applied + stale)", async () => {
    const { map } = makeMockMap();
    const rt = new MapSpecRuntime(map);
    const spec = specWithModel3d();
    await rt.reconcileAsync(spec);
    const applied = rt.getAppliedSpec();

    // 同代 work 信封（model3d 依赖 revision=3）
    const envelope = parseRenderWorkEnvelope({
      schema_version: "render_work.v1",
      work_id: "w-1",
      session_id: "s-1",
      mapspec_revision: 7,
      mapspec_fingerprint: "carto-sha256:abc",
      observation_target_seq: 1,
      data_dependencies: [
        { source_id: "model-a", kind: "model3d", revision: "3", fingerprint: "sha256:deadbeef" },
      ],
      layer_work: [],
      component_work: [],
      invalidation: { data_changed: [], layers_added: [], layers_removed: [], components_changed: [] },
      required_capabilities: ["model3d"],
      diagnostics: [],
      work_fingerprint: "fp-1",
    });
    expect(envelope).not.toBeNull();

    const ack = buildRenderApplyAck({
      spec,
      applied,
      revision: 7,
      mapIdle: true,
      work: envelope,
    });
    expect(ack.sources).toBeDefined();
    const entry = ack.sources?.find((s) => s.source_id === "model-a");
    expect(entry).toMatchObject({ status: "applied", data_revision: "3" });

    // 旧代（信封声明 revision 5 > applied 3）→ stale 披露
    const envelopeStale = parseRenderWorkEnvelope({
      schema_version: "render_work.v1",
      work_id: "w-2",
      session_id: "s-1",
      mapspec_revision: 7,
      mapspec_fingerprint: "carto-sha256:abc",
      observation_target_seq: 1,
      data_dependencies: [
        { source_id: "model-a", kind: "model3d", revision: "5" },
      ],
      layer_work: [],
      component_work: [],
      invalidation: { data_changed: [], layers_added: [], layers_removed: [], components_changed: [] },
      required_capabilities: ["model3d"],
      diagnostics: [],
      work_fingerprint: "fp-2",
    });
    expect(envelopeStale).not.toBeNull();
    const ackStale = buildRenderApplyAck({
      spec,
      applied,
      revision: 7,
      mapIdle: true,
      work: envelopeStale,
    });
    const staleEntry = ackStale.sources?.find((s) => s.source_id === "model-a");
    expect(staleEntry).toMatchObject({
      status: "stale",
      reason_code: "stale_data_revision",
      data_revision: "3",
    });
    expect(ackStale.status).toBe("partial");
    rt.dispose();
  });

  it("scene3d status ring flows into render observation payload", async () => {
    recordSceneAssetStatus("campus", "mounted", "2tri");
    recordSceneAssetStatus("tower", "load_failed", "http_error");
    const { collectRenderObservation } = await import("./render-observation");
    const { map } = makeMockMap();
    const obs = collectRenderObservation({
      map,
      spec: specWithModel3d(),
      layers: [],
      mapspecFingerprint: "carto-sha256:x",
      mapspecRevision: 1,
      errorRing: { drain: () => [] } as never,
      mapIdle: true,
    });
    const block = obs.scene3d_assets as Array<{ asset_id: string; code: string }>;
    expect(Array.isArray(block)).toBe(true);
    expect(block.some((e) => e.asset_id === "campus" && e.code === "mounted")).toBe(true);
    expect(block.some((e) => e.asset_id === "tower" && e.code === "load_failed")).toBe(true);
  });
});
