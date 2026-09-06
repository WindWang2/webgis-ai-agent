/**
 * 披露族共享标签映射（ADR-0101 D6：live 渲染器与 canvas 导出同一
 * 词表 —— kind 内码不得泄漏到可见/可读文本）。
 */
export const UNCERTAINTY_KIND_LABELS: Record<string, string> = {
  interval: '区间',
  variance: '方差',
  confidence: '置信度',
  sample: '样本',
  model: '模型',
};

export function uncertaintyKindLabel(kind: string | undefined): string {
  const key = kind && kind.trim() ? kind : 'interval';
  return UNCERTAINTY_KIND_LABELS[key] ?? key;
}
