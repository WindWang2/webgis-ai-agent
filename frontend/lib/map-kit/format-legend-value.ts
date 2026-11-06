import { currentLocale } from '@/lib/i18n/t';

export function formatLegendValue(n: number): string {
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${significant(n / 1_000_000)}M`;
  if (abs >= 10_000) return `${significant(n / 1_000)}k`;
  // #1628/#1436：数字分组随应用语言格式化（zh-CN 用户输出不变），不硬编码。
  if (Number.isInteger(n)) return n.toLocaleString(currentLocale());
  return significant(n);
}

/** Up to 3 significant digits, trailing zeros dropped, never rounded to zero. */
function significant(n: number): string {
  const abs = Math.abs(n);
  const decimals = abs >= 100 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : 3;
  const out = Number(n.toFixed(decimals));
  // A non-zero input must never print as zero: fall back to exponential.
  if (out === 0 && n !== 0) return n.toExponential(1);
  return String(out);
}
