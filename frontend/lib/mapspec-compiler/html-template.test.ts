import { describe, it, expect } from "vitest";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { generateMapHtml, jsonForScript, MAPLIBRE_CDN_SRI, MAPLIBRE_CDN_VERSION } from "./html-template";
import pkg from "../../package.json";

describe("html-template __ORIGIN__ placeholder (#697)", () => {
  it("replaces __ORIGIN__ with location.origin before creating the map", () => {
    const style = {
      version: 8,
      sources: {
        pts: {
          type: "vector",
          tiles: ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"],
          minzoom: 0,
          maxzoom: 2,
        },
      },
      layers: [{ id: "l", type: "circle", source: "pts", "source-layer": "data" }],
      center: [0, 0] as [number, number],
      zoom: 2,
    };
    const html = generateMapHtml(style);
    // Must contain the replacement logic
    expect(html).toContain('replaceAll("__ORIGIN__"');
    expect(html).toContain("location.origin");
    // Must resolve before new maplibregl.Map and use resolvedStyle
    expect(html).toContain("__resolvedStyle");
    expect(html).toContain("new maplibregl.Map");
    // The resolved style is used as the map's style (not the raw window var)
    expect(html).toContain("style: __resolvedStyle");
    // Mechanics unchanged: __MAP_LOADED__ / __MAP_IDLE__ and window.__MAP__ still present
    expect(html).toContain("window.__MAP_LOADED__");
    expect(html).toContain("window.__MAP_IDLE__");
    expect(html).toContain("window.__MAP__ = map");
    // The literal __ORIGIN__ should appear only inside the style JSON + the replace call,
    // not as a hard-coded URL (i.e. html must not contain localhost/127.0.0.1 ports)
    expect(html).not.toContain("127.0.0.1");
    expect(html).not.toContain("localhost");
  });

  it("keeps inlined style JSON containing the placeholder verbatim", () => {
    const style = {
      version: 8,
      sources: { v: { type: "vector", tiles: ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"] } },
      layers: [],
    };
    const html = generateMapHtml(style as any);
    // The initial assignment keeps __ORIGIN__ literal — replacement happens at runtime
    expect(html).toContain("__ORIGIN__/tiles/{z}/{x}/{y}.mvt");
  });
});

describe("html-template script safety + pinned CDN (F-11)", () => {
  const evil = "</script><script>window.__PWNED__=1</script><!--";
  const style = {
    version: 8,
    sources: { p: { type: "geojson", data: { type: "FeatureCollection", features: [
      { type: "Feature", properties: { name: evil }, geometry: { type: "Point", coordinates: [0, 0] } },
    ] } } },
    layers: [],
  };

  it("never lets inlined data close the <script> element", () => {
    const html = generateMapHtml(style as any, { controls: [{ type: "scale", position: evil } as any] } as any);
    expect(html).not.toContain("</script><script>window.__PWNED__");
    expect(html).not.toContain("<!--");
    // exactly the template's own script elements close
    expect(html.match(/<\/script>/g)?.length).toBe(4);
  });

  it("jsonForScript round-trips through JSON.parse", () => {
    const v = { a: evil, b: "x\u2028y\u2029z & <b>" };
    const out = jsonForScript(v);
    expect(out).not.toMatch(/[<>&\u2028\u2029]/);
    expect(JSON.parse(out)).toEqual(v);
  });

  it("loads the app's maplibre major from the CDN with SRI, never 3.x", () => {
    const html = generateMapHtml(style as any);
    expect(html).not.toContain("maplibre-gl@3.");
    const appMajor = String((pkg as any).dependencies["maplibre-gl"]).replace(/^[^\d]*/, "").split(".")[0];
    expect(MAPLIBRE_CDN_VERSION.split(".")[0]).toBe(appMajor);
    expect(html).toMatch(/maplibre-gl\.css" rel="stylesheet" integrity="sha384-[A-Za-z0-9+/=]+" crossorigin="anonymous"/);
    const map = JSON.parse(html.match(/<script type="importmap">([^<]*)<\/script>/)![1]);
    for (const f of ["maplibre-gl.mjs", "maplibre-gl-shared.mjs", "maplibre-gl-worker.mjs"]) {
      const url = `https://unpkg.com/maplibre-gl@${MAPLIBRE_CDN_VERSION}/dist/${f}`;
      expect(map.integrity[url]).toMatch(/^sha384-/);
    }
  });

  it("SRI hashes match the installed maplibre-gl dist files when versions agree", () => {
    const req = createRequire(import.meta.url);
    const pkgPath = req.resolve("maplibre-gl/package.json");
    const installed = JSON.parse(readFileSync(pkgPath, "utf8")).version;
    if (installed !== MAPLIBRE_CDN_VERSION) return; // bump MAPLIBRE_CDN_VERSION + hashes together
    for (const [file, sri] of Object.entries(MAPLIBRE_CDN_SRI)) {
      const buf = readFileSync(pkgPath.replace(/package\.json$/, `dist/${file}`));
      expect(`sha384-${createHash("sha384").update(buf).digest("base64")}`).toBe(sri);
    }
  });
});
