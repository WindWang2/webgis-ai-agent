/**
 * H02 Wave 7 — 场景工厂 TS 侧：与 Python 消费同一份 scenarios.json
 * （跨语言单一场景真相，仓库 golden_corpus 模式）。
 *
 * 维度：headless style 编译（层产出/zoom 门/热力窗）、MapSpec 版本门
 * （未知版本拒收）、ACK per-source 数据代次对账（stale 拒收）。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { compileMapSpec } from "@/lib/mapspec-compiler/compiler";
import { checkMapSpecVersion } from "@/lib/carto-ir/version";
import { buildRenderApplyAck } from "@/lib/render-protocol/render-apply-ack";
import type { MapSpec } from "@/lib/mapspec-compiler/types.generated";

const SCENARIOS_PATH = resolve(
  __dirname,
  "../../../tests/cartography/golden_corpus/h02_scenarios/scenarios.json",
);
const payload = JSON.parse(readFileSync(SCENARIOS_PATH, "utf-8")) as {
  positive_count: number;
  negative_count: number;
  scenarios: Array<{
    id: string;
    kind: "positive" | "negative";
    spec: MapSpec;
    expect: Record<string, unknown>;
    tags: string[];
  }>;
};
const positives = payload.scenarios.filter((s) => s.kind === "positive");
const negatives = payload.scenarios.filter((s) => s.kind === "negative");

describe("H02 scenario factory (TS side)", () => {
  it("inventory matches generator contract", () => {
    expect(payload.positive_count).toBeGreaterThanOrEqual(18);
    expect(payload.negative_count).toBeGreaterThanOrEqual(12);
    expect(positives.length).toBe(payload.positive_count);
  });

  it.each(positives.map((s) => [s.id, s] as const))(
    "%s compiles to a real MapLibre style",
    (_id, scenario) => {
      const result = compileMapSpec(scenario.spec);
      const style = result.style as unknown as {
        layers: Array<{ id: string; type: string }>;
      };
      expect(style.layers.length).toBeGreaterThanOrEqual(
        scenario.spec.layers?.length ?? 1,
      );
      // 版本门：1.6 已知版本零拷贝通过
      expect(checkMapSpecVersion(scenario.spec).ok).toBe(true);
    },
  );

  it("heatmap window compiles a heatmap sublayer (semantic zoom)", () => {
    const scenario = positives.find((s) => s.id === "pos_heatmap_window")!;
    const result = compileMapSpec(scenario.spec);
    const style = result.style as unknown as {
      layers: Array<{ id: string; type: string; minzoom?: number; maxzoom?: number }>;
    };
    const heat = style.layers.find((l) => l.type === "heatmap");
    expect(heat).toBeDefined();
    expect(heat!.minzoom).toBe(10);
    expect(heat!.maxzoom).toBe(24);
  });

  it("cluster window compiles cluster sublayers", () => {
    const scenario = positives.find((s) => s.id === "pos_cluster_window")!;
    const result = compileMapSpec(scenario.spec);
    const style = result.style as unknown as { layers: Array<{ id: string }> };
    expect(style.layers.some((l) => l.id.includes("cluster"))).toBe(true);
  });

  it("unknown mapspec version is rejected by the version gate", () => {
    const scenario = negatives.find((s) => s.id === "neg_unknown_version")!;
    expect(scenario.expect.ts_version_gate_rejects).toBe(true);
    const verdict = checkMapSpecVersion(scenario.spec);
    expect(verdict.ok).toBe(false);
    // 版本门拒收下 headless 编译产出显式失败报告而非部分渲染
    const result = compileMapSpec(scenario.spec);
    expect((result.style as unknown as { layers: unknown[] }).layers).toHaveLength(0);
  });

  it("ACK marks per-source stale data revision (stale rejection)", () => {
    const scenario = positives.find((s) => s.id === "pos_point_simple")!;
    const spec = scenario.spec;
    // 模拟：信封声明 source revision 3，应用侧实际 content_revision 7
    const applied = JSON.parse(JSON.stringify(spec)) as MapSpec & {
      sources: Record<string, { content_revision?: number }>;
    };
    applied.sources["pts"].content_revision = 7;
    const ack = buildRenderApplyAck({
      spec,
      applied,
      revision: 1,
      mapIdle: true,
      work: {
        schema: "render_work.v1",
        work_id: "rw-1-abcdef012345",
        work_fingerprint: "wfp",
        session_id: "sess-x",
        mapspec_revision: 1,
        mapspec_fingerprint: "fp",
        observation_target_seq: 0,
        data_dependencies: [
          { source_id: "pts", kind: "geojson", revision: 3, fingerprint: "x", ref: "" },
        ],
        layer_work: [],
        component_work: [],
        invalidation: {},
        required_capabilities: [],
        diagnostics: [],
      } as never,
    });
    const sources = (ack as unknown as { sources?: Array<{ status?: string }> })
      .sources ?? [];
    expect(sources.some((s) => s.status === "stale")).toBe(true);
  });
});
