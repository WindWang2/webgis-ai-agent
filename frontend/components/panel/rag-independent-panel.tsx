'use client';

interface RagIndependentPanelProps {
  open: boolean;
  onClose: () => void;
}

export function RagIndependentPanel({ open, onClose }: RagIndependentPanelProps) {
  if (!open) return null;
  return (
    <div className="fixed right-3 bottom-10 z-40 w-96 bg-white/90 backdrop-blur-xl rounded-xl border p-4">
      <div className="text-xs text-slate-400">独立RAG面板 (待实现)</div>
    </div>
  );
}

export default RagIndependentPanel;