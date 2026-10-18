/**
 * Representation corpus 前端 parity 闸（方向 5 / v1.6）。
 *
 * 读取后端生成的 tests/cartography/representation_corpus/
 * representation_golden.json（24 case × 12 zoom，python 权威实现冻结），
 * 用 TS 裁决器（carto-ir/representation）重算 mode-at-zoom 并断言相等
 * —— 双侧任一实现漂移即红（跨语言 parity 的唯一闸）。
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  resolveRepresentationPlan,
  representationModeAtZoom,
} from "./representation";
import type { MapSpecLayer } from "@/lib/mapspec-compiler/types";

const CORPUS_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../tests/cartography/representation_corpus",
);
const golden = JSON.parse(
  readFileSync(resolve(CORPUS_DIR, "representation_golden.json"), "utf-8"),
) as {
  contract: string;
  zoom_samples: number[];
  cases: Array<{
    name: string;
    windows: Array<{ min_zoom: number; max_zoom: number; mode: string }>;
    modes_at_samples: string[];
    geometry_family: string;
    visibility_gate: Record<string, unknown>;
  }>;
};

describe("representation corpus 前端 parity（python 冻结 golden）", () => {
  it("corpus 契约与规模（≥20 case × ≥8 zoom）", () => {
    expect(golden.contract).toBe("representation_corpus.v1");
    expect(golden.cases.length).toBeGreaterThanOrEqual(20);
    expect(golden.zoom_samples.length).toBeGreaterThanOrEqual(8);
  });

  it.each(golden.cases.map((c) => [c.name, c] as const))(
    "%s：TS 裁决器 mode-at-zoom ≡ python 冻结值",
    (_name, caseRow) => {
      const layer = {
        representation: { windows: caseRow.windows },
      } as unknown as MapSpecLayer;
      const plan = resolveRepresentationPlan(layer);
      expect(plan, `${_name} windows 应可归一化`).not.toBeNull();
      for (let i = 0; i < golden.zoom_samples.length; i++) {
        const zoom = golden.zoom_samples[i];
        const mode = representationModeAtZoom(plan!, zoom);
        expect(mode, `${_name} @zoom=${zoom}`).toBe(caseRow.modes_at_samples[i]);
      }
      // 结构门投影 parity：hidden 首窗 → min_zoom 一致（frontend resolver
      // 的门由 visibility 块承载；此处锁窗口→门的推导一致性由 golden 值
      // 直接对拍）。
      const gate = caseRow.visibility_gate as { min_zoom?: number; max_zoom?: number };
      if (gate && typeof gate.min_zoom === "number") {
        const firstVisible = caseRow.windows.find((w) => w.mode !== "hidden");
        expect(firstVisible?.min_zoom).toBe(gate.min_zoom);
      }
    },
  );
});
