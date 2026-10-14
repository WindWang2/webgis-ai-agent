import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import path from "node:path";

/**
 * #1597 — Serve the maplibre-gl Web Worker (+ its self-contained shared module)
 * from node_modules at runtime.
 *
 * maplibre-gl v6 loads vector (MVT) tiles in a Web Worker. Its default worker
 * URL is computed from `import.meta.url`; in the Turbopack dev environment that
 * resolves to a non-`http(s):` value, so `defaultWorkerUrl()` returns `""` and
 * `new Worker("")` throws — the worker never loads and MVT tiles never fetch.
 * The worker script imports `./maplibre-gl-shared.mjs`, so both files are
 * served here (the shared module has no further imports). The URL is absolute
 * (same-origin) so maplibre creates it as a module worker, and the relative
 * import in the worker resolves back to this route.
 */
const ALLOWED = new Set(["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]);

type Ctx = { params: Promise<{ file: string }> };

export async function GET(_req: Request, { params }: Ctx) {
  const { file } = await params;
  if (!ALLOWED.has(file)) {
    return new NextResponse("Not Found", { status: 404 });
  }
  const filePath = path.join(process.cwd(), "node_modules", "maplibre-gl", "dist", file);
  try {
    const content = await readFile(filePath, "utf-8");
    return new NextResponse(content, {
      headers: {
        "Content-Type": "text/javascript; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return new NextResponse("Not Found", { status: 404 });
  }
}
