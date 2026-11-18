/** Literal map action dictionary keys / indexed assignments, not local RPC variables. */
export function scanMapCommandEmissions(source: string): string[] {
  const pattern = /(?<![A-Za-z0-9_])["']command["']\s*(?:\]\s*=|:)\s*["']([A-Za-z][A-Za-z0-9_]{0,48})["']/g;
  return Array.from(source.matchAll(pattern), (match) => match[1].toLowerCase());
}
