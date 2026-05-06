'use client';

interface AgentEnvHudProps {
  open: boolean;
  onClose: () => void;
}

export function AgentEnvHud({ open, onClose }: AgentEnvHudProps) {
  if (!open) return null;
  return (
    <div className="fixed right-3 top-1/2 -translate-y-1/2 z-40 w-80 bg-white/90 backdrop-blur-xl rounded-xl border p-4">
      <div className="text-xs text-slate-400">Agent环境感知 (待实现)</div>
    </div>
  );
}

export default AgentEnvHud;