import { diffSpecs } from "./reconciler";
import { MapSpec, SpecPatch } from "./types";

export interface DiffRequest {
  id: string;
  prev: MapSpec | null;
  next: MapSpec;
}

export interface DiffResponse {
  id: string;
  patch: SpecPatch;
  error?: string;
}

if (typeof self !== "undefined") {
  self.onmessage = (event: MessageEvent<DiffRequest>) => {
    const { id, prev, next } = event.data || {};
    try {
      const patch = diffSpecs(prev, next);
      const response: DiffResponse = { id, patch };
      self.postMessage(response);
    } catch (err: any) {
      self.postMessage({
        id,
        patch: { sources: [], layers: [] },
        error: err?.message || String(err),
      } as DiffResponse);
    }
  };
}
