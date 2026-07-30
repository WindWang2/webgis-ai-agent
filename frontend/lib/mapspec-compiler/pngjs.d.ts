/**
 * Minimal ambient declaration for pngjs (used by runtime-validate.ts to decode
 * the captured canvas PNG). pngjs ships no bundled types; this declares just
 * the sync read API the validator uses.
 */
declare module "pngjs" {
  export interface PNGData {
    width: number;
    height: number;
    data: Buffer | Uint8Array;
  }
  export const PNG: {
    sync: {
      read(buffer: Buffer): PNGData;
    };
  };
}
