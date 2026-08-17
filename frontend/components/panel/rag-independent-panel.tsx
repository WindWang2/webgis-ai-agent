'use client';

interface RagIndependentPanelProps {
  open: boolean;
  onClose: () => void;
}

/**
 * #607: RAG 独立面板已移除。
 *
 * 该面板展示 `ragResults`（setRagResults 全仓零调用），永远「0 个结果」；
 * 配套的实时 RAG 通路不存在，面板对用户是"系统无 RAG 能力"的错误暗示
 * （对齐 #551 诚实性原则）。page.tsx 仍持有动态 import + tweaks 开关的挂载点
 * （超出本修复文件范围），故组件保留为挂载兼容的空壳 stub：不渲染任何节点，
 * 无 a11y / 焦点 / 命中测试负担。
 */
export function RagIndependentPanel(_props: RagIndependentPanelProps) {
  return null;
}

export default RagIndependentPanel;