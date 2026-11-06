/** Reflow the frozen text projection for one renderer's physical width.
 * No truncation or text replacement; Unicode code points stay intact. */
export function reflowLines(lines: readonly string[], maxWidth: number, measure: (text: string) => number): string[] {
  if (!(maxWidth > 0)) return [...lines];
  return lines.flatMap((line) => {
    const output: string[] = [];
    let current = '';
    for (const character of line) {
      const candidate = current + character;
      const width = measure(candidate);
      if (current && Number.isFinite(width) && width > maxWidth) {
        output.push(current);
        current = character;
      } else current = candidate;
    }
    if (current || output.length === 0) output.push(current);
    return output;
  });
}
