import { diffSpecs, SpecPatch } from "./reconciler";
import { MapSpec } from "./types";

export class WorkerReconcilerBridge {
  private worker: Worker | null = null;
  private pending = new Map<string, (patch: SpecPatch) => void>();
  private reqId = 0;

  constructor() {
    if (typeof window !== "undefined" && typeof Worker !== "undefined") {
      try {
        this.worker = new Worker(new URL("./reconciler.worker.ts", import.meta.url), {
          type: "module",
        });
        this.worker.onmessage = (e: MessageEvent) => {
          const { id, patch } = e.data || {};
          const resolver = this.pending.get(id);
          if (resolver) {
            this.pending.delete(id);
            resolver(patch);
          }
        };
      } catch (err) {
        console.warn("Failed to initialize reconciler worker, falling back to sync main thread diffing:", err);
        this.worker = null;
      }
    }
  }

  public async diffSpecsAsync(prev: MapSpec | null, next: MapSpec): Promise<SpecPatch> {
    if (!this.worker) {
      return diffSpecs(prev, next);
    }
    return new Promise((resolve) => {
      const id = `diff_${++this.reqId}`;
      this.pending.set(id, resolve);
      this.worker!.postMessage({ id, prev, next });
    });
  }

  public destroy(): void {
    if (this.worker) {
      this.worker.terminate();
      this.worker = null;
    }
    this.pending.clear();
  }
}
